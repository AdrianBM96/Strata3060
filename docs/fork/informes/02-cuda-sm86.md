# Informe 02 - Kernels CUDA y build en Ampere sm_86 (RTX 3060 12 GB)

Auditoría de solo lectura de `/home/user/Strata3060` (fork de Strata 0.1.38). Sin GPU ni nvcc: todo lo que sigue sale de
leer el código, los docs y `bench/results/`. Etiquetas usadas en todo el informe:

- **[DOC]** lo dice el repo (docs, README, paper, bench/results).
- **[COD]** lo leí en el código, sin ejecutarlo.
- **[EST]** estimación mía, con la aritmética a la vista. No es una medición.
- **[VERIF]** hay que comprobarlo con la GPU (`cuobjdump`, Nsight, el profiler del propio motor).

Citas `archivo:línea` relativas a la raíz del repo.

---

## Resumen

1. **Build.** sm_86 es ciudadano de primera: `setup.py:1988-1996` compila local con `-DCMAKE_CUDA_ARCHITECTURES=86`, que CMake
   expande a SASS sm_86 + PTX compute_86 (no es JIT) [COD]. El motor ya compilado declara sus arquitecturas en `BUILD.json`;
   el fixture de tests usa `[75, 86, 89, 120] + ptx` (`tools/test_setup_choices.py:33`), pero el asset real no está en el
   repo [VERIF: `cuobjdump --list-elf engine/strata`]. **Trampa:** un `cmake` a mano sin `-DCMAKE_CUDA_ARCHITECTURES` fuerza `120`
   (`CMakeLists.txt:78`) y el binario no tiene código para una 3060 (el motor lo detecta y aborta, `src/core/device.cu:213-224`).
2. **La 3060 tiene los mismos recursos por SM que la 5070** (sm_120: 100 KB smem, 1536 hilos, 64K regs, máx. 99 KB opt-in por
   bloque): las configuraciones de lanzamiento afinadas en la 5070 se trasladan 1:1 *por SM*. Lo que cambia: 28 SM (no 48),
   1,78 GHz (no 2,51), 360 GB/s (no 672), L2 de 3 MB (no 48) y **sin clusters/DSMEM** [COD/EST].
3. **Nada asume L2 grande para los pesos.** Es un flujo puro de streaming (pesos 10-1000x mayores que el L2; no hay
   `cudaAccessPolicyWindow` en todo el árbol). El L2 de 3 MB solo duele en dos sitios de prefill (re-lectura de claves del
   indexador por tile de 16 consultas, y K/V entre consultas vecinas) [COD].
4. **Dos kernels de decode solo existen para sm_90+ (cluster)** y en sm_86 caen a versiones de *1 CTA por consulta / fila*:
   top-k de selección QSA y argmax voraz. El repo documenta que en la 5070 los clusters dan +5 % a 4K y +18 % a 128K
   (`docs/DETAILS.md:22-29`) [DOC]. Es la mayor pérdida por arquitectura que encontré.
5. **Dónde está el cuello en una 3060 de 12 GB:** la ronda de decode es `P (GPU antes del doorbell) + máx(Q, C)`, con `C` el
   CPU que computa los expertos que faltan. Con 12 GB de VRAM `Q` (expertos en caché + shared expert) queda *oculto* bajo `C`
   (paper Tabla 5: CPU 13,4-27 ms vs GPU 14,6 ms). Por tanto **optimizar los GEMV de expertos en VRAM rinde ~0 en esta
   tarjeta**; lo que rinde es `P` (~2.230 kernels dependientes por ventana, GR 1,27 GB, mixers, selección/atención, router).
6. **Presupuesto de bytes por ronda ≈ 5,6 GB** (1,75 GB por token aceptado a 3,23 tok/ronda): roofline 15,7 ms a 360 GB/s frente a
   8,4 ms a 672. La ronda real de la 5070 (34,3 ms) está a ~24 % de roofline; la de la 3060 la estimo en 40-54 ms
   (**~60-81 tok/s** con Q2_0 a 4K frente a 94,6 medidos en la 5070) [EST, ±20 %].
7. **Propuestas con mejor relación ganancia/riesgo** (todas con salida idéntica bit a bit o verificable con las
   herramientas del repo): (a) medir primero con `STRATA_VERIFY_PROFILE` + `cuobjdump --dump-resource-usage`; (b) top-k y argmax
   multi-CTA sin clusters; (c) fijar/vigilar ocupación de kernels de "una ola" dimensionados para 48 SM
   (`gdn_rec_kh_kernel` puede caer *en silencio* al kernel lento con 28 SM); (d) MMVQ "exacto" con 2 filas por CTA;
   (e) ramas paralelas en el CUDA graph; (f) autotuning por (nº de SM, L2, ancho de banda) vía `cudaDeviceGetAttribute`.
8. **Validación sin tener la 3060:** `-DCMAKE_CUDA_ARCHITECTURES=86-virtual` + `STRATA_EMULATE_CC=86` en una 5070 ejecuta el
   *código* sm_86 (resultados válidos, velocidad no) (`include/strata/core/emulate.hpp:1-8`), y `gdn_rec_parity --bench` ya sabe
   reservar SM para emular tarjetas más pequeñas (`src/prefill/gdn_rec_parity.cu:621-700`).

---

## Hallazgos (archivo:línea)

### H1. Build y arquitecturas

