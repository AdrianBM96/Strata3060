# 01 - Presupuesto de VRAM y caché de expertos para RTX 3060 12 GB / 8 GB

Auditor 01 de 7 · fork `Strata3060` (HEAD `99f3dbd`, engine 0.1.38) · auditoría de solo lectura, sin GPU ni nvcc.

Convenciones. MiB = 2^20 B. "slot" = un experto residente en la caché de VRAM. Las cifras llevan etiqueta:
**[medido]** = sale de un log/bench/doc del repo; **[calculado]** = salida de las fórmulas del código (aritmética
mostrada); **[derivado]** = despejado de datos medidos; **[estimación]** = razonamiento mío, sin medición propia.
Las rutas son relativas a `/home/user/Strata3060`.

---

## Resumen

1. **El reparto de VRAM está bien diseñado y es reproducible en papel.** Con las fórmulas del código (sesión, KV,
   pooled keys, GDN) más las cifras medidas del log del 5090 (arena 1,466 MiB, dense nativo 1,475 MiB, MTP 835 MiB, head 322 MiB)
   reproduzco los slots medidos en la RTX 5070 12 GB: Δ 1K→128K de Q2_0 = **634 MiB calculado vs 634 MiB medido**, 1K→262K = 863 vs
   858 MiB, IQ2_XS a 64K/128K = 3,975/3,894 slots calculados vs 3,977-4,005/3,896-3,923 medidos, IQ2_XS a 32K = 4,013 vs 4,029-4,057, y el
   caso 6 GB de #496 (276 MiB libres calculados vs 297 MiB reportados). Con ese modelo, **una 3060 12 GB (Windows, monitor en la GPU,
   IQ2_XS, ctx 32K, int8) obtiene ~4,060 slots** (~5.45 GiB de caché = 16.5% de los 24,576 pares); en Linux headless ~4,640; una
   **3060 8 GB obtiene ~1,080 slots (4.4%) en Windows / ~1,660 en Linux headless** (§1.2).
2. **Casi todo lo "grande" ya está resuelto**: embedding fuera de VRAM (mapeado en host), tensores nativos no duplicados
   (skip set), buffers del prompt path *prestados* de la cola de la caché (no permanentes), cuBLAS con workspace externo.
   No hay un solo hallazgo de >300 MiB que se pueda liberar **sin pérdida** en 12 GB. Lo que queda son pepitas de 90-220 MiB.
3. **Sensibilidad medida**: a ~4,100 slots (IQ2_XS/Q2_0) cada 1,000 slots valen **≈ +4% de decode** (anclaje: imágenes
   on = -1,000 slots → -4.0%/-4.2%, `docs/DETAILS.md:784-794`); en el tramo 1,600-3,900 slots **≈ +10%/1,000** (KV streaming
   262K, `DETAILS.md:69-72`); IQ3_XXS ≈ +15%/1,000 (`docs/paper` hallazgo 8). O sea **≈ 0.3-0.5% por cada 100 MiB en
   12 GB con IQ2_XS** (≈ 1% con IQ3_*/Coder o en 8 GB). Por eso los micro-ahorros valen poco cada uno (+0.4-0.8%) y
   solo la suma (~500-600 MiB) y la **política de adaptación** (0 MiB) mueven la aguja.
4. **Hallazgo de política más importante**: la adaptación (`adapt()`, `src/program/generate.cpp:4794`/`6459`) solo aprende de los
   tokens de *decode*; el routing del *prompt* (que el prompt path ya cuenta por (capa, experto) en `m.cnt` en la rama de agrupación en host,
   la de los packs IQ nativos, `src/prefill/prefill.cpp:2057-2062`) se descarta. Tras cada prompt largo el motor además **copia ~3,000 slots de vuelta
   a la caché con exactamente los mismos expertos** (`generate.cpp:5462-5474`): tráfico PCIe ya pagado y desaprovechado. Sembrar `usage` con el histograma del prompt
   es lossless y barato (§Propuestas P1).
5. **PCIe 3.0**: `apply_pending(!adapt_nowait())` (#463, `generate.cpp:5732`/`6590`) espera a que aterricen hasta 96 swaps
   (~138 MB con IQ2_XS) antes de cada ventana siguiente a un adapt; ~11 ms a 12.5 GB/s vs 5 ms a 26 GB/s. Hay un A/B ya
   cableado (`STRATA_ADAPT_NOWAIT=1`) para medir la cota superior en la 3060.
6. **Draft head duplicado**: `MtpDrafter::bind` copia las filas del subset de vocabulario (`gather_rows`,
   `src/core/mtp.cpp:443`) a un buffer propio de 138 MiB (IQ2_XS, subset CJK) hasta 178-213 MiB (heads Q5_K/Q6_K),
   que ya existen en el head principal (Q5_K: 178 MiB, Q6_K: 213 MiB; `setup.py:2753` cita hasta 348 MiB para IQ3_S). Un GEMV con indirección de filas lo elimina bit-exacto (P3).
7. **#496** solo toca tarjetas <8 GB (reserva 700→300 MiB si la caché no puede prestar 256 tokens + 128 slots,
   `generate.cpp:2876-2915`; setup lo activa bajo 7.5 GB, `setup.py:2772`). Una **3060 8 GB arranca con la config por defecto**
   (reserva 700, ctx 32K, subset CJK) con ~1,080 slots → viable pero lento: **~20-30 tok/s [estimación]**; con ajustes
   (ctx 16K, subset `en`, Linux headless, reserva ~450) se pasa a ~2,000 slots, **+15-25% [estimación]**.
