# 07 - Auditoría de portabilidad: de Qwen3.8-Flash-Next a GLM-5.3-Flash (revisión ronda 2)

Fork auditado: `/home/user/Strata3060` (Strata v0.1.38, ~74.000 líneas en `src/` + `include/`). Auditoría de solo lectura, sin GPU.

## Resumen

**Revisión de la ronda 2.** La ronda 1 supuso un modelo tipo DeepSeek/GLM-4.5 (GQA, residual estándar, sin recurrencia lineal). El informe 06 (`06-glm-research.md`) verificó que **GLM-5.3-Flash es un hermano estructural de Qwen3.8-Flash-Next**: 34 capas KDA + 11 capas DSA intercaladas igual que GDN/QSA (DSA en 3, 7, ..., 43), 4 flujos de residual (mHC), indexador de pools de 4 con top-k 2048, MoE sigmoide top-8 de 288 expertos con 1 compartido, sin PLE. Esto cambia el mapa, el diseño y las estimaciones; el detalle está en la sección nueva **"Revisión con la arquitectura real de GLM-5.3-Flash"**, y el plan y los riesgos están reescritos. Las tablas de "Supuestos específicos de Qwen" siguen siendo válidas para el lado Qwen; donde contenían hipótesis de GLM-4.5 están corregidas con la marca **[R2]**.

1. **El motor está "a medias" parametrizado, y la mitad compilada es la que importa.** El código de host (sesión, verify, capas) recibe un `ModelGeometry` por referencia (`include/strata/core/layout.hpp:27-59`), pero (a) sus valores por defecto son los de Qwen y **no se leen del GGUF** salvo `expert_count`, `expert_used_count` y las claves `rope.*` (`src/program/generate.cpp:1845-1868`), y (b) los kernels, el camino de prefill y el CPU de expertos llevan copias `constexpr` (2560, 640, 512, 10, 4, 320, 10240, 128...) y **se niegan a arrancar** si no coinciden (`src/prefill/prefill.cpp:90-91,671`; `src/core/expert_source.cpp:1735`). Único precedente de variación: el Coder (512 -> 256 expertos).
2. **El bloque de Qwen y el de GLM-5.3-Flash tienen la misma estructura**: `gr_read -> mixer -> gr_write -> gr_read -> MoE -> gr_write` con 4 flujos, intercalado `layer % 4 == 3`, MoE con experto compartido y protocolo doorbell (`src/core/layer.cpp:1142-1315`). Ese bloque está **escrito tres veces** (un token: `layer.cpp`/`session.cpp`; ventana de verificación: `verify.cpp`; prefill: `prefill.cpp`) más una cuarta en el drafter MTP (`mtp.cpp`). Como GLM conserva la estructura, **no hace falta duplicar esos bucles: basta cambiar las operaciones** (GDN->KDA, QSA->DSA, GR->mHC, router) mediante ranuras de operación. Esto sustituye a la recomendación de "componentes paralelos" de la ronda 1.
3. **Con experts nativos el decode solo existe como ventana de verificación (hasta T=8 tokens)**: `generate.cpp:1776-1781,1950-1956` exige `--spec T>=2` y `--prefill`. El `Verifier` y el `Prefill` de GLM no son opcionales ni siquiera para un MVP.
4. **Lo que se reutiliza tal cual es lo valioso del producto**: caché de expertos en VRAM, arena en RAM fijada, pool de CPU con `vec_dot` de ggml-cpu, doorbell GPU/CPU, streaming de expertos en prefill, MMQ de ggml, modo de expertos leídos del SSD (`docs/UNSLOTH_Q4.md`), sampler (`n_vocab` en runtime; 154.880 < 262.144), controlador especulativo, API, MCP, UI web, planner. Son ~18.000 de las ~74.000 líneas.
5. **Mapa de componentes frente a GLM-5.3-Flash** (detalle en la sección de revisión):
   - **GDN -> KDA (34 capas): generalizable (G).** Misma ecuación (delta rule, S=128, conv de 4, L2-norm de q/k); cambia el decaimiento, de escalar por cabeza a vector por canal de clave. Afecta a 6 sitios de decaimiento (`gdn.cu:65`, `fused_gdn.cu:38`, `native_gdn.cu:62`, `verify_kernels.cu:144`, `prefill/kernels.cu:296,355`) más el preproceso de la puerta. El prefill de Strata es un scan secuencial por cabeza (no hay forma chunkwise que reescribir).
   - **QSA + indexador -> DSA + indexador (11 capas): el indexador, el top-k y el streaming de KV son los parientes más cercanos del motor** (mismo pool de 4, mismo presupuesto de 2048 celdas, misma regla de cola, misma dim 128, selección idéntica bajo 2.051 celdas). Cambian 32 cabezas con pesos, compresión aprendida y NoPE. **La atención es nueva (N)**: MLA absorbida con 64 cabezas sobre un latente de 512 donde K=V; los kernels actuales son GQA 24/2 de HD=256 (`qsa_decode_attn.cu:16-17`).
   - **GR -> mHC: misma familia (4 flujos), matemática distinta (N).** GR es un cuello low-rank de 320 con puerta sigmoide; mHC es una proyección `[24,16384]` + Sinkhorn de 20 iteraciones sobre una matriz 4x4. Kernels nuevos y más baratos; el plumbing (buffer `[hc,n_embd]`, broadcast, escritura pendiente fusionada, media final) se reutiliza.
   - **Router softmax top-10/512 -> sigmoide + sesgo `noaux_tc` top-8/288 x2,5 (G/N).** El peso del router es BF16 (compatible con `project_bf16`); solo el sesgo es F32. `route_kernel<REG>` ya es por registros (288 = 9 x 32). Añadir `swiglu_limit=10` en >=10 sitios de SwiGLU (CPU y GPU), capas 0-2 densas (12288) y experto compartido sin puerta.
   - **MTP: una capa con DSA + MoE de 288 expertos (G/N).** Sus expertos (~2,1 GB a IQ2_XS) no caben residentes en VRAM como los de Qwen (708 MB, `mtp.cpp:184`).
   - **PLE, mrope y RoPE: ausentes (NA).** GLM-5.3-Flash es NoPE.
6. **Cobertura de cuantización**: los kernels GPU de expertos "grouped" cubren gate/up {IQ2_XXS, IQ2_XS, IQ3_XXS, IQ3_S, IQ2_S, IQ4_XS, IQ1_M, Q2_0, Q4_K, Q5_K, Q5_0, Q8_0} y down {IQ4_NL, IQ4_XS, Q2_0, Q5_1, Q5_0, Q8_0} (`iq_kernels.cu:510-512`); `setup.py:875-886` ya declara que los K-quants estándar (p. ej. UD-Q2_K_XL) no son utilizables hoy ni para Qwen. Con las dimensiones de GLM (todas múltiplos de 256) los K-quants valdrían también en down, pero faltan los kernels. Ampliar tipos es trabajo crítico (2-3 pw).
7. **serve/ y setup.py**: el servidor es en su mayoría genérico (protocolo "ids de token dentro, ids fuera", `server.py:17-20`), pero hay Qwen cableado en cinco puntos: regex del tokenizador `qwen35` (`tools/strata_tokenizer.py:56-65,89`; el servidor ni pasa `pre`, `server.py:2892`), tokens de parada (`server.py:59,993`), parser de tool-calls XML de Qwen (`frontend.py:274-352`), plantilla y catálogo/binarios (`setup.py:100-190`, duplicado en `tools/strata_mcp.py:52-72`). Hechos verificados de GLM: formato `<tool_call>NOMBRE<arg_key>..</arg_key><arg_value>..</arg_value>`, thinking siempre activo, `reasoning_effort` low/high/max, EOS 154820/154827/154829.
8. **Realidad de memoria (la dificultad principal pasa a ser esta)**: GLM-5.3-Flash tiene 321 B parámetros; los expertos solos ocupan ~88-90 GB a IQ2_XS (Qwen: 34-36 GB), cada experto pesa 7,3 MB (Qwen: 1,4 MB) y cada token lee 2,44 GB de expertos (Qwen: 0,66 GB). En un PC de 32-64 GB de RAM hay que leer expertos del SSD: con la tasa efectiva que Strata mide con Qwen (1,5-2,0 GB/s) salen **~0,7-1,7 tok/s (32 GB), ~0,9-2,6 (48 GB), ~1,3-3,9 (64 GB)**, y hasta ~2-7 si un NVMe PCIe 4 sostiene 5 GB/s (06 estima 4-12 con 3,0-5,5 GB/s); con 128 GB, 11-26 tok/s (estimación de 06). Ninguna cifra está medida con GLM. Una variante podada REAP-50 (~44 GB) cabría en 64 GB pero no tiene evaluación de calidad publicada. Ver sección "R8".
9. **Diseño propuesto**: `ModelArch` leído del GGUF + `LayerPlan` ligero (el intercalado ya sirve; faltan el eje FFN denso y la capa MTP) + **ranuras de operación** (`ResidualOps`, `LinearMixerOps`, `SparseMixerOps`, `RouterOps`, `ExpertActOps`) llamadas desde los bucles existentes. Qwen = punteros a las funciones actuales, por lo que la garantía "Qwen bit-idéntico" se comprueba con volcado de grafos CUDA y logits.
10. **Esfuerzo revisado (estimación gruesa, +-40 %, ingenieros que ya conocen el código y con acceso a GPU)**: Fase 0 refactor 6-10 pw; Fase 1 forward GLM 32-50 pw (KDA 4-7, DSA 10-16, mHC 3-5, MoE/tipos/densas 7-10, referencia 3-5, integración y bring-up 5-7); Fase 2 caché/SSD/perfil 3-5 pw; Fase 3 MTP 6-9 pw; Fase 4 serve/setup 4-6 pw; Fase 5 (visión, opcional) 2-3 pw. **Total 51-80 pw sin visión (mediana ~65)**; con 3 ingenieros, 5-7 meses. **No baja respecto a la ronda 1 (47-69 pw en el escenario MLA+DSA): se redistribuye.** Baja el riesgo de diseño (ya no hay duda GQA/MLA, ni residual estándar, ni KV caro) y el esfuerzo de plumbing; sube el de kernels nuevos (KDA, mHC, MLA absorbida), el de MTP y el de memoria/SSD.
11. **Riesgos mayores**: no hay forma de validar bit-identidad de Qwen sin GPU y modelo; el rendimiento esperable en una GPU de 12 GB es de ~0,7-4 tok/s con 32-64 GB de RAM (hasta ~7 con un NVMe rápido; nadie ha medido GLM-5.3-Flash en 12 GB); el pin de llama.cpp (2026-09-20) es anterior a la fusión de `glm5-next` (2026-09-30), así que el oráculo de paridad exige otro checkout; y varios detalles de GLM siguen sin verificar (operador mHC exacto, fórmula de la puerta KDA con cota -5, compresión kpool + APE, alcance de `swiglu_limit`).
12. **Decisión de producto pendiente (de 06 §1.5)**: el modelo que sí encaja con "hardware barato" es GLM-4.7-Flash (30B-A3B, MLA con RoPE, 64 expertos top-4, cabe en 12 GB + 32 GB). Porta otra parte del motor (MLA con RoPE y router sigmoide, sin KDA/mHC/DSA). Conviene confirmar con el usuario cuál quiere.