| Tema | Evidencia | Consecuencia para la 3060 |
|---|---|---|
| Arquitectura por defecto | `CMakeLists.txt:77-79`: si no hay `CMAKE_CUDA_ARCHITECTURES`, `set(... 120 CACHE ... FORCE)` | Un build manual sin el flag produce un motor sin SASS para sm_86. Poner `86` por defecto en este fork. |
| Bucle de validación | `CMakeLists.txt:80-109`: acepta `native/all` (los salta), rechaza `<75` salvo `STRATA_EXPERIMENTAL_SM60`; avisa si sm_120 con CUDA<13 (`:105`) | sm_86 pasa limpio. |
| Comentario obsoleto | `CMakeLists.txt:7-9` dice "kernels need sm_80 or newer … refuses anything older"; el código real admite 75 (`:94`) | Solo documentación. |
| Build local de `setup.py` | `setup.py:1975-1996`: `archs = {arch de la GPU}` → `-DCMAKE_CUDA_ARCHITECTURES=86`; `-DSTRATA_BUILD_TESTS=OFF`; target `strata` | CMake con `86` (sin sufijo) = `-gencode arch=compute_86,code=[compute_86,sm_86]`: SASS nativo + PTX. 10-20 min [DOC `setup.py:1990`]. |
| Motor ya compilado | `setup.py:101-106,1728-1736`: descarga `strata-<os>-x64.zip`, comprueba `BUILD.json{archs, ptx}`; `engine_runs_on` (`:537-542`) acepta `arch in archs` o `ptx and arch>max(archs)` | La 3060 usa SASS nativo si 86 está en `archs`. El asset no está en el repo [VERIF]. |
| Docker | `Dockerfile:58,76`: por defecto `75;80;86;89;120`; `docs/INSTALL.md:98` | Para el fork: `86` solo (compila ~5x más rápido, binario menor). |
| Flags CUDA | Sin `-lineinfo`, sin `-Xptxas -v`, sin `--maxrregcount`. Release = `-O3 -DNDEBUG` de CMake. `--use_fast_math` solo en `native_*.cu` (`CMakeLists.txt:299-312`) y en todo el target `strata_mmq` (moe_mmq/moe_fused/moe_fused_iq + instancias MMQ de ggml, `:925`) | No hay forma cómoda de ver registros/ocupación (necesario para H6). Proponer opción de build de perfil. |
| Carga de módulos | `src/program/generate.cpp:1072-1077`: `CUDA_MODULE_LOADING=EAGER` (~30 MB de VRAM, comentario `:1069-1071`) | ~20 expertos menos en caché (30 MB / 1,38 MB). Irrelevante. |
| ¿SASS o JIT en runtime? | `src/core/device.cu:213-224` (`device_code_error` con `cudaFuncGetAttributes(poison_kernel)`); los kernels cluster comprueban `fa.binaryVersion>=90` (`qsa_select.cu:1008-1011`, `sampler.cu:958-959`) | Se puede añadir un log de `binaryVersion/ptxVersion` al arranque para confirmar "native SASS sm_86" (sin coste). |
| Tests | `CMakeLists.txt:53-57`: `STRATA_BUILD_TESTS` solo ON por defecto si existen `tests/CMakeLists.txt` y `bench/micro` (aquí **no existen**); los `*_parity` de `src/kernels` se declaran sin esa guarda (`:497-704`) | `cmake --build --target <x>_parity` + `ctest` funcionan; `prefill_fused_*_test` requieren `-DSTRATA_BUILD_TESTS=ON` (`:938-949`). Los `native_*_parity` necesitan un llama.cpp de oráculo (`:323`) y fuentes `bench/micro/*` que no están. |

### H2. Puertas por arquitectura (qué hace una sm_86 distinto de la 5070)

