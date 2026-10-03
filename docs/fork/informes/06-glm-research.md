# Informe 06 - Investigación web sobre "GLM 5.3 Flash" (Z.ai / Zhipu)

Fecha de la investigación: 2026-10-03. Auditor 6 de 7. Ningún archivo de `/home/user/Strata3060` fue modificado
(los archivos descargados y los scripts de cálculo están en `glm-datos/`).

Convención: **[medido]** = cifra publicada por un tercero con hardware indicado; **[calculado]** = salida directa de las
formas de tensores del checkpoint real (verificable); **[estimación]** = mi modelo, con la aritmética mostrada;
**no verificado** = no pude confirmarlo.

---

## 1. Resumen

1. **"GLM-5.3-Flash" existe y es oficial**: `zai-org/GLM-5.3-Flash`, repo creado el 2026-08-25 (UTC), presentado el
   2026-08-26, licencia **MIT**. Es el primer modelo GLM nativamente multimodal. Hermano grande: `zai-org/GLM-5.3`
   (753 B, `GlmMoeDsaForCausalLM`). No hay ambigüedad de nombre.
2. **No es un modelo pequeño. Es MÁS GRANDE que Qwen3.8-Flash-Next.** 321.3 B parámetros totales (18 B activos),
   45 capas + 1 capa MTP, 288 expertos enrutados por capa (top-8) + 1 compartido, cada experto 25.2 M parámetros
   (5.1 veces el de Qwen). El checkpoint oficial FP8 pesa 328 GB. Los expertos solos a 2.3 bits/peso ocupan ~88-90 GB
   (Qwen3.8: 34-36 GB). El GGUF completo más pequeño publicado es de **74.9 GB** (bartowski IQ1_S); los de 2 bits pesan
   96-109 GB; el 3 bits GSQ-RCO (comunitario) 117.5 GB.
3. **Arquitectura "hermana" de Qwen3.8-Flash-Next, no igual**: 34 capas KDA (Kimi Delta Attention, lineal) + 11 capas
   DSA (MLA **sin RoPE**, con indexador de pools de 4 tokens y top-k 2048) + hiperconexiones mHC de 4 flujos con
   Sinkhorn + MoE sigmoid/`noaux_tc`. No tiene tabla n-gram/PLE. Contexto 1 048 576.
4. **Para RTX 3060 12 GB + 32-64 GB DDR4 el modelo completo NO cabe en RAM** a ningún tamaño publicado. Aritmética
   (sección 6): solo quedan ~4.8 GB de VRAM para caché de expertos (~660 expertos = 5.5 % de 12 096; Qwen cachea 18 %),
   y cada token lee 2.44 GB de expertos (Qwen: 0.66 GB). **[estimación]** decodificación: 128 GB de RAM 11-26 tok/s;
   64 GB + NVMe 4-12 tok/s; 32 GB + NVMe 2-6 tok/s. Qwen3.8 IQ2_XS en una RTX 5070 + DDR5: 79 tok/s medidos.
5. **Opciones reales para el objetivo "PC barato"**: (a) correr el modelo completo con expertos en SSD (lento, 2-9 tok/s);
   (b) variante podada REAP-50 (144/288 expertos) cuantizada a ~2.3 bits (~45 GB, cabe en 64 GB; **[estimación]**
   12-25 tok/s) pero sin evaluación de calidad publicada y con evidencia de que GLM-5.3-Flash "no tiene expertos de
   sobra"; (c) pedir otro modelo de la familia: el que SÍ está pensado para hardware barato es
   **GLM-4.7-Flash** (30B-A3B, 2026-01-19, MLA, 64 expertos top-4). Conviene confirmar con el usuario cuál quiere.
