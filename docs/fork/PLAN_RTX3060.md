# Plan del fork: Strata a medida de una RTX 3060

Fecha: 2026-10-03, sobre Strata 0.1.38 (commit `99f3dbd`, idéntico a `Niko1221/Strata` `main`). Estado:
**auditoría y plan; todavía no hay cambios en el motor ni en `setup.py`**. Volver al [índice](README.md).

Cómo leer los números (la regla del repo, `AGENTS.md`: nada sin medición):

- **[medido]**: medido en el repo, en un issue o por un agente en este trabajo, con dónde.
- **[est.]**: estimación con la aritmética en el informe enlazado. **Ninguna cifra de este plan está medida en una
  RTX 3060.** La Fase 0 existe para cambiar eso.

Las ganancias de cada propuesta se miden sobre tokens/s de decode (escribir la respuesta) salvo que se diga otra
cosa, y **no se suman**: varias atacan la misma parte de la ronda.

## El hardware objetivo

| | RTX 3060 12 GB (objetivo) | RTX 5070 12 GB (referencia de las docs) |
| --- | --- | --- |
| Arquitectura | Ampere, sm_86, 28 SM | Blackwell, sm_120, 48 SM |
| Ancho de banda de VRAM | 360 GB/s | 672 GB/s |
| Caché L2 | 3 MB | 48 MB |
| Clusters de bloques / FP8 | no / no | sí / sí |
| PCIe | 4.0 x16 (a menudo en placa 3.0: B450, A320, H410) | 5.0 x16 |
| CPU típica | Ryzen 5 3600 / 5600, i5-10400 / 12400: **AVX2, sin AVX-512** | Ryzen 5 7600: AVX-512 |
| RAM típica | 32 GB DDR4-3200 (~40-50 GB/s), a veces 64 GB | 64 GB DDR5-5200 |

También se considera la RTX 3060 de 8 GB (128 bits, 240 GB/s).

## Qué limita a una 3060 (resumen de los 7 informes)

1. **El tiempo de cada ronda lo marca la CPU, no la GPU.** Una ronda de decode es, por capa: cadena GPU (residual,
   mezclador, router) -> la CPU calcula los expertos que no están en VRAM mientras la GPU calcula los que sí ->
   combinar. Con 12 GB, la parte de la GPU tras el reparto queda oculta bajo la de la CPU
   ([02](informes/02-cuda-sm86.md)). La CPU es el 39-55 % de la ronda en la 5070 [medido, paper tabla 5], y en una
   3060 con CPU AVX2 y DDR4 será más. **Afinar los kernels de expertos en VRAM no da casi nada; acelerar los de la
   CPU, sí.**
2. **La ruta AVX2 de la CPU está mucho menos optimizada que la AVX-512.** Los i-quants (IQ2_XS, IQ3_*) están
   limitados por decodificar los pesos (~1 GB/s por GHz y núcleo), no por la DDR4 [medido por el agente 03 en un Xeon
   a 2,6 GHz]. No hay ruta AVX-VNNI para el i5-12400 ([03](informes/03-host-cpu-ram-pcie.md)).
3. **En VRAM ya no queda "grasa" grande.** El reparto de memoria se reproduce en papel con un error de 0-5 MiB frente
   a lo medido en la 5070. En una 3060 de 12 GB caben **~4.060 expertos IQ2_XS en Windows con el monitor en la GPU,
   ~4.640 en Linux sin escritorio**, y **~1.080 / ~1.660 en la de 8 GB** [est.]. No hay ningún ahorro sin pérdida
   mayor de 300 MiB; quedan pepitas de 90-250 MiB ([01](informes/01-vram.md)). Cada 1.000 expertos más valen
   ~+4 % con IQ2_XS y ~+15 % con IQ3 [medido en la 5070].