8. **Lo no recomendado** (pierde calidad o ya medido como no neutral): KV q4_0 (-180 MiB @32K, PPL +8-12% en documentos,
   KL 2.6-4.2x int8), pooled keys FP16 (rompe el contrato bit-exact del indexer), recortar expertos MTP (baja la aceptación).

---

## 1. Hallazgos

### 1.1 Orden de asignación en el arranque (un GPU, pack nativo IQ, `--serve`)

El orden importa: lo que se asigna *después* de la caché sale de la reserva (700 MiB), por eso el motor carga primero
lo grande y dimensiona la caché con lo que queda (`generate.cpp:2817-2825`).

| # | Qué | MiB (IQ2_XS) | Dónde |
|---|---|---:|---|
| 0 | Contexto CUDA + módulos `CUDA_MODULE_LOADING=EAGER` (~30 MiB de código; fuerza que el kernel de MMQ no encuentre VRAM a mitad de prompt) | residual, ver 1.2 | `generate.cpp:1069-1078` |
| 1 | (si `--vision` en GPU) `strata-vision` arranca *antes* y ya ocupa ~1.2-1.4 GB; el motor dimensiona su caché alrededor | 1,200-1,400 | `setup.py:187-191`, `DETAILS.md:745`, `server.py:1078` |
| 2 | Arena de pesos densos (GR/hyper-connections BF16 ~1.26 GB, routers BF16, norms, PLE). `pool_bytes` se **compacta** con el skip set (302 tensores servidos nativos no se cargan) | 1,466 [medido] | `generate.cpp:1968-2014`, `weights.cpp:89-119,193-204`, `engine.log:9` |
| 3 | Proyecciones nativas GGUF: 36 GDN×3 + 12 QSA×4 + 48 shared-expert×3 = 300 matrices | 1,474.85 [medido] | `generate.cpp:2016-2024`, `engine.log:10` |
| 4 | Doorbell, PLE scratch, `d_parts`, tabla mrope (solo vision: ctx×12 B) | <10 | `generate.cpp:2048-2062,2119-2124,2226-2244` |
| 5 | **Sesión** (carve único): GDN 36 capas fp32, KV 12 capas QSA, pooled keys del indexer, RoPE, buffers | 130 (1K) → 570 (32K) [calculado] | `session.cpp:54-129`, `layer.cpp:495-546` |
| 6 | **MTP** (capa draft): 512 expertos Q2_0 675 + densos 111 + KV/buffers | 835 [medido] | `mtp.cpp:144-304`, `engine.log:13` |
| 7 | Arena de expertos en RAM (no VRAM) + token embedding mapeado en host (322 MiB) | 0 VRAM | `generate.cpp:1948-1967,1985,2699-2808` |
| 8 | **Head nativo** (IQ4_XS para el pack IQ2_XS: 248,320×2,560×4.25/8 = 337,715,200 B), cargado *antes* que la caché | 322 [medido] | `generate.cpp:2826-2837`, `engine.log:17` |
| 9 | `d_logits` (n_vocab×4 B) | 0.95 | `generate.cpp:2838-2843` |
| 10 | **Caché de expertos**: `slots = (free - 700 - prefill_mib - mtp_bind)/max_blob`, luego re-expresada en *bytes por par* (slots "sized"), un solo `cudaMalloc` + memset + re-lectura de `free` tras tocar las páginas | el resto | `generate.cpp:2846-2866,2934-2954,2978-3040`, `expert_cache.cpp:161-267` |
| 11 | *Después de la caché, pagado por la reserva*: buffers del R4 hit path (`moe_hit_grouped_scratch`, ~KB), verify arena T=6 (57.1 MiB), grafos de ventana (1..6 tokens, capturados al primer uso, **después** del check "free"), draft head (138 MiB, **reservado aparte** como `mtp_bind`=+143 MiB), cuBLAS handle | ~290 [derivado: 700 - 412 libres, `engine.log:33`] | `generate.cpp:3311-3356`, `verify.cpp:253-376`, `mtp.cpp:306-321,416-452` |
| 12 | Buffers del prompt path: **no se asignan**; se *prestan* de la cola de la caché (slots de menor ranking) y se rellenan al terminar el prompt. Chunk auto hasta 8192: 4.62 GiB = 3,421 slots en el log del 5090 | 0 permanente | `generate.cpp:3959-4024,4086-4290,5516-5565`, `engine.log:29-30` |
| 13 | Check final: "N MiB of VRAM free with everything loaded" (<256 → LOW) | - | `generate.cpp:4943-4964` |