6. **Soporte de software** (a 2026-10-03): llama.cpp **fusionó** el soporte (PR #27773, 2026-09-30, arquitectura
   `glm5-next`; MTP aún en PR abierto #27917); ik_llama.cpp lo fusionó (PR #2376, y MTP en #2548); vLLM, SGLang,
   TokenSpeed, transformers 5.16 y KTransformers (solo FP8, >=350 GB de RAM) figuran en el README oficial.
7. **Línea base a batir** (llama.cpp con expertos en CPU): RTX 4090 + 128 GB DDR5, IQ3_S: ~9 tok/s decode (baja a ~6
   a 128K) y ~300 tok/s prefill; fork con caché de expertos en 2x RTX 3090 + 125 GB DDR4-3200: 11.9 -> 22.4 tok/s (3.0
   bit). **No encontré ninguna medición en una GPU de 12 GB.**
8. **Hallazgo clave para el diseño de Strata**: Strata ya tiene piezas reutilizables (indexador de pools "QSA", 4
   flujos "gated residual", MTP, caché de expertos adaptativa), pero GLM exige kernels nuevos (KDA, MLA-NoPE absorbido,
   Sinkhorn mHC, router sigmoid top-8/288, clamp swiglu 10, expertos 4096x2048) y una caché de expertos mucho menos
   eficaz (5.5 % de expertos en VRAM, no 18 %).

---

## 2. Identificación del modelo

| Dato | Valor | Fuente |
|---|---|---|
| Nombre oficial | **GLM-5.3-Flash** (API: `GLM-5.3-Flash`; variante solo-API `GLM-5.3-FlashX`, "200 tokens/s") | [HF](https://huggingface.co/zai-org/GLM-5.3-Flash), [docs.z.ai](https://docs.z.ai/guides/llm/glm-5.3-flash) |
| Repo HF | `zai-org/GLM-5.3-Flash` (FP8, 62 shards) y `zai-org/GLM-5.3-Flash-BF16` | [HF API](https://huggingface.co/api/models/zai-org/GLM-5.3-Flash) |
| Fecha | repo creado 2026-08-25T06:43Z; lanzamiento/open-source anunciado 2026-08-26; última modificación 2026-09-07 | HF API; [SCMP](https://www.scmp.com/tech/big-tech/article/3365433/zhipu-ai-shares-jump-viral-ox-alpha-model-revealed-glm-53-flash-chinese-chips) |
| Nombre en clave | "Ox-Alpha" (probado anónimamente antes) | SCMP; [Unsloth](https://unsloth.ai/docs/models/glm-5.3-flash) |
| Licencia | MIT (README + archivo LICENSE) | [README](https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/README.md) |
| Descargas HF | 5.43 M (la más descargada de zai-org) | HF API |
| Informe técnico | el README cita arXiv 2602.15763 ("GLM-5: from Vibe Coding to Agentic Engineering"), que es el informe de **GLM-5** (feb 2026), anterior a 5.3-Flash. **No hay informe técnico propio de 5.3-Flash que haya podido verificar.** La arquitectura la reconstruyo de `config.json`, formas de tensores, código de transformers y el PR de llama.cpp. | README |

### Modelos cercanos reales (lista `zai-org`, ordenada por fecha de creación)

| Modelo | Fecha HF | Tamaño | Notas | ¿Cabe en 12 GB + 32-64 GB? |
|---|---|---|---|---|
| **GLM-5.3-Flash** | 2026-08-25 | 321 B / 18 B act. | KDA+DSA+mHC, multimodal, 1M ctx | **No** (>= 75 GB aun a 1.9 bpw) |
| GLM-5.3 | 2026-08-25 | 753 B (Unsloth: 744 B / 40 B act.) | `glm_moe_dsa`, 78 capas, 256 expertos | No |
| GLM-5.2 | 2026-06-16 | 753 B | | No |
| GLM-5.1 / GLM-5 | 2026-04-03 / 2026-02-11 | 753.9 B / 754 B (HF) | | No |
| **GLM-4.7-Flash** | 2026-01-19 | **31.2 B total, "30B-A3B"** (el README lo llama así) | `glm4_moe_lite`; GGUF arquitectura `deepseek2`; MLA (kv_lora 512, rope 64); hidden 2048; 47 capas; 64 expertos top-4 + 1 compartido; contexto 202 752; rope_theta 1e6 | **Sí**: Q4_K_M = 18.3 GB, Q6_K 24.7 GB, UD-Q2_K_XL 11.9 GB ([unsloth GGUF](https://huggingface.co/unsloth/GLM-4.7-Flash-GGUF)) |
| GLM-4.6V-Flash | 2025-12-07 | 9-10 B denso | visión | Sí |
| GLM-4.5-Air | 2025-07-20 | 110.5 B (HF; "106B-A12B" de la familia) | `glm4_moe`, 46 capas, 128 expertos top-8, GQA 96/8 | Apretado (estimación: ~45-55 GB a 3-4 bits) |

No existe "GLM-5.x-Flash" distinto de 5.3-Flash ni un "GLM-5.x-Air" (verifiqué la lista completa de `zai-org`).
**Lo que el usuario "más probablemente" quiere**: literalmente `GLM-5.3-Flash`. Pero hay que avisarle de que el nombre
"Flash" no implica "pequeño": el candidato que sí encaja con "hardware barato" es GLM-4.7-Flash.

---

## 3. Arquitectura (config.json real + formas de tensores)

Fuentes: **[cfg]** [config.json](https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/config.json);
**[idx]** [model.safetensors.index.json](https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/model.safetensors.index.json)
y las cabeceras de los shards (descargadas por HTTP range; formas y dtypes exactos);
**[tf]** [modeling_glm5_next.py](https://github.com/huggingface/transformers/blob/main/src/transformers/models/glm5_next/modeling_glm5_next.py);
**[pr]** [llama.cpp PR #27773](https://github.com/ggml-org/llama.cpp/pull/27773);
**[tok]** [tokenizer.json](https://huggingface.co/zai-org/GLM-5.3-Flash/resolve/main/tokenizer.json);
**[api]** [HF API](https://huggingface.co/api/models/zai-org/GLM-5.3-Flash).

### 3.1 Tabla de hiperparámetros

| Parámetro | GLM-5.3-Flash | Fuente |
|---|---|---|
| Clase / model_type | `Glm5NextForConditionalGeneration` / `glm5_next` (texto: `glm5_next_text`); transformers 5.16.0 | cfg |
| Parámetros totales | **321 323 031 390** (BF16 6.93 B + FP8 314.4 B + F32 0.3 M). Solo texto: **320.759 B** (el header GGUF de Unsloth dice 320 759 404 382; mi suma desde las formas da 320 759 031 646: coincide). Visión ~0.56 B | api; mi cálculo |
| Parámetros activos | **18 B** (oficial). Mi suma: 16.74 B (capas principales) + 0.35 B (capa MTP) + 0.63 B (fila de embedding) = ~17.7 B | README; cálculo |
| Capas | **45** principales (`num_hidden_layers`) + **1 capa MTP** (índice 45, `num_nextn_predict_layers: 1`) | cfg, idx |
| hidden_size | **4096** | cfg |
| Patrón de capas | (3 x `linear_attention` [KDA] + 1 x `deepseek_sparse_attention`) x 11 + 1 KDA final = **34 KDA + 11 DSA**. DSA en capas 3, 7, 11, ..., 43 | cfg `layer_types`, `kda_layers`, `full_attn_layers` |
| Atención DSA = MLA **NoPE** | `num_attention_heads` 64, `num_key_value_heads` 64 (nominal; la caché real es el latente), `q_lora_rank` **1536**, `kv_lora_rank` **512**, `qk_nope_head_dim` **256**, `qk_rope_head_dim` **0**, `qk_head_dim` 256, `v_head_dim` 256, `head_dim` 0, `mla_use_nope: true`, `attention_bias` false | cfg |
| Tensores MLA | `q_a_proj` [1536,4096] FP8; `q_b_proj` [16384,1536] FP8; `kv_a_proj_with_mqa` [512,4096] FP8; `kv_b_proj` [32768,512] **BF16**; `o_proj` [4096,16384] FP8; `q_a_layernorm` 1536; `kv_a_layernorm` 512 | idx |
| Indexador DSA | `index_n_heads` 32, `index_head_dim` 128, `index_topk` **2048**, `index_kpool` **4** (pools de 4 tokens; selecciona 2048/4 = 512 pools), `index_kpool_compress` true, `index_kpool_always_select_tail` true, `index_share_for_mtp_iteration` true, `indexer_types` todo "full"; tensores `wq_b` [4096,1536], `wk` [128,4096], `weights_proj` [32,4096], `index_kpool_compress_gate` [128,4096], `..._ape` [4,128], `k_norm` (con bias) | cfg, idx, tf |
| Atención lineal KDA (34 capas) | `linear_attn_config`: `num_heads` 64, `head_dim` 128, `short_conv_kernel_size` 4, `gate_lower_bound` -5.0. Tensores (todos **BF16**): `q/k/v_proj` [8192,4096], `o_proj` [4096,8192], `q/k/v_conv1d` [8192,1,4], `f_a_proj` [128,4096] + `f_b_proj` [8192,128], `g_a_proj` + `g_b_proj` (igual), `b_proj` [64,4096], `A_log` [64], `dt_bias` [8192], `o_norm` [128] (RMSNorm con gate). Puerta de decaimiento por canal (low-rank), no escalar | cfg, idx, tf |
| **RoPE** | **Ninguno** (NoPE): `qk_rope_head_dim` 0, sin `rope_theta`/`rope_parameters` en la config; el código de transformers dice que la atención principal y el indexador no usan rotary; el PR de llama.cpp: "attention layers are nope only MLA". (GLM-5.3 grande sí usa rope_theta 8 000 000 con 64 dims rope; GLM-4.7-Flash rope_theta 1 000 000) | cfg, tf, pr |
| Contexto máximo | `max_position_embeddings` **1 048 576**; docs.z.ai: 1M de contexto, 128K de salida máxima (el "300 000" del README es el contexto usado en una evaluación HLE, no el límite) | cfg, docs.z.ai |
| MoE: expertos enrutados | `n_routed_experts` **288** por capa MoE | cfg |
| MoE: top-k | `num_experts_per_tok` **8** | cfg |
| MoE: expertos compartidos | `n_shared_experts` **1**, mismo tamaño (2048); sin tensor de puerta del compartido (solo gate/up/down) | cfg, idx |
| `moe_intermediate_size` | **2048**. Experto = gate/up [2048,4096] + down [4096,2048] = **25 165 824 parámetros** | cfg, idx |
| MLP denso | `intermediate_size` **12288**; `first_k_dense_replace` **3** (capas 0-2 densas; capas 3-44 = **42 capas MoE**; `mlp_layer_types`) | cfg |
| Router | `scoring_func` **sigmoid**, `topk_method` **noaux_tc** (`e_score_correction_bias` [288] F32), `n_group` 1, `topk_group` 1 (agrupación inactiva), `norm_topk_prob` **true**, `routed_scaling_factor` **2.5**, `moe_router_dtype` float32, `router_aux_loss_coef` 0.001; `mlp.gate.weight` [288,4096] BF16. Pesos finales = sigmoid(logits) + bias para elegir, luego normalizados y x2.5 | cfg, idx, tf |
| Activación experto | silu con **`swiglu_limit` 10.0**: `gate.clamp(max=10)`, `up.clamp(-10,10)` (para expertos; si aplica también a denso/compartido: no verificado) | cfg, tf |
| Hiperconexiones mHC | `mhc: true`, `hc_mult` **4** flujos, `hc_sinkhorn_iters` **20**, `hc_eps` 1e-6. Por capa: `hc_attn_fn` [24,16384] BF16, `hc_attn_base` [24], `hc_attn_scale` [3] y lo mismo para `ffn` (24 = 4 pre + 4 post + 16 de la matriz 4x4 doblemente estocástica). Antes de `lm_head`: media de los 4 flujos (`Glm5NextTextHyperHead`) | cfg, idx, tf |
| MTP | 1 capa (índice 45): `eh_proj` [4096,8192], `enorm`, `hnorm`, `shared_head.norm`; contiene **atención DSA completa + MoE de 288 expertos** (7.43 B parámetros, de ellos 7.25 B expertos). Comparte embedding y `lm_head`. transformers **no** la implementa | idx, [doc transformers](https://github.com/huggingface/transformers/blob/main/docs/source/en/model_doc/glm5_next.md) |
| Vocabulario | `vocab_size` **154 880** (BPE byte-level: 154 820 base + 36 tokens añadidos; 321 649 merges); pre-tokenizador regex estilo GPT-4/Qwen con `\p{N}{1,3}`; sin normalizador | cfg, tok |
| Embeddings atados | **No** (`tie_word_embeddings` false): `embed_tokens` [154880,4096] y `lm_head` [154880,4096] BF16 | cfg, idx |
| Normas | RMSNorm eps 1e-5; `input_layernorm`, `post_attention_layernorm`; `hidden_act` silu; `attention_bias` false (texto) | cfg |
| PLE / n-gram | **No tiene** (los únicos tensores fuera de capas son embed, norm final, lm_head y visión) | idx |
| Visión | ViT 24 capas, hidden 1024, 16 cabezas, MLP 4096, patch 14, imagen 448, `spatial_merge` 2, `temporal_patch` 2, `out_hidden` 4096, `projection_intermediate` 10240, bias en atención; mmproj GGUF ~1.13-1.16 GB | cfg, HF GGUF repos |
| Cuantización oficial | FP8 e4m3, bloques 128x128, activaciones dinámicas, solo en expertos, compartidos, q_a/q_b/kv_a/o_proj de MLA y MLP denso. **BF16**: toda KDA, indexador, `kv_b_proj`, router, mHC, embed/head, normas. `total_size` 328 326 771 576 bytes | cfg, idx |
| Preentrenamiento | 30 T tokens multimodales | README |
| Eficiencia declarada | "reduce la computación de atención y la KV cache 3.01x y 4.44x frente a GLM-5.3" (no pude reproducir el 4.44x; ver sección 9) | docs.z.ai |

### 3.2 Comparación directa con Qwen3.8-Flash-Next (config oficial de Qwen + docs de Strata)

| | Qwen3.8-Flash-Next | GLM-5.3-Flash | Factor |
|---|---|---|---|
| Parámetros / activos | ~125 B (según el encargo) / activos: no verificado | 321 B / 18 B | 2.6x total |
| Capas | 48 (36 GatedDeltaNet + 12 QSA) | 45 (34 KDA + 11 DSA) + MTP | |
| hidden | 2560 | 4096 | 1.6x |
| Expertos / top-k | 512 / 10, softmax | 288 / 8, sigmoid+bias, x2.5 | |
| `moe_intermediate` | 640 | 2048 | |
| Parámetros por experto | 4.92 M | **25.17 M** | **5.1x** |
| Expertos totales (capas principales) | 24 576 | 12 096 (+288 en MTP) | 0.49x |
| Atención "completa" | GQA 24/2 cabezas, head 256, rope parcial 0.25 (theta 1e7) | MLA absorbido, NoPE, 64 cabezas, latente 512 | |
| Indexador | `indexer_budget` 2048, compress 4, 4 cabezas | `index_topk` 2048, kpool 4, 32 cabezas | mismo concepto |
| Residual | `hc_count` 4 (lo que Strata llama "gated residual") | mHC 4 flujos + Sinkhorn x20 | misma familia |
| Extras | PLE/n-gram 28.8 GB en SSD, vocab 248 320 | sin PLE, vocab 154 880 | |
| Contexto | 262 144 | 1 048 576 | |
| MTP | 1 capa | 1 capa **con MoE de 288 expertos** | |

Fuente de la columna Qwen: [config.json de Qwen](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/raw/main/config.json)
y `docs/paper/Strata-Paper.pdf` (Tabla 1). Que el "gated residual" de Strata sea exactamente el mismo operador que mHC de
GLM (con Sinkhorn de 20 iteraciones y matriz de mezcla 4x4): **no verificado**; solo se que la config de Qwen también
trae `hc_count: 4`.

---

## 4. Chat template / thinking / tool-calling / tokens de parada

Fuente: [chat_template.jinja](https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/chat_template.jinja),
[generation_config.json](https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/generation_config.json),
[tokenizer.json](https://huggingface.co/zai-org/GLM-5.3-Flash/resolve/main/tokenizer.json), README y docs.z.ai.

**Formato del prompt** (todo texto, sin BOS propiamente dicho; los marcadores van como texto/tokens especiales):

```
[gMASK]<sop>
<|system|>Reasoning Effort: Max                      <- siempre se emite (low|high|max; por defecto max)
<|system|>\n# Tools ... <tools>{json por herramienta}</tools> ...   <- solo si hay tools
<|user|>texto del usuario
<|assistant|><think>razonamiento</think>respuesta<tool_call>nombre<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>
<|observation|><tool_response>resultado 1</tool_response><tool_response>resultado 2</tool_response>
<|assistant|><think>        <- prompt de generación (add_generation_prompt)
```

- **Thinking siempre activo**: el prompt de generación termina en `<|assistant|><think>`; docs.z.ai: "thinking.type only
  supports enabled; thinking cannot be disabled". El esfuerzo se controla con `reasoning_effort` = `low | high | max`
  (cualquier otro valor => `max`). Los turnos anteriores del asistente sin razonamiento se renderizan `<think></think>`.
- **`clear_thinking`**: por defecto `false` en la plantilla; el README recomienda pasar `true` en chat (elimina el
  razonamiento de turnos previos al último mensaje de usuario).
- **Tool calling**: formato XML con **pares clave/valor**, NO el de Qwen:
  `<tool_call>NOMBRE<arg_key>K</arg_key><arg_value>V</arg_value>...</tool_call>`; los argumentos no-string se serializan
  con `tojson(ensure_ascii=False)`. Las herramientas van como una línea JSON por función dentro de `<tools></tools>`.
  Los resultados van tras **un** `<|observation|>` como una secuencia de `<tool_response>...</tool_response>` (el
  template reordena por `tool_call_id` si puede). Soporta `tool_reference` / `defer_loading`.
  El servidor actual de Strata (`serve/frontend.py`) parsea el formato de Qwen (`<function=NAME><parameter=P>`): hace
  falta otro parser para GLM. En vLLM los parsers se llaman `--tool-call-parser glm47 --reasoning-parser glm47`
  ([receta](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash)).
- **Tokens de parada** (`eos_token_id`): **154820 `<|endoftext|>`, 154827 `<|user|>`, 154829 `<|observation|>`**
  (`pad` = 154820). Una llamada a herramienta termina en `</tool_call>` y el modelo emite `<|observation|>` (parada).
- **IDs útiles**: `[gMASK]` 154822, `<sop>` 154824, `<|system|>` 154826, `<|assistant|>` 154828, `<think>` 154841,
  `</think>` 154842, `<tool_call>` 154843, `</tool_call>` 154844, `<tool_response>` 154845, `</tool_response>` 154846,
  `<arg_key>` 154847, `</arg_key>` 154848, `<arg_value>` 154849, `</arg_value>` 154850, `/nothink` 154851 (heredado,
  sin efecto en este template), `<|image|>` 154854, `<|video|>` 154855, `<|begin/end_of_image|>` 154830/154831.
- **Muestreo recomendado**: `temperature 1.0`, `top_p 0.95` (generation_config); tareas agénticas largas `top_p 1.0`;
  DeepSWE `temperature 0.95`, `top_p 1.0` ([Unsloth](https://unsloth.ai/docs/models/glm-5.3-flash)).
- **Cuidado con las plantillas dentro de GGUF**: Unsloth convirtió la notación `.0.` de Jinja a `[0]` porque muchos
  motores no la soportan (se ve en los metadatos del GGUF de Unsloth); la plantilla oficial usa `m.content.0.type`.

---

## 5. Disponibilidad y soporte

### 5.1 Pesos y cuantizaciones (tamaños leídos de la API de HF el 2026-10-03)

| Repo | Contenido | Tamaño |
|---|---|---|
| [zai-org/GLM-5.3-Flash](https://huggingface.co/zai-org/GLM-5.3-Flash) | FP8 oficial | 328 GB (306 GiB) |
| [bartowski/GLM-5.3-Flash-BF16-GGUF](https://huggingface.co/bartowski/GLM-5.3-Flash-BF16-GGUF) (subida en curso) | IQ1_S 74.9 GB, IQ1_M 83.0, IQ2_XXS 96.6, **IQ2_XS 101.4**, IQ2_S 107.5, IQ2_M 120.6, IQ3_M 166.6 | desde **74.9 GB** |
| [unsloth/GLM-5.3-Flash-GGUF](https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF) | UD-IQ1_S 93.1, IQ1_M 97.6, IQ2_XXS 101.8, Q2_K_XL 108.7, IQ3_XXS 120.4, Q3_K_XL 147.5, IQ4_XS 156.8, Q4_K_XL 199.7, Q5_K_XL 240.3, Q6_K_XL 291.8, Q8_0 341.0 (+ BF16 641.6) | 93-342 GB |
| [antirez/glm-5.3-flash-gguf](https://huggingface.co/antirez/glm-5.3-flash-gguf) | Q2 96.5 GB, Q4_K 190.9, FP8 327.2 (motor propio `ds4`) | 96.5 GB |
| [AesSedai/GLM-5.3-Flash-GGUF](https://huggingface.co/AesSedai/GLM-5.3-Flash-GGUF) | IQ2_S 113.6, IQ3_S 124.7, IQ4_XS 159.2, Q4_K_M 202.0, Q5_K_M 240.8 | 113.6 GB+ |
| [pfeifferj/GLM-5.3-Flash-GSQ-RCO-GGUF](https://huggingface.co/pfeifferj/GLM-5.3-Flash-GSQ-RCO-GGUF) (reproducción **comunitaria** de GSQ+RCO de ISTA-DASLab; sin MTP) | 3.0 bpw 117.5 GB, 3.5 bpw 137.1 GB | 117.5 GB |
| [neuralll/...3.0bit-Q4Kattn](https://huggingface.co/neuralll/GLM-5.3-Flash-GSQ-RCO-3.0bit-Q4Kattn-GGUF) | el anterior con atención/denso en Q4_K | 113.6 GB |
| [patrickbdevaney/GLM-5.3-Flash-REAP50-GGUF](https://huggingface.co/patrickbdevaney/GLM-5.3-Flash-REAP50-GGUF) | 50 % de expertos podados (144/288): IQ3_M 72.1, Q3_K_M 78.8, IQ4_XS 88.1, Q4_K_S 93.5, Q4_K_M 99.3 | 72.1 GB+ |
| [OpenMOSE/...REAP-250B-A18B](https://huggingface.co/OpenMOSE/GLM-5.3-Flash-REAP-250B-A18B) | 20.8 % podado (228/288), FP8 | 265 GB |
| [autotrust/...GGUF-DGX-Spark](https://huggingface.co/autotrust/GLM-5.3-Flash-GGUF-DGX-Spark) | 256/288 expertos, IQ2_XXS/Q2_K | 79.1 GiB |
| [Justvugg/...colibri-int4-g64](https://huggingface.co/Justvugg/GLM-5.3-Flash-colibri-int4-g64) | int4 para streaming desde disco (motor `colibri`) | 194.7 GB |
| [incoai/GLM-5.3-Flash-DFlash2](https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2) | modelo borrador de difusión por bloques (1.17 B) para SGLang; licencia **CC BY-NC-ND 4.0** (no comercial) | 2.34 GB |

Hay además cientos de variantes (NVFP4, AWQ, EXL3, MLX, "uncensored"). **Ninguna publicada llega a caber en 64 GB de
RAM con todos sus expertos**; ninguna REAP-50 publicada baja de 72 GB. No existe una versión GSQ-RCO oficial de
ISTA-DASLab para GLM (solo la reproducción comunitaria).

Calidad de las cuantizaciones (Unsloth, vs BF16, retención de top-1 / KLD medio):
IQ1_S 70.9 % / 0.67; IQ2_XXS 76.3 % / 0.45; Q2_K_XL 78.3 % / 0.38; IQ3_XXS 81.6 % / 0.28; IQ4_XS 88.2 % / 0.12;
Q4_K_XL 92.2 % / 0.05 ([guía](https://unsloth.ai/docs/models/glm-5.3-flash)). GSQ-RCO 3.0 bit (reproducción): PPL
+9.07 % y MMLU-Pro 60.00 % frente a 61.95 % del Q8_0 (-1.95 pp, p=0.0097; 2000 preguntas) ([card](https://huggingface.co/pfeifferj/GLM-5.3-Flash-GSQ-RCO-GGUF)).
**A 2 bits GLM-5.3-Flash pierde bastante; no hay ninguna cifra de calidad a 2 bits comparable con las que Strata
publica para Qwen.**

### 5.2 Motores

| Motor | Estado (2026-10-03) | Detalle / fuente |
|---|---|---|
| **llama.cpp** | **Fusionado** el 2026-09-30 06:20Z: PR [#27773](https://github.com/ggml-org/llama.cpp/pull/27773) (timkhronos), arquitectura **`glm5-next`**. Los PR competidores [#27754](https://github.com/ggml-org/llama.cpp/pull/27754) (Unsloth) y [#27752](https://github.com/ggml-org/llama.cpp/pull/27752) se cerraron el 2026-10-01 (estado de merge: no verificado, probablemente duplicados). Correcciones posteriores fusionadas: #29745 (carrera de datos en máscara del indexador), #29805 (límite de pools kpool). **MTP: PR [#27917](https://github.com/ggml-org/llama.cpp/pull/27917) aún abierto.** | KDA reutiliza la implementación de Kimi-K3, MLA "nope only", mHC reutiliza la de DeepSeek-V4, el indexador puntúa pools de 4 tokens; ~1 GB de tensores sensibles (indexador, mezcladores mHC, puertas KDA, rutas low-rank de MLA) se mantienen sin cuantizar; varias secuencias requieren `--kv-unified`; los GGUF de Unsloth anteriores usan la cadena de arquitectura `glm5next` y los demás `glm5-next` (compatibilidad cruzada: no verificado). El PR de Unsloth indicaba `NVIDIA_TF32_OVERRIDE=0` y `-fa off` por corrección numérica. |
| **ik_llama.cpp** | **Fusionado**: [#2376](https://github.com/ikawrakow/ik_llama.cpp/pull/2376) (2026-09-14) y MTP [#2548](https://github.com/ikawrakow/ik_llama.cpp/pull/2548) (2026-09-28). Fix aarch64 [#2503](https://github.com/ikawrakow/ik_llama.cpp/pull/2503). | |
| Fork `neurall/llama.cpp` | Caché de expertos en VRAM (se apoya en [#27861](https://github.com/ggml-org/llama.cpp/pull/27861), caché LRU de expertos) | [repo](https://github.com/neurall/llama.cpp) |
| **vLLM / SGLang / TokenSpeed / transformers** | Soportados (README). vLLM: `--speculative-config '{"method":"mtp","num_speculative_tokens":5}' --tool-call-parser glm47 --reasoning-parser glm47`, requiere FlashInfer >= 0.6.17 (sparse MLA NoPE), GPUs Hopper+ o MI355X, ~306 GiB de pesos FP8. | [receta vLLM](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash), [SGLang](https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3-Flash) (configurador JS; flags concretos no verificados) |
| **KTransformers** | Tutorial oficial; **solo FP8** (`--kt-method FP8`), "reservar al menos 350 GB de RAM", GPUs SM89/SM120 (RTX 40/50); 1 GPU = `--kt-num-gpu-experts 0`. Sin cifras de velocidad. No sirve para 32-64 GB ni para una 3060 (SM86), según un resumen automático del tutorial (verificar) | [tutorial](https://github.com/kvcache-ai/ktransformers/blob/main/doc/en/kt-kernel/GLM-5.3-Flash-Tutorial.md) |
| Streaming desde NVMe | `sqliteai/warp` (C, fp8, contenedor de 112 GB) y `colibri` | ver sección 7 |
| Unsloth Desktop | Usa su propio llama.cpp; MTP "hasta 3.3x a contexto largo" | guía Unsloth |

---

## 6. Cálculo de memoria para RTX 3060 12 GB (con aritmética)

Todo con las formas reales de tensores. Mi modelo reproduce el nº de parámetros del GGUF al 0.0001 % y el tamaño de los
GGUF reales (ver comprobaciones). bpw usados: Q2_0 2.25 (paper de Strata), **IQ2_XS 2.31**, **IQ3_XXS 3.06**,
**Q4_K_M 4.8**, IQ1_S 1.56 (los pedidos por el encargo + los de Strata).

### 6.1 Bytes de expertos

Experto = 3 x 4096 x 2048 = **25 165 824** parámetros. Por capa: x288 = 7.248 B; x42 capas = 304.4 B; capa MTP: +7.25 B.

| Cuantización | bpw | MB / experto | GB / capa (288) | GB 42 capas | GB + capa MTP | Lectura de expertos por token (42 x 8 = 336 activaciones) |
|---|---:|---:|---:|---:|---:|---:|
| IQ1_S | 1.56 | 4.91 | 1.41 | 59.4 | 60.8 | 1.65 GB |
| Q2_0 | 2.25 | 7.08 | 2.04 | 85.6 | 87.7 | 2.38 GB |
| **IQ2_XS** | 2.31 | **7.27** | **2.09** | **87.9** | **90.0** | **2.44 GB** |
| IQ3_XXS | 3.06 | 9.63 | 2.77 | 116.4 | 119.2 | 3.23 GB |
| Q4_K_M | 4.8 | 15.10 | 4.35 | 182.6 | 187.0 | 5.07 GB |
| FP8 (oficial) | 8 | 25.17 | 7.25 | 304.4 | 311.7 | 8.46 GB |

Comprobaciones con archivos reales: IQ2_XS = 90.0 GB expertos + ~10 GB no-expertos = ~100 GB vs **101.4 GB**
(bartowski IQ2_XS); Q2_0 = 87.7 + ~9 = ~96.7 GB vs **96.5 GB** (antirez Q2); colibri int4 g64: 25.17 M x 4.5/8 =
14.2 MB/experto y 42 x 8 x 14.2 MB = **4.8 GB por token**, idéntico a lo que publica su README.
Mi método aplicado a Qwen (48 x 10 x 4.92 M x 2.25/8) da 0.66 GB por token, igual que la Tabla 1 del paper de Strata.

**Comparación con Qwen3.8-Flash-Next (docs de Strata)**: expertos 34.0 GB (Q2_0) / 35.5 (IQ2_XS) / 43 (IQ3_XXS);
lectura por token 0.66 GB. GLM a ~2.3 bpw: **2.6x más bytes de expertos y 3.7x más lectura por token**.

### 6.2 Pesos "siempre en GPU" (no expertos)

Parámetros (formas reales): KDA 34 x 137.7 M = 4.683 B; DSA 11 x 124.9 M = 1.374 B (de ellos indexador 7.5 M c/u);
MLP denso 3 x 151 M = 0.453 B; expertos compartidos 42 x 25.2 M = 1.057 B; routers 0.050 B; mHC 0.035 B;
`lm_head` 0.634 B. **Total residente por token = 8.29 B**; + capa MTP no-experta 0.185 B; el embedding (0.634 B) puede
quedarse en RAM (solo se lee una fila por token); visión 0.56 B opcional.

| Cuantización de los no-expertos | GB (8.29 B) | Observación |
|---|---:|---|
| BF16 (como el checkpoint para KDA) | 16.6 | imposible en 12 GB |
| Q8_0 (8.5 bpw) | **8.8** | lo que usan los GGUF comunitarios (el fork neuralll dice ~8.3 GB de Q8_0 en el archivo; pasarlo a Q4_K ahorra 3.9 GB: 117.48 -> 113.59 GB) |
| Q6_K (6.56) | 6.8 | |
| **Q4_K (4.5)** | **4.7** | coste: ~+1 % de perplexidad según neuralll |

+ ~0.5 GB de tensores sensibles que llama.cpp deja sin cuantizar. **Qwen: ~3.5 GB** en total (paper). Es decir: en
GLM los pesos "siempre en GPU" son **1.4-2.6x** mayores y se leen enteros en cada token (a 360 GB/s con 80 % de eficiencia = 288 GB/s: 4.7 GB = 16 ms; 8.8 GB = 31 ms;
en Qwen la parte de GPU mide ~14.6 ms por ventana en una RTX 5070 de 672 GB/s leyendo ~3.5 GB, Tabla 5 del paper).

### 6.3 KV cache y estado

- **Latente MLA** (solo 11 capas DSA, f16): 11 x 512 x 2 B = **11.0 KiB/token** (12.0 KiB con la capa MTP).
- **Caché del indexador** (llama.cpp guarda clave|puerta por token, f16): 11 x 256 x 2 B = 5.5 KiB/token. **Total
  ~16.9 KB/token**: 8K = 0.14 GB; 32K = 0.55; 128K = 2.2; 262K = 4.4; 1M = 17.7 GB.
  Comprobación: colibri declara **33 KB/token** (1.1 GB a 32K), que es lo mismo en f32 (33.8 KB); el PR de llama.cpp
  menciona checkpoints de "~1.6 GB a 90K tokens" = 17.8 KB/token con el estado KDA.
- **Estado KDA** (constante): 34 capas x 64 cabezas x 128 x 128 x 4 B (fp32) = **142.6 MB** + conv ~10 MB.
- Si se cacheara K y V expandidos en vez del latente: 64 cabezas x (256+256) x 2 B = 64 KB/capa/token x 11 = 704 KB/token
  (262K = 184 GB): inviable; el latente absorbido es obligatorio.
- Qwen: ~13 KB/token (docs de Strata). **La KV no es el problema; los pesos sí.**

### 6.4 Cuántos expertos caben en la VRAM de una 3060 12 GB

Presupuesto (decimal): 12 GB nominales, ~11.0 GB utilizables tras contexto CUDA/escritorio (Strata en una 5070 de 12 GB:
3.5 de denso + 0.8 MTP + 0.3-3 KV + ~6 de caché de expertos). Resto de GLM: KV+estado ~0.4 GB (8-32K), buffers de
cómputo ~0.5 GB, residente no-experto como arriba.

| No-expertos en | Residente | VRAM libre para expertos | IQ2_XS (7.27 MB) | IQ3_XXS (9.63 MB) | Q4_K_M (15.1 MB) | % de 12 096 (IQ2_XS) |
|---|---:|---:|---:|---:|---:|---:|
| Q4_K | 5.3 GB | **4.8 GB** | **660** (~16 por capa) | 498 | 318 | **5.5 %** |
| Q6_K | 7.4 GB | 2.7 GB | 371 | 280 | 179 | 3.1 % |
| Q8_0 | 9.5 GB | 0.6 GB | 83 | 62 | 40 | 0.7 % |

**La premisa "~8-9 GB libres" no se cumple**: sólo quedan ~4.8 GB incluso con los no-expertos en Q4_K. (Con 8.5 GB
serían 1 169 expertos IQ2_XS = 9.7 %, aún la mitad que Qwen.) Qwen: ~4 500 de 24 576 expertos = **18 %** y 72 % de
aciertos medidos con 6 GB.

### 6.5 Fracción del trabajo de expertos que cae en la CPU

Sólo hay dos medidas de sesgo del enrutamiento de GLM, y se contradicen en parte:
- neurall: "~100 expertos más usados por capa (de 288, 35 %) cubren ~74 % de lo que los tokens eligen en chat real".
- OpenMOSE (2.1 M tokens chat+código): ninguno de los 12 096 expertos está sin usar, entropía por capa 0.92-0.99
  (casi uniforme), sólo 1.4 % reciben <10 % del tráfico medio: **"GLM-5.3-Flash no tiene expertos de sobra"**.

Ajustando una distribución tipo Zipf que dé 74 % con 100/288 (exponente s = 0.81), el top-16 cubriría ~40 %; con s = 0.5
(más plano, más cercano a OpenMOSE) ~20 %; uniforme 5.6 %. **Uso tasa de acierto en VRAM h = 0.15 / 0.30 / 0.40 (pesimista /
central / optimista) [estimación, sin medición real a este tamaño de caché]**. Entonces la CPU hace **60-85 % de las
lecturas de expertos** (central 70 %) frente a 28 % en Qwen (h = 0.72).

### 6.6 Techo de decode (todo con aritmética)

Modelo (calibrado, ver abajo): `t_token = max(t_CPU, t_GPU) + t_SSD + 6 ms` (CPU y GPU se solapan por capa, como en Strata).
- `t_GPU = (5.3 GB + h x 2.44 GB) / (360 x 0.8 GB/s)`; para h = 0.30: (5.3 + 0.73)/288 = **21 ms**.
- `t_CPU = (1-h) x 2.44 GB x (fracción en RAM) / BW_CPU`. BW_CPU: **45 GB/s** (premisa del encargo; techo de DDR4-3200
  de 2 canales) o **25 GB/s** (lo que Strata mide para los kernels i-quant en 6 núcleos: 20-24 GB/s, Tabla 5 del paper).
  h = 0.30: 1.71 GB / 45 = 38 ms o / 25 = 68 ms.
- Con RAM suficiente: 21 / 38 ms -> max = 38 + 6 = 44 ms -> **22.7 tok/s**; con 25 GB/s: 68 + 6 = 74 ms -> **13.4 tok/s**.
- Techo teórico sin VRAM alguna: 2.44 GB / 45 GB/s = 54 ms -> 18 tok/s.
- MTP: las lecturas de expertos **no se amortizan** (con ventana de 3 tokens: 288 x (1 - (1 - 8/288)^3) = 23.3 expertos
  distintos por capa de 24 posibles; en Qwen 29.4 de 30), sólo la parte densa de la GPU. ik_llama.cpp mide +20 %
  de media (+7 % relato, +27 % código; aceptación 61.5 %, n_max=3) en configuración híbrida; uso **x1.2**.
  Unsloth: n=2 es el óptimo; n=5 empeora.
- **SSD**: si los expertos no caben en RAM, los fallos van al NVMe a `BW_SSD` = 3.0 GB/s (PCIe 3) o 5.5 GB/s (PCIe 4,
  lecturas de ~7 MB). RAM útil = RAM - 10 GB (SO, apps, buffers). Con los expertos más calientes en RAM, la parte
  de lecturas que va al SSD: 64 GB -> 18-26 % de los fallos; 32 GB -> 48-62 %.

**Resultados [estimación]** (tok/s sin MTP; entre paréntesis x1.2 con MTP). IQ2_XS, no-expertos Q4_K, caché 660 expertos:

| Escenario (RTX 3060 12 GB) | Dónde viven los expertos | tok/s decode |
|---|---|---|
| Referencia: 128 GB de RAM, todo en RAM | RAM 100 % | **11-26** (13-31) |
| **64 GB RAM + NVMe PCIe 3** | 54 GB RAM + ~30 GB SSD | **4-8** (5-10) |
| **64 GB RAM + NVMe PCIe 4** | idem | **6-12** (7-15) |
| **32 GB RAM + NVMe PCIe 3** | 22 GB RAM + ~63 GB SSD | **2-4** (2.6-4.6) |
| **32 GB RAM + NVMe PCIe 4** | idem | **4-6** (4-8) |
| 64 GB, REAP-50 re-cuantizado a IQ2_XS (6 048 expertos, 44 GB, h = 0.25-0.40) | todo en RAM | **13-26** (15-31); calidad sin medir |
| 32 GB, cualquier variante | REAP-50 a IQ1_S = 29.7 GB de expertos: no cabe con SO | no viable |

**Calibración del modelo con datos medidos**: el fork neuralll en 2x RTX 3090 + Ryzen 7 3700X + 125 GB DDR4-3200 mide
11.9 tok/s sin caché (33 % del trabajo en GPU) y 22.4 con caché (74 %). Mi modelo (3.0 bit = 3.07 GB/token, CPU 25 GB/s,
+6 ms) da **11.3** y **26.4**: error de -5 % y +18 %. Contraste con otros dos puntos: `warp` (streaming NVMe, Mac M5 Pro 64 GB)
3.3-3.9 tok/s; Strata con Qwen UD-Q4_K_XL (77 GB de expertos, 40 GB en RAM, RTX 5070 12 GB) 7-8.5 tok/s medidos
(`docs/UNSLOTH_Q4.md` y `docs/MODELS.md` del repo).

**Contra Qwen3.8-Flash-Next** (medido, RTX 5070 12 GB + Ryzen 5 7600 + 64 GB DDR5): IQ2_XS **79 tok/s** decode, **2 090 tok/s**
prefill. GLM-5.3-Flash en una 3060 de 12 GB es **3-20 veces más lento** según la RAM.

**Prefill [estimación]**: en cada trozo de 8K tokens se usan todos los expertos; con ~88 GB de expertos por pasar por PCIe 4.0
x16 (~25 GB/s) -> 3.5 s por trozo si todo está en RAM = ~2 300 tok/s de techo (real, probablemente 500-1 000);
con 36 GB en SSD (64 GB de RAM): +12 s (PCIe 3) -> ~600 tok/s; con 66 GB en SSD (32 GB de RAM): ~370 tok/s.
Referencias medidas: fork neuralll 112-156 tok/s (12K tokens, 2x3090), RTX 4090 ~300 tok/s a 256K.

---

## 7. Línea base de otros motores (lo que el fork debe batir)

| Motor / configuración | Hardware | Cuantización | Decode | Prefill | Fuente |
|---|---|---|---|---|---|
| llama.cpp (PR #27773, expertos en CPU) | RTX 4090 24 GB + 128 GB DDR5, ctx 256K, ub 2048 | IQ3_S | ~9 tok/s, baja a ~6 hacia 128K | ~300 tok/s | [comentario en el PR](https://github.com/ggml-org/llama.cpp/pull/27773) (un solo usuario) |
| fork neurall con caché de expertos | 2x RTX 3090 (48 GB), Ryzen 7 3700X, 125 GB DDR4-3200 | GSQ-RCO 3.0 bit (106-117 GB) | 11.9 -> **22.4** (README); 12.5 -> 18.5 (corto), 13.8 -> **21.5** (Q4K-attn) (card HF) | 156 -> 112 (12K) | [repo](https://github.com/neurall/llama.cpp), [card](https://huggingface.co/neuralll/GLM-5.3-Flash-GSQ-RCO-3.0bit-Q4Kattn-GGUF) |
| ídem, 3.5 bit | idem | 137 GB | 6.9 -> 15.1 | | repo neurall |
| ik_llama.cpp híbrido (`-ot` + `--cpu-moe`) | 2 GPU + CPU 24 hilos | IQ4_XS (AesSedai) | 13.6 -> **17.4** con MTP (código +27 %, relato +7 %; aceptación 61.5 %) | | [PR #2548](https://github.com/ikawrakow/ik_llama.cpp/pull/2548) |
| ik_llama.cpp, MTP (versión previa) | EPYC 9224 24 hilos | Q4_K_M | 9.2 -> 13.2 (aceptación 67 %) | | [PR #2399](https://github.com/ikawrakow/ik_llama.cpp/pull/2399) |
| ik_llama.cpp solo CPU | Neoverse-N2 de 96 núcleos | UD-Q4_K_XL | tg128 19.4 | pp512 95.4 | [PR #2503](https://github.com/ikawrakow/ik_llama.cpp/pull/2503) |
| llama.cpp (Unsloth) | 1x B200 | UD-IQ1_S | 63 (4K: 59.5; 64K: 49.0); MTP n=2: 86.5 a 4K | pp512 1 122 | [guía Unsloth](https://unsloth.ai/docs/models/glm-5.3-flash) |
| `warp` (streaming desde NVMe) | MacBook Pro M5 Pro 64 GB (también 16 GB: ~3.06) | fp8 -> contenedor 112 GB | 3.3-3.9, acierto de caché de expertos 87.7 % | decodifica a velocidad de decode (minutos por prompt largo) | [repo](https://github.com/sqliteai/warp) |
| `colibri` | 25 GB de RAM, disco a ~200 MB/s | int4 g64 (194.7 GB) | ~20-44 **segundos** por token (suelo de 24 s) | | [card](https://huggingface.co/Justvugg/GLM-5.3-Flash-colibri-int4-g64) |
| agregador `llamaperf` (no verificado) | 4x RTX 3090 + 96 GB, Q3: 28 tok/s; 2x RTX 5090 + 96 GB, UD-Q4_K_XL: 24.5 tok/s (prefill 159); Strix Halo 128 GB EXL3: 26-30; M4 Pro 48 GB (oMLX): **1.8** tok/s | | | | [llamaperf](https://llamaperf.com/model/glm-5-3) |
| KTransformers | requiere >= 350 GB de RAM y FP8 | | sin cifras | | tutorial |

Fuentes de baja fiabilidad (SEO, sin mediciones propias): Atomic Chat afirma que con "menos de 64 GB no hay camino"
([enlace](https://atomic.chat/blog/guides/how-to-run-glm-5-3-flash-locally)); Unsloth pide 100 GB de RAM+VRAM para 1 bit,
115 GB para 2 bits y 128-150 GB para 3 bits.

**No hay ninguna medición publicada con una GPU de 12 o 16 GB** (revisé el PR #27773 y las cards). La línea base más
cercana al objetivo es el fork neurall (misma idea que Strata: caché de expertos en VRAM + CPU en paralelo) y los
datos de `warp`.

---

## 8. Implicaciones para el fork de Strata (solo lo que se desprende de la investigación)

1. **Viabilidad**: para el objetivo declarado (3060 12 GB + 32-64 GB DDR4) GLM-5.3-Flash completo da ~2-9 tok/s;
   el diseño de Strata (caché de expertos) rinde mucho menos porque el 5.5 % de los expertos en VRAM cubre ~15-40 %
   de las lecturas (Qwen: 72 %).
2. **Lo que es reutilizable**: indexador de pools (Strata: `qsa_*`; GLM: kpool 4 / top-k 2048), 4 flujos de residual
   (`gr.cu`, `fused_gr.cu`), MTP, caché de expertos adaptativa, streaming de expertos a la GPU en prefill,
   modo de expertos mapeados desde SSD (`UNSLOTH_Q4.md`).
3. **Lo nuevo**: KDA (puerta de decaimiento por canal, `gate_lower_bound` -5, conv1d de 4 en q/k/v, `o_norm` con gate;
   estado 64x128x128 por capa) en lugar de Gated DeltaNet; MLA absorbido sin RoPE (latente 512, 64 cabezas de 256);
   mHC con Sinkhorn x20; router sigmoid + bias + norm + x2.5 con top-8 de 288 (los kernels de Strata asumen top-10 de 512
   softmax); `swiglu_limit` 10; expertos 4096x2048 (7.3 MB a IQ2_XS contra 1.4 MB); capa MTP con MoE completo;
   tokenizer/template/parser de tools distintos; sin PLE (ahorra la tabla de 28.8 GB); contexto de 1M.
4. **Palancas**: (a) cuantizar los no-expertos a Q4_K (ahorra ~4 GB de VRAM = +550 expertos); (b) poda de expertos
   estilo "Coder" (RCO) sería la única forma de entrar en 32-48 GB, pero la evidencia de OpenMOSE (rutado casi
   uniforme, especialistas por dominio) indica riesgo alto de calidad; (c) KV/estado KDA a RAM no aportan (poca KV).

---

## 9. Fuentes

Oficiales / primarias:
- Modelo: https://huggingface.co/zai-org/GLM-5.3-Flash (README, `config.json`, `chat_template.jinja`, `generation_config.json`, `tokenizer.json`, `tokenizer_config.json`, `model.safetensors.index.json`; cabeceras safetensors por rango HTTP)
- API de HF: https://huggingface.co/api/models/zai-org/GLM-5.3-Flash ; lista de la organización https://huggingface.co/api/models?author=zai-org
- Docs Z.ai: https://docs.z.ai/guides/llm/glm-5.3-flash ; blog https://z.ai/blog/glm-5.3-flash (renderizado por JS: no legible)
- transformers: https://github.com/huggingface/transformers/blob/main/src/transformers/models/glm5_next/modeling_glm5_next.py y https://github.com/huggingface/transformers/blob/main/docs/source/en/model_doc/glm5_next.md
- Otros configs: https://huggingface.co/zai-org/GLM-4.7-Flash , https://huggingface.co/zai-org/GLM-5.3 , https://huggingface.co/zai-org/GLM-4.5-Air , https://huggingface.co/Qwen/Qwen3.8-Flash-Next
- vLLM: https://recipes.vllm.ai/zai-org/GLM-5.3-Flash ; SGLang: https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3-Flash
- KTransformers: https://github.com/kvcache-ai/ktransformers/blob/main/doc/en/kt-kernel/GLM-5.3-Flash-Tutorial.md

llama.cpp / ik_llama.cpp:
- https://github.com/ggml-org/llama.cpp/pull/27773 (fusionado 2026-09-30), /27754, /27752, /27917 (MTP, abierto), /27861 (caché LRU de expertos), /29745, /29805
- https://github.com/ikawrakow/ik_llama.cpp/pull/2376 , /2548 , /2399 , /2503
- https://github.com/neurall/llama.cpp (caché de expertos)

GGUF / cuantizaciones: https://huggingface.co/unsloth/GLM-5.3-Flash-GGUF , https://huggingface.co/bartowski/GLM-5.3-Flash-BF16-GGUF ,
https://huggingface.co/AesSedai/GLM-5.3-Flash-GGUF , https://huggingface.co/antirez/glm-5.3-flash-gguf ,
https://huggingface.co/pfeifferj/GLM-5.3-Flash-GSQ-RCO-GGUF , https://huggingface.co/neuralll/GLM-5.3-Flash-GSQ-RCO-3.0bit-Q4Kattn-GGUF ,
https://huggingface.co/patrickbdevaney/GLM-5.3-Flash-REAP50-GGUF , https://huggingface.co/OpenMOSE/GLM-5.3-Flash-REAP-250B-A18B ,
https://huggingface.co/autotrust/GLM-5.3-Flash-GGUF-DGX-Spark , https://huggingface.co/Justvugg/GLM-5.3-Flash-colibri-int4-g64 ,
https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2 , https://huggingface.co/DogContext/GLM-5.3-Flash-Uncensored-Q2-ds4

Guías y medios: https://unsloth.ai/docs/models/glm-5.3-flash , https://github.com/sqliteai/warp ,
https://llamaperf.com/model/glm-5-3 , https://www.scmp.com/tech/big-tech/article/3365433/zhipu-ai-shares-jump-viral-ox-alpha-model-revealed-glm-53-flash-chinese-chips ,
https://atomic.chat/blog/guides/how-to-run-glm-5-3-flash-locally , https://www.mindstudio.ai/blog/glm-5-3-flash-local-benchmarks

Strata (locales): `docs/paper/Strata-Paper.pdf` (Tabla 1, Fig. 3, Tabla 5), `docs/DETAILS.md`, `docs/MODELS.md`,
`docs/UNSLOTH_Q4.md`, `docs/HOW_IT_WORKS.md`, `serve/frontend.py`.

---

## 10. Lo no verificado

- **Contenido del blog de Z.ai** (z.ai/blog/glm-5.3-flash): la página es JS y WebFetch devolvió vacío.
- **Informe técnico específico de 5.3-Flash**: no encontré; el README cita el de GLM-5 (arXiv 2602.15763, anterior).
  Detalles de entrenamiento de KDA/mHC: no verificados; la arquitectura sale de config, formas de tensores, código de
  transformers y PR de llama.cpp.
- **Parámetros activos**: oficial 18 B; mi suma de formas da 16.74 B + 0.35 B (MTP) + 0.63 B (embedding) = 17.7 B (mi
  hipótesis de por qué difiere; no confirmada).
- **"3.01x / 4.44x" menos computación/KV** (docs.z.ai): no pude reproducir el 4.44x. Mi cuenta de latente: GLM-5.3 78 x
  (512+64) = 44 928 valores/token contra 11 x 512 = 5 632 de 5.3-Flash (~8x).
- **Alcance de `swiglu_limit`** a MLP densa / compartido / visión: no verificado.
- **Si el "gated residual" de Strata es el mismo operador que mHC** de GLM: no verificado.
- **Tasa de acierto de la caché de expertos de GLM** con ~16 expertos por capa: no hay medición. Uso h = 0.15/0.30/0.40
  extrapolando dos datos que se contradicen en parte (neurall: 74 % con 35 % de expertos; OpenMOSE: casi uniforme).
  Es la mayor fuente de incertidumbre de la sección 6 (rango de tok/s +-50 %).
- **Velocidad en RTX 3060 12 GB (o cualquier GPU de 12-16 GB)**: ninguna publicada; todas las cifras de la sección 6 son
  estimación mía, calibrada con un único punto medido (-5 % / +18 %).
- **Calidad de las variantes podadas REAP-50**: la propia card dice que la v3 "no ha sido evaluada de extremo a extremo";
  no hay una versión RCO oficial de ISTA-DASLab para GLM; calidad de 2.3 bpw GSQ-RCO en GLM: no medida (hay GSQ-RCO 3.0 bit
  comunitario con -1.95 pp en MMLU-Pro).
- **Estado de fusión de #27754 y #27752** en llama.cpp: la búsqueda de GitHub confirma #27773 con `merged_at`
  2026-09-30T06:20:33Z; los otros dos aparecen "closed" el 2026-10-01 y un resumen automático de #27754 dijo
  "merged"; no pude confirmarlo (GitHub bloquea curl directo y la API devuelve 403).
- **Compatibilidad cruzada de GGUF** entre la cadena `glm5next` (Unsloth) y `glm5-next` (mainline): no probada.
- **SGLang cookbook** (flags concretos), **requisitos exactos de KTransformers** (SM89/SM120, 350 GB): vienen de resúmenes
  automáticos de páginas; verificar antes de citarlos.
- **Cifras de agregadores** (`llamaperf`, "17.7 tok/s con Q2_K_XL" visto en un fragmento de búsqueda sin hardware):
  no verificadas.
- **Hilos de r/LocalLLaMA**: la búsqueda web no indexó ninguno con cifras de GLM-5.3-Flash en GPUs de consumo; no los
  he podido usar.
- **MTP en llama.cpp mainline**: PR #27917 sigue abierto, no hay medición oficial; la mejor es la de ik_llama.cpp
  (+20 %) y la de Unsloth (1.6x corto, 3.3x a contexto largo, solo en una B200 con todo en VRAM).