4. **El contexto ya está resuelto en el motor, pero no en la configuración.** Con KV streaming, la VRAM que ocupa un
   contexto de 128K es la de 32K: solo las 32K posiciones más leídas quedan en VRAM. Los 262K caben en 12 GB con
   ~3.700-3.900 expertos en caché ([04](informes/04-decode-spec-context.md)). Pero `setup.py` recomienda 32K a toda
   tarjeta de menos de 14 GB.
5. **La configuración por defecto de `setup.py` no es la que dicen las docs para una 3060 con 32 GB**
   ([05](informes/05-setup-profile.md)). Instala Qwen Q2_0 leyendo los expertos del SSD (`--mmap-experts`, con la GPU
   guardando ~21 %), cuando el README y `docs/MODELS.md` recomiendan el Coder.
6. **Las funciones de RTX 40/50 que una 3060 no tiene cuestan poco.** El top-k de la atención y el argmax usan
   clusters (solo sm_90+); en sm_86 caen a un bloque por fila. Cuesta ~0,2 % a 4K, ~1,5 % a 128K y ~6,5 % a 262K
   [est. por microbenchmarks del repo] ([02](informes/02-cuda-sm86.md)).
7. **Un build manual para la 3060 no funciona.** Un `cmake` manual sin arquitectura usa `120` (`CMakeLists.txt:78`), y
   ese binario no sirve en una 3060. `setup.py` sí compila sm_86 cuando compila.

**Qué esperar hoy (sin cambios):**