**Advertencia de alcance**: no tuve GPU ni los fuentes de ggml/llama.cpp (en este fork `third_party/ggml/` solo contiene `ggml-common.h`, `LICENSE` y `VERSION.txt`; el resto se descarga con `FetchContent` al configurar, `CMakeLists.txt:866-875`), y el único intento de consultar la red fue bloqueado por el entorno. **Los hechos de GLM-5.3-Flash provienen del informe 06** (config.json real, formas de tensores, PR #27773 de llama.cpp, README); lo que sé de ggml/llama.cpp proviene de memoria y está marcado **(a verificar)**.

---

## Revisión con la arquitectura real de GLM-5.3-Flash

Fuente de los hechos de GLM: `06-glm-research.md` (config.json real de `zai-org/GLM-5.3-Flash`, formas de tensores de los shards, PR #27773 de llama.cpp, README). Lo que digo de Strata viene de mi lectura del código (archivo:línea). **(a verificar)** = no confirmado en ninguna de las dos fuentes.

### R0. Qué cambia respecto a la ronda 1

| Supuesto de la ronda 1 | Realidad (06) | Efecto |
| --- | --- | --- |
| Atención GQA 96/8 con RoPE parcial, o MLA con RoPE | 11 capas **DSA = MLA sin RoPE** (NoPE) con indexador + 34 capas de **atención lineal KDA** | Desaparecen los kernels GQA de HD=128, el RoPE parcial y el KV por cabezas. Aparecen KDA y MLA absorbida |
| "Sin recurrencia lineal: GDN = NA" | 34 de 45 capas son KDA, primo directo de GDN | GDN pasa de NA a **G** (el commit de verify y el estado de conversación sí hacen falta) |
| Residual estándar pre-norm (`PreNormAdd`) | 4 flujos mHC con Sinkhorn | No hay `PreNormAdd`; GR se sustituye por mHC y se conserva el plumbing de `hc=4` |
| `LayerPlan` arbitrario necesario | Intercalado idéntico: DSA en 3,7,...,43 (`l%4==3`), 45 capas | `is_qsa_layer` + `n_qsa_layers()=n_layers/4` ya dan 11 DSA y 34 KDA (`layout.hpp:56-65`); solo faltan el eje FFN denso (capas 0-2) y la capa MTP |
| Router F32 (GEMV F32 nuevo) | `mlp.gate.weight [288,4096]` BF16; solo `e_score_correction_bias [288]` es F32 | Cabe en `project_bf16` (`layer.cpp:355-388`); basta añadir el sesgo |
| `ff` no múltiplo de 256 | `hidden 4096`, `moe_intermediate 2048`, `intermediate 12288`: todos múltiplos de 256 | Los K-quants valen también para down (hoy faltan los kernels) |
| KV de GQA ~7,7x el de Qwen (riesgo alto) | Latente 512 f16 = 11 KiB/token + indexador 5,5 KiB = ~16,9 KB/token (06 §6.3) | El riesgo desaparece: es de la escala de Qwen (13,7 KB INT8). 1M de contexto = 17,7 GB |
| Componentes paralelos (`GlmVerifier`, `GlmPrefill`...) | Estructura de bloque idéntica a la de Qwen | Mejor: ranuras de operación dentro de los bucles existentes (sección de diseño) |
| "Modelo Flash = pequeño" | 321 B / 18 B activos; experto = 25,2 M parámetros (5,1x el de Qwen); 12.096 expertos + 288 en MTP | El problema principal pasa a ser la **memoria** (R8) |
| Oráculo llama.cpp = el pin actual | El pin es del 2026-09-20 (`third_party/ggml/VERSION.txt`) y `glm5-next` se fusionó el 2026-09-30 | Hace falta otro checkout de llama.cpp como oráculo |

### R1. Mapa componente a componente

Clas.: R reutilizable, G generalizable, N nuevo, NA ausente en GLM. La columna pw es el esfuerzo de esa pieza (ver Plan).

| Pieza de Strata | En GLM-5.3-Flash | Diferencia y reutilización | Clas. | pw |
| --- | --- | --- | --- | --- |
| Intercalado y reparto de capas (`layout.hpp:56-65`) | 45 capas: DSA en 3,7,...,43; KDA en el resto (incluye 0-2 y 44) | El predicado actual vale. Falta el eje `ffn` (capas 0-2 densas) y la capa MTP (índice 45) | R (+G ligera) | 1 |
| GDN (`gdn.hpp`, `layer.cpp:223-333`) | **KDA**, 34 capas | Decaimiento por canal en vez de por cabeza; 64 cabezas q/k/v; 3 convs; puerta de salida low-rank (R2) | G | 4-7 |
| Atención QSA (`layer.cpp:848-993`, `qsa_decode_attn.cu`, `qsa_prompt_attn.cu`) | **MLA absorbida NoPE**, 11 capas | K=V=latente de 512, 64 cabezas; sin puerta de salida ni QK-norm; sin RoPE (R3) | N | 6-10 |
| Indexador, top-k y streaming de KV (`qsa.hpp:196-282`, `qsa_select.cu`, `kv_stream.hpp`) | Indexador de pools de 4, top-k 2048, 32 cabezas | El pariente más cercano del motor (R3) | G | 4-6 |
| GR (`gr.hpp`, `fused_gr.cu`, `layer.cpp:1142-1315`) | **mHC**: 4 flujos, Sinkhorn x20 | Misma familia, matemática distinta (R4) | N (+R el plumbing) | 3-5 |
| Router softmax top-10/512 (`router_top10.cu`, `native_router.cu`, `prefill/kernels.cu:616-654`) | Sigmoide + sesgo `noaux_tc`, top-8 de 288, `norm_topk`, x2,5 | Router BF16 (R5) | G/N | 1,5-2 |
| Expertos 640 x 2560 (`cpu/expert.hpp:34-45`, `native_expert.hpp:17,19`) | 2048 x 4096; 7,3 MB/experto a IQ2_XS | Capacidades de buffer y `swiglu_limit` 10 (R5) | G | 2,5-4 |
| Tipos de cuantización (`iq_kernels.cu:510-512`) | Mezclas de Unsloth/bartowski (K-quants, IQ) | Faltan tipos en GPU/MMQ/embed | G | 2-3 |
| Capas 0-2 densas y experto compartido (`layer.cpp:390-423`) | MLP densa 12288; 1 compartido de 2048 sin puerta | Reutilizar `shared_expert` sin puerta | G | 1-1,5 |
| MTP (`mtp.cpp`) | 1 capa con DSA + MoE de 288 expertos + `eh_proj [4096,8192]` | Expertos fuera de VRAM (R5) | G/N | 6-9 (Fase 3) |
| PLE, mrope, RoPE, tabla n-gram | Ausentes (NoPE, sin PLE) | Basta no activarlos (`PleRun::ready()==false`, `layer.hpp:551-562`); hoy `--native` exige `--ple-gguf` (`generate.cpp:1609-1613`) | NA (+G ligera) | 0,5 |
| Cabeza y embedding (`layer.cpp:1060-1116`) | Media de los 4 flujos antes de `lm_head`; embeddings no atados; vocab 154.880 | `output_hc_*` de Qwen = un `gr_read` sin inject; en GLM es una media | G | 0,5 |
| Verify y commit (`verify.cpp:960-1010`) | Commit de KDA (conv + recurrencia) y del indexador | Igual de complejo que el de Qwen (no es un no-op, a diferencia de lo que dije en la ronda 1) | G | incluido en integración |
| Cache de conversación (`conversation_state.cpp`) | KDA + latente + indexador | Mismo esquema GDN+QSA con otras formas | G | incluido |
| Visión | ViT de 24 capas, mmproj ~1,1 GB | NoPE: no necesita `mrope`; solo sustituir embeddings | NA para el MVP (Fase 5) | 2-3 |
| Tokenizador, plantilla, tools, stops | Ver tabla de serve/setup | | N/G | Fase 4 |

### R2. GDN -> KDA

**Igual**: S = 128, `d_conv = 4`, L2-norm de q y k, orden "decaer y luego actualizar rango 1" (delta rule), `y = rms_norm(o) * sigmoid(g)`, estado `(S, h_v, S)`, y la estructura de registros de los kernels (el hilo es una columna `(cabeza, j)` y sus filas viven en registros: `fused_gdn.cu:38-60`, `native_gdn.cu:62-75`).

**Cambia** (KDA frente a Qwen GDN):

| | Qwen GDN | GLM KDA | Dónde se toca |
| --- | --- | --- | --- |
| Decaimiento | escalar por cabeza `g = exp(gate[h])` de `ssm_alpha [n_embd,48]` + `ssm_dt` + `ssm_a` | **vector por canal de clave** `alpha[h][i]` (128 por cabeza) de dos GEMV low-rank `f_a [128,4096]`, `f_b [8192,128]`, con `A_log [64]`, `dt_bias [8192]` y cota inferior -5 | 6 sitios: `gdn.cu:65`, `fused_gdn.cu:38`, `native_gdn.cu:62`, `verify_kernels.cu:144`, `prefill/kernels.cu:296,355` |
| Cabezas | h_k = 16, h_v = 48 (empareja `h % h_k`) | h_k = h_v = 64 | las variantes de prefill "una cabeza-k con sus 3 cabezas-v" dejan de aplicar |
| Conv | un peso `[4,10240]` sobre q, k, v concatenados | tres pesos `[8192,1,4]` (q, k, v) = 24.576 canales | concatenar al empaquetar; `conv_silu` es genérico en `channels` (`native_gdn_preprocess.cu:53-65`) |
| Puerta de salida | `attn_gate [n_embd,6144]` | low-rank `g_a`, `g_b` | preproceso |
| beta | `ssm_beta [n_embd,48]` | `b_proj [64,4096]` | igual |
| Estado | 36 x 3,1 MB | 34 x 4,2 MB = 142,6 MB (+ conv ~10 MB) | `session.cpp:41-44` |

**Por qué el cambio es local.** Strata no materializa el estado decaído: usa `kv = g * sum_i s[i]*k[i]` y `s[i] = g*s[i] + k[i]*delta`. Con decaimiento por canal pasa a `kv = sum_i s[i]*(alpha[i]*k[i])` y `s[i] = alpha[i]*s[i] + k[i]*delta`; `alpha[i]` se carga igual que `k[i]` (`native_gdn.cu:54-57`). No hay barreras nuevas ni cambio de reparto de trabajo.

**¿Se puede generalizar el prefill por trozos?** Sí, y es más fácil de lo que parece: el "prefill por trozos" de Strata son trozos del *prompt* (hasta 8.192 tokens por vez) y la recurrencia dentro del trozo es un **scan secuencial por cabeza dentro de un kernel** (`gdn_rec_kernel`, `prefill/kernels.cu:276-335`, y variantes `_cols`/`_pipe`), no una forma chunkwise/WY. No hay álgebra de bloques que reescribir. Cuidados: (a) `HK=16, HV=48, C=10240` son `constexpr` (`prefill/kernels.cu:18`, `prefill.cpp:91`) y hay que plantillarlos (KDA: 64/64/24.576); (b) el buffer de puerta de verify pasa de `[T,HV]` a `[T,HV,S]` (`verify.cpp:301-302,526-529`: 32 KB por token y capa; 8 tokens x 34 capas = 8,9 MB); (c) `gdn_conv_commit` y `gdn_step_norm_multi` (`verify_kernels.hpp:28,35`) son genéricos salvo la puerta.

**Oráculo**: llama.cpp (PR #27773) reutiliza la implementación KDA de Kimi-K3 (06 §5.2). El `native_gdn.cu` de Strata está transcrito de `gated_delta_net.cu` del pin; si el upstream añadió el modo por canal al mismo kernel, la transcripción es directa **(a verificar tras el fetch)**. La fórmula exacta de la puerta con cota -5 también **(a verificar)**.

**Esfuerzo 4-7 pw** (kernels de 1 token, ventana y prefill 2-3; preproceso de la puerta y de las convs 1-1,5; estado, commit y snapshot 1; referencia y parity 1).

### R3. QSA + indexador -> DSA + indexador (MLA NoPE)

**Lo que coincide con el QSA de Strata (reutilizable):**
- Intercalado `l%4==3`.
- Pool de 4 celdas (`idx_block = 4` frente a `index_kpool 4`) y presupuesto de 2048 celdas más la cola: `qsa_selection_width = min(n_kv, 2048 + 3)` (`qsa.hpp:133`), frente a `index_topk 2048` + `index_kpool_always_select_tail`. Misma regla de cola (`score += 1e9` a las celdas del bloque incompleto, `qsa.hpp:259`).
- Dimensión de clave 128; puntuación `sum_h ReLU(q_h . k)` sin softmax; **selección idéntica por debajo de 2.051 celdas**.
- KV paginado con página = bloque de 4 celdas y *streaming* a RAM guiado por la selección (`kv_stream.hpp:1-20`, `qsa_kv_resolve` en `layer.cpp:726`), top-k por radix sobre bloques (`qsa_block_topk`).
- Proyecciones cuantizadas (`gemv_quantized`, `native_mmvq`), RMSNorm ponderada (`native_qsa_rms_norm_weighted`; sirve para `q_a_layernorm` y `kv_a_layernorm`), `kv_append` paginado, INT8 por grupos de 64 (512 = 8 grupos).

**Lo que difiere en el indexador:**
- 32 cabezas x 128 (Qwen 4 x 128): `IDX_HEADS = 4` es literal en `qsa_select.cu:17,875,899` y `native_qsa_score.cu:194` (que además exige `idx_top_k == 2048`, igual que GLM).
- Peso por cabeza y token (`weights_proj [32,4096]`): `score = sum_h w_h * ReLU(q_h . k)`.
- Las consultas salen del latente `q_a` (`wq_b [4096,1536]`), no de `x` (Strata: `indexer.q_proj [n_embd,512]`).
- Pooling aprendido (`index_kpool_compress_gate [128,4096]` + `ape [4,128]`) en lugar de media + rms_norm + rope (`indexer_key_append`, `qsa.hpp:196-248`); `k_norm` con bias; **sin rotación** (NoPE). Si GLM tiene el "slot muerto" que Strata deriva de la celda 0 **(a verificar)**.
- `index_share_for_mtp_iteration`: el indexador se comparte entre iteraciones del MTP.

**Lo que es nuevo: la atención (N).** MLA absorbida NoPE: `q_a_proj [1536,4096] -> RMSNorm -> q_b_proj [16384,1536]` da 64 x 256 (parte nope); se multiplica por `W_UK` (de `kv_b_proj [32768,512]` BF16) y queda 64 x 512; `kv_a_proj_with_mqa [512,4096] -> kv_a_layernorm` da el latente de 512 que **hace de K y de V**; la salida 64 x 512 se multiplica por `W_UV` -> 64 x 256 -> `o_proj [4096,16384]`. Los kernels actuales son GQA 24/2 de `HD=256` con K y V separados y `G=12` (`qsa_decode_attn.cu:16-17,214,258`; `qsa_prompt_attn.cu:21-22,1053-1058`; `native_flash_attn.cu`) y no se pueden plantillar a esta forma. Coste de cálculo por (consulta, celda): 64 x (512+512) x 2 = 131 kFLOP, frente a 24 x (256+256) x 2 = 24,6 kFLOP en Qwen (5,3x).

**Qué hay que escribir**: decode MQA 64 x 512 con K=V (split-K) y ventana de T tokens; prefill con tensor cores (el `qsa_prompt_attn.cu` de 1.087 líneas es específico de HD=256/G=12); dos GEMV por lote para la absorción; scorer a 32 cabezas con pesos; pooling y proyecciones del indexador; una pool única K=V en `QsaState`/`KvHostPools`/`QsaAttnPools` (hoy `k_pool` + `v_pool`, `layer.hpp:203-268`, `kv_stream.hpp:30-45`).

**Sin RoPE**: se omiten `cos_tab/sin_tab` (`layer.cpp:543,613-614,663-669`), `mrope` y el escalado YaRN en esta arquitectura (en `setup.py` no hay nada que resolver con `resolve_rope`).

**Contexto 1M**: el KV son 17,7 GB (cabe en RAM con el streaming de Strata si no se come el presupuesto de expertos). No verificado: el scorer dimensiona `max_blocks = max_cells/4 + 2` (262.146 bloques a 1M) y la selección radix; `setup.py:151` solo ofrece hasta 524.288.

**Esfuerzo 10-16 pw**: atención 6-10 (decode+ventana 3-4, prefill 3-5), indexador 3-4, KV/pool única/streaming 1-2.

### R4. GR -> mHC

| | GR de Qwen (`gr.hpp:41-120`) | mHC de GLM (06 §3.1) |
| --- | --- | --- |
| Pila | `hc = 4` flujos de `n_embd`, `R` en sitio | `hc_mult = 4`, igual |
| Lectura | `lo = silu((bf16(xn) @ w_down.T)/hc)` con cuello `hc_lr = 320`, puerta `sigmoid(lo @ w_up.T)` por flujo, `mixed = media_flujos(xn * puerta)` | una proyección lineal `hc_attn_fn [24,16384]` (+ `base [24]`, `scale [3]`): 4 pesos de entrada, 4 de salida y una matriz 4x4 que se hace doblemente estocástica con **Sinkhorn de 20 iteraciones**; `mixed = sum_c pre_c * R_c` |
| Escritura | `R_out = R + out * 2*sigmoid(inject/hc)` por flujo (sin mezcla entre flujos) | `R' = M @ R + post (x) out` (**con** mezcla entre flujos) |
| Cabeza | `output_hc_*` (un `gr_read` sin inject) | media de los 4 flujos |
| Pesos por mitad | `w_down` 320 x 10240 + `w_up` + `w_inject` (~6,5 M de parámetros) | 24 x 16384 = 0,39 M |

Es **la misma familia con otra matemática**: los kernels de `gr.cu`/`fused_gr.cu` (2.000 líneas, especializados en `N=2560, HC=4, LR=320`: `fused_gr.cu:20-28`) no se generalizan, se sustituyen por `mhc_read`/`mhc_write` (1 token, ventana y prefill) y por su variante fusionada "escritura pendiente + lectura siguiente" (el truco de `fused_gr_read`, `layer.cpp:1142-1260` con `pending_ffn`). Se reutiliza todo el plumbing: buffer `[hc,n_embd]`, broadcast del embedding (`generate.cpp:3512`), captura en grafos, `R` final para el MTP y los hashes de estado. Son más baratos de ejecutar que GR (24 filas frente a dos matrices de ~3,3 M); el Sinkhorn de 4x4 con 20 iteraciones es latencia, no ancho de banda. **Operador exacto por confirmar** contra llama.cpp (reutiliza el de DeepSeek-V4; 06 §3.2 lo deja como no verificado). **Esfuerzo 3-5 pw.**

### R5. MoE: router, expertos, capas densas y MTP

- **Router (1,5-2 pw)**: `p = sigmoid(logits)`; la selección usa `p + e_score_correction_bias`; los pesos son `p` de los elegidos normalizados (`norm_topk_prob`) por `routed_scaling_factor = 2.5`; `n_group = topk_group = 1` (sin agrupación). `route_kernel<REG>` ya es por registros (`prefill/kernels.cu:616-654`): 288 = 9 x 32. Hay que tocar los 3 caminos: 1 token (`router_top10.cu`, `native_router.cu:49` con 512/10 literal), ventana (`verify.cpp:677`: `NE == 512 && K == 10`) y prefill (`ids[t*10+rank]` literal en `kernels.cu:649,653,711`). `moe_combine` nativo admite k hasta 15 (`native_moe.cu:62,79`); `kMaxWindowEntries = 128` cubre 8 x 8.
- **Dimensiones (G)**: `kNativeActBytes = 4096` es menor que un `n_embd = 4096` en Q8_K (16 x 292 = 4.672 B) y `kNativeHBytes = 1024` menor que `n_ff = 2048` en Q8_0 (2.176 B): hoy `native_fmt` los **rechaza** (`native_expert.cpp:64`); basta subir las constantes (`native_expert.hpp:17,19`; `pool.hpp:264` reserva `MAXT x kNativeHBytes` por job). `CAP = MAXT*10` (`peer_experts.cpp:26`, `remote_experts.cpp:17`) pasa a `MAXT*k`.
- **`swiglu_limit = 10`**: `gate.clamp(max=10)` y `up.clamp(-10,10)` en los expertos (alcance a denso/compartido: no verificado). Hay que añadirlo en todos los sitios de SwiGLU y con **igual redondeo** en CPU y GPU: CPU `iq_avx512.cpp`, `iq_avx2.cpp`, `kq_avx2.cpp`, `native_expert.cpp`; GPU `iq_kernels.cu:958,1049`, `shared_expert.cu:54,62`, prefill `kernels.cu:681,688` y los `moe_fused*.cu`. 1-1,5 pw (transversal, no difícil).
- **Capas 0-2 densas (12288)**: sin router ni doorbell; el bucle de capas salta el paso del pool; `layout.cpp:68-110` y `expert_layout.cpp:226-231` exigen hoy expertos en todas las capas; `sh_scratch` se dimensiona con un único `n_ff` (`layer.cpp:335-354`). Se reutiliza `shared_expert` sin puerta con `n_ff = 12288` (múltiplo de 256).
- **Experto compartido**: 1 de ancho 2048 (= el de los enrutados, así que `ModelGeometry.n_ff` sirve), **sin** puerta escalar (flag `shared_gate = false`).
- **MTP (Fase 3)**: la capa lleva DSA completa + MoE de 288 expertos (7,43 B de parámetros, 7,25 B de expertos) + `eh_proj [4096,8192]`, y comparte embedding y `lm_head`. El drafter de Qwen mantiene sus 512 expertos canónicos Q2_0 **residentes en VRAM** (708 MB, `mtp.cpp:184,572-580`); los de GLM serían ~2,1 GB a IQ2_XS: hay que tratarlos como la capa 45 del tier de expertos (caché de VRAM + pool de CPU + doorbell dentro de la cadena de borradores).
- **Tipos de cuantización**: ver el hallazgo 6 del resumen; con dimensiones múltiplos de 256 el soporte de K-quants en down es posible, pero falta `iq_kernels.cu:510-512`.

### R6. Qué significa para el diseño

La ronda 1 recomendaba componentes paralelos (`GlmVerifier`, `GlmPrefill`, `GlmSession`) porque suponía un bloque distinto. Con GLM-5.3-Flash eso duplicaría ~9.200 líneas (`verify.cpp` 2.201 + `prefill/*` 7.037) que son casi idénticas. **Se recomienda ranuras de operación dentro de los bucles existentes** (sección "Diseño de abstracción propuesto"). El `LayerPlan` queda casi gratis.

### R7. Qué se abarata y qué sigue siendo difícil

**Se abarata** frente a la ronda 1:
1. Ya no hay kernels GQA de HD=128, ni RoPE parcial, ni KV por cabezas.
2. El `LayerPlan` casi no cambia (el predicado `l%4==3` vale).
3. El indexador, el top-k y el streaming de KV tienen un pariente muy cercano en el motor.
4. El router es BF16; no hace falta GEMV F32.
5. El plumbing de 4 flujos se conserva; no hace falta una política de residual estándar.
6. No hay riesgo de "KV caro": el KV es de la escala de Qwen.
7. Existe un oráculo mainline (`glm5-next` fusionado) y los GGUF ya los publican varios terceros.
8. Las dimensiones son múltiplos de 256.

**Sigue siendo difícil o sube**:
1. Los tres kernels de mezcla son nuevos o casi: KDA, mHC y, sobre todo, la **MLA absorbida con tensor cores** (prefill).
2. El **MTP con MoE** no cabe residente en VRAM.
3. La **escala**: expertos 5,1x, 12.096 expertos, 5,5 % de ellos en una caché de 4,8 GB frente al 18 % de Qwen, 2,44 GB de expertos por token, y 88 GB de expertos por trozo de prefill (06 §6).
4. El oráculo tiene un pin anterior a la fusión y el MTP no está en mainline (PR #27917 abierto; solo ik_llama.cpp #2548).
5. 1M de contexto (límites del scorer sin verificar).
6. Detalles sin verificar: mHC, puerta KDA, compresión kpool + APE, alcance de `swiglu_limit`.

### R8. Realidad de memoria: 88-90 GB de expertos en un PC de 32-64 GB

**Hechos (06 §6)**: expertos del modelo principal 87,9 GB a IQ2_XS (42 capas x 288 x 7,27 MB), +2,1 GB de la capa MTP; no-expertos 4,7 GB en Q4_K (8,8 GB en Q8_0); lectura de expertos por token 2,44 GB (Qwen 0,66 GB); con los no-expertos en Q4_K quedan ~4,8 GB de VRAM para expertos en una 3060 de 12 GB (~660 expertos = 5,5 %). Los GGUF completos más pequeños publicados pesan 74,9 GB (IQ1_S) y 96-109 GB a 2 bits. **Ninguno cabe en 64 GB de RAM con todos sus expertos.**

**Qué ofrece hoy Strata para expertos fuera de RAM** (`docs/UNSLOTH_Q4.md`): el modo `--resident-budget-gib` lee los expertos del GGUF *en sitio* desde el SSD (sin `experts.bin`); la RAM guarda los más usados según el perfil; prefetch por el router de la capa siguiente (`RouterLookahead`, `generate.cpp:3283-3310`); en prompts, 32 hilos y hasta 128 blobs en vuelo (`prefill.cpp:706-714`, `STRATA_STAGER_*`); perfil aprendido con `--expert-profile-save`. Medido con Qwen UD-Q4_K_XL (77 GB de expertos, presupuesto de 40 GiB en 64 GB, RTX 5070): **7-8,5 tok/s**, 1,1 GB leídos del SSD por ronda de verificación de ~3,5 tokens (~0,33 GB por token) en 550-750 ms, es decir **1,5-2,0 GB/s efectivos**. Restricciones actuales: validado solo con Qwen en una GPU NVIDIA y 64 GB; el motor rechaza `--resident-budget-gib` con reparto de capas; la política de setup es "RAM menos 24 GB" (`UNSLOTH_RAM_LEFT_GB`, `setup.py:145`; `resident_budget_gib`, `setup.py:946`).

**Estimación para GLM-5.3-Flash a IQ2_XS** (mía; ninguna cifra está medida con GLM). Fracción residente `f = (presupuesto + 4,8 GB de VRAM) / 88 GB`; cobertura de las lecturas `f^a` con `a` = 0,29 (sesgo fuerte: neurall, 74 % de las lecturas con el 35 % de los expertos), 0,61 (el de Qwen, calibrado con la medida de Strata: 74 % con el 61 %) o 1 (casi uniforme: OpenMOSE); tiempo por token = lectura SSD / tasa + 60 ms (CPU, GPU y sincronización, 06 §6.6):

| RAM del PC | Presupuesto (RAM-24 GiB) | Residente f | Lectura SSD por token (a=0,29 / 0,61 / 1) | tok/s a 1,5-2,0 GB/s (medido con Qwen) | tok/s a 5 GB/s (NVMe PCIe 4, no medido) |
| --- | --- | --- | --- | --- | --- |
| 128 GB | todo (88 GB) | 100 % | 0 | 11-26 (06, todo en RAM, limitado por CPU) | igual |
| 96 GB | 72 GiB | 93 % | 0,05 / 0,10 / 0,16 GB | ~5-12 (limitado por CPU) | ~6-12 |
| 64 GB | 40 GiB | 54 % | 0,40 / 0,76 / 1,12 GB | 1,3-3,9 | 3,5-7 |
| 48 GB | 24 GiB | 35 % | 0,64 / 1,16 / 1,59 GB | 0,9-2,6 | 2,6-5,3 |
| 32 GB | 8 GiB | 15 % | 1,03 / 1,67 / 2,07 GB | 0,7-1,7 | 2,1-3,8 |

06 estima 4-12 tok/s para 64 GB con 3,0-5,5 GB/s y un 18-26 % de fallos a SSD; es coherente con la columna de 5 GB/s. Con Qwen, en la misma PC, el modelo de Strata da 79 tok/s (IQ2_XS en RAM): GLM-5.3-Flash sería 3-20 veces más lento según la RAM (06 §6.6). **La conclusión práctica: con 32 GB no es utilizable (~1 tok/s), con 48-64 GB es utilizable solo para tareas pacientes y con 96-128 GB sí.**

**Prefill desde SSD (06 §6.6)**: cada trozo usa todos los expertos. Con 64 GB hay ~40-47 GB que leer del SSD por trozo: ~350-600 tok/s; con 32 GB, ~200-370 tok/s. Strata ya agranda el trozo (`kAutoChunks` hasta 32.768, `generate.cpp:4008`) para amortizar; la lectura por blob de 7,3 MB (frente a ~2,5 MB de Qwen) es más favorable para el NVMe.

**Qué necesitaría el motor para esto con GLM**:
1. Todo lo de Fase 1 (sin el forward no hay nada que probar) más que el modo "en sitio" soporte los tipos de GLM (`native_expert_supported` por capa, `generate.cpp:1787-1799`) y mapas de 100-200 GB.
2. **Anillos de E/S dimensionados en bytes, no en blobs**: `STRATA_STAGER_RING = 128` blobs son 0,93 GB pinned a 7,3 MB (`prefill.cpp:238,714`); `FETCH_THREADS` (8) y el tamaño de lectura se afinan por separado.
3. **Prefetch por *lookahead* válido para GLM**: `RouterLookahead` aplica el router de la capa siguiente a la entrada MoE de esta capa y asume router BF16 con softmax (`generate.cpp:3287-3298`); con sigmoide + sesgo y mHC entre capas la predicción hay que medirla. Con rutado casi uniforme, un prefetch más profundo (varias capas) es más importante que el perfil estático.
4. **Perfil de expertos de GLM** (`tools/make_profile.py:23` fija `N_LAYER, N_EXPERT = 48, 512`; `data/expert-profile*.bin` son de Qwen): recoger trazas con `--dump-routing`, y usar el perfil aprendido. 06 §6.5 recoge dos medidas que se contradicen (neurall: sesgo fuerte; OpenMOSE: casi uniforme, "no tiene expertos de sobra"); sin medición propia no se puede prometer que la caché RAM/VRAM ayude.
5. **Política de presupuesto en setup** propia de GLM (el 24 GB de margen de Qwen no sirve de dato) y de `kv_ram_gb` (11 capas x 512 + indexador, ~16,9 KB/token): a 262K son 4,4 GB, a 1M 17,7 GB, que compiten con el presupuesto de expertos.
6. **Modelo de costes especulativo con término de SSD**: con rutado casi uniforme, una ventana de 3 tokens toca 23,3 de los 24 expertos posibles por capa (06 §6.6; en Qwen 29,4 de 30 pero con `distinct_ratio` de 1,7 para T=2, `controller.hpp:30`), de modo que las lecturas de expertos **no se amortizan** y, estando limitado por SSD, el controlador elegiría casi siempre `k=0`. El MTP solo ayuda cuando los expertos están en RAM (ik_llama.cpp mide +20 %).
7. **MTP**: sus 2,1 GB de expertos no pueden ser residentes en VRAM además de lo anterior; ver R5.
8. **Poda de expertos (REAP)**: el motor ya corre un modelo podado con `expert_count` menor (precedente: el Coder, 256 de 512; `generate.cpp:1863`, `expert_layout.cpp:170`, `data/expert-profile-coder.bin`). Para GLM haría falta: sesgo `e_score_correction_bias [n]` y filas del router con el nuevo `n`; el mismo `n` por capa (`ExpertLayout` tiene un único `n_expert`, `expert_layout.hpp:20`; una poda no uniforme exigiría una tabla por capa); `route_kernel` con `n` no múltiplo de 32 (p. ej. 144) usa el camino genérico `router_top10`; y un perfil nuevo. **Con REAP-50 (144 expertos, ~44 GB a IQ2_XS) entraría todo en el presupuesto de 64 GB (06 estima 13-26 tok/s)**, pero **no hay evaluación de calidad publicada y la evidencia de OpenMOSE sugiere que GLM-5.3-Flash no tiene expertos de sobra**; las reglas de documentación del repo (`AGENTS.md:22-24`) impiden afirmar nada sin medirlo. La única poda con calidad medida es la del Coder de Qwen (91 % de SWE-bench Verified, según sus autores).
9. **Alternativa de producto**: GLM-4.7-Flash (30B-A3B, 18,3 GB en Q4_K_M) sí cabe en 12 GB + 32 GB; es otra arquitectura (MLA con RoPE, 64 expertos top-4, sin KDA/mHC/DSA) con otro conjunto de trabajo.

**Esfuerzo de esta parte**: 3-5 pw (incluido en la Fase 2): anillos en bytes, lookahead, perfil y trazas, presupuesto de setup, modelo de costes, medición en dos o tres PCs (32/64/128 GB) y en NVMe PCIe 3/4.

### R9. Servidor y setup con los hechos verificados

| Pieza | Hecho de GLM-5.3-Flash (06 §4) | Efecto en Strata |
| --- | --- | --- |
| Tokenizador | BPE byte-level, 154.880 tokens (154.820 base + 36 añadidos), 321.649 merges, regex estilo GPT-4/Qwen con `\p{N}{1,3}` (Qwen: `\p{N}` suelto), sin normalizador | Tabla `pre` -> regex en `strata_tokenizer.py:56-65,89`; el servidor debe pasarla (`server.py:2892`) |
| Prompt | `[gMASK]<sop>` + `<\|system\|>Reasoning Effort: Max` (siempre) + `<\|user\|>`... y generación `<\|assistant\|><think>` | Plantilla del GGUF; `add_bos` por perfil |
| Razonamiento | siempre activo (`thinking` no se puede desactivar); `reasoning_effort` low/high/max (otro valor = max); `clear_thinking` | El nivel "none" de la UI y del servidor (`frontend.py:66-95`, `app.js:464,936`) no existe: ocultarlo o mapearlo a `low`; `REASONING_WRAP_UP` usa `</think>` = 154842 |
| Herramientas | `<tool_call>NOMBRE<arg_key>K</arg_key><arg_value>V</arg_value>...</tool_call>`; resultados tras **un** `<\|observation\|>` como secuencia de `<tool_response>` | Nuevo parser y *scanner* de streaming (`frontend.py:275-352,367-`); en vLLM se llaman `glm47` |
| Paradas | EOS 154820 `<\|endoftext\|>`, 154827 `<\|user\|>`, 154829 `<\|observation\|>` | Leer del GGUF y pasar `--eos-ids`; `server.py:993` y `generate.cpp:384` son de Qwen |
| Muestreo | `temperature 1.0`, `top_p 0.95` (agentes largos `top_p 1.0`) | `sampling` del config |
| Ficheros | desde 74,9 GB (bartowski IQ1_S) a 342 GB; IQ2_XS 101,4 GB; Unsloth 93-342 GB; solo existe GSQ-RCO comunitario (117,5 GB, sin MTP) | `MODELS`/`FAMILIES` con cifras medidas; `gguf_unsupported` con la lista de tipos que Strata pueda ejecutar |
| Calidad a 2 bits | Unsloth: IQ2_XXS retiene 76,3 % del top-1 (KLD 0,45); IQ4_XS 88,2 % | No se puede prometer la calidad que Strata publica para Qwen; documentar solo lo medido |

---

## Supuestos específicos de Qwen

### A. ¿Está el motor parametrizado por metadatos GGUF o las formas están compiladas?

Respuesta corta: **compiladas, con una capa de host a medias parametrizada**.

| Nivel | Qué hace hoy | Evidencia |
| --- | --- | --- |
| Guard de arquitectura | Exige `general.architecture == "qwen4exp"` y compara `block_count=48`, `embedding_length=2560`, `head_count=24`, `head_count_kv=2` con valores compilados (`experts`/`experts_used` = 0 = solo presencia) | `include/strata/artifact/gguf_reader.hpp:575-603`; se llama desde `native_dense.cpp:89`, `native_head.cpp:29,113`, `gguf_reader.cpp:57` |
| Geometría por defecto | `ModelGeometry g;` con los números de Qwen; sólo `n_expert`, `K` y rope se sobrescriben desde `qwen4exp.*` | `generate.cpp:1845-1868` (`K = 10` en :1846; claves en :1863-1868) |
| Geometría que NO viene del GGUF | `n_ff=640`, `hc=4`, `hc_lr=320`, `ssm_*`, `head_dim=256`, `idx_*`, `qsa_interval=4`, vocabulario (248.320, `generate.cpp:1958`), `rms_eps=1e-6` (`layer.cpp:45`, `ngram.hpp:42`), `n_rot=64` (`qsa.hpp` `qsa_real_shapes`) | `layout.hpp:27-53` |
| Host genérico | `session_bytes/init`, `Verifier::init`, buffers de `layer.cpp` derivan sus tamaños de `g.*` en runtime | `session.cpp:54-137`, `verify.cpp:240-256`, `layer.cpp:184-217,335-354,733-746` |
| Kernels/prefill/CPU | Copias `constexpr` y rechazo si no coincide | `prefill.cpp:90-91,671`; `prefill/kernels.cu:17-18`; `fused_gr.cu:20-28`; `cpu/expert.hpp:34-45`; `s2_expert_grouped.cu:37-46`; `expert_source.cpp:1735` |
| Expertos por capa | Tabla `native_experts.txt` ya admite formatos y tamaños de blob distintos por capa y un `n_expert` distinto (cabecera `(n_expert N)`), pero exige que TODAS las capas tengan expertos y contiguos | `src/kernels/cpu/expert_layout.cpp:170,183,226-231` |

### B. Tabla de dependencias

Clasificación: **R** = reutilizable tal cual; **G** = necesita generalización; **N** = trabajo nuevo; **NA** = no aplica a GLM (se desactiva/omite).

#### B1. Geometría, arquitectura y reparto de capas

| Componente | archivo:línea | Tipo de dependencia | Clas. |
| --- | --- | --- | --- |
| `ModelGeometry` (48 capas, 2560, 24/2/256, hc 4/320, 512 exp., ff 640, ssm 128/16/48/4/10240/6144, idx 4/128) | `layout.hpp:27-59` | Constantes por defecto compiladas, pasadas a ~50 funciones | G |
| Predicado de capa `is_qsa_layer` (`layer % 4 == 3`) y derivados `n_qsa_layers()/n_gdn_layers()` | `layout.hpp:56-65` | Aritmética de intervalo fija; usada en ~25 sitios: `session.cpp:58-108,168,214,792-808,858`; `verify.cpp:248,454,512,975-978,1150`; `prefill.cpp:1468,1725,1902`; `layer.cpp:1200`; `layout.cpp:47,153-166`; `conversation_state.cpp:14,115-133`; `generate.cpp:2604,3667,4991,6004` | G (-> `LayerPlan`); **[R2]** para GLM-5.3-Flash el predicado ya da 11 DSA + 34 KDA con `n_layers=45` |
| Comprobación de forma de tensores por capa (nombres, `ne0/ne1`, formas F32/BF16) | `src/core/layout.cpp:48-129` | Tabla de 23 tensores Qwen + exige exactamente 12 QSA / 36 GDN (:153-166) | G (-> `TensorSchema` por arquitectura) |
| Planner de VRAM `strata::plan::Geometry` y `expert_blob = 1382400` | `include/strata/plan/plan.hpp:22-75` | Constantes Qwen (12 QSA, 36 GDN, `kv_group 64`, indexer) | G |
| Copias constexpr del geometría en prefill | `prefill.cpp:90-91` (`N=2560, HC=4, LR=320, K=10, NE=512, C=10240, ZV=6144, HV=48`), `prefill.cpp:671` rechazo `g.n_embd != N ...\|\| ss.k != K` | Compilado + refuse | G |
| Copias en kernels GR/GDN/PLE | `prefill/kernels.cu:17-18`; `fused_gr.cu:20-28`; `gr.hpp:41`; `verify_kernels.cu:16`; `fused_gdn.cu:12`; `native_gdn.cu:37`; `ngram.hpp:31-48` | Compilado | **[R2]** GR: se sustituye por kernels mHC (no se generaliza); GDN: `S=128` se mantiene y `HK/HV/C` pasan a parámetros (KDA: 64/64/24.576); PLE: NA |
| `RMS_EPS = 1e-6f` global y `qsa_rms_eps()` | `layer.cpp:45`, `qsa.hpp` | La épsilon de RMSNorm debe leerse de `attention.layer_norm_rms_epsilon`; GLM-5.3-Flash usa 1e-5 **[R2, verificado]** -> deriva numérica silenciosa | G |
| Vocabulario/tokens: `n_vocab=248320`, EOS `{248044,248046}`, token inicial `--serve` 248045, `kImagePad=248056`, `PLE_EOS_TOKEN_ID` | `generate.cpp:1958,384,1539,5043`; `ngram.hpp:37` | Constantes de Qwen | G (EOS/vocab desde GGUF) |
| Sampler (`n_vocab` runtime, `kSplitMaxBlocks=64` => n_vocab <= 262.144) | `sampler.cu:607` | Genérico; GLM-5.3-Flash (vocab 154.880, **[R2]**) cabe | R |
| Arranque `--native`: obliga a `--ple-gguf` y activa en bloque `native_gdn/native_qsa/native_qsa_indexer/native_ple_postops/...` | `generate.cpp:1593-1620,1649-1651` | Política de arranque de Qwen | G (activar según `ModelArch`) |
| Native pack => solo ventana de verificación (`--spec>=2`, `--prefill`) | `generate.cpp:1776-1781,1950-1956` | Obliga al `Verifier` para decodificar | G |
| Control vectors (cvec) `n_embd <= 4096` y `hc` | `cvec.cu:12,103`; `cvec.hpp:31`; `generate.cpp:913-980` | Límite 4096: GLM-5.3-Flash tiene `n_embd=4096`, justo en el límite **[R2]** | G (opcional) |

#### B2. GGUF: tensores, metadatos y cuantización

| Componente | archivo:línea | Tipo | Clas. |
| --- | --- | --- | --- |
| Lectura de claves `qwen4exp.*` (expert_count, expert_used_count, rope.freq_base, rope.scaling.*) | `generate.cpp:1863-1868` | Prefijo de arquitectura cableado | G (prefijo = `general.architecture`) |
| Nombres de tensor por capa leídos por nombre (`blk.<L>.attn_qkv/attn_gate/ssm_*/attn_q/k/v/output/attn_q_norm/attn_k_norm/indexer.*/hc_*/ffn_gate_inp/ffn_*_shexp/ffn_gate_inp_shexp`, `output_hc_*`, `blk.1.ple_*`, `token_embd`, `output.weight`) | `layout.cpp:48-129`; `layer.cpp:1062-1064,1200`; `verify.cpp:833`; ~60 literales en `src/` | Esquema Qwen. Varios coinciden por convención de llama.cpp con GLM (`attn_q/k/v/output`, `ffn_gate_inp`, `ffn_*_shexp`, `ffn_*_exps`), pero faltan los de KDA/DSA/mHC/indexador, `exp_probs_b.bias`, `ffn_gate/up/down` densas y `nextn.*`; los nombres exactos de `glm5-next` salen del PR #27773 (la cadena `glm5next` de Unsloth y la `glm5-next` de mainline: compatibilidad no probada) **(a verificar)** | G / N |
| Tensores servidos "nativos" desde el GGUF al GPU (lista de sufijos) | `native_dense.cpp:16-33` (`attn_qkv, attn_gate, ssm_out, attn_q/k/v/output, ffn_*_shexp, ple_key`) | Lista Qwen; para GLM añadir `ffn_gate/up/down` densas (capas 0-2), q/k/v/o de KDA y `q_a/q_b/kv_a/o` de MLA **[R2]** | G |
| Tabla `FORM` del empaquetador (BF16 forzado para routers/hc/indexer/ssm_alpha/beta; F32 -> BF16 solo si exacto, "anything else is refused") | `tools/iq_pack.py:31-33,59-89` | Reglas Qwen; **[R2]** el router de GLM-5.3-Flash ya es BF16 (`mlp.gate.weight [288,4096]`); solo `e_score_correction_bias` es F32; hay que añadir las reglas de KDA/mHC/indexador | G |
| Geometría de bloque GGUF conocida | `gguf_reader.hpp:122-190` | Cubre F32/F16/BF16/Q4_0..Q8_1/Q2_K..Q6_K/IQ2_XXS..IQ4_XS/IQ1_M/I8/Q2_0; **sin** IQ1_S(19), Q8_K(15), MXFP4(39), NVFP4(40), TQ | G |
| Tipos de experto en GPU (grouped): gate/up `{16,17,18,21,22,23,29,42,12,13,6,8}`, down `{20,23,42,7,6,8}` | `iq_kernels.cu:510-512,1360-1363,1510-1514` | Lista cerrada; faltan Q2_K(10), Q3_K(11), Q4_0(2), Q4_1(3), Q6_K(14), IQ1_S como experto | G (ampliar) |
| Tipos de proyección densa (MMVQ) `{2,6,7,8,11,12,13,14,20,23,42,16,17,18,21,22,29}`; dequant a BF16 `{Q4_0,Q5_0,Q8_0,Q3_K,Q4_K,Q5_K,Q6_K,IQ4_NL,IQ4_XS,Q2_0}` | `native_mmvq.cu:1441-1446`; `dequant_bf16.hpp:1-8` | Falta Q2_K y Q4_1 en denso/prefill | G |
| Tipos MMQ para prompt (CMake) `q2_0 iq2_xxs iq2_xs iq2_s iq3_xxs iq3_s iq4_nl iq4_xs q8_0` (+`q5_0`, +`q4_k q5_k q5_1` opcionales) | `CMakeLists.txt:894-919` | Instancias ggml-cuda compiladas a mano | G |
| Embedding/cabeza en tipos soportados (`embed_type_supported` = is_iq o BF16: **no** Q6_K, F16, F32, Q4_0) | `iq_kernels.cu:1425`; `native_head.cpp:119` | Un `token_embd` en Q6_K/F16 (habitual) no cargaría | G |
| Soporte por nombre de GGUF en setup ("other GGUFs (UD-IQ3_XXS, UD-Q2_K_XL, K-quants) cannot be used") | `setup.py:875-886,880-919` | Política de catálogo | G |
| Dimensiones no múltiplo de 256: ya se maneja (n_ff=640 => down solo en tipos legacy; Q8_K "estructuralmente imposible") | `shared_expert.hpp:50-58`; `s_gemv.hpp:122-124`; `native_expert.cpp:46-47` | Mecanismo por tensor; **[R2]** en GLM-5.3-Flash todo es múltiplo de 256 (4096, 2048, 12288): no estorba, y los K-quants valdrían también en down | R |

#### B3. Routing, MoE y expertos

| Componente | archivo:línea | Tipo | Clas. |
| --- | --- | --- | --- |
| Router genérico: softmax sobre TODOS los expertos, argsort estable, gather, renorm `max(sum, 2^-14)`; `k<=64`, `n_expert <= 512*64`; variante rápida HIP para `64 < n_expert <= 512`, `k <= 32` | `router_top10.cu:1-13,334,369-376` | Matemática softmax+renorm fija; **no** sigmoide, bias de selección (`noaux_tc`), grupos (`n_group/topk_group`), `routed_scaling_factor`, `norm_topk_prob` opcional | G/N (nuevo kernel `router_sigmoid_bias_grouped`) |
| Router "nativo" fijado a ggml topk-moe: 512 expertos, k=10, softmax, sin bias, scale 1 | `native_router.hpp:10-17`; `native_router.cu:49,111-122`; condición `layer.cpp:370`; `verify.cpp:677` | Literales 512/10 | G (ya hay *fallback* genérico) |
| Router en prefill: `route_kernel<REG>` solo `n_expert` 512 (REG=16) o 256 (REG=8), `rank < 10`, `ids[t*10+rank]`, softmax; si no, `router_top10(..., 10, ...)` con k literal | `prefill/kernels.cu:616-654,927-936` | K=10 literal x3 | G |
| Combinación de expertos en prefill: `for k<10`, `shared * sigmoid(sg[t])` | `prefill/kernels.cu:703-713` | K=10 y puerta escalar del shared expert fusionada | G |
| `moe_combine` nativo `k in [1,15]`, suma ponderada + shared sin ponderar | `native_moe.cu:62,79`; `shared_expert.hpp:77-95` | OK para top-4..8 de GLM; `routed_scaling_factor` hay que plegarlo en los pesos | R |
| Matriz del router siempre BF16 (`project_bf16`, `kind == Bf16InF32`) | `layer.cpp:355-388`; `expert_source.cpp` RouterLookahead (`generate.cpp:3287-3298`) | **[R2]** GLM: router BF16 (cabe en `project_bf16`) y `e_score_correction_bias [288]` F32 aparte | G (añadir el sesgo; sin GEMV F32) |
| Prefetch por *lookahead* del router de la capa siguiente | `generate.cpp:3283-3310`; `expert_source.hpp:153-` | Asume softmax implícito y router BF16 | G |
| Shared expert: `h*sigmoid(x·ffn_gate_inp_shexp)`, 4 tensores obligatorios, ancho = `n_ff` de los enrutados | `shared_expert.hpp:1-10`; `layer.cpp:390-423` | Puerta escalar y ancho único. **[R2]** GLM: 1 compartido de ancho 2048 (= el de los enrutados, así que `ModelGeometry.n_ff` sirve), **sin** puerta | G (flag `shared_gate=false`) |
| FFN densa en capas iniciales (`first_k_dense_replace`) | (no existe) `layout.cpp:68-110` exige `ffn_gate_inp` en TODA capa; `expert_layout.cpp:226-231` exige expertos en toda capa; buffer `sh_scratch` dimensionado con un solo `n_ff` (`layer.cpp:335-354`) | Supone MoE en las 48 capas | G (+ pequeño kernel nuevo: reutilizar `shared_expert` sin puerta) |
| Geometría de blob de experto en CPU: `H=2560, FF=640, NE=512, BLOB=1.382.400`; `native_fmt(gt, dt, H, FF)`; buffers `kNativeActBytes=4096`, `kNativeHBytes=1024` (con 5120 en Q8_K -> 5840 B: **rechazado** en `native_expert.cpp:64`, no desborda) | `cpu/expert.hpp:34-45`; `expert_layout.cpp:183`; `native_expert.hpp:17,19`; `native_expert.cpp:64`; `pool.hpp:264` | Constantes de capacidad | G |
| Capacidades de ventana `CAP = MAXT*10`, `kMaxWindowEntries = 128` | `peer_experts.cpp:25-26`; `remote_experts.cpp:15-17`; `expert_source.cpp:1851-1852` | top-10 literal | G |
| Kernels de expertos canónicos Q2_0 con H/FF literales (usados por el drafter MTP) | `s2_expert_grouped.cu:37-46`; `mtp.cpp:184,572-580` | Formato canónico Qwen | NA (para GLM solo ruta "native"; **[R2]** también el MTP de GLM tendrá que usarla) |
| Caché de expertos en VRAM (por (capa, experto), slots de tamaño variable) | `expert_cache.cpp` (0 constantes de modelo) | Genérico | **R** |
| Fuente de expertos / arena fijada en RAM / modos mmap/resident / swaps adaptativos | `expert_source.cpp`, `pinned.cu`, `direct_file.cpp` | Genérico salvo los puntos de arriba | R |
| Perfil de expertos `STRP` (pares capa/experto ordenados); `make_profile.py` con `N_LAYER, N_EXPERT = 48, 512` | `tools/make_profile.py:23`; `serve/server.py:804-816` | Formato genérico (la cabecera lleva `nl, ne`); **datos** (`data/expert-profile*.bin`) y constantes son de Qwen | G (+ datos nuevos) |
| Pool CPU de expertos y protocolo *doorbell* (rings, `PoolFn`, `PoolMultiFn`) | `layer.hpp:402-425`; `session.cpp:568-780`; `verify.hpp:44-45` | Genérico en `k`/`n_embd` | **R** |

#### B4. Residual (GR), PLE, mrope, visión

| Componente | archivo:línea | Tipo | Clas. |
| --- | --- | --- | --- |
| GR: `hc=4` streams, `gr_read/gr_write`, tensores `hc_attn_*`, `hc_ffn_*`, `output_hc_*`; broadcast del embedding a 4 streams; `R` es `[hc, n_embd]` | `layer.cpp:1142-1315,1060-1116`; `verify.cpp:833`; `generate.cpp:3512`; `fused_gr.cu` (1094 líneas), `gr.cu`, `native_gr_*` (~2.000 líneas) | Todo el flujo de bloque, cabeza, MTP y vectores de control supone GR | **[R2]** N: mHC sustituye a GR (kernels nuevos), con el plumbing de 4 flujos reutilizado; la cabeza pasa a ser una media de los 4 flujos |
| PLE (n-gram, tabla SSD de 28,8 GB) solo en capa 1; `ple_hist` siempre reservado | `layer.cpp:1146-1190` (cond. `layer == 1` en :1164); `layer.hpp:534-575`; `session.cpp:49-53`; `ngram.hpp:31-48`; `ple.cu`, `ple_reader.cpp` (~2.000 líneas); `setup.py:3543-3548` ("is this a Qwen3.8-Flash-Next GGUF?") | `PleRun::ready()==false` ya salta el módulo (`layer.hpp:551-562`), pero el arranque lo exige | NA (hacer opcional por arquitectura) |
| mrope intercalado (secciones 11/11/10/0), tabla `[cell][3]` | `mrope.hpp:1-24`; `generate.cpp:2046-2061,5043-5208`; `native_rope.cu:121` (head_dim 128/256, `n_rot==64`) | Para texto es identidad; `nullptr` deja los kernels "exactamente como antes" | R |
| RoPE NEOX parcial + YaRN/lineal por tabla f64 | `rope.hpp:1-80`; `rope_scaling.hpp`; `layer.cpp:663-669` | `n_rot`, `head_dim` ya son argumentos; la ruta nativa exige `n_rot==64` | R (+G menor) |
| Visión (mmproj + `strata-vision` sobre mtmd) | `tools/vision/strata_vision.cpp`; `generate.cpp:2046-2061`; `setup.py:153-190` | Específico de Qwen. **[R2]** GLM-5.3-Flash trae un ViT de 24 capas (mmproj ~1,1 GB); al ser NoPE no necesita `mrope`, solo sustituir embeddings; sus ids de imagen (`<\|image\|>` 154854) difieren de `kImagePad` | NA para el MVP (Fase 5 opcional) |

#### B5. Atención, KV, GDN

| Componente | archivo:línea | Tipo | Clas. |
| --- | --- | --- | --- |
| Cadena QSA completa (proyecciones, norma+rope q/k, `kv_append`, indexer pooled r=4, `qsa_block_scores/topk` (2048 celdas), gather, atención split-K, **puerta de salida `sigmoid(gate)`**, `attn_output`) | `layer.cpp:848-993`; `qsa.hpp:1-140` | Mixer Qwen: `attn_q` es `2*n_head*head_dim` (q+gate) | NA/G |
| Kernels de atención con `HD=256`, `G=12` en `constexpr`; rechazo si `head_dim != HD` o `n_head != G*n_head_kv` | `qsa_decode_attn.cu:16-17,214,258`; `qsa_prompt_attn.cu:21-22,1053-1058`; `kv_q4.cu:176`; `native_flash_attn.cu` (`kv = head/12`, D=256, scale 1/16); `native_rope.cu:121` | Kernels especializados. **[R2]** GLM-5.3-Flash no es GQA: es MLA absorbida (64 cabezas sobre un latente de 512 con K=V); no sirven `HD=256/G=12` ni `native_flash_attn` (D=256, escala 1/16) | N (atención MQA 64x512 nueva) |
| Indexer: `IDX_DIM=128, IDX_HEADS=4, R=4, top_k=2048` | `qsa_select.cu:17,875,899`; `native_qsa_score.cu:194`; `qsa.hpp` `QsaShapes` | **[R2]** Es el pariente más cercano del indexador de GLM (mismo pool de 4, top-k 2048, cola, dim 128); cambian las cabezas (32), los pesos por cabeza y el pooling aprendido sin RoPE | G |
| Almacén KV paginado FP16/INT8/Q4/K8V4, rotación Hadamard, *streaming* a RAM con ventana residente | `layer.hpp:203-320`; `kv_q8.cu`, `kv_q4.cu`, `kv_stream.cu` (~830 líneas) | **[R2]** Sirve: la atención de GLM también es dispersa (top-2048 de pools de 4) y el *streaming* guiado por la selección (`qsa_kv_resolve`, `layer.cpp:726`) aplica tal cual; hay que soportar una sola pool K=V de 512 por celda | G |
| GDN (DeltaNet): estado recurrente 128x48x128, conv 10240x3, kernels fusionados, commit/rollback en verify | `gdn.hpp:7-11`; `layer.cpp:223-333`; `session.cpp:41-44`; `verify.cpp:960-1010`; ~900 líneas de kernels | Específico de Qwen; **[R2]** la KDA de GLM es el mismo esquema con decaimiento por canal | G |
| Cache de conversación (`geometry_key` de 18 campos; snapshot GDN+QSA+PLE) | `conversation_state.cpp:14-18,115-133`; `conversation_snapshot.cpp` | Con solo KV es más simple (truncar posición) | G |

#### B6. MTP y especulación

| Componente | archivo:línea | Tipo | Clas. |
| --- | --- | --- | --- |
| Drafter MTP de Qwen: `fc_embedding`/`fc_hidden`, GR, atención QSA densa propia, MoE de 512 expertos **canónicos Q2_0 residentes en VRAM** (708 MB), cabeza nativa obligatoria | `mtp.hpp:1-26`; `mtp.cpp:184,424,481-488,572-580` | Todo Qwen. **[R2]** La capa MTP de GLM lleva DSA + MoE de 288 expertos (~2,1 GB a IQ2_XS): no cabe residente como la de Qwen (708 MB) | G/N |
| Descarga y empaquetado de los pesos MTP desde el checkpoint BF16 original por *range requests* + SHA256 fijados | `tools/mtp_fetch.py:32-44`; `mtp_pack.py:38`; `mtp_rt.py:27`; `setup.py:3521-3541` | Repo, revisión y nombres `mtp.*` de Qwen | G/N |
| Subconjunto de vocabulario del cabezal de borrador (`data/draft_vocab*.bin`) | `tools/draft_vocab.py`; `mtp.cpp:309,430-433`; `setup.py:2824-2845` | Ids del vocabulario Qwen (opcional) | N (recalcular) |
| Ventana de verificación `Verifier` (T<=8, aritmética por token = decode de un token, "aceptado <=> greedy lo habría producido") | `verify.hpp:1-24,34-120`; `verify.cpp`; `generate.cpp:1590`; `kVerifyMaxT=8` (`verify_kernels.hpp:21`) | Mecanismo genérico, **implementación acoplada** a GDN/QSA/GR/PLE | G |
| Controlador (`Controller`, `CostModel`), `SuffixDrafter` (búsqueda de sufijo) | `spec/controller.hpp:25-37`; `spec/suffix_drafter.hpp` | `CostModel` por defecto = mediciones de Qwen (`dense_ms=11.0`, `hit_rate=0.55`, `distinct_ratio`...) | R (+recalibrar) |

### C. Clasificación resumida por componente

**R - Reutilizable tal cual o con parametrización trivial** (~18.000 líneas):
caché de expertos y `ExpertSource`/arena/pinned/direct_file; pool CPU (`vec_dot` de ggml-cpu por tipo, `native_expert.cpp`, `iq_avx512/avx2`, `kq_avx2`); doorbell y solapamiento GPU/CPU; streaming de expertos en prefill + anillo de copia; MMQ de ggml (`strata_mmq`); `native_mmvq` y GEMV BF16 para proyecciones densas; `NativeHead`; sampler; `spec/*`; plataforma (`direct_file`, `memory`, `device`, `graph`, `hip_compat`); API server, MCP, UI web, telemetría; RoPE NEOX+YaRN; planner (con nueva `Geometry`).

**G - Generalizar** *(revisado en la ronda 2)*: `ModelGeometry`/guard/`layout.cpp`; `LayerPlan` ligero (FFN densa en 0-2 y capa MTP; el intercalado ya sirve); GDN -> KDA (decaimiento por canal); indexador, top-k y KV streaming -> DSA; router (kernels single/multi/prefill/lookahead + sesgo); bloque MoE (compartido sin puerta, FFN densa inicial, k=8, capas sin expertos en `expert_layout`, `swiglu_limit`); constantes `H/FF/NE/K` y capacidades de buffers; cobertura de tipos GPU (grouped, MMQ, embed); KV (pool única K=V de 512); `Verifier`, `Prefill`, `MtpDrafter` y `generate.cpp` (por ranuras de operación); herramientas de pack/perfil/MTP.

**N - Nuevo** *(revisado en la ronda 2)*: atención MLA absorbida NoPE (decode, ventana y prefill); mHC (Sinkhorn) en lugar de GR; capa MTP con DSA + MoE de 288 expertos; referencia Python y arnés de paridad GLM; tokenizador/plantilla/parser de herramientas GLM; catálogo y empaquetado GLM; perfil de expertos GLM.

### D. Qué se puede tomar de llama.cpp/ggml

- **Estado real en este fork**: `third_party/ggml/` solo trae `ggml-common.h` (layouts de bloque y codebooks de i-quants), `LICENSE` y `VERSION.txt` (commit `3cf03257...`). ggml completo se baja con `FetchContent` en configuración (`CMakeLists.txt:866-875`) o con `-DSTRATA_GGML_DIR`. **No pude inspeccionar esas fuentes**; lo siguiente es de memoria **(a verificar tras el fetch)**.
- **Ya tomado y con patrón establecido**: `ggml-cpu` enlazado para `vec_dot` de expertos; `ggml-cuda/quantize.cu` y `template-instances/mmq-instance-*.cu` compilados *sin* el backend completo mediante los "shims" de `src/prefill/ggml_cuda_host.cu` (`CMakeLists.txt:894-919`); kernels *transcritos y especializados* con atribución MIT (p. ej. `native_flash_attn.cu` desde `fattn-vec.cuh`/`fattn-common.cuh`; `native_mmvq.cu`; `native_router.cu` desde el topk-moe; `native_rope.cu`), validados contra una build de llama.cpp con `STRATA_ORACLE_SOURCE_DIR` (`CMakeLists.txt:320-340`).
- **Candidatos para GLM-5.3-Flash [R2]**: (1) dequant/MMVQ/MMQ de los tipos que falten (Q2_K, Q3_K, Q6_K, Q4_0/Q4_1, IQ1_S): se obtienen añadiendo instancias `mmq-instance-<t>.cu` y casos en `iq_kernels.cu`; (2) la atención MLA absorbida del grafo `glm5-next` de llama.cpp (K=V latente) y, si existen en el pin nuevo, los kernels FlashAttention de ggml-cuda con cabezas de 512 **(a verificar)**; patrón "transcribir y especializar" (los lanzadores de ggml dependen de `ggml_tensor` y del contexto del backend); (3) KDA: la implementación que llama.cpp reutiliza de Kimi-K3 y, quizá, el modo por canal del kernel `gated_delta_net.cu` del que ya se transcribió `native_gdn.cu` **(a verificar)**; (4) mHC: llama.cpp reutiliza la de DeepSeek-V4 (06 §5.2); (5) la semántica de `build_moe_ffn` (puerta SIGMOID, `exp_probs_b`, `norm_w`, `scale_w`), de la que ya se transcribió el router softmax (`router_top10.cu:1-13`); (6) el grafo `glm5-next` como oráculo de paridad (el MTP solo está en ik_llama.cpp, PR #2548).
- **Cuidado (concreto) [R2]**: `third_party/ggml/VERSION.txt` fija el commit `3cf03257` del **2026-09-20** y `glm5-next` se fusionó en llama.cpp el **2026-09-30** (PR #27773): el pin actual **no** contiene la arquitectura. Re-pinear todo ggml rompe la garantía de bit-exactitud de los kernels Qwen; mitigación: segundo checkout de llama.cpp solo como oráculo GLM (y para las fuentes que se transcriban).

---

## serve/setup

### Tokenizador, plantilla, razonamiento, herramientas, parada

| Pieza | archivo:línea | Qué asume de Qwen | Qué hay que hacer |
| --- | --- | --- | --- |
| Tokenizador BPE a nivel de byte, cargado del GGUF | `tools/strata_tokenizer.py:56-65` (`QWEN35_PATTERN`), `:89` (`regex.compile(QWEN35_PATTERN)` sin mirar `pre`), `:276-279` (`add_bos_token: False` y `pre_pattern` fijos); `serve/server.py:2884-2892` (`ST.Tokenizer(tokens, merges, types)` ignora `pre`) | Regex de pre-tokenización `qwen35` único; tabla de tokens "especiales" por tipo 3/4 | **[R2, verificado]** GLM-5.3-Flash: BPE byte-level de 154.880 tokens y 321.649 merges, regex estilo GPT-4/Qwen con `\p{N}{1,3}` (Qwen: `\p{N}` suelto), sin normalizador y con `[gMASK]<sop>` en la plantilla: tabla `tokenizer.ggml.pre` -> regex, `add_bos` por perfil y test de ida y vuelta + oráculo llama.cpp (la propia herramienta documenta que un patrón casi igual "round-trips perfectly and is wrong") |
| Plantilla de chat | `serve/chat_template.jinja:58-181` (`<\|im_start\|>`, `<think>`, bloque XML de herramientas, `reasoning_effort` low/medium/xhigh); `server.py:2936-2937` carga `tokenizer/chat_template.jinja` si existe (lo exporta `iq_pack`) | Plantilla de reserva = Qwen; **ya se usa la del modelo si está en la carpeta** | **[R2]** La plantilla de GLM-5.3-Flash emite siempre `<\|system\|>Reasoning Effort: Max`, abre `<think>` en el prompt de generación (el pensamiento no se puede desactivar) y usa `m.content.0.type` (Unsloth la reescribió a `[0]` en sus GGUF). Usar la del GGUF y verificar el render con goldens propios (`serve/chat_golden.json` es de Qwen) y el soporte de `.0.` en el Jinja del servidor |
| Esfuerzo de razonamiento | `frontend.py:66-95` (`EFFORT`, `effort_kwargs`, `budget_effort` -> `enable_thinking`/`reasoning_effort`); `web/app.js:464,686,936` (none/low/medium/high) | Los niveles son los de la plantilla Qwen. **[R2]** GLM-5.3-Flash: `reasoning_effort` = `low`/`high`/`max` (otro valor => `max`), `enable_thinking=false` no existe y hay `clear_thinking` (recomendado `true` en chat) | Mapear niveles por perfil de modelo; ocultar niveles que no existan |
| Parser de razonamiento | `frontend.py:274` (`THINK_END = "</think>"`), `OutputParser` estado inicial `reasoning` si `thinking` (`:367-`); `server.py:63` (`REASONING_WRAP_UP`) | La plantilla Qwen termina el prompt con `<think>\n`, así que la salida arranca dentro del pensamiento | `ReasoningFormat` por perfil: etiqueta de apertura/cierre y si el prompt ya abre `<think>`. **[R2]** GLM: el prompt acaba en `<think>` y cierra con `</think>` = 154842 |
| Parser de llamadas a herramientas | `frontend.py:275-352` (`<tool_call><function=NAME><parameter=P>VALUE</parameter>...</function></tool_call>`, `param_end`, `call_end`, `parse_tool_call`) y su *scanner* en streaming (`OutputParser._scan`); la plantilla impone ese formato (`chat_template.jinja:83,134`) | Formato XML de Qwen3-Coder | `ToolFormat` enchufable. **[R2, verificado]** GLM-5.3-Flash: `<tool_call>NOMBRE<arg_key>K</arg_key><arg_value>V</arg_value>...</tool_call>` (los no-strings con `tojson`) y resultados tras **un** `<\|observation\|>` como secuencia de `<tool_response>`: nuevo parser + scanner de streaming + tests (en vLLM se llama `glm47`) |
| Tokens de parada | `server.py:59,993-994` (`<\|im_end\|>`, `<\|endoftext\|>`); `generate.cpp:384,1539` (motor: `{248044,248046}`) | Ids y literales Qwen | **[R2]** GLM: `eos_token_id` = 154820 `<\|endoftext\|>`, 154827 `<\|user\|>`, 154829 `<\|observation\|>` (una llamada a herramienta termina en `</tool_call>` y el modelo emite `<\|observation\|>`). Leer del GGUF y pasar `--eos-ids` al motor |
| Nombre de modelo por defecto | `server.py:946,2938` (`"qwen3.8-flash-next"`) | Cosmético | De la config |
| `calibrate.py` | `tools/calibrate.py:44` (prompt con `<\|im_start\|>user... <think>`) | Prompt Qwen cableado | Renderizar con la plantilla del perfil |
| `ByteTokenizer` de pruebas | `server.py:892-914` (especiales Qwen) | Solo tests | Parametrizar |
| Esquema de "sampling defaults" (`top_k` 1..64) | `server.py:2772-2812` | Genérico | R |

### Catálogo, descargas, perfiles, nombres de modelo

| Pieza | archivo:línea | Asunción | Cambio |
| --- | --- | --- | --- |
| `MODELS` (por tamaño: Q2_0, IQ2_XS, IQ3_XXS, IQ3_S, IQ1_M, UD-Q4_K_XL con GB de descarga/RAM/arena) | `setup.py:111-131` | Clave = cuantización, filtro `families`; cifras medidas de Qwen | Entradas nuevas por modelo GLM y por cuantización realmente soportada (**[R2]** ficheros de 74,9 GB a 342 GB; IQ2_XS de bartowski 101,4 GB; no hay GSQ-RCO oficial); cifras **medidas** (la regla de estilo de `AGENTS.md:22-24` exige medición) |
| `FAMILIES` (qwen, swift, coder, unsloth: HF repo, nombre de fichero, mmproj, `profile`, `pack_args`, `sha256`) | `setup.py:153-184` | Todas comparten arquitectura y mmproj | Primera familia con arquitectura distinta: `family -> arch` |
| URLs HF y motor *prebuilt* | `setup.py:76-101` (`PREBUILT_URL = github.com/Niko1221/Strata/releases/...`), `:107` (`MIN_ENGINE`) | El motor descargable es el del upstream (sin GLM) | Canal de releases propio del fork o compilar; `MIN_ENGINE` por arquitectura |
| `get_llama_cpp()` pin de llama.cpp para gguf-py y build | `setup.py:989-1020` | Un solo pin | gguf-py debe conocer la arquitectura (solo se usa para dequant) |
| Detección de GGUF no soportado y `--gguf-dir` | `setup.py:875-919` | Lista blanca de cuantizaciones Qwen | Lista blanca GLM |
| Paso de empaquetado | `setup.py:3498-3520`: canónico Q2_0 (`strata_pack.py`) o nativo (`iq_pack.py`); busca `per_layer_token_embd.weight` y falla si no está | PLE obligatorio | Rama por arquitectura |
| MTP "viene del checkpoint original de Qwen" | `setup.py:3521-3541` | Descarga `mtp_*` | Descarga/pack GLM `nextn` |
| Argumentos del motor | `setup.py:3550-3567`: `--spec 4 --spec-min-p 0.5 --mtp`, y la **cuenta de KV** `kv_ram_gb = ctx*13*(576\|1056)` ("12 QSA + draft") | 12 capas de atención con 2 KV heads x 256 | Fórmula de KV desde `ModelArch` **[R2]**: GLM-5.3-Flash ~16,9 KB/token (11 capas x (latente 512 + indexador 256) x 2 B), de la escala de Qwen |
| Contexto/rope: `CONTEXTS`, entrenado = 262144 | `setup.py:151,2883-2918` | Longitud entrenada de Qwen | Desde GGUF; **[R2]** GLM-5.3-Flash: 1.048.576 y sin RoPE (no hay escalado YaRN que resolver) |
| `--control-vector-layer-range 4 44` (proyección experimental) | `setup.py:3624-3627`; `data/experimental-speed-projection/` | Vector de Qwen | Omitir en GLM |
| Perfil de expertos por familia (`profile`) | `setup.py:3556-3557`; `data/expert-profile.bin`, `...-coder.bin` | Trazas de Qwen | Generar con `--dump-routing` + `make_profile.py --n-layer/--n-expert` |
| Catálogo duplicado para IA/MCP | `tools/strata_mcp.py:52-72,425,663-665,1212,1238,1539`; `docs/AI_SETUP.md`, `docs/MODELS.md` | Segunda copia de MODELS/FAMILIES (hay test que las compara: `tools/test_strata_mcp.py`) | Un solo catálogo (JSON) importado por ambos |
| Docker | `docker-entrypoint.sh:10,26-27` (`FAMILY=qwen`) | Prefijo por familia | Ampliar |
| Tests de setup | `tools/test_setup_golden.py/.json`, `test_setup_*.py` | Golden de decisiones Qwen | **Deben seguir pasando sin cambios** (guardia de regresión Qwen en la capa Python, ejecutable sin GPU) |

Notas: el servidor ya aísla el motor tras `Engine.generate(ids, max_new, sampling, cancel) -> Iterator[int]` (`server.py:17-20,83-85`), lo que permite un motor alternativo (ver "Opción puente" en el plan). El perfil de expertos que `server.py` valida por `(layers, experts)` (`:804-816`) ya impide mezclar perfiles de modelos distintos.

---

## Diseño de abstracción propuesto

*(Revisado en la ronda 2: GLM-5.3-Flash comparte la estructura de bloque con Qwen, así que la abstracción pasa de "componentes paralelos" a "ranuras de operación".)*

### Principios

1. **Qwen intacto por construcción**: las operaciones de Qwen son *las funciones actuales* (`gdn_layer`, `qsa_layer`, `gr_read/gr_write`, `router_top10`...) detrás de una tabla de punteros, sin cambiar su aritmética ni el orden de lanzamiento de kernels. El código GLM se añade en ficheros nuevos.
2. **Un descriptor de datos** (`ModelArch`), leído del GGUF, sustituye a `Qwen4ExpGuard` y a los valores por defecto de `ModelGeometry`. `ModelGeometry` queda como *vista de compatibilidad* de Qwen para no tocar ~50 firmas en la Fase 0.
3. **Tres ejes por capa**: `mixer` x `ffn` (y a nivel de modelo `residual`).
4. **Abstracción por ranura de operación dentro de los bucles existentes** (`layer.cpp`, `verify.cpp`, `prefill.cpp`, `mtp.cpp`), porque el bloque de GLM-5.3-Flash tiene la misma estructura que el de Qwen: mismo intercalado, mismos 4 flujos, mismo MoE con compartido y doorbell. Cada sitio de llamada pasa de `gdn_layer(...)` a `ops.linear_mixer->decode1(...)`: un cambio local. Esto evita duplicar ~9.200 líneas (`verify` 2.201 + `prefill` 7.037) casi idénticas.
5. **Consistente con el estilo del repo**: funciones libres, `enum` + tablas de punteros, ficheros planos (`index.txt`, `native_experts.txt`) en vez de JSON en el motor (`weights.hpp:28-30`).

### `ModelArch` (nuevo `include/strata/arch/arch.hpp`)

```cpp
enum class Mixer : uint8_t { Gdn, Kda, Qsa, Dsa };          // lineal: Gdn|Kda; disperso: Qsa|Dsa
enum class Ffn   : uint8_t { Moe, Dense };
enum class Gate  : uint8_t { SoftmaxTopK, SigmoidBias };
enum class Resid : uint8_t { Gated, Mhc };                   // ambos con hc=4 flujos

struct MoeSpec  { int n_expert, top_k, n_group = 1, topk_group = 1; Gate gate; bool norm_topk = true;
                  float routed_scale = 1.f; bool sel_bias = false;       // exp_probs_b
                  float swiglu_limit = INFINITY;                          // 10 en GLM
                  int n_shared = 0; int n_ff_exp = 0, n_ff_shared = 0; bool shared_scalar_gate = false; };
struct LinSpec  { int heads_k, heads_v, head_dim /*S*/, d_conv; bool gate_per_channel; float gate_lower_bound; };
struct AttnSpec { /* QSA */ int n_head, n_head_kv, head_dim, n_rot; bool out_gate, qk_norm;
                  /* DSA */ int q_lora = 0, kv_lora = 0, d_nope = 0, d_v = 0; bool nope = false;
                  /* indexador */ int idx_heads, idx_dim, idx_pool, idx_topk; bool idx_weights, idx_learned_pool; };
struct LayerDesc{ Mixer mixer; Ffn ffn; int mixer_ordinal; int moe_ordinal; bool is_nextn; };

struct ModelArch {
  std::string name;                         // general.architecture ("qwen4exp", "glm5-next")
  int n_embd, n_vocab, n_layers;            // capas principales (sin MTP)
  int n_nextn = 0; float rms_eps; Resid resid; int hc = 4, hc_lr = 0;
  MoeSpec moe; int n_ff_dense = 0; LinSpec lin; AttnSpec attn; bool ple = false; RopeSpec rope;
  std::vector<int32_t> eos_ids; std::vector<LayerDesc> layers;
  const TensorSchema* schema;               // nombres, formas y forma de motor esperada por tensor
  static bool from_gguf(const GgufModel&, ModelArch&, std::string& err);  // registro por general.architecture
  ModelGeometry qwen_geometry() const;      // vista de compatibilidad (solo Qwen)
};
```

- Para Qwen `from_gguf` reproduce exactamente hoy: `qwen_geometry() == ModelGeometry{}` campo a campo y `geometry_key` idéntico (`conversation_state.cpp:14-18`); un test unitario sin GPU lo comprueba.
- **LayerPlan** (`layers[]`): para GLM-5.3-Flash sale de la misma regla `l % 4 == 3` y añade `ffn = Dense` en las capas 0-2 y la capa MTP como `is_nextn`. Sustituye a `is_qsa_layer` y a la aritmética `layer / interval` (`session.cpp:58-108`, `verify.cpp:454,975`, `prefill.cpp:1468`...). Hoy el 25 % de ese trabajo ya lo hace el predicado: el `LayerPlan` es sobre todo para los ejes nuevos.
- **TensorSchema**: tabla por arquitectura (sufijo, rol, forma, forma de motor esperada), que sustituye `want1/want2` de `layout.cpp:48-129` y se reutiliza en `iq_pack.py` (hoy `FORM`). El empaquetador puede escribir un `arch.txt` plano con `clave=valor` como contrato verificable por el motor (mismo estilo que `native_experts.txt`).

### Ranuras de operación

```cpp
struct ResidualOps    { read, write, fused_write_read, head_mix, workspace_bytes };            // Gated (GR) | Mhc
struct LinearMixerOps { state_bytes, zero, decode1, window, chunk, commit, snapshot, restore }; // Gdn | Kda
struct SparseMixerOps { state_bytes, zero, decode1, window, chunk, commit, snapshot, restore }; // Qsa | Dsa
struct RouterOps      { single, multi, chunk };                  // SoftmaxTop10 (512/256) | SigmoidBias (288)
struct ExpertActOps   { swiglu /*gate, up, limit*/ };            // limit = inf (Qwen) | 10 (GLM)
struct ArchOps { const ResidualOps* resid; const LinearMixerOps* lin; const SparseMixerOps* sparse;
                 const RouterOps* router; const ExpertActOps* act; };
```

Los ~30-40 sitios de llamada de `layer.cpp:1142-1315`, `verify.cpp`, `prefill.cpp` y `mtp.cpp` usan `ops.*`. Las ranuras de Qwen son envoltorios triviales de las funciones actuales. Comunes e inmóviles: tier de expertos, doorbell, caché, pool de CPU, sampler, GEMV densos, `NativeHead`, KV paginado/streaming (con una pool única K=V para DSA).

**Alternativas descartadas.** (a) *Componentes paralelos* (`GlmVerifier`, `GlmPrefill`...; recomendación de la ronda 1): solo se justifica si el bloque es distinto; aquí duplicaría ~9.200 líneas. Sigue siendo la opción segura si en el futuro se añade una arquitectura de bloque realmente distinta (p. ej. GLM-4.7-Flash, sin 4 flujos ni recurrencia). (b) *Herencia virtual por capa*: añade indirección en caminos de captura de grafos sin ganancia.

### Cómo se garantiza "Qwen bit-idéntico"

1. No se modifican kernels Qwen; la Fase 0 añade datos y despacho, y convierte literales en `assert` derivados de `ModelArch` (la comprobación existente, p. ej. `prefill.cpp:671`, pasa a compararse con `arch`).
2. Evidencia automática (requiere GPU, ver Riesgos): (i) *volcado de nodos de cada grafo CUDA capturado* (p. ej. `cudaGraphDebugDotPrint` o hash de parámetros de nodos) antes y después de la refactorización con la misma configuración; (ii) logits idénticos bit a bit con `--dump-logits` y estado con `STRATA_STATE_HASH` (`generate.cpp:5836-5880`) en N prompts, con/sin especulación, KV FP16/INT8/Q4/K8V4, Coder (256 expertos) y Unsloth; (iii) los 33 `*_parity.cpp` de `src/kernels`; (iv) `tools/conversation_cache_parity.py` (A/B/A); (v) `tools/test_setup_*.py` y `serve/test_*.py` (sin GPU).
3. Opción de compilación `STRATA_ARCH_GLM` para poder liberar un binario "solo Qwen" mientras GLM madura.

### Serve/setup: `ModelProfile`

Un perfil por familia/arquitectura, serializado en el config que genera setup y leído por `serve/server.py`: `tokenizer_pre` (clave a regex), `eos_ids`, `chat_template`, `tool_format` {qwen_xml, glm_arg_kv}, `reasoning` {open_in_prompt, tags, efforts disponibles}, `stop_literals`, `default_model_name`, `sampling` recomendado. `OutputParser` delega en `ToolFormat`. El catálogo (MODELS/FAMILIES) sale a un JSON único usado por `setup.py` y `tools/strata_mcp.py`.

---

## Plan por fases con esfuerzo

Unidad: person-weeks (pw). Estimación gruesa +-40 %. Supone ingenieros con CUDA que ya conocen el repo y acceso a una máquina de desarrollo con GPU (>=16 GB) y >=128 GB de RAM (para cargar los ~100 GB de GLM sin SSD). *(Reescrito en la ronda 2: ver R7 para lo que se abarata y lo que no.)*

### Fase -1 (opcional): opción puente - 1,5 a 2,5 pw
Un `LlamaServerEngine` que implemente el `Engine` Protocol (`server.py:83-85`) hablando con `llama-server` (llama.cpp ya soporta `glm5-next`, PR #27773; con `--n-cpu-moe`/`-ot exps=CPU`), reutilizando tokenizador, plantilla y parsers GLM de la Fase 4. Da GLM funcionando en semanas, sin caché de expertos ni especulación de Strata (el valor diferencial), y sirve de **línea base medida** y de oráculo. 06 §7 recoge la línea base de terceros (RTX 4090 + 128 GB DDR5: ~9 tok/s con IQ3_S). **Decisión de producto.**

### Fase 0 - Refactor con Qwen bit-idéntico: 6-10 pw
| Tarea | pw |
| --- | --- |
| `ModelArch` + `from_gguf` + registro + test "Qwen == defaults" (sustituye `Qwen4ExpGuard`, `generate.cpp:1845-1868`, `plan.hpp`) | 1-1,5 |
| `LayerPlan` ligero (eje `ffn`, capa MTP, máscara de capas con expertos; el intercalado ya sirve) | 0,5-1 |
| Literales -> parámetros o aserciones derivadas de `ModelArch`: `prefill.cpp:90-91`, `prefill/kernels.cu:17-18` (GDN/KDA), `kNative*Bytes`, `CAP`, `kMaxWindowEntries`, `RMS_EPS`, EOS y vocabulario desde GGUF, opcionalidad de PLE y de `--native` | 2-3 |
| Ranuras de operación (`ArchOps`) en `layer.cpp`, `verify.cpp`, `prefill.cpp`, `mtp.cpp`; Qwen = envoltorios de las funciones actuales | 1,5-2,5 |
| Arnés de bit-identidad (volcado de grafos, golden de logits, hash de estado) y CI en GPU | 1-2 (+ acceso a GPU) |
**Salida**: Qwen/Coder/Swift/Unsloth pasan el arnés sin diferencias. Se puede avanzar sin GPU hasta el 70 %, pero **no se puede cerrar** sin GPU.

### Fase 1 - Forward GLM-5.3-Flash en GPU+CPU con referencia Python: 32-50 pw
| Subfase | Contenido | pw |
| --- | --- | --- |
| 1a | Referencia `ref/glm5next.py` (KDA, DSA + indexador, mHC, router, dense; **hoy `ref/` solo tiene `load.py`**: las especificaciones `ref/moe.py`, `gdn.py`, `qsa.py`, `model.py`, `ngram.py` que citan los comentarios no están en el fork); segundo checkout de llama.cpp como oráculo (`glm5-next`); `TensorSchema` GLM e `iq_pack.py` (nombres reales de tensor, formas BF16/F32, `exp_probs_b`, `nextn`); lectura del PR #27773 para cerrar mHC, puerta KDA y compresión kpool | 3-5 (sin GPU) |
| 1b | **KDA**: kernels de 1 token, ventana y prefill con decaimiento por canal; preproceso de la puerta y de las 3 convs; estado, commit y snapshot (R2) | 4-7 |
| 1c | **DSA**: atención MLA absorbida (decode, ventana, prefill con tensor cores) 6-10; indexador (32 cabezas con pesos, pooling aprendido, sin RoPE) 3-4; pool única K=V, streaming y cuantización de KV 1-2 (R3) | 10-16 |
| 1d | **mHC**: lectura/escritura de 1 token, ventana y prefill, y variante fusionada (R4) | 3-5 |
| 1e | **MoE, densas y tipos**: router sigmoide + sesgo (3 caminos) 1,5-2; capacidades de buffers y `swiglu_limit` 2,5-4; capas 0-2 densas y compartido sin puerta 1-1,5; tipos de cuantización en GPU grouped, MMQ, dequant y embed 2-3 (R5) | 7-10 |
| 1f | Integración (ventana, prefill, sesión, cache de conversación, cabeza con media de flujos, captura de grafos) 2,5-3,5; bring-up en hardware real y paridad capa a capa contra la referencia y contra llama.cpp 2,5-3,5 | 5-7 |
**Hitos dentro de la fase**: **M1** decode correcto con atención MLA *densa* (la selección es la identidad por debajo de 2.051 celdas, como en el QSA de Qwen, así que M1 no necesita indexador y es exacta en contextos de hasta ~2K); **M2** indexador DSA + KV streaming (contexto largo) y prefill por trozos; el M1 llega hacia la semana 12-14 con 3 ingenieros. **Restricción**: `Verifier` y `Prefill` son obligatorios (native pack => solo ventanas, `generate.cpp:1950-1956`); con T=1 permitido por `Verifier::run` (`verify.cpp:1026`) se puede arrancar sin drafter.

### Fase 2 - Caché de expertos, SSD y perfil: 3-5 pw
Trazas con `--dump-routing` en GLM, `make_profile.py` generalizado (`N_LAYER/N_EXPERT`), `CostModel` y `pcie_frac` calibrados, planner de VRAM con la nueva geometría, modos `--resident-experts/--mmap-experts` y reparto multi-GPU con `LayerPlan`; y **la parte de memoria de R8**: anillos de E/S en bytes, *lookahead* para router sigmoide, presupuesto de setup propio, modelo de costes con término de SSD, medición en PCs de 32/64/128 GB y NVMe PCIe 3/4. Subió de 2-3 pw (ronda 1) a 3-5 pw.

### Fase 3 - MTP y especulación: 6-9 pw
Ver subsección siguiente. Incluye el drafter con DSA + MoE de 288 expertos como capa 45 del tier de expertos, empaquetado de pesos `nextn`, vocabulario de borrador (opcional), y medida de aceptación. Subió de 5-7 pw.

### Fase 4 - serve/setup: 4-6 pw (paralelizable desde la semana 1, sin GPU)
`ModelProfile`, regex del tokenizador, plantilla y goldens propios, `ToolFormat` GLM (con *scanner* de streaming), parser de razonamiento (thinking siempre activo, sin nivel "none"), EOS desde el GGUF, catálogo único (JSON) y familia GLM con cifras medidas, rama de empaquetado, fórmula de KV, canal de binarios del fork, tests (`serve/test_*.py`, `tools/test_setup_*.py`) y documentación al estilo de `AGENTS.md`.

### Fase 5 (opcional) - Visión: 2-3 pw
mmproj de ~1,1 GB y ViT de 24 capas; NoPE, así que solo hay que sustituir embeddings (sin `mrope`); depende de que `mtmd` del pin nuevo soporte el proyector.

### Totales y calendario
| Concepto | pw |
| --- | --- |
| Fase 0 | 6-10 |
| Fase 1 | 32-50 |
| Fase 2 | 3-5 |
| Fase 3 | 6-9 |
| Fase 4 | 4-6 |
| **Total sin visión** | **51-80 (mediana ~65)** |
| Fase 5 | +2-3 |
Calendario: 5-7 meses con 3 ingenieros (4-5,5 con 4; KDA, DSA, mHC y MoE son independientes tras la Fase 0). Camino crítico: 0 -> 1c (MLA absorbida) -> 1f -> 3. Paralelo desde el inicio: 1a, 4, parte de 1e y el trabajo de Fase 2 que no depende del forward.

**Comparación con la ronda 1**: Fase 0 7-11 -> 6-10; Fase 1 29-42 (MLA + DSA) -> 32-50; Fase 2 2-3 -> 3-5; Fase 3 5-7 -> 6-9; total 47-69 -> 51-80. No es más barato: baja el riesgo de diseño y el plumbing, pero KDA, mHC y la MLA absorbida son kernels nuevos, el MTP lleva un MoE completo y la escala (memoria, SSD, caché al 5,5 %) añade trabajo.

### Cómo se transfiere la decodificación especulativa

**Se hereda sin cambios**
- Garantía y protocolo: la ventana de verificación (T<=8) acepta un borrador exactamente cuando el decode greedy lo habría producido (`verify.hpp:1-24`), con muestreo acoplado (`coupled_draft`).
- `spec/Controller` (maximiza E[tokens]/T(paso), `controller.hpp:1-23`) y `SuffixDrafter` (solo tokens, sin pesos): aplican a cualquier modelo. Hay que **recalibrar** `CostModel` (`dense_ms=11.0`, `hit_rate`, `distinct_ratio`, `mtp_draft_ms` son mediciones de Qwen del 23-sep, `controller.hpp:25-37`).
- La interfaz verificador -> drafter: la ventana exporta las filas finales de residual por posición y `MtpDrafter::draft(T, tokens, p, a, drafts)` las consume (`mtp.hpp:61-80`). En GLM esas filas son los 4 flujos mHC (igual que en Qwen son los 4 de GR).

**Corrección a la ronda 1**: dije que el `commit` de GLM sería "casi un no-op". **No lo es**: KDA tiene estado recurrente y conv, así que el commit repite la convolución y la recurrencia (como `verify.cpp:960-1010` para GDN, con la puerta `[T,HV,S]` guardada por token) y el indexador DSA tiene su cola y su pooling aprendido que restaurar. Es tan complejo como el de Qwen.

**Lo que hay que construir** (06 §3.1, §5.2, §6.6)
- Drafter de la capa MTP de GLM: `eh_proj [4096,8192]` sobre la concatenación de la normalización del embedding del token siguiente y la de la entrada oculta (`enorm`, `hnorm`), seguido de un bloque con **DSA completa + MoE de 288 expertos**, `shared_head.norm` y el `lm_head` compartido (7,43 B de parámetros, de ellos 7,25 B de expertos).
- Reutilizar del `MtpDrafter`: estado KV en anillo, grafos de ronda/paso, muestreo acoplado, cabeza de borrador con subconjunto de vocabulario (opcional con 154.880 tokens) y la política de atención densa sobre la ventana, que solo afecta a la calidad del borrador, no a la salida (`mtp.hpp:11-23`). `index_share_for_mtp_iteration` indica que el indexador se comparte entre iteraciones.
- **Cambiar**: el drafter de Qwen usa expertos canónicos Q2_0 residentes en VRAM con kernels `s2` de dimensiones literales (`mtp.cpp:184,572-580`; 708 MB). Los 288 expertos de GLM (~2,1 GB a IQ2_XS) no caben residentes en 12 GB junto al resto: deben pasar por el camino "native" y el tier de expertos (capa 45), con CPU pool y doorbell dentro de la cadena de borradores (cada paso del drafter lee 8 x 7,3 MB = 58 MB de expertos).
- **Los expertos no se amortizan en la ventana**: con rutado casi uniforme, una ventana de 3 tokens toca 23,3 de los 24 expertos posibles por capa (06 §6.6). Qwen mide `distinct_ratio` de 1,70 para T=2 (`controller.hpp:30`). Así que solo se acelera la parte densa (KDA, DSA, compartido): ik_llama.cpp mide +20 % de media (+7 % relato, +27 % código; aceptación 61,5 %, n_max=3), Unsloth encuentra n=2 óptimo. **Limitados por SSD, el controlador elegirá casi siempre `k=0`**: el modelo de costes necesita el término de SSD y `distinct_ratio` medido.
- Pesos: llama.cpp mainline aún no soporta MTP (PR #27917 abierto); ik_llama.cpp sí (#2548). Los GGUF de Unsloth parecen incluir las capas MTP **(a verificar)**; si no, generalizar `mtp_fetch.py` (peticiones *range* a safetensors, revisión fija + SHA256; la capa son 7,4 GB en FP8 con bloques 128x128), `mtp_pack.py` y `mtp_rt.py` (cuantizador propio; hoy Q2_0/Q4_0/Q8_0).
- Medición: la aceptación por paso de Qwen fue 0,89/0,86/0,85 (`mtp.hpp:4-6`); en GLM no hay medición propia. Criterio de salida de la Fase 3: aceptación por posición y tokens por ventana con prompts de chat y de código, en configuración con expertos en RAM, y ganancia neta >= 5 % (el `min_gain` del controlador).
- **Decisión pendiente**: el "bit a bit" de Qwen se apoya en que el kernel multi-token reproduce el de un token. Una atención MLA con tensor cores (G=64) acumula en otro orden con T > 1; o se escriben variantes "por token", o se relaja la garantía a "igual dentro de tolerancia" (como hace llama.cpp). Afecta coste y mensaje de producto.

---

## Riesgos

| # | Riesgo | Prob. | Impacto | Mitigación |
| --- | --- | --- | --- | --- |
| 1 | **Sin GPU/modelo no se puede validar la bit-identidad de Qwen** tras la Fase 0 (ni los *parity* de kernels; en este fork `tests/CMakeLists.txt` y `bench/micro/` no existen: `CMakeLists.txt:53-57`, `:325`) | Alta | Alto | Runner GPU dedicado; volcado de grafos CUDA y golden de logits creados *antes* de empezar la Fase 0; PR pequeños |
| 2 | **Rendimiento en el PC objetivo**: expertos 5,1x mayores, 12.096 expertos, caché de VRAM al 5,5 % y 2,44 GB de expertos por token. Nadie ha medido GLM-5.3-Flash en una GPU de 12 GB. Estimación: 0,7-3,9 tok/s con 32-64 GB (R8) | Alta | Alto | Fase 2 temprana para medir; confirmar con el usuario el hardware y las expectativas; ofrecer la opción puente y/o GLM-4.7-Flash |
| 3 | **Detalles sin verificar** que cambian kernels: operador mHC exacto, fórmula de la puerta KDA (cota -5), compresión kpool + APE y "slot muerto", alcance de `swiglu_limit`, nombres de tensores de `glm5-next` (cadenas `glm5next` de Unsloth y `glm5-next` de mainline: compatibilidad no probada) | Media | Alto | Leer el PR #27773 y el código de transformers antes de 1b-1d; la referencia Python (1a) se escribe contra ellos |
| 4 | **Cobertura de cuantización**: los GGUF públicos usan K-quants/IQ en mezclas que hoy no tienen kernels GPU de experto, MMQ ni dequant (`iq_kernels.cu:510-512`); `setup.py:875-886` los rechaza incluso para Qwen | Alta | Alto | Subfase 1e temprana; fijar los tipos de los 2-3 GGUF objetivo (bartowski IQ2_XS, Unsloth); parity contra ggml |
| 5 | **Pin de llama.cpp**: `third_party/ggml/VERSION.txt` es del 2026-09-20 y `glm5-next` se fusionó el 2026-09-30; el oráculo y las fuentes a transcribir están en un commit posterior. Re-pinear todo rompe la bit-exactitud de los kernels "native" de Qwen | Alta | Medio | Segundo checkout solo para el oráculo GLM; transcribir kernels con atribución MIT al estilo actual |
| 6 | **Oráculo del MTP**: PR #27917 abierto en mainline; solo ik_llama.cpp lo tiene | Media | Medio | Validar el MTP contra ik_llama.cpp y contra transformers (que no lo implementa): la referencia propia es la fuente de verdad |
| 7 | **MTP con MoE**: 288 expertos (~2,1 GB) no caben residentes en VRAM; coste del paso del drafter; la aceptación puede no compensar | Media | Medio | Tratar la capa 45 como capa del tier de expertos; medir antes de optimizar; `k=0` por defecto si no compensa |
| 8 | **SSD**: el modo "en sitio" está validado solo con Qwen UD-Q4_K_XL, NVIDIA, 1 GPU y 64 GB; rutado casi uniforme de GLM (OpenMOSE) limita el beneficio de cualquier caché; el prefill depende del SSD (200-600 tok/s) | Alta | Alto | Medición propia en Fase 2 (trazas, perfil aprendido, lookahead); documentar solo lo medido |
| 9 | **Calidad a 2 bits**: Unsloth mide que IQ2_XXS retiene el 76 % del top-1 (KLD 0,45). Strata no tiene cifra de calidad para GLM, y la poda REAP no tiene evaluación | Alta | Medio | No prometer calidad (regla de `AGENTS.md:22-24`); medir con el arnés de `docs/UNSLOTH_Q4.md`; ofrecer cuantizaciones mayores si hay RAM |
| 10 | **`generate.cpp` es una función `main` de ~5.800 líneas** con ~40 accesos directos a estado Qwen (`ss.qsa_states`, `ss.gdn_alloc`, `ss.R`) | Alta | Medio | Ranuras de operación; PR por ranura; sin tocar cuerpos de funciones Qwen |
| 11 | Deriva respecto al upstream (Niko1221/Strata, v0.1.38 con releases frecuentes) | Alta | Medio | Parches localizados; proponer `ModelArch` al upstream; rebase periódico |
| 12 | Ventana de verificación con aritmética por token idéntica: MLA con tensor cores (G=64) acumula en otro orden (ver decisión pendiente en especulación) | Media | Medio | Decidir pronto "bit-exacto" vs "tolerancia" para GLM |
| 13 | **Contexto de 1M**: límites del scorer y de la selección sin verificar (`max_blocks` 262.146); KV de 17,7 GB compite con el presupuesto de expertos | Media | Medio | Fijar por defecto 128-256K; verificar el scorer por encima de 524.288 antes de ofrecerlo |
| 14 | HIP/AMD: todo kernel nuevo debe compilar y validarse en HIP (gfx1100/1201; `cmake/hip_backend.cmake`, `include/strata/hip_compat`); algunos caminos rápidos difieren por backend (`CMakeLists.txt:271,472-477`, `docs/AMD_HIP.md:267-277`) | Media | Medio | +15-25 % al esfuerzo CUDA; ruta portable (FMA) primero |
| 15 | Distribución: el motor *prebuilt* de `setup.py` apunta a releases del upstream (`:100-107`) | Alta | Medio | Pipeline de releases del fork; `MIN_ENGINE` por arquitectura |
| 16 | Parser/plantilla distintos: thinking siempre activo (el nivel "none" de la UI deja de existir), tool-calls `arg_key/arg_value` con streaming, plantilla con `.0.` en Jinja | Media | Medio | Scanner propio, goldens de plantilla y pruebas con clientes (Claude Code, opencode) |
| 17 | Regresión silenciosa por `rms_eps` (GLM usa 1e-5; `layer.cpp:45` fija 1e-6), formas o BF16/F32 mal leídos (el propio código documenta el modo de fallo "plausible pero erróneo", p. ej. `layout.cpp:1-15`) | Media | Alto | `TensorSchema` con comprobación de forma y forma de motor al cargar; referencia Python capa a capa |

---

## Preguntas abiertas

1. **¿Qué modelo quiere el usuario realmente?** GLM-5.3-Flash completo (321 B, ~88-90 GB de expertos, 1-4 tok/s en 32-64 GB), una variante podada REAP, o GLM-4.7-Flash (30B-A3B, cabe en 12 GB + 32 GB, otra arquitectura). Cambia por completo el plan (06 §1.5).
2. **Detalles de arquitectura sin verificar**: operador mHC exacto (Sinkhorn: ¿normalización por filas/columnas, orden, `hc_eps`?), fórmula de la puerta KDA con cota -5, compresión kpool con `ape` y si hay "slot muerto", alcance de `swiglu_limit` a denso/compartido, y si el MTP usa mHC propio.
3. **GGUF y nombres de tensor**: cuál es la fuente objetivo (bartowski, Unsloth, antirez), si incluye las capas MTP, y qué tipos de cuantización lleva cada tensor (define 1e y el catálogo); compatibilidad entre las cadenas de arquitectura `glm5next` y `glm5-next`.
4. **Hardware objetivo** (VRAM, RAM, tipo de NVMe): condiciona la viabilidad (R8), la caché de expertos y si el MTP con MoE compensa.
5. **Garantía bit-exacta también para GLM**, o tolerancia numérica (afecta a la atención MLA y a transcribir kernels de ggml).
6. **Visión de GLM**: ¿se pide? (Fase 5, 2-3 pw.)
7. **Fase puente** con `llama-server` mientras se construye el motor nativo.
8. **Producto**: ¿una instalación sirve varios modelos (Qwen y GLM) o un proceso por modelo como hoy? ¿Se mantiene el nombre y el canal de releases?
9. **Upstream**: ¿se propone `ModelArch` al proyecto original?
10. **Acceso a GPU y CI** para la Fase 0 (sin él, "Qwen bit-idéntico" no es verificable).
11. **Contexto**: ¿se ofrece 1M o se limita por defecto a 128-256K?
12. **Licencias**: GLM-5.3-Flash es MIT; el modelo borrador de difusión `incoai/GLM-5.3-Flash-DFlash2` es CC BY-NC-ND 4.0 (no comercial) y queda fuera.

---

## Apéndice: tamaño del código por grupo (líneas, `src/` + `include/`, sin tests)

| Grupo | Líneas | Comentario |
| --- | ---: | --- |
| Expertos (source/cache/pool/pinned/peer/remote/CPU kernels/layout) | 8.207 | Reutilizable |
| Kernels GPU de expertos y router (grouped, iq, shared, native_moe, moe_*) | 5.088 | Mixto: grouped/shared R; router y combine G |
| GEMV/GEMM densos (mmvq, bf16, s_gemv, native_bf16, gemm) | 2.948 | R |
| Sampler | 1.081 | R |
| Spec (controller, draft_policy, suffix) | 718 | R |
| Prefill (`prefill.cpp`, `kernels.cu`, moe_*, gemm) | 7.037 | G |
| `layer/session/layout` | 3.620 | G |
| `verify` | 2.201 | G |
| MTP | 1.171 | G/N |
| `generate.cpp` | 6.913 | G |
| GR (kernels) | 1.976 | NA |
| PLE/n-gram | 2.043 | NA |
| GDN | 926 | NA |
| QSA + KV | 4.805 + 824 | NA/G |
| Total `src/`+`include/` | 74.290 | |

**Qué no pude verificar**: ejecución alguna (sin GPU); contenido de `ggml`/`llama.cpp` (no está en el árbol); los detalles de GLM-5.3-Flash que 06 deja sin verificar (operador mHC exacto, puerta KDA, compresión kpool, `swiglu_limit`) y lo que sé de ggml/llama.cpp (marcado "a verificar"); las cifras de R8 son estimaciones mías, sin medición con GLM; que `docs/semantics.md`, `docs/pack-format.md`, `ref/moe.py`, `ref/gdn.py`, `ref/qsa.py`, `ref/model.py` y `ref/ngram.py` (citados en comentarios) no estén en este fork: solo existe `ref/load.py`.