Notas de la tabla. `pinned.cu` (`:258-297,313-359`) y `weights.cpp` solo tocan RAM del host salvo la arena densa; el registro por rebanadas en Windows
carga el segmento compartido WDDM y puede dejar a WDDM rechazando `cudaMalloc` posteriores (#243, `STRATA_ARENA_PIN_GIB`), un riesgo de arranque, no un consumidor de VRAM.
`layout.cpp` solo valida geometría (no reserva memoria). No hay `cublasCreate` con workspace propio en el prompt path: scratch y workspace (96 MiB) son parte del préstamo (`prefill.cpp:765-770`).

Reserva y headroom: `--vram-reserve-mib` por defecto **700** (`generate.cpp:341`), la misma que setup escribe con visión
(`setup.py:190-191`: el encoder ya está cargado, "no hace falta más"). Reglas laterales: tarjeta pequeña → baja a
300 (`:2876`, #496); AMD Linux con escritorio → recomienda 3072 (`setup.py:2795`); servidor: `min_free_vram_mib` para
convivir con juegos (`DETAILS.md:405`). **No existe una reserva específica NVIDIA/WDDM**: se confía en `cudaMemGetInfo`
al arrancar + los 700 + la re-lectura tras tocar las páginas (`generate.cpp:2978-3040`, que ya vio "free ~1 GB demasiado alto"
bajo WDDM). En HIP/Windows sí se descuenta el presupuesto DXGI del proceso (`src/core/device.cu:105-140`,
`docs/AMD_HIP.md:87-93`: 0.8-1.8 GiB por debajo del tamaño de la tarjeta); en CUDA/Windows no.

### 1.2 Presupuesto cuantificado y validación

**Modelo** (IQ2_XS, nativo, int8 KV, visión off, MiB):

```
caché = TOTAL - 700 (reserva) - 143 (draft head+logits) - 1,466 (arena) - 1,475 (dense nativo) - 322 (head)
        - 802 (MTP sin KV) - KV_MTP(ctx) - sesión(ctx) - residual
KV_MTP(ctx) = ~1 MiB a 1K, ~37 MiB a 32K, 33 MiB (ring) con streaming   [mtp.cpp:214-233]
residual = contexto CUDA + kernels + WDDM/escritorio/otras apps   [derivado de la 5070 con el slot de 1K: ~1,197 MiB]
```

(El MTP "835 MiB" del log del 5090 está medido a 131K con ring de ventana; a 1K es ~802.)

`sesión(ctx)` [calculado, `layer.cpp:531-546`, `kv_q8.hpp:22-27`]:
- GDN: 36 × (128·48·128 + 10,240·3) × 4 B = 117.7 MB = **112.2 MiB** (fijo; coincide con `plan.hpp`).
- KV int8: 12 capas × ctx × 1,056 B/celda (K+V: 2·2 kv-heads·256 B + escalas fp16 por 64) = **12.37 KiB/celda** (q4_0: 576 B; k8v4: 816 B; fp16: 2,048 B).
- pooled keys del indexer FP32: 12 × (ctx/4 + 2) × 128 × 4 B = **1,536 B/celda**; RoPE cos/sin: 256 B/celda (una vez); `cell_scores` 4 B/celda.
- buffers de capa (`gdn/moe/block/qsa_buffers`): ~5 MiB.

Tabla de calibración con la **5070 (12,227 MiB)**; solo la fila de 1K se usó para despejar el residual, las demás son predicciones:

| ctx (int8) | sesión MiB | slots IQ2_XS calculados | medido 5070 (`matrix.json` 0114 / 0126) |
|---:|---:|---:|---:|
| 1K | 131 | 4,359 (calibración) | 4,386 / 4,359 |
| 8K (fp16 en setup) | 332 | 4,205 | - |
| 16K | 343 | 4,191 | - |
| **32K** | **569** | **4,013** | 4,057 / 4,029 |
| 64K stream 32768 | 625 + 33 (ring MTP) | 3,975 | 4,005 / 3,977 |
| 128K stream | 737 + 33 | 3,894 | 3,923 / 3,896 |
| 262K stream | 961 + 33 | 3,731 | 3,565 (medido con imágenes on, no comparable, `DETAILS.md:41`) |

Validación independiente con Q2_0 (blob uniforme 1,382,400 B = 1.318 MiB, `plan.hpp`): 1K→128K calculado 601 + 33 (ring del
MTP, `mtp.cpp:214`) = **634 MiB**, medido (4,176-3,695)×1.318 = **634 MiB**; 1K→262K calculado 830 + 33 = **863**, medido **858 MiB**.
Para IQ2_XS el Δ 1K→32K es 475 MiB calculados (sesión 438 + KV del MTP 37) vs ~454 medidos (330 slots × 1.3745; ±4%, el slot medio de la cola del perfil no es el medio global).
Y #496: 6,144 - [1,197 + 1,466 + 1,475 + 322 + 802 + 37 + 569] = **276 MiB libres calculados** vs "0.29 GiB" (297 MiB) en el commit `e1ee248` (esa tarjeta es una laptop; otro modelo/ctx posible).

**Presupuesto por tarjeta** (IQ2_XS, ctx 32K int8, visión off, reserva 700, subset CJK):

| Componente | 3060 12 GB (12,288) | 3060 8 GB (8,192) |
|---|---:|---:|
| arena + dense nativo + head | 3,263 | 3,263 |
| MTP (802 + 37 de KV) + draft head | 839 + 143 | 839 + 143 |
| sesión @32K | 569 | 569 |
| reserva | 700 | 700 |
| residual Windows con monitor en la GPU [derivado 5070] | ~1,197 | ~1,197 |
| **caché** | **5,577 MiB ≈ 4,057 slots (16.5%)** | **1,481 MiB ≈ 1,077 slots (4.4%)** |
| residual Linux headless [estimación 300-450] | caché ~6,370 ≈ 4,640 slots | caché ~2,270 ≈ 1,660 slots |

Notas: la 3060 tiene 28 SM (la 5070 48): su contexto CUDA es algo menor, así que el residual puede ser un poco más bajo
[estimación]. **Medir con `STRATA_TRACE=1`** (`generate.cpp:840-847`: imprime MiB libres tras pesos/sesión, caché, hit path,
grafos, verifier+drafter) para sustituir el residual por el valor real de la 3060 en Windows y Linux. Un slot IQ2_XS medio = 1.3745 MiB
(23.44 GiB / 17,463 slots, `engine.log:19`); el `max_blob` es 1.51 MB, de ahí que los slots "sized" den +4.8% (`engine.log:18` 16,663 → :19 17,463).

Coder (32 GB de RAM → modo low-RAM resident): dense ~3.4 GB (`MULTI_GPU.md:90`) en vez de 3.08, ~2,600 slots en 12 GB
(`bench/results/2026-09-28-coder/README.md:12`), ~21% de los 12,288 pares.

### 1.3 KV: q8 vs q4, streaming, contexto

- int8 es el default por encima de 8K (setup usa fp16 hasta 8K, `setup.py:3314`): "indistinguible de fp16" (KL 0.009-0.014, `bench/results/2026-09-27-kv-q4/README.md`).
- q4_0 (576 B/celda): KL 2.6-4.2x el de int8, perplexity +8% (1K) / +12% (8K) en documentos, needles 5/5. **No es lossless**
  (-180 MiB @32K = +131 slots ≈ +0.5-0.8%). k8v4 (816 B): -90 MiB @32K; **sin KL publicado** (solo needles, `DETAILS.md:81-85`) y no hace streaming.
- Streaming (`--kv-resident 32768`): setup solo lo activa con ctx ≥ 64K (`setup.py:3585-3587`); el motor lo permite con
  cualquier ctx > resident y resident ≥ 20,480 (`layer.cpp:495-513,528`). Medido a 131K con 32K residentes (5090): 96-99.4% de
  lecturas de bloque aciertan en VRAM en generaciones normales y 77-87% en los recall checks con needle (80-146 MiB leídos de RAM por petición;
  `engine.log:39-91`; granulo 4,224 B/bloque, `generate.cpp:6014`).
  Con ctx 32K y `--kv-resident 20480` bajan 12×(32,768-20,480)×1,056 = **148 MiB** (=108 slots); ratio resident/ctx 62% (mejor que
  el 25% de 131K) → pocos fallos esperables. **No probado en el repo.**
- Costes por celda que **no** se streamean: pooled keys 1,536 B + RoPE 256 B (1,792 B/celda: 117 MiB por cada 64K; a 262K
  las pooled keys, 384 MiB, igualan la ventana KV de 396 MiB).

### 1.4 Prompt path: préstamo, anillo, chunk

- `Prefill::bytes_needed` (`prefill.cpp:1156-1191`): ≈ **430 KB/token** [calculado: 197 KB fuera de la región compartida
  + 234 KB la región máx(GDN 144, QSA 125, MoE 234)] → 3.5 GB a 8192, + GEMM scratch 64 MiB + workspace 32 MiB (`:566-567`,
  prestados, no `cublasCreate`) + anillo de streaming `ring_slots` × `max_blob` = **384 × 1.51 MB = 580 MB** (`:140-156`; 96 si
  <90% del arena está pinned; para IQ nativos el fused es opt-in → `fused_ring()` falso) + dq ring 20 MB + grupos MMQ ~23 MB.
  Total ≈ 4.0-4.6 GiB (log 5090: 4.62 GiB con la etapa KV a 131K).
- Auto-chunk: el mayor de {32768..256} tal que `k+128 ≤ slots` y `k ≤ 85-90%` de los slots (`generate.cpp:3999-4024`). En 12 GB
  → 8192 (~3,100-3,400 de ~4,000 slots, 76-84%). En **8 GB (~1,100 slots)**: 2048 no cabe con anillo 384 (1.6 GB = 1,070 slots > 85%) →
  **chunk 1024**; con anillo 96 sí cabría 2048 (880+143+145+8 MiB = 1.18 GB = 780 slots). Es una palanca para ≤8 GB (`STRATA_PREFILL_RING=96`).
- **Over-count** en `bytes_needed`: línea `prefill.cpp:1165` cuenta `f(T*D)` dos veces (R y `xn`); `carve` solo reserva `xn` con
  `STRATA_GR_UNFUSED=1` (`:787`) y `grs`+`inj` (2×T·HC, bytes_needed solo cuenta una). Sobreconteo = T×40,944 B = **320 MiB a 8192**, 160 a
  4096, 80 a 2048. No es VRAM permanente (solo tamaño del préstamo → ~230 slots más evictados durante el prompt), pero en 8 GB
  puede mover el chunk elegido.
- El préstamo no es gratis por petición: cada prompt evicta k slots y los vuelve a copiar (4 GB ≈ 0.16 s a 26 GB/s, **~0.33 s a 12.5 GB/s** [calculado]);
  se presta solo lo que el prompt necesita (`lend`, `generate.cpp:5516-5565`).

### 1.5 Cómo se llena y adapta la caché

1. **Perfil**: `data/expert-profile.bin` (48×512, 24,576 pares rankeados por frecuencia de routing; `tools/make_profile.py`).
   `--expert-cache auto` llena con el prefijo global de mayor frecuencia (`generate.cpp:3076-3118`, lectura `expert_cache.cpp:14-70`):
   el reparto por capa es el que resulta del ranking global (a S=4,100: 53-109 slots por capa, mediana 86; a S=1,100: 1-43,
   9 capas con <11 slots [calculado sobre el .bin]). Coder: `expert-profile-coder.bin` (mapeo del perfil base). **Sin eviction en el fill** ("PROFILE ... no eviction", `:3069`).
   Persistencia opt-in: `expert_profile_save` (#477, `expert_cache.cpp:72-141`, `DETAILS.md:417-426`): "residentes primero, luego heat, luego prior".
2. **Adaptación** (`generate.cpp:4794-4862` serve, `6459-6523` generate; cadencia `:5756`): cada `adapt_every=4` rondas
   (`:377`), `usage[capa][exp] += 1` por cada (token×k) enrutado (`expert_source.cpp:1886-1888`, cuenta hits *y* misses). Candidatos = no residentes con
   `usage ≥ 2`; víctima = residente de **la misma capa** con menor `usage`; swap si `cand ≥ vict + 1.5`; hasta `adapt_swaps=96` (`:400`) por ronda de adapt,
   ordenados por ganancia; `usage *= 0.7` tras cada adapt (`:378`) → semivida ≈ 1.9 rondas de adapt ≈ **8 ventanas (~24 tokens)**.
   Los swaps copian async en `adapt_stream` (evicta ya, admite al aterrizar: Finding 10 del paper, 91.7→94.4 tok/s).
3. **#463** (0.1.37): `apply_pending(wait=true)` por defecto antes de cada ventana (`generate.cpp:5732,6590`;
   `STRATA_ADAPT_NOWAIT=1` restaura la consulta no bloqueante, `:849-853`). Motivo: el resultado (GPU vs CPU redondean distinto) dependía del timing de la copia.
4. Invariantes: el reparto por capa **nunca cambia** (swap solo intra-capa); la adaptación no ve el prompt; `usage` persiste entre peticiones (solo decae en adapts de decode).

### 1.6 Hit rate → tokens/s (con los números del repo)

Ronda de decode (paper §5, Tabla 5, 5070, 4K): `round = GPU + CPU_experts + draft + otros`.

| Modelo | round ms | GPU | CPU experts | hit | ms por punto de fallo | tok/ronda |
|---|---:|---:|---:|---:|---:|---:|
| Q2_0 | 34.3 | 14.6 | 13.4 | 0.72 | 0.48 | 3.23 |
| IQ2_XS | 42.0 | 14.7 | 21.1 | 0.78 | 0.96 | 3.28 |
| IQ3_XXS | 49.4 | 15.3 | 27.1 | 0.71 | 0.93 | 3.24 |

- Curva estática (Fig. 3 del paper): ~0.50 con 4,500 slots, ~0.67 con 7,500, ~0.93 con 13,000; la adaptación sube a 0.72-0.78 en 12 GB (+22 puntos).
  Pendiente estática ≈ 5.7 puntos por 1,000 slots a 4,500. Medido hoy en 12 GB / 4K: 0.72-0.78; Coder 2,537 slots: 72% (`coder/README.md:29`); 5090 con 17,463 slots: 97.8-99.7% (`engine.log:38-91`).
- Comprobación del modelo: Q2_0 4K→262K: slots -2,876 → -16 puntos → +7.8 ms; ×(2.46/3.23 tok/ronda) → 94.6×(34.3/42.1)×0.762 = **58.7 tok/s vs 56.3 medido** (-4%).
- **Anclajes medidos de sensibilidad a slots**: visión on (-1,000 slots): -4.2% Q2_0, -4.0% IQ2_XS, -8% IQ3_XXS (`DETAILS.md:786-793`); KV streaming 262K (+2,283): +23% (`DETAILS.md:69-72`);
  IQ3_XXS slots por bytes (+883): +13% (`paper` hallazgo 8). → **IQ2_XS/Q2_0 @~4,100 slots: ~4%/1,000 (0.3-0.5% por 100 MiB)**; 1,600-3,900 slots: ~10%/1,000; IQ3/Coder: ~8-15%/1,000.
- Estimación 3060 12 GB, IQ2_XS, 4K [estimación, método del propio paper §8: GPU×(672/360)]: 27.4 + CPU 21-27 + draft 4 + otros 5 = 57-65 ms/ronda × 3.28 tok = **~50-57 tok/s** (vs 79 de la 5070). 8 GB (240 GB/s): GPU 41 ms + CPU ~90 ms (fallos ~65%, pcie_frac 0.34 en PCIe 3.0) → **~20-30 tok/s**.

### 1.7 ¿Es buena la política para una caché pequeña?

Hallazgos concretos. Ojo con la palabra "lossless": estos cambios no son bit-idénticos a la salida actual (cambian *qué* experto corre en GPU y cuál en CPU, y los dos redondean distinto), pero es exactamente el ruido que el repo ya midió y aceptó en `bench/results/2026-09-27-cache-parity` (top-1 igual 95-98%, perplexity igual dentro de 1 SE) y que el adapt actual ya introduce en cada petición; se validan con ese mismo método (KL teacher-forced) y con needles:

1. **Memoria muy corta y sin prior** (decay 0.7/adapt ≈ 24 tokens; umbral 2; ΔU 1.5). La víctima es "el residente con menos `usage` reciente",
   **sin consultar su rank de perfil**; al arrancar la sesión la mayoría de residentes tiene `usage == 0` exacto y `std::partial_sort`
   (no estable) los ordena de forma no especificada. Con 1,100 slots el swap de 96 cada 4 rondas es 8.7% de la caché por adapt (2.2% con 4,400): riesgo de **churn** sacando
   expertos de larga duración por otros vistos 2 veces. Mejora: score = `usage_fast + λ·usage_lenta(decay 0.97) + prior(rank)`; "núcleo protegido" (no evictar el top-P% del perfil salvo ganancia grande).
2. **No usa el prompt** (ver Resumen 4): sembrar `usage` con `m.cnt` acumulado y forzar un adapt con tope alto tras el prompt (reutiliza `resident_stage_swaps`, también válido en modo low-RAM resident, el de 32 GB).
   Evidencia de que hay transitorio que recuperar incluso con 71% de los pares en VRAM: en el 5090 la primera petición de 19 tokens acierta 90.8% (`engine.log:38`), los recall checks de 9-10 tokens tras un prompt
   nuevo 92.6-97.7% (`:75-91`), y las generaciones de 256 tokens 97.8-99.7% (`:43-72`). Con 12 GB (hit estacionario 0.72-0.78) el déficit inicial en puntos será mayor [estimación].
3. **Intra-capa**: el reparto entre capas queda fijo por el perfil global. Con coste de fallo lineal el ranking global por frecuencia es óptimo
   para el estático; la adaptación inter-capa solo ayudaría a conversaciones atípicas (medible offline con un trace).
4. **Coste por byte**: con CPU limitado por cómputo (i-quants, paper §5) el ahorro de un acierto es ~constante por experto pero el coste en VRAM es `blob_bytes(capa)` (IQ3_XXS 1.3-2.3 MB, paper hallazgo 8). Ranking por `freq/bytes` para IQ3_*/Coder (no para Q2_0, que está limitado por ancho de banda); en IQ2_XS la dispersión es pequeña (≤15%).
5. **Pinning de expertos MTP**: ya están **todos** residentes (512 × 1.32 MiB = 675 MiB), fuera de la caché (`mtp.cpp:193`). Sacar parte a CPU exigiría un hook de pool dentro del grafo del drafter.
6. **Prefetch con "router del draft"**: no aplica (el router del MTP no predice el routing de las 48 capas principales). El predictor útil es el del paper §7
   (router de la capa l+1 sobre la entrada de la capa l), ya implementado solo para la capa de ficheros (`generate.cpp:3297-3306`, `expert_source.cpp:1865`); llevarlo a PCIe/VRAM necesita un anillo de staging (VRAM) y en PCIe 3.0 rinde menos.

### 1.8 #496 y la 3060 8 GB

- **Qué hizo #496** (`e1ee248`, `4731a9b`; `generate.cpp:2866-2915`, `setup.py:2772-2830,3617-3621`, `INSTALL.md:210-213`): en una RTX 3060 Laptop 6 GB los pesos+KV+draft dejaban 0.29 GiB, la reserva (700) + draft head se lo comían y la caché
  daba 0 slots. Ahora (a) si `auto` da `< min_slots = ceil(bytes_needed(256)/blob)+128` y no se pasó reserva explícita, la reserva baja hasta `kSmallReserveMib=300` (la máxima que deja `min_slots`) y marca `reserve_adapted` (aviso "solo cabe justo" en vez de "LOW");
  (b) si sigue en 0 slots, el arranque se detiene diciendo cuántos MiB faltan y las opciones (ctx menor, `--kv q4_0`, `--draft-vocab en`, imágenes en CPU); (c) setup pone `--vram-reserve-mib 300` en tarjetas únicas <7.5 GB y solo imprime los tips.
- **3060 8 GB**: `SMALL_CARD_GB = 7.5` la excluye (una 8 GB figura como 7.99), así que usa la config estándar. Calculado: ~1,480 MiB para caché
  (~1,080 slots IQ2_XS), `min_slots` ≈ 310 → **no** se activa la rebaja de reserva. Con el anillo de 384 el chunk cae a **1024** (§1.4), el prompt path funciona pero lento. **Viable, ~20-30 tok/s [estimación]**.
  La pendiente es mayor aquí (la curva estática da ~13-20 puntos de hit por 1,000 slots en el tramo 0-2,000 → ≈ +1-1.5% decode por 100 MiB).
  Combo de ajustes (todo lossless): ctx 16K (+226 MiB), subset `en` (+85-110), Linux headless (+800), reserva 450 (+250), dedupe del draft head (+138) → ~2,000-2,500 slots **[calculado]**, **+15-25% decode [estimación]**.
- MTP en 8 GB cuesta 835+138 MiB ≈ 700 slots (64% de la caché); aun así debe quedarse: el servidor exige `--mtp/--spec` (`generate.cpp:4086-4090`) y en CPU-bound la especulación sigue compartiendo expertos entre tokens.

---

## 2. Propuestas priorizadas

Ganancia: MiB → slots (÷1.3745 para IQ2_XS) → % decode con la pendiente del §1.6. "Riesgo de calidad" = riesgo para el *texto*; la residencia GPU/CPU ya está aceptada
como ruido de redondeo (cache-parity: top-1 igual 95-98%, perplexity igual dentro de 1 SE).

| # | Propuesta | Ganancia estimada | Riesgo de calidad | Esfuerzo | Validación |
|---|---|---|---|---|---|
| P1 | **Sembrar `usage` con el routing del prompt** (acumular `m.cnt` por (capa, exp) en `Prefill`; tras el prompt, `usage += α·cnt` y un adapt forzado con tope ~500-1,000 swaps; opcional: re-rankear los slots prestados en el refill) | 0 MiB. **+2-8%** en respuestas de ~256 tokens tras un prompt largo (+3-6 puntos de hit en las primeras ~100 rondas × 0.5-1.0 ms/punto) [estimación]; ~0 en generaciones largas | ninguno al texto; mismo tipo de ruido que el adapt actual | M (host-only, ~100 líneas; el brazo fused de Q2_0 puede agrupar en device: revisar) | `--stats`/log `decode expert cache hit rate` por petición, A/B tok/s en 256 tokens tras prompts de 4K/32K; teacher-forced KL como en `2026-09-27-cache-parity` |
| P2 | **Medir y suavizar #463 en PCIe 3.0**: instrumentar tiempo en `cudaEventSynchronize(adapt_ev)`; si es relevante, aplicar los swaps en la ventana N+2 (calendario fijo → sigue determinista) y/o `adapt_swaps` según el ancho de banda medido por `probe_pcie_h2d_gbps` | 0 MiB. 0.5-1.5% (PCIe 4.0), **hasta ~5% (PCIe 3.0, si se llegan a 96 swaps)** [estimación: 138 MB/12.5 GB/s = 11 ms - 3 ms de commit+draft, cada 4 rondas de ~40 ms] | ninguno | A/B ya disponible: `STRATA_ADAPT_NOWAIT=1` vs defecto en 3060+PCIe3; `--adapt-swaps 24/48/96`; línea "adaptive tier ... ms/round" |
| P3 | **Eliminar la copia del draft head**: GEMV `native_mmvq` con `row_ids` (indirección de filas) sobre el head principal en vez de `gather_rows`→`dhead_` | **138 MiB** (IQ4_XS, 106,299 tokens) a **178-213 MiB** (heads Q5_K/Q6_K) → 100-155 slots → **+0.4-0.8%** (IQ2_XS), +0.8-1.5% (IQ3/Coder/8 GB) | ninguno (bit-idéntico: mismas filas, mismo kernel) | M (parámetro de kernel en `native_mmvq.cu`/`iq_kernels.cu` + HIP + test de paridad) | `native_head` parity; tokens idénticos con `--adapt-every 100000` (determinista); `mtp_bind` baja en el log de dimensionado |
| P4 | **KV streaming por debajo de 64K**: `--kv-resident 20480` con ctx 32K (setup hoy solo ≥64K) | **148 MiB** → 108 slots → **+0.4-0.8%** (12 GB); en 8 GB +1.5% | ninguno (misma KV, otra ubicación; `kv_stream_parity`) | S (flag + golden test de setup); probar ya editando `args` a mano | needle 32K (`tools/needle_bench.py --lengths 32k`), % hit de bloques del log "KV streaming", decode y prompt A/B |
| P5 | **Política de adaptación para caché pequeña**: tie-break por rank de perfil, contador lento (decay 0.97) + prior, núcleo protegido, `freq/bytes` para IQ3_*/Coder; refactor de la selección de swaps a una función pura | 0 MiB. **+1-3%** (12 GB), más en 8 GB [estimación: +0.5-2 puntos de hit] | ninguno | M (offline: simulador sobre traces `--dump-routing`; luego 30 líneas en `adapt()`) | simulador en Python con traces de 3-5 dominios (código, prosa, CJK); A/B hit-rate y tok/s |
| P6 | **Reserva menor en Linux headless** (450-550 en vez de 700) | 150-250 MiB → +110-180 slots → **+0.5-1.0%** | ninguno al texto; riesgo de stalls/paging si algo más usa la GPU | S (setup pregunta/flag) | línea "N MiB free with everything loaded" ≥256 (`generate.cpp:4950`); en el 5090 sobraron 412 con 700 |
| P7 | **Subset de draft vocab `en` por defecto para usuarios no CJK/cirílico** (hoy solo un *tip*, `setup.py:2757-2770`) | 85-110 MiB (+60-80 slots, +0.3-0.6%) + 1-2% medido en la latencia del head draft = **~+1.5-2.5%** en inglés/código; CJK -15-38% de velocidad | ninguno (el draft solo propone; el verificador decide) | S | tools/draft_vocab.py; tok/s en prompts en/ES/zh |
| P8 | **`expert_profile_save` activado por defecto** en configs de un solo usuario/GPU | 0 MiB; arranque en caliente (más hit en los primeros minutos) [no cuantificable con datos del repo] | ninguno; privacidad: el fichero es una huella de uso (queda en local, `DETAILS.md:425`) | S (golden test de setup) | A/B hit-rate en los primeros 3 prompts tras reinicio |
| P9 | **Hardware/OS**: monitor en la salida de la placa (hosts Intel con iGPU: i5-10400/12400; Ryzen 3600/5600 no tienen), Linux headless, cerrar apps con aceleración GPU | **+500-1,200 MiB** → +365-870 slots → **+1.5-6%** (el paper §7 ya lo recomienda) | ninguno | 0 (documentar) | `STRATA_TRACE=1` antes/después |
| P10 | **Solo ≤8 GB**: ctx por defecto 16K, anillo 96 (`STRATA_PREFILL_RING=96`, permite chunk 2048), reserva 450 | ctx 16K: 226 MiB (+165 slots); anillo: chunk 1024→2048 | ninguno | S (setup + env) | prompt tok/s 1K/4K/8K y decode con los tres cambios por separado |
| P11 | **Corregir el sobreconteo de `bytes_needed`** (cuenta `T·D` fp32 que `carve` no reserva sin `GR_UNFUSED`) | 0 MiB permanentes; -320 MiB de préstamo a 8192 (-230 slots evictados por prompt) | ninguno | XS | invariante `bytes_needed ≥ carve` con `Alloc.count_only`; `do not fit` no aparece |
| P12 | Cache elástica de KV (prestar slots de la cola según crece el contexto, reutilizando la maquinaria de préstamo) | 150-400 MiB típicos a 32K en sesiones cortas (+0.5-1.5%) | ninguno | **Alta** (pool KV por capa contiguo) | no recomendado ahora |

**Descartes** (con razón): KV q4_0 (no lossless, ver §1.3); k8v4 (sin KL, 90 MiB @32K, sin streaming); pooled keys FP16 (el indexer es bit-exact por contrato, `qsa.cu:29`, `native_qsa_score.cu`; ahorra solo 24 MiB @32K, 96 @128K);
recortar expertos MTP (675 MiB; -337 MiB = +245 slots ≈ +1%, pero baja `tokens/ronda` que vale 1.6-1.8x); compresión lossless de los 1.3 GB BF16 de GR tipo DFloat11 (-420 MiB pero añade decodificación a un GEMV ya limitado por latencia y por ancho de banda de la 3060);
cargar el encoder de visión bajo demanda (la caché no puede crecer después).

Suma realista para 12 GB (P3+P4+P6): ~440-610 MiB → +320-440 slots → **+1.3-3%** (P7 añade +1-2% de velocidad del head draft en inglés, sin más VRAM si ya se aplicó P3); con P1+P2+P5 (política) **+3-10%** [estimaciones].
Para 8 GB, la misma lista da +15-25%.

---

## 3. Cambios de bajo riesgo implementables sin GPU

1. **Instrumentación (cero cambio de comportamiento)**: (a) acumular y mostrar el tiempo bloqueado en `apply_pending(true)` y los swaps por adapt en la línea "adaptive tier" (`generate.cpp:6699`) y en el log de petición; (b) imprimir `Budget/CurrentUsage` DXGI también en CUDA/Windows (portar `device.cu:105-140` a un diagnóstico) para saber si hay demotion en la 3060 con monitor.
2. **`bytes_needed`** (`prefill.cpp:1165`): contar `T*D` solo si `gr_unfused()` y `2×T*HC`; test `bytes_needed(T) ≥ Σ carve` con `Alloc.count_only` (sin CUDA).
3. **Refactor puro de la selección de swaps** (`generate.cpp:4799-4819`/`6476-6494`, duplicada en dos sitios) a una función en `expert_cache.cpp` (como `rank_learned_profile`) con `prior_rank` para tie-break; test unitario en CPU con trazas sintéticas y un simulador `tools/sim_cache.py` que reproduzca el adapt sobre traces de `--dump-routing` (el trace se genera una vez en cualquier GPU y se comparte).
4. **Acumulador del routing del prompt**: `PrefillStats::route` (float 48×512) incrementado con `m.cnt` en `prefill.cpp:2057-2062`; `Prefill::route_counts()` expuesto; el sembrado de `usage` y el adapt forzado en `generate.cpp` tras `sp.run`. Todo código de host.
5. **setup.py (cubierto por `tools/test_setup_*.py`, sin GPU)**: `--kv-streaming on` aceptado con ctx ≥ 32K (envía `--kv-resident 20480`), `expert_profile_save` por defecto, tip/pregunta `--draft-vocab en` según locale, en <10 GB: ctx recomendado 16K y `STRATA_PREFILL_RING=96` solo como opción. Actualizar `test_setup_golden.json`.
6. **Documentación**: añadir a `docs/DETAILS.md` la tabla del §1.2 (con la validación contra `matrix.json`) y una calculadora `tools/vram_plan.py` que implemente las fórmulas de §1.2 (el viejo `strata-plan` de `src/plan/plan_main.cpp` usa una geometría anterior y no se usa en el arranque real).

---

## 4. Preguntas abiertas

1. **Residual real de la 3060** (contexto CUDA, módulos EAGER, grafos, escritorio): ejecutar con `STRATA_TRACE=1` en Windows con monitor en la GPU, Windows con monitor en iGPU y Linux headless. Mi ~1,197 MiB es el residual de una 5070 en Windows 10 despejado de `bench/results/2026-09-29-speed-0126/matrix.json` (1K) con el modelo del §1.2.
2. ¿Cuántos swaps por adapt se producen en régimen estable y cuánto bloquea #463 en PCIe 3.0? (`STRATA_TRACE_ADAPT=1` ya lo imprime.)
3. ¿`cudaMemGetInfo` en CUDA/WDDM incluye el presupuesto DXGI? En HIP sí hubo que corregirlo (decode 41→30 tok/s al pasarse); en NVIDIA el motor lo compensa solo con reserva + re-lectura tras tocar páginas.
4. ¿El brazo `fused` del prompt path (Q2_0 canónico) agrupa por experto en el host (`m.cnt`) o en el device? Determina si P1 es trivial para Q2_0 o solo para los packs nativos.
5. Pendiente de P4: tasa de fallos de KV con ventana 20K en ctx 32K (esperable alto porque 20K/32K = 62% de residencia frente a 25% en el caso medido [estimación], no medida) y su efecto en el prompt path (staging de la KV completa por capa/chunk).
6. ¿El ranking del perfil es válido para Q2_0, IQ2_XS e IQ3 (un solo `.bin`)? Un perfil por cuantización/dominio podría valer más que cualquier ajuste de política.
7. Tamaño medio de blob por capa para IQ3_XXS/IQ3_S/Coder (`native_experts.txt`) para cuantificar `freq/bytes`.
8. 8 GB: no hay una sola medición en el repo; las cifras de §1.6-1.8 son extrapolaciones del método del paper (§8). Hace falta un run real (3060 8 GB) con 16K y 32K, subset `en`/CJK y anillo 384/96.
9. Fragmentación: la caché es un único `cudaMalloc` grande; en WDDM/Linux la VRAM es virtual, así que no es un problema de fragmentación sino de *commit* de Windows (`DETAILS.md:862`, ya hay reintento al 75%); el paso de reducción (`shrink_to(…/4*3)`, `generate.cpp:3009`) es grueso: 25% de la caché por intento.
10. Política "Sysmem Fallback" del driver NVIDIA en Windows (controlador ≥ 536): con "Prefer No Sysmem Fallback" una sobresuscripción de VRAM fallaría en vez de degradarse arrastrándose a RAM del sistema (el modo de fallo que describen `generate.cpp:2978-2984` y #199). ¿Conviene recomendarlo con 12 GB y monitor en la GPU? Fuera del repo; no medido.
