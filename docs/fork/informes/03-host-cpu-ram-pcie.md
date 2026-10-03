# Informe 03 - Lado host (kernels CPU, RAM, PCIe, SSD) para una RTX 3060 12 GB

Auditoría de solo lectura de `/home/user/Strata3060` (fork de Strata). Foco: qué hace el host en cada token y cada
prompt, y qué se puede mejorar para una PC de gama media (Ryzen 5 3600/5600, i5-10400/12400, DDR4-3200, 32/64 GB,
PCIe 3.0/4.0, SSD SATA o NVMe) sin perder calidad.

Convenciones: **[medido-aquí]** = lo medí yo en esta sandbox (ver "Mediciones propias"); **[paper]** = tabla del
`docs/paper/Strata-Paper.pdf`; **[estimado]** = razonamiento con la aritmética a la vista, no medido. No hay GPU ni
red a llama.cpp en esta sandbox, así que nada de lo que dependa de CUDA/ggml-cpu está medido.

---

## 1. Resumen

1. **La ruta CPU de una PC sin AVX-512 existe y es completa, pero está mucho menos optimizada que la AVX-512.**
   El kernel Q2_0 "canónico" (VBMI `vpmultishiftqb` + `vpdpbusd`) exige AVX-512 y el motor sale si no lo hay
   (`src/kernels/cpu/expert.cpp:375-384`). En CPUs AVX2 (Zen 2/3, i5-10400/12400) `setup.py` empaqueta Q2_0 como
   pack *nativo* (`setup.py:3501-3515`) y la CPU usa `q2_avx2.cpp` (`vpmaddubsw`+`vpmaddwd`, sin VNNI) y `iq_avx2.cpp`.
   **No hay ninguna ruta AVX-VNNI** (el detector `cpu_avx512_ok` exige F+BW+VL+VNNI+VBMI,
   `src/kernels/cpu/expert_layout.cpp:24-54`; el i5-12400 cae a AVX2 puro).
2. **Cuello de botella en AVX2 [medido-aquí, ~2,6 GHz Skylake-SP]:** los formatos i-quant (IQ2_S, IQ2_XS, IQ3_XXS,
   IQ3_S) están limitados por *decodificación* (1,5-2,9 GB/s por núcleo en L2, es decir ~0,8-1,1 GB/s por GHz), no por
   DDR4. Q2_0 AVX2 queda en 3,8 GB/s/núcleo con 1 token y 2,2 con 3 tokens: en una Zen 3 de 6 núcleos eso es ~44 / 26 GB/s
   [estimado], o sea *a la altura de* DDR4-3200 con 1 token y *por debajo* con ≥2 tokens.
3. **Dos kernels AVX2 prototipados (en scratchpad, sin tocar el repo):**
   - Q2_0 AVX2 con activaciones "en planos" (desentrelazadas por stride 4 una vez por capa): **2,3-2,6x más rápido**
     en L2 y 1,6x en streaming con 4 hilos; la parte entera es exacta, el float cambia de orden (|dif| media relativa
     3,5e-7, la misma clase de diferencia que ya existe entre AVX-512 y AVX2).
   - IQ2_S AVX2 con índices/escalas/signos vectorizados por bloque: **1,21-1,34x**, **bit a bit idéntico** al kernel
     actual (0 floats distintos en 8 semillas x NT 1-4). IQ2_S es el tipo gate/up más frecuente de los packs IQ2_XS
     (34/48 capas), IQ3_S y Coder (20/48) (`tests/data/native_experts/*.txt`).
4. **Solapamiento GPU/CPU:** estrictamente por capa. Cada capa es: GPU `pre(l)` (CPU ociosa) -> doorbell ->
   [GPU: experto compartido + hits en VRAM + copia PCIe || CPU: planifica + expertos fallados] -> GPU combina. No hay
   ningún pipelining entre capas (la dependencia de datos del residual lo impide); el único "lookahead" (predicción del
   router de la capa siguiente) solo se usa para el tier de ficheros (`expert_source.cpp:1865`), no para la RAM.
   La CPU está ociosa ~43 % de cada ronda en la 5070 [paper, tabla 5: 14,6 de 34,3 ms] y lo estará más en la 3060.