| Característica | Dónde | sm_86 | 5070 (sm_120) |
|---|---|---|---|
| Top-k de selección QSA en cluster (CL_N=8) | `qsa_select.cu:673-868, 989-1053, 1062-1070`; env `STRATA_QSA_CLUSTER` | **No** (`cc_major>=9` y `binaryVersion>=90`) → `block_topk_reg_kernel` (1.024 hilos, 1 CTA/consulta, ≤33.792 bloques) o `block_topk_kernel` (256 hilos) | Sí |
| Argmax voraz en cluster | `sampler.cu:171-259, 927-974, 985-998`; env `STRATA_ARGMAX_MULTI` | **No** → `sampler_greedy_kernel` 1 CTA de 1.024 hilos por fila de 248.320 logits (`:102-169`) | Sí (40 → 6 µs/llamada [DOC `sampler.cu:174-176`]) |
| Lectura GR "staged" con `cp.async` | `fused_gr.cu:569-594, 619-688, 1059-1091` (se elige tras comprobación bit a bit en la tarjeta) | Sí (sm_80+) | Sí |
| `gdn_rec_kh_kernel` (prefill) | `prefill/kernels.cu:436-594`; `gdn_keyhead_ok` exige `major>=8` **y** `per_sm*SMs >= 64` | Depende de los registros (ver H6-R3) | Sí |
| Prefill Q2_0 en int8 TC (`mma.sync m16n8k32.s8`, `cp.async` 4 etapas) | `prefill/moe_fused.cu:169-198, 360-411`; `cc>=80` y ocupación ≥1 | **Sí, por defecto** ("RTX 30 y nuevas", `docs/DETAILS.md:22`) | Sí |
| Prefill i-quants en int8 TC | `prefill/moe_fused_iq.cu:534-592`; opt-in `STRATA_PF_FUSED=1` | Disponible, **off** por defecto; MMQ de llama.cpp en su lugar | Igual |
| MMQ (llama.cpp) con hechos de dispositivo | `prefill/ggml_cuda_host.cu:87-103`: `cc, smpbo, nsm, smpb, warp_size` por `cudaDeviceGetAttribute` (#542) | `cc=860`, `smpbo=101376`, `nsm=28` | `cc=1200`, `nsm=48` |
| Prompt attention en TC | `qsa_prompt_attn.cu:1022-1085` (`cc>=75`; `cc>=80` → `launch_i8` con cp.async) | `launch_i8` | Igual |
| Scorer de bloques 3xTF32 (prefill) | `qsa_select.cu:195-281, 896-957` (`cc>=8`) | Sí | Sí |
| `dp4a` / `__nanosleep` | `include/strata/kernels/dp4a.hpp:23-41` | nativo | nativo |
| Emulación de CC (tests) | `include/strata/core/emulate.hpp` (`STRATA_EMULATE_CC=75|80|86|89`) | - | Sirve para ejercitar el código sm_86 en una 5070 |

### H3. Hechos de hardware que importan (aritmética)

| Magnitud | RTX 3060 12 GB | RTX 5070 | Ratio 3060/5070 |
|---|---|---|---|
| SM | 28 | 48 | 0,58 |
| Reloj boost | ~1,78 GHz | ~2,51 GHz | 0,71 |
| SM x GHz | 49,8 | 120,5 | **0,41** |
| Ancho de banda | 360 GB/s | 672 GB/s | 0,54 |
| BW por SM | 12,9 GB/s | 14,0 GB/s | 0,92 |
| BW por SM-ciclo | 7,2 B | 5,6 B | 1,29 |
| L2 | 3 MB | 48 MB | 0,06 |
| smem/SM, hilos/SM, regs/SM | 100 KB, 1536, 64K | iguales | 1 |
| Clusters / TMA / PDL | no | sí (sm_90+ para clusters/PDL) | - |

Lecturas: (i) la 3060 es "una rebanada 28/48" de la 5070 con reloj 0,71x; (ii) los kernels limitados por latencia/por número de
olas escalan con **SM x GHz (x2,4)**, los limitados por ancho de banda con **x1,87**, los de un solo CTA con **el reloj (x1,41)**;
(iii) hay *más* bytes por SM-ciclo: los kernels con mucha ALU entera por byte (dp4a, tablas de i-quants) se acercan antes a ser
ALU-bound. [VERIF] la tabla de throughput de la CUDA Programming Guide para cc 8.6 (INT32/dp4a ~64/ciclo/SM; el whitepaper de
Blackwell habla de INT32 unificado a 128/ciclo/SM): si es así, la 3060 tiene ~2,6x menos ALU entera por byte de DRAM.

### H4. Inventario de kernels de DECODE (ventana de verificación, T≈3-4, capturada en un CUDA graph)

Ruta completa: `Verifier::record_window` (`src/core/verify.cpp:390-865`): por capa `pre()` (`:460-725`) y `post()` (`:728-812`).
Instrumentación existente: `STRATA_VERIFY_PROFILE=1` (`verify.cpp:334`, imprime "decode GPU stages (ms/window)" con `--stats`,
`generate.cpp:5826`) y `STRATA_VERIFY_NODES=1` (`verify.cpp:913-940`, lista los nodos del graph).

| Kernel (archivo:línea) | Lanzamiento en sm_86 | ¿Supone L2 grande? | Ocupación con 28 SM |
|---|---|---|---|
| **Lectura GR** `fused_gr_read_multi` (`fused_gr.cu:762-800`): `gr_norm_split` + `gr_down_staged` + `gr_up_multi`; 2 lecturas/capa (96/ventana) | norm (T,4)x256; down **41x256**, smem dinámica `T·10.240` B (T=4: 40 KB; T=8: 80 KB); up **160x256**, smem estática 12,3 KB | No. `xn` (T·40 KB) se re-estagea por CTA desde L2 (160 KB, cabe) | down: 2 CTA/SM con T≤4 (1 con T=8); 41 CTA en 28 SM = 13 SM con 2 y 15 con 1, pero ~820 KB en vuelo >> 216 KB (BDP) → sigue limitado por DRAM. up: 160 ≤ 28x6 = 168 → 1 ola |
| **GEMV densos** `native_mmvq` multi-columna "exacto" (`native_mmvq.cu:999-1062`) para qkv/gate/out/q/k/v/o/shared/head | grid = `n_out` CTAs (ROWS=1) o `n_out/4` (K pequeño), bloque (32,4)=128 hilos, `__launch_bounds__(128,1)`; activación Q8_1 (T·2560/32·36 B) | No | 12 CTA/SM (hilos) → 336 resident. qkv: 10.240 CTA = ~30 olas; **head: 248.320 CTA = ~739 olas** (`sampler.cu:92`) |
| **BF16 mmvf** (router, indexer q/k) (`native_bf16.cu:120-170`) | grid `n_out` x 256 hilos (`mmvf_block_size(2560)=256`) | No | router 512 CTA vs 168 slots = 3 olas |
| `gdn_conv_l2_multi` / `gdn_ab_multi` (`verify_kernels.cu:25,70,390,407`) | conv (80,T)x128; **ab: 12 CTA x256** (lee 0,49 MB) | No | ab usa 12 de 28 SM |
| **`gdn_step_norm_multi`** (`verify_kernels.cu:115-188,420`) | **48 CTA x 512 hilos**, estado 3,1 MB/capa en registros (`s[32]`), T tokens en serie, 5 `__syncthreads`/token | No (estado 113 MB/ventana, >> L2) | **48 CTA = justo 48 SM de la 5070.** En 28 SM: 1 ola solo si caben 2 CTA/SM (≤64 regs/hilo); si no, 2 olas [VERIF regs] |
| QSA: `kv_append` y `native_qsa_indexer_append` **por token** (`verify.cpp:587-608`; `native_qsa_indexer.cu:245-286` `<<<1,THREADS>>>`) | T lanzamientos de 1 CTA cada uno x2 por capa QSA | No | latencia pura |
| `qsa_block_scores` decode (`qsa_select.cu:606-646,880-886`) | `block_scores_multi_kernel<<<256,256>>>`, smem 16 KB (5 CTA/SM) | Lee 512 B por bloque de clave: 4,2 MB (32K) a 33 MB (262K) por capa | 256 vs 140 slots = 1,8 olas |
| **Top-k QSA** (`qsa_select.cu:1055-1109`) | sm_86: `<<<nq,1024>>>` (reg, smem 33 KB) o `<<<nq,256>>>`; **1 CTA = 1 SM por consulta** | No | nq=3-4 CTA en 28 SM |
| `qsa_decode_attn_batch` (`qsa_decode_attn.cu:83-249`) | `attn_chunk_kernel<<<(n_chunks=33, 2, nq),256>>>` smem 15,5 KB + `attn_merge<<<(24,nq),256>>>`; cap=2051 celdas, CHUNK=64 | Parciales `n_chunks·24·258·4 B` = 0,8 MB/consulta (cabe) | 264 CTA vs 168 = 1,6 olas |
| Router top-10 (`native_router.cu:115-123`) | `route<<<n_tok,(32,8)>>>` | No | 1 CTA/token |
| Doorbell y esperas (`elementwise.cu:309`; `verify_kernels.cu:426-430,474-478`) | `doorbell_publish<<<1,1024>>>`, `wait_flag_ge<<<1,1>>>` con `__nanosleep(100)` (3 esperas/capa) | No | latencia PCIe (~µs) |
| Shared expert multi (`shared_expert.cu:161-197`) | 9 kernels seguidos (quantize, 2 mmvq, swiglu, quantize, mmvq, gate, sigmoid, scale) | No | tras el doorbell: oculto bajo C |
| Expertos agrupados canónico/Q2_0 (`s2_expert_grouped.cu:918-1041,1099-1136`) | `gu_grouped_t <<<(40, cap_groups),256>>>`, smem 25,6 KB; `down_grouped_t <<<(40,cap),256>>>` | No | ≤3 CTA/SM por smem (≤2 por registros [VERIF]); los CTA con `g>=*n_groups` salen al instante |
| Expertos agrupados nativos/i-quants (`iq_kernels.cu:895-1018,1541-1581`) | gu `<<<(160,gy),256>>>`, down `<<<(320,gy),256>>>`, `gy=cap_groups` en la llamada VRAM (`:1555`) y `kPcieGroupRows` en la de PCIe | No | ~19.200 CTA/capa, la mitad vacíos |
| Combine / hit_add / copias (`native_moe.cu:81`; `s2_expert_grouped.cu:1138`; `elementwise.cu:278`) | grids pequeños | No | latencia |
| Head + muestreo (`verify.cpp:830-862`; `sampler.cu:102-169,985-998`) | mmvq head (arriba) + `sampler_greedy_kernel<<<T,1024>>>` | No | T CTA = T SM |

Recuento de nodos por ventana [EST, a confirmar con `STRATA_VERIFY_NODES`]: capa GDN ~41 kernels, capa QSA ~63 (T=4)
→ 36·41 + 12·63 = 2.232, más ~25 de cabecera/head ≈ **2.260 nodos/ventana**. El comentario de `fused_gr.cu:503-507`
confirma el patrón (96 lanzamientos de la norma GR por ronda). Con ~2 µs de hueco por nodo dependiente en un graph de Ampere
[EST] son ~4,5 ms/ventana, ~10 % de la ronda. Sin PDL (sm_90+) solo se reduce fusionando.

### H5. Inventario de kernels de PREFILL

| Etapa | Ruta en sm_86 | Lanzamiento | L2 / ocupación |
|---|---|---|---|
| GEMM densos | cuBLAS `GemmEx` con `CUBLAS_DEFAULT_MATH` (`prefill/gemm.cu:320,388-411`) | heurística de cuBLAS por dispositivo | no aplica |
| Expertos Q2_0 (pack canónico) | `fused::experts` int8 TC (`moe_fused.cu:443-457`), **por defecto** | grid persistente `SMs x occ` = **28 CTA x 512 hilos**; smem dinámica 57.600 B (gate/up) y 42.240 B (down) → 1 CTA/SM; `STAGES=4` | trabajo estático por paso `gridDim`; con 28 CTA cada uno hace ~1,7x más ítems que en la 5070 |
| Expertos i-quants | MMQ de llama.cpp (por defecto) o `fused::experts_native` (opt-in `STRATA_PF_FUSED=1`, `moe_fused_iq.cu:586-628`) | persistente `SMs x occ`; tile 64 vs 128 filas por `pick_ww` (`:573-582`, **umbrales medidos en la 5070**) | 1 CTA/SM por smem (66-78 KB) |
| Selección QSA | scores TC 3xTF32 (`qsa_select.cu:195-281,947-951`) + top-k por regla de capacidad (`:1076-1106`; el límite activo solo para Turing, `:972-986`) | scores: grid `(reach/128, nq/16)` x128, smem 49,9 KB (2 CTA/SM) | **re-lee las claves (512 B/bloque) una vez por tile de 16 consultas; con 3 MB de L2 no hay reuso** (H6-R6) |
| Atención de prompt | `launch_i8` (`qsa_prompt_attn.cu:678-714,1022-1085`) | grid `(nq, 2)` x128, smem ~45,6 KB (2 CTA/SM) | K/V de ~1,05 MB por (consulta, cabeza); reuso entre consultas vecinas sin garantía con 3 MB |
| GDN conv/L2/recurrencia | `gdn_recurrence` (`prefill/kernels.cu:907-926`): `gdn_rec_kh_kernel` **64 CTA x128** si `gdn_keyhead_ok()`, si no `gdn_rec_cols_pipe_kernel` 192 CTA x128; cada CTA recorre todo el chunk en serie | latencia/reloj | 64 CTA / 28 SM exige 3 CTA/SM (≤168 regs/hilo) |
| Copias de expertos por kernel | `fetch_blobs`/`gather_rows` `<<<48*8,256>>>` (`verify_kernels.cu:311,342-346`) | 384 CTA (grid-stride) | cifra fijada para 48 SM; inocua |

### H6. Supuestos de "5070" que no se trasladan (riesgos concretos)

- **R1. Arquitectura por defecto 120** (`CMakeLists.txt:78`). Corrección trivial.
- **R2. `gdn_step_norm_multi_kernel`: 48 CTA** (uno por SM de la 5070). Si usa >64 registros por hilo (el kernel tiene
  `__launch_bounds__(512)` sin `minBlocks`, `verify_kernels.cu:115`), solo cabe 1 CTA/SM y la 3060 lo ejecuta en 2 olas:
  ~+25 µs x 36 capas = **+0,9 ms/ventana (~2 %)** [EST, VERIF regs].
- **R3. `gdn_keyhead_ok()` puede devolver falso en silencio** (`prefill/kernels.cu:578-594`). Necesita 3 CTA/SM (28x3=84≥64) y con
  2 CTA/SM (56<64) cae al kernel lento. El kernel guarda `s[3][32]` + `kc[32]` en registros (~150-175 regs [EST]); el umbral
  para 3 CTA/SM es **≤168 regs/hilo** (128 hilos x 168 = 21.504 regs/CTA, x3 = 64.512 ≤ 65.536). El propio autor midió 1,28-1,57x a
  32-38 SM y **no exploró <32 SM** (bandas de `gdn_rec_parity.cu:600,835`).
- **R4. Top-k y argmax sin cluster** (H2). Además el top-k decode elige kernel por **capacidad** (`--max-context`), no por el
  contexto real (`qsa_select.cu:1076-1106`, el grafo se captura una vez): con `--context` > 135K (>33.792 bloques) *toda* llamada
  de decode, incluso a 4K reales, usa `block_topk_kernel` (256 hilos, 4 pasadas + búsqueda de dígito en serie por un hilo).
- **R5. Umbrales de `pick_ww`** (56-112 filas/experto) medidos con 48 SM (`moe_fused_iq.cu:568-572`): con 28 SM cada CTA trata
  ~1,7x más ítems, así que el tile grande (128 filas) debería ganar antes [EST].
- **R6. Scorer TC de prefill y L2.** `blockIdx.x` recorre los rangos de claves y `blockIdx.y` los tiles de 16 consultas
  (`qsa_select.cu:947-951`): los ~56 CTA residentes comparten tile de consultas y tocan rangos de claves distintos; el siguiente
  tile relee *todas* las claves (distancia de reuso = el array entero: 16,8 MB a 128K, 33 MB a 262K). En la 5070 (48 MB) son
  aciertos de L2; en la 3060 (3 MB) van a DRAM. Cota: `512 tiles x reach x 512 B` por capa y chunk de 8.192 tokens = 8,6 GB a 128K
  (reach 32.768) → x12 capas = 103 GB = 0,29 s a 360 GB/s [EST, cota superior, cache 0 %].
- **R7. Llamada VRAM de expertos agrupados** lanza `cap_groups` filas de bloques (`iq_kernels.cu:1555`; `verify.cpp:765`
  pasa `gy=0`); el comentario `iq_kernels.cu:887-894` ya reconoce el coste en la llamada PCIe. ~19.200 CTA/capa, ~11 µs de
  lanzamiento a 1 CTA/ciclo [EST]; oculto bajo `C` en 12 GB.
- **R8. Atención decode: fase V en serie** (`qsa_decode_attn.cu:154-179`): 64 iteraciones con una carga de 1 B por hilo; cada CTA ~20-40 µs
  [EST]. Solo ~0,4 ms/ventana en total; no prioritario.
- **R9. Fusión de lanzamientos pequeños**: ver H4 (2.260 nodos).

### H7. Presupuesto de bytes por ventana/ronda (decode) y modelo P/Q/C

Todos los tamaños salen del código o del paper (Tabla 1), salvo lo marcado [EST].

- **GR** (BF16): `w_down` 320x10.240x2 B = 6,55 MB; `w_up` 10.240x320x2 = 6,55 MB; `w_inject` 4x10.240x2 = 82 KB → **13,19 MB por lectura**, 2 por capa, 48 capas = **1,266 GB** (coincide con "1,3 GB" del paper).
- **Mixers GDN** (36 capas): qkv 10.240x2.560 a ~3,82 bpw ponderado (`layer.cpp:121-124`: IQ4_XS x13, Q3_K x18, Q4_K x4, Q2_0 x1) = 12,5 MB; z 6.144x2.560 a 3,48 bpw = 6,9 MB; out 2.560x6.144 a ~3,7 bpw = 7,3 MB; estado 3,15 MB; alpha/beta 0,49 MB; router 2,62 MB.
- **Capa QSA** (12): q 14,2 MB, k+v 1,2, o 7,1, indexer q 2,62 + k 0,66, KV a 4K (3,2 consultas x 2.051 celdas x 1.056 B int8) 6,9 MB.
- **Head**: 0,44 GB (248.320 x 2.560 a ~5,5 bpw).

| Componente (por ventana, T≈3,2) | Bytes | ms @360 GB/s (100 %) | ms @672 GB/s |
|---|---|---|---|
| Densos: mixers+atención+routers+shared (1,8) + GR (1,27) + head (0,44) | **3,54 GB** [DOC Tabla 1] | 9,8 | 5,3 |
| Expertos en caché VRAM: ~16 distintos/capa x 1,38 MB (34 GB/24.576) x 48 | 1,06 GB [EST] (T=1: 0,48; T=4: 1,26) | 2,9 | 1,6 |
| Estado GDN 36 x 3,15 MB | 0,113 GB | 0,31 | 0,17 |
| KV decode a 4K (12 capas) | 0,083 GB (0,16 en fp16) | 0,23 | 0,12 |
| Drafts MTP (3 pasos x ~0,28 GB: capa + head de 106K ids ≈ 180 MiB [DOC `DETAILS.md:104-105`]) | ~0,84 GB [EST] | 2,3 | 1,25 |
| **Total por ronda** | **≈5,64 GB** (1,75 GB por token aceptado) | **15,7** | **8,4** |

Calibración con la 5070 [DOC paper Tabla 5, Q2_0 4K]: ronda 34,3 ms = GPU 14,6 + CPU-experts 13,4 + draft 2,0 + otros 4,3. Bytes/tiempo =
164 GB/s = 24 % de 672.

**Modelo de capa** [EST]: `capa = P + máx(Q, C) + ε`. P = cadena GPU antes del doorbell (GR, mixer, GR, router); Q = shared expert + expertos
en VRAM; C = CPU sobre los expertos que faltan. Con la 5070: P≈304 µs/capa (14,6 ms/48), C-Q≈280 µs → C≈337 µs si Q≈57 µs. Q < C en las tres
cuantizaciones del paper → **Q está oculto** y solo P (y la cola tras el CPU) está en la ruta crítica.

Bytes ideales de P por capa (a 306 GB/s = 85 % de 360): GDN 59,3 MB → 194 µs; QSA 61,6 MB → 201 µs. Por ventana:
`36·194 + 12·201 = 9,4 ms` (+ head 1,4) = **10,8 ms ideales** frente a ~20-35 ms estimados de P real en la 3060 (14,6 x 1,41-2,42):
eficiencia de P ~31-52 %. Ese hueco (~10-24 ms, 20-40 % de la ronda) es el espacio que atacan las propuestas P1-P5.

**Estimación de decode en la 3060** (Q2_0, 4K, mismos 12 GB, misma CPU): escalo P y el draft por 1,41 (reloj), 1,87 (ancho de banda) o 2,42
(SM x GHz); Q oculto escala igual; `C` constante.

| Régimen | Escala | Ronda | tok/s (3,23 tok/ronda) |
|---|---|---|---|
| limitado por reloj (latencia) | x1,41 | 40,0 ms | 80,8 |
| limitado por ancho de banda | x1,87 | 46,4 ms | 69,6 |
| limitado por olas (SM x reloj) | x2,42 | 54,0 ms | 59,8 |

Rango **60-81 tok/s** (≈0,63-0,85x de los 94,6 de la 5070) [EST]. Script de la cuenta: `simulaciones/calc.py`.
Dato ancla de otra Ampere: una 3090 (936 GB/s, 24 GB, sm_86) midió IQ3_XXS a 4K: 89,2 tok/s de decode y 1.772 tok/s de prefill
(`bench/results/2026-09-29-rtx3090-epyc-milan/matrix.md`) [DOC]: los kernels sm_86 funcionan bien; la diferencia con la 3060
es SM, BW y VRAM.

### H8. Qué kernels están más lejos del roofline en sm_86 (orden estimado)

1. **Argmax (`sampler_greedy_kernel`) y `row_top_prob`** (`verify_kernels.cu:355-358`): 1 CTA/fila lee 1 MB: ~26 GB/s efectivos (7 % de 360).
2. **Top-k** (1 CTA/consulta): ~22-58 µs a 32K-128K en la 5070 con cluster desactivado; serie de 4 pasadas + dígito en 1 hilo.
3. **Micro-kernels de una CTA / pocas CTA**: `doorbell_publish`, `wait_flag`, `indexer_append`, `gdn_ab_multi` (12 CTA, 0,49 MB en ~9 µs = 55 GB/s, 15 %; un GEMV BF16 de la misma forma [2560x48] midió 8,73 µs en una RTX 2070, `bench/results/2026-09-30-tc-gemv-sm75/README.md`), `gr_norm_split` (16 CTA).
4. **`gdn_step_norm_multi`**: 3,1 MB en ~25-30 µs [EST] = ~110 GB/s (30 %), latencia por token x T.
5. **`native_mmvq` con filas de 1-2 KB** (qkv Q3_K: 10 bloques de 110 B por fila, 8 por iteración → 2 iteraciones, 25 % de hilos ociosos en la segunda): vida del CTA dominada por 2 viajes a DRAM; olas x latencia. Si cada CTA vive ~2 µs y hay 12 por SM: 12·1,1 KB/2 µs = 6,6 GB/s/SM x 28 = 185 GB/s ≈ 51 % [EST, VERIF].
6. **Atención decode**: fase V por hilo.
7. Los más cerca de roofline: GR down (`~280 GB/s` en la 5070, `fused_gr.cu:311-313`; 280/360 = **78 % en la 3060**: aquí está más cerca del techo que en la 5070), BF16 GEMV (276-374 GB/s en una 2070 de 448 GB/s = 62-83 %, `2026-09-30-tc-gemv-sm75`), GR up.

Corolario: en la 3060 los kernels que en la 5070 estaban topados a ~250-300 GB/s por pocos CTA ya están casi saturados; **reducir bytes y reducir
la cadena de latencia vale más que afinar esos kernels**.

---

## Propuestas priorizadas

Ranking por (ganancia esperada x confianza) / esfuerzo, respetando "sin pérdida de calidad". "Id." = salida idéntica bit a bit por construcción.

| # | Propuesta | Ganancia estimada [EST] | Riesgo de calidad | Esfuerzo | Validación con las herramientas del repo |
|---|---|---|---|---|---|
| **P0** | **Medir antes de tocar:** `STRATA_VERIFY_PROFILE=1 --stats` (tiempos por etapa), `STRATA_VERIFY_NODES=1` (nº de nodos), `STRATA_PREFILL_TIMING=1`, `cuobjdump --dump-resource-usage` de `gdn_rec_kh_kernel`, `gdn_step_norm_multi_kernel`, `gu_grouped_t_kernel`, `expert_kernel<true/false>`, `block_topk_reg_kernel`; Nsight Compute en `native_mmvq`, `block_scores_tc_kernel`, `prompt_attn_i8_kernel` | 0 directa; decide el orden de todo lo demás | ninguno | 0,5-1 día | `ctest` de los `*_parity` + A/B de variables (Apéndice A) |
| **P1** | **Top-k de selección y argmax multi-CTA sin clusters** (sm_75/80/86/89): CTAs parciales (histograma/ candidatos) + CTA final "el último en llegar" con contador atómico y `__threadfence` (re-armable en cada replay del graph), o dos kernels. Mismo orden total (clave desc, índice asc) ⇒ mismos ids. Reutilizar el diseño del muestreo por split (`sampler.cu:13-21`, 61 bloques x filas) | Microbench de la 5070 (`qsa_select.cu:656-657`: 21,9→15,6 µs a 32K, 58→18 a 128K, 200→22 a 262K) x12 capas x1,4 (reloj) = 0,1 / 0,67 / 3,0 ms por ventana ⇒ **0,2 % / 1,5 % / 6,5 %** de una ronda de ~45 ms; argmax 40→6 µs x ~4 llamadas x1,4 = 0,2 ms (0,4 %). *El repo documenta +5 % (4K) y +18 % (128K) end-to-end* (`DETAILS.md:22-29`): la suma de microbenchmarks no lo explica (≤1,5 %) → medir primero (P0) | **ninguno si los ids/token son idénticos** | 4-6 días | Extender `decode_cluster_parity` (arm nuevo; hoy "skips below sm_90", `CMakeLists.txt:613-619`), `qsa_topk_active_parity`, `sampler_parity` (+`_one_block`,`_old`); `STRATA_EMULATE_CC=86` en una 5070 |
| **P2** | **Fijar/vigilar la ocupación de kernels de "una ola"** con `__launch_bounds__(…, minBlocks)`: `gdn_rec_kh_kernel` (≥3 CTA/SM para que `gdn_keyhead_ok` siga verdadero con 28 SM), `gdn_step_norm_multi_kernel` (2 CTA/SM), y revisar `expert_kernel`. Medir spills con `-Xptxas -v` | prefill 2-5 % si hoy cae al kernel lento (`gdn_rec` x1,2-1,5 de su fase); decode 0-2 % (R2) | ninguno (el tope de registros no cambia la aritmética; Id.) | 0,5-1 día con GPU | `gdn_rec_parity` (compara kh vs pipe bit a bit, `CMakeLists.txt:572-576`) + `--bench` con SM reservados; `gdn_parity`; hash de estado GDN tras un prompt de 32K (`bench/results/2026-09-28-prefill-speed`) |
| **P3** | **MMVQ "exacto" con 2 filas por CTA** (`ROWS=2`, `NW=4`) para `n_in=2560`/6144: cada fila conserva su orden de suma (el kernel ya admite `ROWS=4` para K pequeño con el mismo orden por fila, `native_mmvq.cu:1010-1061`); mitad de CTAs, 2 cargas independientes por hilo | GEMV tipo-mmvq ≈1,8 GB/ventana ⇒ 5,9 ms ideales; si hoy están al 55-65 % (9-10,7 ms) y la variante llega al 75-85 %: ahorro 2-4 ms ⇒ **2-6 %** de la ronda (muy dependiente de P0) | ninguno (Id. por fila) | 2-3 días | `mmvq_multi_parity` (T columnas vs 1 columna, bit a bit, `CMakeLists.txt:559-562`), `iq_multi_parity`, `native_mmvq_multi` (requiere `bench/micro`, no está) |
| **P4** | **Ramas paralelas en el CUDA graph** (fork/join con `cudaEventRecord`/`cudaStreamWaitEvent` durante la captura): GDN: GEMV `z` (6,9 MB) en paralelo con `conv_l2`+`ab` (13 µs); QSA: cadena del indexador (k_proj, norm/rope, append, q_proj, scores) en paralelo con la de q/k/v | 13 µs x36 + 25-40 µs x12 = 0,8-1,0 ms ⇒ **~2 %** | ninguno (mismos kernels, mismas entradas; Id.) | 3-5 días | `STRATA_VERIFY_NODES`, `gr_parity`/`gdn_parity`, comparar ids de tokens con y sin ramas |
| **P5** | **Menos lanzamientos**: batchear `kv_append`/`indexer_append` por token (T→1; -6/capa QSA), cuantización Q8_1 en el epílogo de `gr_up_multi` con `UPM_COLS=32` (80 CTA, un bloque Q8_1 por 32 columnas), fusionar `hit_add`+`combine`, quitar copias intermedias | ~200 nodos x 2-2,5 µs = 0,4-0,5 ms ⇒ **~1 %** | ninguno si se conservan operaciones y orden (Id.) | 1-2 semanas | `gr_parity`, `elementwise_parity`, `kv_q8_parity`, `native_grouped_parity`, `quantize_act_parity` |
| **P6** | **Swizzle/orden de grid para L2 en `block_scores_tc_kernel`** (CTAs residentes comparten un rango de claves y barren tiles de consultas) y medir `lts__t_sector_hit_rate` de `prompt_attn_i8_kernel` | prefill a contexto largo: cota 0,29 s por chunk de 8.192 a 128K ⇒ **2-5 %** (cota superior) | ninguno (solo orden de CTAs; Id.) | 1-2 días | `qsa_prompt_attn_parity` + comparar `scores` bit a bit antes/después |
| **P7** | **Autotuning por "DeviceFacts"** (SM, `cudaDevAttrL2CacheSize`, ancho de bus y reloj de memoria o NVML, ocupación medida) en vez de `cc`: `pick_ww`, `gdn_keyhead_ok`, `grid_groups` de la llamada VRAM, `256` de `block_scores_multi`, `48*8` de `fetch/gather`, umbral reg/ref del top-k | 1-3 % repartido; sobre todo hace explícito el ajuste del fork | ninguno (solo formas de grid; Id.) | 2-3 días | todos los `*_parity` (resultados no dependen de la forma del grid, `iq_kernels.cu:887-894`) |
| **P8** | **Evaluar `STRATA_PF_FUSED=1` y `STRATA_PF_FUSED_TILE=64|128` en sm_86** (prefill i-quants) | ±5-12 % (signo desconocido; en la 5070: IQ2_XS +12 % a 4K, IQ3 ≈ 0, `DETAILS.md:27-28`) | numérico: no es Id.; ya validado vs FP64 y KL (`prefill_fused_iq_test`) | 1 día | `prefill_fused_iq_test` (con `-DSTRATA_BUILD_TESTS=ON`), KL teacher-forced como en `bench/results/2026-09-28-prefill-speed` |
| **P9** | **Recortar CTAs vacíos de los expertos agrupados** (`grid_groups` de 8-12 para la llamada VRAM; stride en `s2_expert_grouped`) | ~0 % en 12 GB (Q oculto); útil con VRAM grande | ninguno (Id., documentado `iq_kernels.cu:887-894`) | 1 día | `native_grouped_parity`, `s2_expert_grouped_parity` |
| **P10** | **A/B de reloj de GPU** (`nvidia-smi -lgc`/`-pm`) durante decode: la GPU espera ~50 % del tiempo al CPU y puede bajar de estado de potencia | 0-10 % (sin datos) | ninguno | horas | `nvidia-smi dmon -s pc` durante una respuesta |
| **P11** | Claves del indexador en FP16 (halvan 16,8 MB/capa a 128K) y `CHUNK` mayor en la atención de la ventana del drafter | ≤1-2 % a 128K-262K | **cambia la selección/redondeo: no es Id.** | 3-5 días | `STRATA_IDX_FP16_CHECK` (`prefill.cpp:1860-1893`) debe dar 100 % de selecciones idénticas; si no, KL |

**No recomendado (con la razón):**
- *L2 persistence (`cudaAccessPolicyWindow`)*: no hay datos reutilizados >1 MB entre kernels; reservar hasta el 75 % de 3 MB al L2 persistente solo perjudica al streaming de pesos.
- *Tensor cores para los GEMV de decode*: medido en sm_75: 1,3-2,2x más lento (`bench/results/2026-09-30-tc-gemv-sm75`).
- *FP8, clusters, TMA, PDL*: inexistentes en sm_86.
- *`upstream layout` de MMVQ (2 filas, 2 warps)*: no es Id. (cambia el orden de la reducción entre warps, `native_mmvq.cu:993-997`); P3 logra lo mismo sin tocar el orden.
- *Dividir K en los GEMV de `ab`/router/indexer* para usar más SM: cambia el orden de suma (contrato "native" con llama.cpp).

**Detalle de P1 (por qué es la primera con código).** El top-k actual hace 4 pasadas de radix select con búsqueda del dígito en un solo hilo (hasta 255
iteraciones, `qsa_select.cu:91-99,551-559`) y dos escaneos de bloque; el kernel de cluster lo reparte en 8 CTA y suma histogramas por DSMEM.
Sin DSMEM, la alternativa exacta es: kernel A (grid `(S, nq)`, S≈8-14): cada CTA construye el histograma de su tramo y lo escribe a
un buffer global; el último CTA por consulta (contador atómico en memoria global con época monótona para repetir en cada replay) suma
los S histogramas, decide el dígito (versión en warp como `qsa_select.cu:790-822`) y repite 4 pasadas leyendo las claves del L2/DRAM
(≤262 KB por consulta a 262K), luego emite ids en orden ascendente con los mismos prefijos de `gt/eq` que la versión cluster
(`:827-863`). Misma función de (clave, peso) ⇒ mismos ids. Alternativa en 2 kernels si se prefiere evitar el contador. El argmax
usa el mismo patrón (regla "mayor valor; a igualdad, menor índice", `sampler.cu:143-152,176-181`).

---

## Cambios de bajo riesgo implementables sin GPU

(No aplicados: auditoría de solo lectura. Todos son host-side, de build o de documentación, salvo donde se indica.)

1. **`CMakeLists.txt:77-79`**: por defecto `86` en este fork (o autodetección con `nvidia-smi --query-gpu=compute_cap`); mantener `120`/`89`/`75`
   solo si se piden. Arregla el comentario obsoleto de `:7-9`.
2. **Opción de build de perfil** (`STRATA_PROFILE_BUILD=ON`): `-lineinfo` y `-Xptxas=-v` para los `.cu`; añadir a `docs/` un recordatorio
   de `cuobjdump --dump-resource-usage engine/strata`. Sin cambio de código generado en el build normal.
3. **Diagnóstico de arranque (host)**: bajo `--stats` o `STRATA_VERIFY_DEBUG`, imprimir para los ~10 kernels críticos
   `cudaFuncGetAttributes` (`numRegs`, `localSizeBytes`, `binaryVersion`, `ptxVersion`) y `cudaOccupancyMaxActiveBlocksPerMultiprocessor x SMs` frente al grid
   que se lanzará, con aviso "needs a 2nd wave on this card". Cierra R2/R3/R4 sin tocar kernels.
4. **`DeviceFacts` (host)**: struct cacheada por dispositivo (SM, `cudaDevAttrL2CacheSize`, ancho de bus/reloj de memoria con la caída a NVML si CUDA 13 retira
   los atributos de reloj, `cudaDevAttrMaxPersistingL2CacheSize`, ocupación de los kernels críticos) impresa al arrancar; las decisiones de P7 vendrán después.
   El motor ya hace algo parecido para el reparto multi-GPU: `generate.cpp:2478-2490` usa `SM x GHz` con la constante `0,33 ms·(84·2,617)` por capa.
5. **`Dockerfile:58,76`**: `CUDA_ARCHITECTURES=86` por defecto en el fork.
6. **Pruebas primero**: añadir a `src/kernels/decode_cluster_parity.cpp` (y registrar en `CMakeLists.txt:613-619`) un brazo "multi-CTA sin cluster" que
   hoy marque "pendiente", más un caso de `qsa_topk_active_parity` con capacidad >33.792 bloques.
7. **Bench de ocupación a 28 SM**: ampliar la lista `{... 34, 32}` de `gdn_rec_parity.cu:677-678` (y las bandas de `:600` y `:835`) con 30, 28, 24 y relajar la condición "ambos grids caben a la vez"
   (`:679`) para poder medir `gdn_rec_kh` vs `pipe` emulando 28 SM en cualquier Ampere/Ada/Blackwell.
8. **`tools/` script** (Python, sin GPU para escribirlo) que corre la matriz A/B de variables del Apéndice A y recoge las líneas "decode GPU stages".
9. **Documentar en `docs/`** la 3060 con las cifras de este informe marcadas como estimación (siguiendo `AGENTS.md`: sin afirmaciones sin medición).
10. **Sugerencia de configuración (no kernel)**: `--draft-vocab en` por defecto en tarjetas de 12 GB; ya lo sugiere el motor/`DETAILS.md:110-113` para <14 GB. Ahorra ~110 MiB de VRAM (~80 expertos) y ~110 MB x 3 por ronda
    (~0,9 ms ≈ 2 %); salida idéntica (los drafts solo proponen), pero casi sin drafts para CJK.

---

## Preguntas abiertas

1. ¿Qué trae el `BUILD.json` real del release (`archs`, `ptx`, `cuda`)? ¿Incluye 86 como SASS? (`unzip -p strata-linux-x64.zip BUILD.json` + `cuobjdump`).
2. ¿Cuántos registros/hilo y qué ocupación tienen en sm_86 `gdn_rec_kh_kernel`, `gdn_step_norm_multi_kernel`, `expert_kernel`, `gu_grouped_t_kernel`, `block_topk_reg_kernel`? (decide R2/R3).
3. ¿Cómo se descompone el +5 % (4K) / +18 % (128K) que `DETAILS.md:22-29` atribuye a los clusters, si los microbenchmarks del mismo código (`qsa_select.cu:656-657`, `sampler.cu:174-176`) suman ≤1,5 %? Hay otro coste en el graph que no veo.
4. ¿Qué contexto objetivo para la 3060? Con ≤32K el top-k vale ~0,2 %; a 128K-262K 1,5-6,5 % (o más si el end-to-end documentado se confirma).
5. ¿Qué CPU tendrá la máquina de la 3060 (AVX-512 → pack canónico `s2_*`; AVX2 → pack nativo `iq_kernels`)? Cambia qué kernels de expertos se usan y el valor de C.
6. ¿Política de tolerancia? Las propuestas P1-P7 son bit-idénticas por diseño; P8 y P11 no (KL teacher-forced como en `bench/results/2026-09-28-prefill-speed`: "mismo top-1 / KL medio" frente al yardstick de otro tamaño de chunk).
7. ¿Se quiere que el fork sea solo-86? Permite especializar (p. ej. `if constexpr`) y recortar compilación; rompe la portabilidad a 75/80/89/120.
8. Comparar siempre misma arquitectura antes/después: `mma.sync` con TF32/FP16 puede redondear distinto entre generaciones, así que la salida de **prefill** de una 3060 no tiene por qué ser bit-idéntica a la de una 5070 (el decode, con `dp4a` y FP32, sí debería).
9. ¿Los reloj/estados de potencia de la 3060 bajan durante la espera del CPU? (P10).
10. ¿Cuántos nodos y qué `GPU-reach wait`/`per-layer host` reporta `STRATA_VERIFY_NODES` y `--stats` en la 3060? Con eso se confirma (o no) el modelo P/Q/C de H7.

---

## Apéndice A - Procedimiento mínimo de medición en la 3060

```
# Build de este fork para la tarjeta (SASS sm_86 + PTX) y con info de registros
cmake -S . -B build -DSTRATA_ENABLE_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 -DSTRATA_BUILD_TESTS=OFF \
      -DCMAKE_CUDA_FLAGS="-lineinfo -Xptxas=-v" -DSTRATA_GGML_DIR=<llama.cpp 3cf0325>
cmake --build build --target strata
cuobjdump --dump-resource-usage build/strata | grep -A1 -E "gdn_rec_kh|gdn_step_norm_multi|expert_kernel|gu_grouped_t|block_topk_reg"
cuobjdump --list-elf build/strata | head        # debe listar sm_86

# Paridad (sin modelo): ctest sobre los *_parity y --bench de los que lo admiten
cmake --build build --target gr_parity gdn_parity gdn_rec_parity s2_expert_grouped_parity native_grouped_parity \
      iq_multi_parity mmvq_multi_parity sampler_parity qsa_parity decode_cluster_parity qsa_prompt_attn_parity
ctest --test-dir build -R "parity|multi" --output-on-failure
build/gdn_rec_parity --bench      # fewer_sms: bandas de SM (añadir 28/30, ver cambio 7)
build/decode_cluster_parity --selftest --bench   # hoy: "skips below sm_90"

# Decode con el perfil por etapas y el recuento de nodos
STRATA_VERIFY_PROFILE=1 STRATA_VERIFY_NODES=1 strata ... --stats   # "decode GPU stages (ms/window)", "window graph has N nodes"
STRATA_PREFILL_TIMING=1 ...                                         # fases del prompt

# A/B de variables ya existentes (una por vez; mismo prompt, 3 repeticiones)
STRATA_HC_SPLIT=0|1|2   STRATA_GR_V3=1   STRATA_DEC_BATCH=0   STRATA_SCORES_MULTI=0   STRATA_TOPK_OLD=1
STRATA_GDN_KEYHEAD=0|1  STRATA_GDN_PIPELINE=0   STRATA_PF_FUSED=0|1   STRATA_PF_FUSED_TILE=64|128
STRATA_GROUPED_V1=1     STRATA_OLD_IQ_MMVQ=1    --pcie-frac N   --spec 3|4|5   --calibrate
nvidia-smi dmon -s pc    # reloj SM y potencia durante una respuesta (P10)
```

Calidad: igualdad de tokens (greedy) y hash de estado GDN (`STRATA_STATE_HASH_GDN`) para las propuestas Id.; KL teacher-forced y needles
(`tools/needle_bench.py`) para las que no.