- Q2_0 a 4K con la CPU de la 5070: ~60-81 tokens/s [est.], frente a 94,6 medidos en la 5070.
- Con una CPU AVX2 y DDR4: menos, sin cifra todavía.
- Medido en la comunidad: RTX 3060 12 GB + Ryzen 7 7800X3D (AVX-512) + 64 GB, Swift IQ3_XXS a 128K: **30-35 tokens/s**
  (issue #534 de upstream).
- Otro Ampere con CPU AVX2: RTX 3090 + EPYC Milan (Zen 3), IQ3_XXS a 4K, **89,2 tokens/s**
  (`bench/results/2026-09-29-rtx3090-epyc-milan`).

## Fase 0: medir antes de tocar

Sin una 3060 en el banco de pruebas, nada de lo que sigue se puede validar. Lo primero:

1. **Un banco de pruebas:** RTX 3060 12 GB + Ryzen 5 5600 + 2 x 16 GB DDR4-3200, en una placa B550 (PCIe 4.0) y otra
   B450 (PCIe 3.0); Windows 11 y Linux. Repetir con 64 GB. Si se puede, una 3060 de 8 GB.
2. **La línea base con el motor de upstream (0.1.38):** `tools/needle_bench.py` y la matriz de `bench/` a 4K, 32K y
   128K con Coder, Q2_0, IQ2_XS e IQ3_XXS.
3. **Las trazas que deciden el orden de lo demás:**
   - `STRATA_VERIFY_PROFILE=1 --stats` para el tiempo por etapa;
   - `STRATA_DECODE_TIMING=1` para las esperas GPU/CPU;
   - `STRATA_PREFILL_TIMING=1`;
   - el log `decode expert cache hit rate`;
   - `cuobjdump --dump-resource-usage` de los kernels "de una ola" (lista en [02](informes/02-cuda-sm86.md));
   - una traza de rutado con `--dump-routing` para el simulador de caché de la Fase 3.
4. **A/B de interruptores que ya existen:**
   - `STRATA_ADAPT_NOWAIT=1`;
   - `--pcie-frac` 0,15 / 0,25 / 0,34;
   - `--pcie-mode dma`;
   - `STRATA_PF_FUSED=1`;
   - `--spec-min-p` 0,3-0,9;
   - `--draft-vocab en`;
   - `--kv-resident 20480` con contexto de 32K.

Esfuerzo: ~1 semana con el hardware. Publicar los resultados en `bench/results/` como hace upstream.

## Fase 1: configuración (`setup.py`), sin tocar el motor

Todo se prueba sin GPU con `python tools/test_setup_<nombre>.py`. El agente 05 dejó un prototipo con 19 tests nuevos
que pasan; **no está aplicado**, pendiente de aprobación. Se detecta por VRAM (de 7,5 a 14 GB), RAM y CPU, nunca por
el nombre de la tarjeta, así que las recomendaciones para tarjetas de 16 GB o más no cambian (los tests golden lo
comprueban).

| # | Cambio | Por qué | Ganancia | Calidad |
| --- | --- | --- | --- | --- |
| 1.1 | **12 GB + 32 GB de RAM -> el Coder** (expertos copiados en RAM, `--resident-experts`) | hoy: Q2_0 leído del SSD; las docs ya dicen Coder | evita un modo limitado por el SSD | el Coder es más débil fuera de código y en chino/japonés/coreano; Q2_0 sigue disponible con `--model` |
| 1.2 | **Arreglar la prueba de RAM del KV streaming** (`setup.py:3572`) | usa la RAM "necesaria" del tamaño, no la de la arena: un Linux con 64 GB (62,7 GiB) con IQ3_XXS a 128K deja 1,8 GB de KV en VRAM sin avisar | ~1.260 expertos más en ese caso | ninguna |
| 1.3 | **128K de contexto por defecto en 12 GB cuando la RAM lo permite** (`--kv-resident 32768`) | con streaming, la VRAM es la de 32K | x4 de contexto; 5070: 82 -> 74 tokens/s con un prompt de 32K -> 128K [medido] | ninguna (mismo KV de 8 bits) |
| 1.4 | **Subconjunto del draft según el idioma del sistema** en tarjetas de menos de 14 GB (`en` salvo chino/japonés/coreano o cirílico) | el cabezal del subconjunto por defecto (CJK) ocupa hasta ~348 MiB de VRAM y el `en` hasta ~133 (`setup.py:2753`, IQ3_S); con IQ2_XS, ~110 MiB menos (`docs/DETAILS.md:105`) | +80-155 expertos, +1,3-2 % [est.] | ninguna: el draft solo propone, el modelo decide |
| 1.5 | `fit_max_tokens: true` en tarjetas de menos de 14 GB | con 32K, un agente que pide 32K de salida recibe un 400 | menos errores | ninguna |
| 1.6 | `--check` avisa de la VRAM que usa el escritorio, de un enlace PCIe 3.0 y de la falta de AVX-512 | el monitor en la salida de vídeo de la placa base (solo con CPU con gráficos integrados: i5, no Ryzen 3600 / 5600) devuelve 0,5-1,2 GB = +365-870 expertos [est.] | consejo | ninguna |
| 1.7 | `CMakeLists.txt`: por defecto `86;120` en vez de `120`; `Dockerfile`: 86 | un build manual hoy no corre en una 3060 | arranca | ninguna |
| 1.8 | `tools/test_setup_golden.py`: arreglar `normalize()` en Linux | hoy fallan 46 subtests en Linux (`setup.EXE` = "strata" se sustituye en cada ruta) | tests | - |

Esfuerzo: 3-5 días con los tests.

## Fase 2: la CPU (el mayor margen en un PC barato)

Todas son sin pérdida: o idénticas bit a bit, o solo cambian el orden de sumas en coma flotante, la misma clase de
diferencia que ya hay entre las rutas AVX-512 y AVX2. Detalle: [03](informes/03-host-cpu-ram-pcie.md). Prototipos
compilables en [informes/prototipos-cpu](informes/prototipos-cpu).

| # | Propuesta | Ganancia | Calidad | Esfuerzo |
| --- | --- | --- | --- | --- |
| 2.1 | **Kernels AVX2 de IQ2_S / IQ2_XXS / IQ3_\* con la decodificación vectorizada por bloque** (`src/kernels/cpu/iq_avx2.cpp`) | kernel IQ2_S **x1,21-1,34 [medido, prototipo]**; **+3-5 %** tokens/s [est.] (IQ2_S es el gate/up de 34 de 48 capas del pack IQ2_XS) | **idéntico bit a bit** [medido, 8 semillas] | 1-2 semanas |
| 2.2 | **Kernel AVX2 de Q2_0 con activaciones "en planos"** (`q2_avx2.cpp`) | kernel **x2,3-2,6 en L2, x1,6 en streaming [medido, prototipo]**; +4-6 % con Q2_0, +2-4 % con IQ [est.] | orden de sumas (diferencia relativa media 3,5e-7 [medido]) | 1-2 semanas |
| 2.3 | **Reparto GPU/CPU (`pcie_frac`) y copias del tier adaptativo según el PCIe y la CPU medidos** | PCIe 3.0: **+3-10 %** [est.]; el óptimo para Q2_0 está cerca de 0,15-0,25, frente al 0,34 actual | ninguna (cambia dónde corre un experto, como hoy) | 1-2 semanas |
| 2.4 | Hilos SMT fijados en los núcleos hermanos (`--pool-smt`) | 0-7 % [est.] | ninguna | días |
| 2.5 | Ruta AVX-VNNI (`vpdpbusd` en ymm) para i5-12400 y posteriores | +3-5 % solo en esas CPUs [est.] | entero exacto | 1 semana |
| 2.6 | **Tests de los kernels AVX2 sin CUDA** | hoy `pool_test`, `expert_parity` y `expert_multi_test` salen con "no AVX-512": nada prueba la ruta AVX2 sin GPU | requisito de 2.1-2.5 | días |
| 2.7 | `madvise(MADV_HUGEPAGE)` cuando no hay `MAP_HUGETLB` (`src/core/pinned.cu:204-226`) | sin efecto medible en la VM [medido]; medir en Zen 2/3 | ninguna | horas |

Suma realista de 2.1-2.4: **+10-20 %** [est.].

## Fase 3: VRAM y la caché de expertos (sin pérdida)

Detalle: [01](informes/01-vram.md).

| # | Propuesta | Ganancia | Esfuerzo |
| --- | --- | --- | --- |
| 3.1 | **Aprender del prompt:** la caché adaptativa solo aprende del decode y tira el rutado del prompt, que el prefill ya cuenta (`src/prefill/prefill.cpp:2057`). Tras un prompt largo, el motor además recopia ~3.000 expertos tal cual a la caché (`src/program/generate.cpp:5462-5474`). Sembrar la caché con el histograma del prompt cuesta 0 MiB | +2-8 % en respuestas cortas tras prompts largos [est.]; en la 5090 la primera petición corta acierta 90,8 % frente a 99 % en generaciones largas [medido] | 1 semana |
| 3.2 | **Quitar la copia del cabezal del draft:** `src/core/mtp.cpp:443` copia 138-213 MiB de filas que ya están en el cabezal principal; un GEMV con índice de filas lee el original | +100-155 expertos, +0,4-0,8 % (más con IQ3, Coder y 8 GB) [est.]; idéntico bit a bit | 1 semana |
| 3.3 | **Copias del tier adaptativo sin bloquear** (#463: espera ~136 MB cada 4 rondas, ~11 ms en PCIe 3.0) | hasta ~3-5 % en PCIe 3.0 [est.]; se solapa con 2.3 | días |
| 3.4 | **Política de caché para una caché pequeña** (desempate por perfil, contador con decaimiento, núcleo protegido), afinada con un simulador sobre trazas de `--dump-routing` | +1-3 % (12 GB), más en 8 GB [est.] | 1-2 semanas |
| 3.5 | Corregir el sobreconteo de `Prefill::bytes_needed` (cuenta `T·D` en fp32 que no reserva) | 320 MiB menos de préstamo con trozos de 8192 | horas |
| 3.6 | Reserva de VRAM menor en Linux sin escritorio (450-550 MiB en vez de 700) | +110-180 expertos [est.] | horas |

Para la **3060 de 8 GB** la misma lista, más un contexto de 16K y el anillo de prefill de 96, da **+15-25 %** [est.]
sobre una configuración por defecto que estimamos en ~20-30 tokens/s.

## Fase 4: la adivinanza MTP (sin pérdida)

Detalle: [04](informes/04-decode-spec-context.md). La longitud del borrador no usa un modelo de coste. Es un corte
fijo por probabilidad (`--spec-min-p 0.5`), elegido con las proporciones de coste de la 5070.

| # | Propuesta | Ganancia | Esfuerzo |
| --- | --- | --- | --- |
| 4.1 | **`--calibrate` más amplio y automático:** `--spec-min-p` de 0,3 a 0,9 (hoy 0,3 / 0,5 / 0,7), profundidad MTP, y lanzarlo en el primer arranque cuando la GPU no es de clase 5070 | +1-4 %; hasta +9 % con una CPU lenta [est., simulado] | días |
| 4.2 | Longitud del borrador por modelo de coste con calibración en línea | +1-4 %, +5-10 % si mandan la CPU o el PCIe [est.] | 2 semanas |
| 4.3 | Medir la velocidad con muestreo (la web app usa temperatura 0,6; todo lo publicado es greedy) y activar el borrador acoplado (`STRATA_SPEC_COUPLED`) si gana | +3-10 % a temperatura ≥ 0,6 [est., sin medir] | 1 semana |

No recomendado: árboles de borradores (el coste marginal por token es 0,2-0,3 de la ronda) y precargar expertos con
el router del MTP (tiene sus propios 512 expertos, no predice los del modelo).

## Fase 5: kernels CUDA para sm_86 (idénticos bit a bit)

Detalle: [02](informes/02-cuda-sm86.md). La 3060 tiene los mismos recursos por SM que la 5070. Ningún kernel depende
de una L2 grande, así que los lanzamientos afinados en la 5070 valen por SM.

| # | Propuesta | Ganancia | Esfuerzo |
| --- | --- | --- | --- |
| 5.1 | Top-k de selección y argmax en varios bloques sin clusters (mismo orden total, mismos ids) | 0,2 / 1,5 / 6,5 % a 4K / 128K / 262K [est.]; el repo documenta +5 % / +18 % de extremo a extremo en la 5070, sin explicar del todo: medir primero | 1 semana |
| 5.2 | `__launch_bounds__` con bloques mínimos en los kernels "de una ola": `gdn_rec_kh_kernel` necesita 3 bloques por SM con 28 SM, si no cae sin avisar al kernel lento (`src/prefill/kernels.cu:578-594`) | prefill +2-5 % si hoy cae [est.] | días |
| 5.3 | MMVQ con 2 filas por bloque y el mismo orden de suma por fila | +2-6 % [est.] | 3 días |
| 5.4 | Ramas paralelas en el CUDA graph (GDN y el indexador QSA) | ~+2 % [est.] | 1 semana |
| 5.5 | Fusionar lanzamientos pequeños (~200 nodos por ventana) | ~+1 % [est.] | 1-2 semanas |
| 5.6 | Orden de bloques para la L2 en el scorer de prefill | prompts largos +2-5 % (cota) [est.] | días |
| 5.7 | Ajuste por "datos del dispositivo" (SM, L2, ancho de banda vía `cudaDeviceGetAttribute`) en vez de por compute capability | 1-3 % [est.] | días |

`STRATA_PF_FUSED=1` en sm_86 (prompts más rápidos con IQ2_XS en la 5070, +12 % a 4K) cambia los números, aunque ya
está validado por KL. Se mide en la Fase 0 y no se activa sin medir.

## Lo que no se hará (pierde calidad o no gana)

| Idea | Por qué no |
| --- | --- |
| KV cache de 4 bits (`--kv q4_0`) por defecto | perplejidad +8-12 %, KL 2,6-4,2 veces la del KV de 8 bits [medido, `bench/results/2026-09-27-kv-q4`] |
| Claves del indexador en FP16 | cambia qué bloques selecciona la atención |
| Quitar expertos a la capa MTP | baja los tokens por ronda, que valen 1,6-1,8x |
| Árboles de borradores | negativo con este coste por token |
| L2 persistente, GEMV en tensor cores, FP8, clusters | no existen en sm_86, o miden peor (`bench/results/2026-09-30-tc-gemv-sm75`) |
| Compresión sin pérdida de los pesos BF16 (tipo DFloat11) | añade trabajo a un GEMV ya limitado por ancho de banda |

## Qué ganaría el fork, en total

| | Hoy (upstream) | Con las fases 1-5 |
| --- | --- | --- |
| Configuración con 32 GB | Q2_0 desde el SSD | el Coder en RAM (o Q2_0 a elección) |
| Contexto por defecto en 12 GB (con 48-64 GB de RAM) | 32K | 128K, misma VRAM |
| Decode | línea base de la Fase 0 | **+15-30 %** [est.; las ganancias no se suman] |
| 3060 de 8 GB | arranca, lenta | +15-25 % más sobre eso [est.] |
| Calidad | la del modelo | la misma: todo es sin pérdida o con el redondeo GPU/CPU que ya existe |

## Mantener el fork

- **El motor que se descarga es el de upstream.** `PREBUILT_URL` apunta a `Niko1221/Strata/releases` (`setup.py:101`).
  Mientras el fork solo cambie `setup.py` y docs, vale. En cuanto cambie el motor, hace falta una de dos cosas: releases
  propios (un workflow de GitHub Actions que compile sm_86 y sm_120), o que setup compile siempre (`--build`, solo
  sm_86, 10-20 minutos una vez).
- **Upstream se mueve rápido**: de la 0.1.25 a la 0.1.38 entre el 2026-09-29 y el 2026-10-03. Casi todo lo de este plan es una mejora
  general, no algo exclusivo de la 3060: kernels AVX2, aprender del prompt, quitar la copia del draft, el arreglo de
  `setup.py`. Mandarlo upstream como PR reduce el coste de mantener el fork. Lo exclusivo del fork es el perfil
  de configuración.
- **Errores de documentación encontrados en upstream:**
  - la tabla n-gram se lee con E/S directa, no "a través de la caché del SO" (`docs/DETAILS.md:890`,
    `docs/HOW_IT_WORKS.md:31`);
  - "8-bit KV above 4K", pero `setup.py` usa fp16 hasta 8K;
  - el comentario de `CMakeLists.txt:7-9` (sm_80) está desfasado.

## Orden recomendado

1. **Fase 0** (medir) y **Fase 1** (configuración): días, y arreglan lo que más se nota al instalar.
2. **2.6 -> 2.1 -> 2.2** (CPU), **3.2** y **3.1** (VRAM): las de mayor ganancia con calidad idéntica.
3. **2.3 / 3.3** (PCIe), **4.1** (calibración): necesitan el banco de pruebas para afinar.
4. **Fase 5** (kernels), cuando la Fase 0 diga cuánto pesa cada etapa.
5. GLM: ver la [auditoría](GLM_AUDITORIA.md); no antes de tener los puntos 1-2.

## Informes completos

Los siete informes de los agentes, con citas `archivo:línea`, tablas y preguntas abiertas:

| Informe | Tema |
| --- | --- |
| [01-vram](informes/01-vram.md) | Reparto de VRAM en 12 / 8 GB, caché de expertos |
| [02-cuda-sm86](informes/02-cuda-sm86.md) | Kernels CUDA y build en Ampere sm_86 |
| [03-host-cpu-ram-pcie](informes/03-host-cpu-ram-pcie.md) | CPU AVX2, DDR4, PCIe 3.0 / 4.0, SSD; prototipos medidos |
| [04-decode-spec-context](informes/04-decode-spec-context.md) | Ronda de decode, MTP, KV cache y contexto |
| [05-setup-profile](informes/05-setup-profile.md) | `setup.py`, servidor, perfil RTX 3060, evidencia de la comunidad |
| [06-glm-research](informes/06-glm-research.md) | GLM-5.3-Flash: arquitectura, memoria, soporte, líneas base |
| [07-portability](informes/07-portability.md) | Qué es específico de Qwen y cómo portar a GLM |