5. **PCIe 3.0 en decode:** el reparto fijo `pcie_frac` (0,55 para packs nativos, escalado lineal con el ancho de banda
   medido: 12,5 GB/s -> 0,34, `generate.cpp:1058-1060`) **no mira la velocidad de la CPU**, y desde 0.1.14 la copia PCIe
   por defecto es un *kernel de copia* que corre en el camino crítico de la GPU (`verify.cpp:769-774`; el propio paper
   dice que eso era más lento que DMA, hallazgo 9). En PCIe 3.0 con Q2_0 rápido el óptimo está cerca de 0,15-0,25
   [estimado]. Además, el `apply_pending(wait=true)` por defecto (#463) bloquea el inicio de ventana hasta que las 96
   copias del tier adaptativo aterrizan: 96 x 1,4-2 MB a 12,5 GB/s = 11-15 ms cada 4 ventanas [estimado: 3-6 % de la ronda].
6. **PCIe 3.0 en prefill:** con chunk de 8192 el prefill reenvía ~32-48 GB de expertos por chunk (todos los no
   residentes + los "prestados"). A 12,5 GB/s son 2,6-3,9 s por chunk; la 3060 tarda ~7-10 s en calcular 8192 tokens
   [estimado], así que **no queda limitada por PCIe con chunk >= 4096**, pero **sí con chunk <= 2048** (prompts cortos,
   la cola de un prompt, contextos >= 64K donde `--prefill auto` baja a 4096-6144). Una política "CPU para expertos con
   pocos tokens" no compensa (la CPU es ~30x más lenta por experto con 160 tokens y solo gana con <= 3 tokens).
7. **RAM:** en 32 GB solo cabe el Coder (`setup.py:2023-2058`: 10 GB de margen + arena - 7 GB que guarda la GPU); Q2_0/IQ2_XS
   necesitan >= 37-39 GB en modo residente. La arena en Linux solo intenta `MAP_HUGETLB` (necesita pool reservado por
   root); **no hay fallback `MADV_HUGEPAGE`** (`pinned.cu:204-226`). Mi prueba del efecto de páginas grandes en esta VM:
   sin diferencia medible (ver §5), así que la prioridad es baja.
8. **SSD/PLE:** la tabla n-gram de 28,8 GB se lee **sin caché del SO** (O_DIRECT, 16 hilos pread, 4 KiB por fila,
   `ngram.hpp:106-127`, `direct_file.cpp:263-314`), contra lo que dicen `docs/DETAILS.md:890` y `HOW_IT_WORKS.md:31`
   ("through the OS cache"). Con SATA cuesta ~0,3 ms por token y ~2-3 s en el primer chunk de un prompt largo
   [estimado]; con NVMe es despreciable.
9. **No hay una palanca única > 10 % en el host.** Las mejoras se acumulan: kernels AVX2 (+2-6 % cada uno), controlador de
   `pcie_frac`/`adapt_swaps` para PCIe 3.0 (+2-10 %), SMT para kernels limitados por decodificación (0-7 %), y un hueco
   de pruebas: **ninguna prueba del repo ejecuta las rutas AVX2 de la CPU sin CUDA** (`pool_test`, `pool_stress`,
   `expert_parity`, `expert_multi_test` salen con "no AVX-512").

---

## 2. Hallazgos (con file:line)

### 2.1 Kernels de expertos en CPU

**Matriz formato x ISA** (qué corre dónde; los tipos son los ids de ggml):

| Formato (tipo) | Dónde sale | AVX-512 (VNNI+VBMI) | AVX2 (Zen 2/3, Intel 10/12ª) | nt = 1 | nt >= 2 |
| --- | --- | --- | --- | --- | --- |
| Q2_0 canónico (pack plano, solo AVX-512) | pack `strata_pack.py` | `expert.cpp:174-221` (`row_dot_z`, 1 desempaquetado `multishift` + 1 `vpdpbusd` por 64 pesos) | **no existe**: `cpu_require_expert_support` sale (`expert.cpp:375-384`; `generate.cpp:1840`) | si | si |
| Q2_0 GGUF (42): Q2_0 nativo y down de packs IQ | `expert.cpp:557-609` | `q2g_row_multi` | `q2_avx2.cpp:38-88` (`maddubs`+`madd`, NT<=4 por llamada) | si, sin fallback a ggml (ggml no tiene SIMD Q2_0 en x86) | si |
| IQ2_XXS(16) IQ2_XS(17) IQ3_XXS(18) IQ3_S(21) IQ2_S(22) | gate/up de packs IQ | `iq_avx512.cpp` | `iq_avx2.cpp:99-228` (genérico), IQ2_XS con kernel propio `:236-316` | **ggml-cpu `vec_dot`** (portable AVX2) | kernel Strata (`native_expert.cpp:104-114`) |
| IQ4_XS (23) | 1 capa de IQ3_S/Coder | no (ggml) | `iq_avx2.cpp:180-200` solo en CPUs sin AVX-512 (#415, `native_expert.cpp:101-105`) | ggml | Strata AVX2 |
| IQ4_NL (20) down | 18-39 capas de IQ3_XXS/IQ3_S/Coder | ggml | `iq_avx2.cpp:411-462` (`STRATA_NO_IQ4NL` lo apaga) | ggml | Strata AVX2 |
| IQ1_M (29) | 3 capas del pack "IQ2_XS" | ggml | ggml | ggml | ggml (`iq_avx2.cpp:12`) |
| Q4_K/Q5_1/Q8_0 (Unsloth) | UD-Q4_K_XL | ggml | `kq_avx2.cpp` solo con `STRATA_KQ256=1` (bit-exacto, medido sin ganancia) | ggml | ggml (o kq256) |

Composición real de los packs (de `tests/data/native_experts/*.txt`): **IQ2_XS** = gate/up IQ2_S x34 capas + IQ2_XXS x11 +
IQ1_M x3, down Q2_0 x48; **IQ3_XXS** = IQ2_XXS x9, IQ2_S x10, IQ3_XXS x6, IQ3_S x13, IQ2_XS x10, down Q2_0 x30 / IQ4_NL x18;
**IQ3_S y Coder** = IQ4_XS x1, IQ2_S x20, IQ3_XXS x17, IQ3_S x10, down Q2_0 x9 / IQ4_NL x39 (el Coder con 256 expertos/capa).
Tamaño medio por experto: Q2_0 1,382 MB; IQ2_XS 1,443; IQ3_XXS 1,746; IQ3_S y Coder 2,046.

**Elección de ruta (dispatch):**
- `cpu_avx512_ok()` (`expert_layout.cpp:24-54`) pide F, BW, VL, VNNI, VBMI + XCR0; `STRATA_FORCE_AVX2=1` la apaga.
  `cpu_avx2_ok()` (`:56-86`) pide AVX2+FMA+F16C y es el mínimo (`generate.cpp:1766-1771`). **No se detecta AVX-VNNI** (CPUID 7.1 EAX[4]).
- Q2_0 GGUF: `q2_rows_any`/`act_quant_any` (`expert_layout.cpp:111-120`) eligen AVX-512 o AVX2 en tiempo de ejecución.
- i-quants: `native_gu_rows` (`native_expert.cpp:79-127`). La regla por defecto `mt_min = 2` (línea 92) manda los grupos de
  **1 token a `vec_dot` de ggml** (compilado AVX2 portable, `CMakeLists.txt:855-862`: AVX, AVX2, FMA, F16C, BMI2; sin
  AVX-VNNI ni AVX-512 en ggml) y los de >= 2 tokens al kernel Strata. El comentario de `:82` dice que el kernel Strata
  "no es más rápido con 1 token" y el paper (hallazgo 7) que ambos van a ~5 GB/s por núcleo. `STRATA_IQ_MT_MIN=1`
  fuerza Strata siempre (salida independiente del drafting, #152; coste medido -1..-3 % en IQ3_S AVX-512).
- Compilación: ficheros con flags AVX-512 aislados (`CMakeLists.txt:816-829, 879-891`); el resto corre en cualquier x86-64.

**Modelo de hilos (`pool.cpp`, `pool.hpp`):**
- `ExpertPool` (`pool.cpp:345-373`): `n_` = núcleos **físicos - 1** (el primero se reserva para el hilo del host,
  `detect_cpu_topology(skip_first=true)`, líneas 139-257 Linux, 32-137 Windows) y el **hilo del host también drena**
  (`host_works`, `pool.cpp:601`, `pool.hpp:112-123`): en un 6C/12T son **5 workers + host = 6 hilos, uno por núcleo
  físico, ningún hilo en los hermanos SMT**. Se fijan por afinidad (`pool.cpp:267-277, 367`). El hilo del host se fija
  con `pin_current_thread` (`pool.hpp:96-105`: el comentario mide 36,3 GB/s con 5 workers solos y 26,9 GB/s dentro del
  bucle del host cuando el host sin fijar caía sobre el núcleo de un worker o su hermano SMT).
- `--pool-workers N` mayor que los núcleos físicos crea hilos **sin fijar** (`pool.cpp:367`: `core = -1`), es decir no
  existe una forma controlada de usar los hermanos SMT. `tools/calibrate.py:63-69` solo prueba *menos* workers (2/3 y 1/2).
- **No hay conciencia de CCX, NUMA ni L3** (`grep -i ccx|numa|l3` en `src/` y `include/`: nada). Para el streaming de
  pesos es irrelevante (se leen una vez), pero la sincronización (CAS en `head_`, `done_`, `parked_`, `epoch_`,
  `pool.cpp:447-470, 397-445`; 4 esperas de "todos aparcados" por capa, 2 por fase) cruza el IOD en una Zen 2 de 2 CCX
  (cientos de ns por línea transferida): ~0,3-1 ms por ventana de 35-70 ms [estimado, ~1 %].
- Reparto: `3 x hilos` tareas por fase (`pool.cpp:644, 672`), filas repartidas entre *todos* los expertos de la capa
  (dos fases con barrera: gate/up, cuantización de la activación intermedia **en serie en el host** `pool.cpp:648-650`, down).
  Con 8-10 expertos fallados por capa es el reparto correcto; el coste serie `act_quant_q8_1_avx2` lo medí: 1,5 us por
  2560 valores, 0,4 us por 640 [medido-aquí] -> despreciable.
- Parking: spin con `_mm_pause` 20 ms y luego sueño (`pool.cpp:397-445`, `pool.hpp:187`). Durante el decode todos
  los núcleos físicos están al 100 %: en una 6C una tarea del SO que desaloje a un worker retrasa toda la capa (la barrera
  espera al más lento; el reparto dinámico solo amortigua tareas no empezadas).

**¿Ancho de banda DDR o cómputo en Zen 2/3 con AVX2?** (medidas en §5; escala a otras CPUs = [estimado], ~1 GB/s por GHz
y núcleo para IQ2_XS/IQ3_XXS, coincide con los 5 GB/s/núcleo a ~5 GHz que da el paper para Zen 4):

| Kernel AVX2 (gate/up, GB/s de pesos por núcleo, en L2) | nt=1 | nt=2 | nt=3 | 6 núcleos @4,4 GHz (Zen 3) nt=1 / nt=3 [estimado] | Límite |
| --- | ---: | ---: | ---: | --- | --- |
| Q2_0 GGUF (actual) | 3,81 | 2,70 | 2,21 | ~44 / ~26 GB/s | nt=1 ~ DDR4; nt>=2 cómputo |
| IQ2_S | 2,12 | 1,73 | 1,54 | ~25 / ~18 | cómputo (decodificación) |
| IQ2_XS | 2,70 | 2,26 | 2,08 | ~31 / ~24 | cómputo |
| IQ3_XXS | 2,90 | 2,61 | 2,24 | ~34 / ~26 | cómputo |
| IQ3_S | 2,39 | 1,76 | 1,76 | ~28 / ~21 | cómputo |
| IQ4_XS | 8,01 | 6,25 | 4,27 | ~94 / ~50 | DDR4 |
| IQ4_NL down (filas de 640) | 5,98 | 4,38 | 3,42 | ~70 / ~40 | DDR4 |

DDR4-3200 de doble canal rinde ~40-45 GB/s de lectura real en Zen 3 y menos mientras el DMA de PCIe también lee RAM
(en la 7600/DDR5 el paper mide 52 GB/s solos y 41 con la GPU leyendo [paper]); asumo 33-36 GB/s para la CPU con DMA activo.
Conclusión: **Q2_0 está en el borde del muro DDR4; los i-quants (el Coder, la única opción de 32 GB) están lejos de él.**

**Bytes por token que la CPU lee, y techo teórico** (base = tabla 5 del paper, RTX 5070 12 GB, 4K de contexto; reescalado
para la 3060 solo por el reparto PCIe; hit-rate de VRAM 0,71-0,78 [paper], supuesto igual en una 3060 12 GB):

| Modelo (blob) | Fallos totales por token (CPU+DMA) | CPU con f=0,55 (PCIe 4.0) | CPU con f=0,34 (PCIe 3.0) | Techo DDR4 solo-RAM (36-45 GB/s) | Cómputo CPU (Zen 3, 6 núcleos) |
| --- | ---: | ---: | ---: | ---: | --- |
| Q2_0 (1,38 MB) | 213 MB (a f=0,2 medido: CPU 170) | 96 MB | 140 MB | 169-212 tok/s | ~36 GB/s mixto -> 3,9 ms/token |
| IQ2_XS (1,44 MB) | 286 MB (CPU 129 a f=0,55) | 129 MB | 189 MB | 126-157 tok/s | ~25 GB/s -> 7,6 ms/token |
| IQ3_XXS (1,75 MB) | 446 MB (CPU 201) | 201 MB | 294 MB | 81-101 tok/s | ~26 GB/s -> 11,3 ms/token |
| IQ3_S / Coder (2,05 MB) | ~ (no hay tabla; >= IQ3_XXS) | | | <= 80-95 tok/s | |

(Aritmética: CPU MB/token = ms CPU x GB/s CPU / tokens por ventana [paper, tabla 5]: 13,4 x 41 / 3,23 = 170; 21,1 x 20 /
3,28 = 129; 27,1 x 24 / 3,24 = 201. Fallos totales = CPU / (1 - f).) Estos techos solo acotan el lado host: la ronda
real es `GPU serie + CPU expuesta + draft + otros` (34-49 ms en la 5070 [paper]); en la 3060 la parte "GPU serie" crece
~1,6-1,9x (ancho de banda 360 vs 672 GB/s) y la CPU expuesta se reduce porque la GPU tiene más trabajo en paralelo que
esconde parte de la CPU. **Mi estimación para IQ2_XS en 3060 + Zen 3 + PCIe 3.0: 45-65 tok/s** (5070: 79) [estimado, sin medir].

### 2.2 Solapamiento GPU/CPU en decode y en verify de MTP

Secuencia por capa y ventana (`verify.cpp`, capturada en un CUDA graph por tamaño de ventana, `:893-954`):
1. GPU `pre(l)` (`:460-725`): hc-read, mixer GDN/QSA, router; **`doorbell_publish`** (`:694`) escribe x, ids y pesos en
   memoria mapeada y toca `seq`. La CPU está ociosa hasta aquí.
2. Host: spin en `*seq < want` con `_mm_pause` (`:1074-1100`), luego llama al pool (`:1107-1109` -> `drive_pool_multi`,
   `generate.cpp:671-691` -> `expert_pool_dispatch_multi`).
3. `expert_pool_dispatch_multi` (`expert_source.cpp:1855-2101`): clasifica cada entrada (VRAM / PCIe / CPU) y **publica
   el plan primero** (`:1889-1983`, `publish` -> flag A), de modo que la GPU arranca los hits mientras la CPU sigue;
   cuantiza activaciones en serie (`:2004-2010`), pide `prefetch` de los fallados (`:2012-2020`) y corre
   `run_split_multi_native` (`:2068`). Los tokens de la ventana que comparten un experto lo comparten *en el kernel*
   (una lectura de pesos, NT <= 8).
4. GPU `post(l)` (`verify.cpp:728-812`): espera flag A (plan) `:737`, hits VRAM `:765`, espera flag B `:768`, **copia PCIe**
   `:769-774`, grupos PCIe `:778`, **espera flag CPU** `:785`, copia las filas de la CPU `:787-789`, combina `:793-803`.
5. Host marca el flag de capa (`:1114-1124`); tras la última capa `cudaStreamSynchronize` (`:1134`).

Quién espera a quién: la capa dura `GPU_pre + max(GPU_paralelo, CPU) + GPU_combine`. La CPU solo está "expuesta" por lo que
excede al trabajo paralelo de la GPU (experto compartido + hits + copia PCIe). Con una 3060 la GPU paralela es ~1,9x más
larga, así que **las mejoras del kernel CPU rinden menos que en la 5070 hasta que la CPU deja de ser el camino más largo**.
No hay pipelining entre capas: la entrada de `pre(l+1)` depende de la combinación de la capa `l`. El modo "ventana
dividida" (`G=2`, `verify.cpp:380-389, 406`) solapa mixer de un grupo con expertos del otro; el paper lo mide 7 % más lento
(lee los pesos densos dos veces), y en una 3060 (GPU limitada por ancho de banda) lo sería aún más.

**Copia PCIe en decode.** `--pcie-mode auto` = 2 = *kernel de copia* (`generate.cpp:374, 4546`): `fetch_dma` ni emite DMA
(`verify.cpp:1249-1253`: con n=0 sube el flag B de inmediato) y el kernel `fetch_blobs_kernel` (`verify_kernels.cu:218-226,
308-312`; 384 bloques x 256 hilos, `uint4`) lee la RAM pinned por PCIe **dentro del stream de la GPU** y escribe en staging
(<= 16 expertos por capa, `verify.hpp:256`). El paper (hallazgo 9) midió que esa forma "sentaba las lecturas en el camino
crítico de la GPU" y pasó a DMA; `DETAILS.md:874` dice que 0.1.14 volvió al kernel por el bloqueo del driver (#31). En
PCIe 3.0 una capa con 3-4 expertos por PCIe son 4,8 MB -> ~0,4 ms de kernel de copia, mayor que los ~0,3 ms de CPU de
esa capa [estimado]. **Pregunta abierta: A/B `--pcie-mode dma` en una 3060 PCIe 3.0.**

**Reparto GPU/CPU/PCIe.** `m = (nmiss * pcie_num) >> 8` (`expert_source.cpp:1908`): fracción fija **por número de
expertos fallados**, sin coste por experto. El valor lo fija `generate.cpp:1824-1838`: 0,2 (pack canónico), 0,55 (nativo) y
para nativo se escala `0,55 x min(1, GB/s / 20)` con una sonda H2D de 4 x 256 MiB (`generate.cpp:995-1051`). Con la
balance `CPU = GPU_ruta`: `f* = B_pcie / (B_pcie + B_cpu)`. Ejemplos [estimado]: IQ con CPU a 22-25 GB/s y PCIe 12,5 -> f* ~ 0,33
(el escalado lineal da 0,34: acierta); **Q2_0 con CPU a ~35 GB/s y PCIe 12,5 -> f* ~ 0,26 (kernel de copia en el camino
crítico: ~0,15-0,2)**, el escalado da 0,34 y el modelo anterior estima ~10 ms de más por ventana. `--calibrate` mide
{0, 0,2, 0,35, 0,55, 0,75} pero solo NVIDIA y a mano (`tools/calibrate.py:33, 162-166`).

**El tier adaptativo (#463, `STRATA_ADAPT_NOWAIT`).** Cada `--adapt-every 4` rondas se intercambian hasta 96 expertos
(`generate.cpp:5756-5757`, `adapt()` `:4794-4862`, copias en `adapt_stream`). Al inicio de cada ventana
`apply_pending(!adapt_nowait())` (`:5732`, `:4777-4792`) espera con `cudaEventSynchronize` a que aterricen las copias
(determinismo: dónde corre un experto, GPU o CPU, no debe depender del tiempo, #463). Con `STRATA_ADAPT_NOWAIT=1` hace una
consulta no bloqueante y puede aplazar el cambio. En PCIe 4.0: 96 x 1,4 MB = 134 MB -> 5 ms, casi oculto por commit+draft
(~4 ms). En PCIe 3.0: 10,7-15,4 ms (Q2_0..IQ3_S) -> ~6-11 ms expuestos por ronda de adaptación = **~1,7-2,8 ms por ventana
(3-6 %)** [estimado]. La copia además compite con el DMA/kernel de la copia por capa.

### 2.3 PCIe en prefill (chunk, buffers, doble buffer)

- Con chunk >= `stream_all_min()` = 1024 (`prefill.cpp:99-106`) **cada experto no residente de cada capa se envía** en un
  orden fijo por un anillo de `ring_slots()` (384, 512 o 1024 slots; `:107-156`), de modo que el copy engine trabaja
  mientras corre la atención (`:1469-1549`); un hilo "issuer" emite los `cudaMemcpyAsync` (`:1577-1626`). Por debajo de
  1024 solo viajan los expertos enrutados (STAGE=8).
- El chunk lo elige `--prefill auto` (`generate.cpp:3983-4024`): el mayor de {32768 (opt-in), 16384, 8192, 6144, 4096, 3072,
  2048, 1024, 512, 256} cuyos buffers "prestados" ocupen <= 90 % de los slots de la caché de expertos (85 % si hay copias
  de host). Mientras dura el prompt los slots prestados no son residentes, así que viaja casi todo: con 69 % prestado a
  8192 [medido en la 5070, `generate.cpp:3992-3994`] quedan residentes ~31 % de la caché.
- Experto no pinneado (arena no pinneable entera, p. ej. Windows con poca RAM): lo copian 2-4 hilos del `Stager` a buffers
  pinned (`prefill.cpp:207-338`, `:703-715`: `max(2, min(4, hw/4))`; 3 en un 6C/12T).

**Volumen por chunk completo y techo PCIe** (`bytes = (24576 - 0,31 x slots_caché) x blob`; slots de la tabla 4 del paper,
IQ3_S y Coder supuestos ~3000 [estimado]):

| Modelo | Expertos enviados / chunk | GB/chunk | Tiempo a 12,5 GB/s (PCIe 3.0) | a 25 GB/s | Techo PCIe 3.0 con chunk 1024 / 2048 / 4096 / 8192 (tok/s) |
| --- | ---: | ---: | ---: | ---: | --- |
| Q2_0 | 23 192 | 32,1 | 2,56 s | 1,28 s | 399 / 798 / 1597 / 3194 |
| IQ2_XS | 23 160 | 33,4 | 2,67 s | 1,34 s | 383 / 766 / 1532 / 3065 |
| IQ3_XXS | 23 488 | 41,0 | 3,28 s | 1,64 s | 312 / 624 / 1248 / 2497 |
| IQ3_S | 23 646 | 48,4 | 3,87 s | 1,94 s | 265 / 529 / 1058 / 2116 |
| Coder | 11 358 | 23,2 | 1,86 s | 0,93 s | 551 / 1101 / 2203 / 4406 |

Comparación con el cómputo: la 5070 lee a 2 650 / 2 090 / 1 750 / 1 620 tok/s (Q2_0/IQ2_XS/IQ3_XXS/IQ3_S, 8192 de chunk,
`README.md`, PCIe 4.0): muy por debajo del techo (6 388...4 232). Una 3060 12 GB tiene ~0,3-0,45x del cómputo tensorial y
0,54x del ancho de banda de la 5070 [estimado]; con la referencia real de una 3090 que lee IQ3_XXS a 2 160 tok/s y el Coder
a 2 502 a 32K (`bench/results/2026-09-29-rtx3090-epyc-milan/matrix.md`), escalando por ~0,36x de FP32 la 3060 daría
**~800-1 200 tok/s** a chunk 8192 [estimado, con esa 3090 a PCIe 4.0 y la mitad de los expertos ya residentes]. Es decir, 7-10 s de cómputo por chunk frente a 2,6-3,9 s de PCIe 3.0: **compute-bound** (PCIe al
25-40 %). Con chunk 4096 (contextos de 64K-128K en 12 GB): PCIe al 50-76 % (límite). Con chunk <= 2048: **PCIe-bound**.

Política más inteligente: (a) "CPU para expertos con pocos tokens": coste CPU ~ n_tokens x 2,7 ns x 76 800 bloques / (6 núcleos x 1,7)
~ 20 us por token y experto frente a 110 us de DMA en PCIe 3.0 -> solo gana con <= 3-5 tokens por experto, y a chunk 1024 la
media es ~20 (a 8192, ~160); descartada. (b) Chunk mayor: `--prefill auto:16384` ya existe (opt-in, #282, +15 % en una
5090) pero necesita VRAM de buffers que una 3060 no tiene. (c) **Prefill por capas (layer-major):** una capa son 512
expertos (~700 MB); procesar *todos los chunks* de una capa antes de pasar a la siguiente enviaría cada experto una
vez por prompt en vez de una vez por chunk (para 32K = 4 chunks: 110 GB -> ~30 GB) a costa de guardar los estados
ocultos del prompt (40 KB/token FP32: 1,3 GB a 32K, 5,2 GB a 128K) y de rehacer los checkpoints de conversación
(`generate.cpp:5650`). Es la solución de fondo si el enlace limita, pero **para la 3060 no aporta** (es compute-bound). (d) Autoajuste
con la sonda H2D que ya existe: `stream_all_min` y el anillo según el ancho medido -> poco valor, porque por debajo de ~512
tokens "solo enrutados" casi ya es "todos" (a 1024 tokens x 10 / 512 = 20 por experto).

### 2.4 RAM: memoria pinned, modos de poca RAM, SSD, tabla PLE

**Arena de expertos.** `PinnedArena` (`pinned.cu:313-409`): reserva anónima, **solo `MAP_HUGETLB 2 MB` en Linux**
(`:204-226`, falla sin `vm.nr_hugepages` suficientes y cae a 4 KB) o `VirtualAlloc(MEM_LARGE_PAGES)` en Windows (`:62-112`,
requiere `SeLockMemoryPrivilege`, "el caso común es 4 KB"). **Falta `madvise(MADV_HUGEPAGE)`** (THP, sin privilegios; con THP
en modo `madvise`, el predeterminado de Ubuntu, la arena no recibe páginas grandes). Luego `cudaHostRegister` de todo el
rango (o por rodajas por capa si el driver lo rechaza, `:335-380`) y, si no se puede pinnear, `lock_resident` (`mlock`,
`platform/memory.cpp:112-120`; en Linux depende de `ulimit -l`, p. ej. 8 MiB por defecto en esta sandbox).

**Carga.** `load_experts_ranges` (`pinned.cu:568-666`): `fread` buffered por hilo + `memcpy` + **FNV-1a por byte** (`:637`,
~1 GB/s por hilo): irrelevante con SATA (límite 0,5 GB/s: 34 GB ~ 70 s; IQ3_S 50 GB ~ 100 s) y acota ~4 GB/s con 4 hilos en NVMe
rápido. En Windows hay carga sin búfer cuando el SO no puede cachear (`experts_unbuffered`, `:505-566`; #357/#362) con
16 lectores de 8 MiB (`expert_source.cpp:2621`).

**Cuánta RAM hace falta** (`setup.py:111-130, 2023-2058`; margen `LOW_RAM_HEADROOM_GB = 10`; la GPU se supone que guarda
`VRAM - 5` = 7 GB de expertos):

| RAM | Q2_0 (34 GB) | IQ2_XS (35,5) | IQ3_XXS (42,9) | IQ3_S (50,3) | Coder (23,4*) |
| --- | --- | --- | --- | --- | --- |
| 32 GB | no (33 < 34 para `low_ram_fits`) | no | no | no | **sí, modo residente** (23,4-7+10 = 26,4) |
| 48 GB | sí (34+10 = 44) | sí (45,5) | residente (35,9+10 = 45,9) | no | sí |
| 64 GB | sí | sí | sí | sí (60) | sí |

(*) El `arena_gb` del Coder es 23,4 (GiB) mientras que `native_experts.txt` suma 25,15 GB decimales como el resto de
tamaños: la comprobación de ajuste subestima 1,7 GB.

Con 12 GB de VRAM y 128K de contexto sin KV streaming (modo de poca RAM) la KV (13 x 1056 B por token) sale de la VRAM
de expertos (`setup.py:2033-2039`). El modo `--resident-budget-gib N` (presupuesto de RAM + resto leído de la SSD por la
caché del SO con *lookahead* de router, `docs/DETAILS.md:158-168`) solo se ofrece para Unsloth (`setup.py:130`, `"budget": True`).

**Tabla PLE (28,8 GB, `per_layer_token_embd`).** Modo por defecto `Direct` (`ngram.hpp:115-127`, `generate.cpp:224, 2137-2154`):
sin mmap ni caché del SO, filas de 90 B en páginas de 4 KiB (16 filas por token = ~64 KiB leídos por token para 1,4 KB
útiles), 16 hilos con `pread` O_DIRECT en Linux (`direct_file.cpp:263-314`), `--ple-inflight 256`, caché de filas de 1 M filas (95 MB)
y un "keep-alive" de SSD (100 ms). En decode, `Verifier::run` hace `gather_batch` **síncrono antes de lanzar la ventana**
(`verify.cpp:1044-1053`): 16 x T filas en paralelo, SATA a QD16 ~ 50-60 K IOPS -> ~0,3-1 ms por ventana (~1-2 % de una ventana
de 35-70 ms) [estimado]; NVMe ~0,1-0,15 ms. En prefill, 16 x chunk filas (131 K a 8192) ~ 2-3 s en SATA, escondidas tras el
cómputo de la 3060 excepto las del primer chunk (se leen "junto a la capa 0", `prefill.cpp:1424-1432`): ~2-3 s por prompt
frío [estimado]. **Documentación desfasada:** `DETAILS.md:890` y `HOW_IT_WORKS.md:31` hablan de la caché del SO;
`DETAILS.md:895` de chunks de 2048 (hoy 8192).

**Ruta SSD de Unsloth.** `docs/UNSLOTH_Q4.md:219`: el lookahead acierta ~la mitad de las lecturas (+14 %: 6,9 -> 7,9 tok/s);
requiere NVMe ("matters", `:79`). En SATA (0,5 GB/s) cada experto de 1,4-2,2 MB cuesta ~3-4 ms.

---

## 3. Propuestas priorizadas

Orden = valor esperado / esfuerzo, para el perfil "3060 12 GB + CPU AVX2 + DDR4". Las ganancias son sobre tok/s de decode salvo
indicación; la fracción de CPU en la ronda es 39 % (Q2_0), 50 % (IQ2_XS), 55 % (IQ3_XXS) en la 5070 [paper].

| # | Propuesta | Ganancia estimada | Riesgo de calidad | Esfuerzo | Validación (tests del repo + nuevos) |
| --- | --- | --- | --- | --- | --- |
| 1 | **Kernel AVX2 IQ2_S/IQ2_XXS/IQ3_* con decodificación vectorizada por bloque** (índices, escalas, signos; prototipo IQ2_S medido 1,21-1,34x) y usarlo también con nt=1 (`mt_min=1`) | Kernel +21-34 % en IQ2_S [medido-aquí]; IQ2_S son ~50 % de los bytes CPU del pack IQ2_XS (34/48 capas) y ~25 % de IQ3_S/Coder (20/48 capas, ~60 % de su blob) -> CPU -10 % / -5 % -> **+5 % tok/s (IQ2_XS), +3 % (IQ3_S/Coder)**; extender a IQ2_XXS/IQ3_XXS/IQ3_S (no prototipado) podría duplicarlo; con `mt_min=1` además la salida deja de depender del drafting (#152) | **Ninguno**: bit a bit idéntico al kernel AVX2 actual (medido) | Bajo-medio (~100-200 líneas por formato, sin cambiar la aritmética) | Test CPU-only nuevo bit a bit contra `row_dot` actual (mi arnés `prototipos-cpu/iq2s_proto.cpp` es la semilla); `native_expert_parity` (CUDA+ggml: `rg < 1e-5` vs ggml, y el chequeo #152 de nt=1 vs grupo) |
| 2 | **Kernel AVX2 Q2_0 con activaciones en planos** (desentrelazar la activación por stride 4 una vez por capa/experto; 4 AND/shift en vez de 17 uops de desempaquetado) | Kernel **2,3-2,6x** en L2 y 1,6x en streaming/4 hilos [medido-aquí]; en decode el tope es DDR4: Q2_0 queda ~al muro con nt=1 y compute-bound con nt>=2 (30-40 % de los expertos) -> CPU -10..-13 % -> **+4-6 % en Q2_0 AVX2**, +2-4 % en IQ (down Q2_0 = 1/3 de los bytes); además permite usar menos workers (dejar 1-2 núcleos al SO) | Entero exacto; float cambia de orden (|dif| rel. media 3,5e-7 [medido-aquí]), igual que ya difiere AVX2 vs AVX-512 ("only the order of the float additions differs") | Medio (~150 líneas + `ActQ` con planos; mantener el kernel actual como fallback `STRATA_Q2_PLANES=0`) | Prueba CPU-only nueva: planos vs `s2_expert_scalar(quant_acts=true)` y vs kernel actual; `native_expert_parity` (b2: `q2_0 AVX-2 down vs ggml down: rel`); KL teacher-forced 256 tokens (`bench/results/2026-09-28-prefill-speed/README.md`) y `window_logprobs` (`verify.cpp:1267`) |
| 3 | **Controlador de `pcie_frac` + `adapt_swaps` según PCIe medido y velocidad de CPU** (`f* = B_pcie / (B_pcie + B_cpu)`, ajustado por hill-climb en línea con ventanas A/B alternas; `--calibrate` también en la instalación por defecto si PCIe < 20 GB/s; `adapt_swaps` proporcional al ancho medido o `--adapt-every 2 --adapt-swaps 48`) | PCIe 3.0: **+3-10 %** (f real vs 0,34; adapt -1..-3 ms/ventana) [estimado]; PCIe 4.0: 0-2 % | Sin pérdida de calidad (dónde corre un experto cambia el redondeo GPU/CPU, ya aceptado: `DETAILS.md:93-101`); no es bit a bit reproducible entre arranques (como hoy) | Bajo-medio (ya existen `req_pcie_frac` por petición, `generate.cpp:5078`, sonda y `calibrate.py`) | `tools/test_calibrate.py` (CPU); `STRATA_DECODE_TIMING=1`; KL no necesario (mismo cómputo, otro lugar) |
| 4 | **Hilos SMT fijados** (`--pool-smt auto`: tras los núcleos físicos, workers en los hermanos) y candidatos `> físicos` en `calibrate.py` | 0-15 % de la fase CPU en kernels limitados por decodificación/latencia de carga (IQ), ~0 en Q2_0 -> **0-7 %** [estimado, no medible aquí: la VM no tiene SMT] | Ninguno (la fila la calcula cualquier hilo igual) | Bajo (topología ya detectada, `pool.cpp:139-257`) | `pool_test`/`pool_stress` (hoy solo en AVX-512: ampliar a AVX2), `native_expert_parity` |
| 5 | **RAM-budget para Q2_0/IQ2_XS en 32 GB + NVMe** (`--resident-budget-gib`, ya existe para Unsloth) como opción en `setup.py` | Habilita el modelo completo en 32 GB (hoy solo Coder). Cola fría: con 22 GiB en RAM + ~4 400 expertos en VRAM quedan ~3 100 (12,5 %) en SSD, ~1-2 % de las entradas -> 22 lecturas x 1,4 MB por ventana ~ +10 ms NVMe / +60 ms SATA -> **~55-70 tok/s NVMe, ~30-40 SATA** [estimado, depende de la cola real] | Ninguno (mismos bytes) | Medio (setup + validación de `low_ram_fits`) | `tools/test_setup_lowram.py`, `test_setup_choices.py` (CPU, sin descargas); medición `GET /metrics` `file_blobs`/`file_mb` |
| 6 | **Ruta AVX-VNNI (`vpdpbusd` ymm)** para Q2_0/IQ en CPUs con AVX-VNNI y sin AVX-512 (i5-12400+) | 4 `vpdpbusd` sustituyen 4 `maddubs`+3 `add`+`madd` en el kernel de planos: -30 % de uops por token [estimado, solo i5-12xxx] -> +3-5 % solo en esas CPUs | Entero exacto | Medio (detección CPUID 7.1, flags `-mavxvnni`/MSVC, despacho) | Igual que #2 |
| 7 | **`madvise(MADV_HUGEPAGE)` en la arena (Linux)** + documentar `SeLockMemoryPrivilege` en Windows | [medido-aquí] 20,3 vs 20,4 GB/s (mediana de 6, VM con paginación anidada): **sin efecto medible**; no esperar > 3 % hasta medirlo en Zen 2/3 reales | Ninguno | Muy bajo (~10 líneas en `pinned.cu:204-226`) | `AnonHugePages` en `/proc/PID/smaps_rollup` (comprobado: 817 MB con madvise); A/B con `STRATA_NO_LARGEPAGES` |
| 8 | **Lookahead de L3 para la arena en RAM** (reutilizar `RouterLookahead` de `expert_source.cpp:1074-1145` para lanzar `prefetch` de los expertos predichos de la capa siguiente en un hilo SMT durante la fase GPU, cuando la CPU está ociosa) | Hasta -30 % de la fase CPU de Q2_0 si se calientan ~50 % de los bytes de la capa siguiente (precisión ~50 % según Unsloth §219): **<= +3-6 %** [estimado]; ~0 en IQ (compute-bound); en Zen 2 el L3 es por CCX y el reparto dinámico lo estropea | Ninguno (solo prefetch) | Alto | Contadores `warmed`; mismas pruebas de salida |
| 9 | **Adelantar las filas PLE del primer token de la ventana siguiente** (emitir tras la verificación, antes de commit+draft) | SATA: -0,2..-0,5 ms por ventana (<= 1 %); NVMe ~0 | Ninguno | Bajo | `ple_reader_test --selftest`, `ple_parity` |
| 10 | Afinidad CCX (Zen 2) / reubicar el hilo del host | <= 1 % [estimado] | Ninguno | Bajo | `pool_stress` |
| 11 | Prefill layer-major / CPU para expertos con pocos tokens | 0 en 3060 (compute-bound); solo útil con GPU mucho más rápida que PCIe | Cambia órdenes de cálculo (bit a bit si se conserva el orden por capa) | Muy alto | No recomendado |

Notas de priorización:
- Los puntos 1 y 2 son los que tienen **número medido**; 3 y 4 tienen el mayor techo pero necesitan hardware real.
- El punto 5 no acelera nada: *amplía* qué modelos entran en 32 GB (calidad: modelo completo frente al Coder, que el propio
  README califica de débil fuera de código y en CJK).
- Suma realista de 1+2+3+4 sobre una 3060/Zen 3/PCIe 3.0: **+10-20 %** (no son independientes: todos tocan la misma fase CPU
  expuesta, que ya es el 35-50 % de la ronda).

---

## 4. Cambios de bajo riesgo implementables sin GPU

1. **Test CPU-only de los kernels AVX2** (prerrequisito de 1 y 2). Hoy `expert_parity`, `expert_multi_test`, `pool_test` y
   `pool_stress` salen con "no AVX-512" (`pool_test.cpp:66-68`, `expert_parity.cpp:98-101`, `pool_stress.cpp:27-28`,
   `expert_multi_test.cpp:35`) y los kernels AVX2 solo se comprueban dentro de `native_expert_parity` (necesita CUDA + ggml
   + fixtures). Un `tests/core/expert_avx2_test.cpp` que genere pesos sintéticos y compare `q2_0_gguf_rows_multi_avx2` y
   `iq256_*` contra un oráculo escalar (y entre sí, nt=1 vs grupo) cabe en `ctest` sin GPU: es mi arnés `bench_cpu.cpp` +
   comprobación bit a bit.
2. **Kernel IQ2_S vectorizado por bloque** en `src/kernels/cpu/iq_avx2.cpp` (hoy `Fmt32<22>::decode`, `:132-148`, con índices,
   escalas y bits de signo en escalar). Ya existe el patrón en `row_dot_iq2xs` (`:236-316`). Aplicar a IQ2_XXS y a
   IQ3_XXS/IQ3_S (firmas/escala/`qh` en vector) y comprobar bit a bit.
3. **Kernel Q2_0 en planos** en `q2_avx2.cpp` (+ campos `pl/asc/hxp` en `ActQ` rellenados por `act_quant_q8_1_avx2`,
   `q2_avx2.cpp:90-128`) con interruptor de entorno.
4. **`--pool-smt`** en `pool.cpp:139-257` (listar `lps[1..]` como segunda oleada de `worker_cores`) y candidatos
   mayores en `tools/calibrate.py:63-69`.
5. **THP**: `madvise(MADV_HUGEPAGE)` antes del primer acceso en `pinned.cu:204-226`; registrar en el log si
   `AnonHugePages` > 0.
6. **Correcciones de documentación**: `DETAILS.md:890`, `HOW_IT_WORKS.md:31` (PLE es O_DIRECT, no caché del SO);
   `DETAILS.md:895` (chunks de 8192); `setup.py:125` (`arena_gb` del Coder 23,4 en GiB frente a 25,15 GB decimales).
7. **Controlador de `pcie_frac`**: la parte de política (fórmula `f*`, escalado de `adapt_swaps`, candidatos de calibración)
   es pura y testeable sin GPU (`tools/test_calibrate.py`); el bucle en línea requiere GPU para validarse.
8. **Documentar en `AI_SETUP.md`/`MODELS.md`** que "Q2_0 en CPU sin AVX-512" no tiene cifras publicadas (las tablas son de
   AVX-512 canónico) y que las 3090/EPYC (AVX2) publicadas son IQ3_XXS/Coder/IQ3_S.

---

## 5. Mediciones propias (scratchpad, nada tocó el repo)

Entorno: VM KVM, Xeon Cascade Lake (núcleo Skylake-SP, 4 vCPU sin SMT, L2 1 MB/núcleo; AVX2, AVX-512 F/BW/VL/DQ/VNNI, **sin VBMI**,
así que el kernel canónico no corre), reloj ~2,4-2,8 GHz (test de sumas dependientes: 2,38-2,47 GHz), lectura DRAM simple
9,4 / 16,5 / 34,5 GB/s con 1 / 2 / 4 hilos, ruido de VM de +-10-20 %. Se compilaron con `g++ 13.3 -O3 -mavx2 -mfma -mf16c`
los **ficheros del repo sin modificar** (`q2_avx2.cpp`, `iq_avx2.cpp`) junto a arneses propios, con bytes aleatorios válidos.
Código: `prototipos-cpu/`
(`bench_cpu.cpp`, `iq4nl_bench.cpp`, `iq2s_proto.cpp`, `q2plane_proto.cpp`/`q2plane_proto2.cpp`, `q2plane_thp.cpp`, `aq_bench.cpp`, `memread.cpp`).

| Medida | Resultado |
| --- | --- |
| Kernels AVX2 del repo en L2 (GB/s de pesos por núcleo; nt=1/2/3) | Q2_0 3,81/2,70/2,21; IQ2_XS 2,70/2,26/2,08; IQ3_XXS 2,90/2,61/2,24; IQ3_S 2,39/1,76/1,76; IQ2_S 2,12/1,73/1,54; IQ4_XS 8,01/6,25/4,27; IQ4_NL down 5,98/4,38/3,42 |
| Streaming 4 hilos nt=1 (pesos de DRAM, 4 KB) | Q2_0 9-13; IQ2_XS 9,2; IQ3_XXS 8,9; IQ3_S 8,1; IQ2_S 6,9; IQ4_XS 27 GB/s en total (el IQ cae ~15 % respecto a L2 por latencia, aunque la VM da 34 GB/s a una lectura simple) |
| **Q2_0 en planos** vs kernel del repo, L2, nt=1/2/3/4 | x2,58 / 2,33 / 2,38 / 2,48 (2.ª pasada: 2,52/2,34/2,31/2,53); dif. relativa media 3,5e-7 |
| Q2_0 en planos, streaming 4 hilos, nt=1 (mediana de 6 pasadas) | repo 12,5 -> planos 20,3 GB/s total; nt=3: 4,2-7,5 -> 8,6-11,2 |
| **IQ2_S por bloques** vs kernel del repo, L2, nt=1/2/3/4 | x1,27 / 1,21 / 1,23 / 1,34; **0 floats distintos** (8 semillas x nt 1,2 + nt 1-4) |
| Variante con `vpgatherdq` para la rejilla | x0,39 (2,5x más lenta): confirma lo que midió el repo en AVX-512 (`iq_avx512.cpp:31-33`); además las CPUs Skylake con el microcódigo anti-Downfall (i5-10400) penalizan las gathers |
| THP (2 MB) vs 4 KB en el kernel en planos, 6 pasadas alternadas | 20,35 vs 20,3 GB/s (mediana): **sin diferencia** (la VM paga paginación anidada; no concluyente para Zen 2/3 reales) |
| `act_quant_q8_1_avx2` | 1,52 us / 2560 valores; 0,38 us / 640 |

Límites: reloj y IPC de Zen 2/3/Golden Cove son suposiciones (factor = GHz/2,6 x IPC relativo ~1,0-1,35); no pude medir
`vec_dot` de ggml (sin red a llama.cpp) ni el efecto SMT (VM sin hermanos); el prototipo de planos hace la preparación
de la activación en escalar (fuera de la medida: ~2 us por 2560 valores si se vectoriza ~0,3 us).

---

## 6. Preguntas abiertas

1. **¿Cuánto rinde el Q2_0 AVX2 real?** El repo no publica ninguna cifra de Q2_0 en CPU sin AVX-512 (las tablas son del kernel
   canónico en Zen 4). Hace falta una medición en un Ryzen 5 3600/5600 y un i5-12400 (`STRATA_DECODE_TIMING=1`).
2. **¿`vec_dot` de ggml frente al kernel Strata a nt=1 en AVX2?** Determina si `mt_min=1` + kernel mejorado gana también a 1 token.
3. **¿`--pcie-mode dma` o `kernel` en PCIe 3.0?** (y el reparto óptimo; `--calibrate` en la 3060).
4. **¿Hit-rate real de una 3060 12 GB?** Los números del paper son de una 5070; la VRAM libre y el escritorio cambian el tamaño
   de la caché (~4 400 expertos Q2_0).
5. **¿SMT ayuda a los i-quants en Zen 3?** y **¿ayudan las páginas grandes en Zen 2/3 reales?** (aquí no medible).
6. **¿Coste real del PLE con SATA?** El motor imprime `ple io: ... read p50/p99 ... blocked ... ms` (`ngram.cpp:376-391`): comprobarlo
   en una instalación SATA con prompt frío y con decode.
7. **Cola fría del reparto de expertos** (cuántas entradas caen en el 12,5 % menos usado): la estimación del modo RAM-budget
   para 32 GB depende de ella (1-2 % supuesto).
8. **Variabilidad por RAM no XMP**: el propio `DETAILS.md:869` advierte que DDR4 a 2133/2400 sin XMP reduce el tope; en un i5-10400
   en H410 (DDR4-2666 máx.) el muro baja a ~34 GB/s.
9. **Coder en 32 GB**: ¿se puede reducir `LOW_RAM_HEADROOM_GB = 10` en Linux sin entorno gráfico para dejar sitio a contextos mayores?
