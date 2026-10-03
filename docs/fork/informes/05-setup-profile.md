# 05 - Auto-configuración (setup.py / serve) para un PC con RTX 3060 y evidencia de la comunidad

Auditor 5 de 7. Alcance: `setup.py`, `serve/`, docs y evidencia pública. El repo `/home/user/Strata3060` NO se modificó
(`git status` limpio). Todo lo experimental se hizo en una copia en el scratchpad
(`.../scratchpad/work/Strata`). Los números de línea de `setup.py` son los del repo original (HEAD `99f3dbd`).

Archivos que entrego junto a este informe (en `.../scratchpad/reports/`):

- `05-setup-profile.setup.patch`: prototipo de los cambios de `setup.py` (+ el arreglo de `normalize()` del test golden).
- `05-test_setup_budget.py`: 19 tests nuevos (pasan; ver sección de tests). Va a `tools/test_setup_budget.py`.

> Nota del fork: ni el parche ni los tests están aplicados en el repo todavía. Los cambios se describen en
> [docs/fork/PLAN_RTX3060.md](../PLAN_RTX3060.md#fase-1-configuración-setuppy-sin-tocar-el-motor), pendientes de aprobación.

Convención: **(MEDIDO)** = aparece medido en el repo o en un issue; **(ESTIMACIÓN)** = lo calculé yo a partir de ratios,
sin medición en una 3060; **(SIMULADO)** = salida real de `setup.main()` con hardware simulado (arnés de
`tools/test_setup_golden.py`), que prueba lo que setup *decide*, no la velocidad.

---

## Resumen

1. **El camino "a ciegas" (`--yes`, o Enter en todo) en una 3060 12 GB + 32 GB RAM instala la opción equivocada.**
   Elige familia `qwen` (`setup.py:3196`, default `"1"`) y tamaño `Q2_0` (`setup.py:3221`), lo deja en modo
   `--mmap-experts` con "la GPU guarda ~21 % de los expertos" y solo avisa con un `[!]` (`setup.py:3357-3368`). Las
   propias docs dicen que con 32 GB la opción es el Coder (`README.md:101`, `docs/MODELS.md:14`, `docs/AI_SETUP.md:72`),
   que sí entra en modo `--resident-experts`. (SIMULADO, sección 1a.)
2. **Con 64 GB el default del código es `IQ3_XXS`** (`setup.py:3221`, `ram >= 60`) mientras las docs dicen `IQ2_XS`
   (`README.md:103`, `docs/MODELS.md:16`, `docs/AI_SETUP.md:74`). En una 3060 el dato comunitario medido es
   30-35 tok/s con Swift IQ3_XXS a 128K, 3060 + 7800X3D + 64 GB (issue #534).
3. **Contexto usable:** el default es 32K para tarjetas <14 GB (`setup.py:3266`), pero desde 64K el KV se "streamea" y en VRAM
   solo quedan 32 768 celdas (`--kv-resident 32768`, `setup.py:3586`; ayuda del motor `generate.cpp:459-461`). O sea
   64K/128K cuestan la misma VRAM que 32K y solo cuestan RAM (0,9 / 1,8 GB). Peor: el test de RAM para activar el
   streaming (`setup.py:3572`, `ram >= ram_gb + KV + 1`) usa el "needs" del tamaño (60 GB IQ3_XXS, 62 IQ3_S) y **falla en
   un PC Linux de "64 GB" (62,7 GiB)**: a 128K con IQ3_XXS el KV completo (1,8 GB) se queda en VRAM, sin ningún
   aviso, y en Windows (63,9 GiB) sí hace streaming. En una tarjeta de 12 GB esos 1,35 GB extra son ~30 % de la caché de
   expertos. (SIMULADO.)
4. **Faltan detecciones baratas y muy relevantes para este PC:** VRAM ya usada por el escritorio / `display_active`,
   generación del enlace PCIe, canales y velocidad de RAM (XMP), tipo de disco, comprobación de RAM *libre* (no solo total)
   antes de arrancar. Hoy nada de eso se consulta (`gpus()` solo pide 5 campos, `setup.py:385-397`).
5. **El motor ya hace bien dos cosas que no hay que tocar:** sondea el PCIe y reduce `--pcie-frac` en proporción
   por debajo de 20 GB/s (`generate.cpp:1058-1062`, `1824-1837`: PCIe 3.0 x16 ≈ 12-13 GB/s da ≈ 0,34 en vez de 0,55, ESTIMACIÓN
   de la cuenta), y usa por defecto un worker por **núcleo físico** (`generate.cpp:260`, `586-589`), no por hilo SMT.
6. **Perfil propuesto** (sección 4): se activa por VRAM (8-14 GB) + RAM + AVX + PCIe, nunca por nombre. Prototipado
   y probado: 19 tests nuevos, y los golden de las tarjetas de 16 GB o más no cambian.
7. **Tests actuales:** 9 de 11 módulos `test_setup_*` pasan en Linux sin tocar nada (10 de 11 con el arreglo del golden). Dos fallos que ya existían y son específicos de
   Windows: `test_setup_amd::test_prebuilt_hip_zip` (el test mete `strata.exe` en el zip, en Linux `EXE="strata"`) y
   `test_setup_golden` (46 subtests; `normalize()` reemplaza `setup.EXE` también dentro de `strata-<modelo>.log`; con un
   cambio de una línea pasa entero). Detalle en la sección 5.
8. **Evidencia 30-series/12 GB:** casi no hay. Un solo dato directo de 3060 12 GB (#534). Lo más parecido es el
   issue #610 (RTX A3000 12 GB, GA104 sm_86, 336 GB/s, casi la misma clase que la 3060). Nada en
   `docs/COMMUNITY_BENCHMARKS.md` ni en `bench/results/` (solo RTX 5090 y 3090). Hay que medir (sección 6).

---

## 1. Qué hace setup.py hoy, paso a paso

### 1.0 Común a todos los casos

| Aspecto | Qué hace | Dónde |
| --- | --- | --- |
| Detección GPU | `nvidia-smi --query-gpu=index,name,memory.total,compute_cap,driver_version`. Nada más: ni SMs, ni ancho de bus, ni PCIe, ni `display_active`, ni `memory.used`. | `setup.py:385-397` |
| GPU admitida | `compute_cap >= 7.5`. Una 3060 es sm_86. Con 2 tarjetas distintas pregunta; con una sola, esa. | `setup.py:423-432`, `557-593` |
| Driver | exige >= 580 (CUDA 13.0); si no, `fail()`. Es un bloqueo real en 3060 con drivers viejos. | `setup.py:106`, `3139-3141` |
| Aviso VRAM | `if vram_gb < 11: warn("less than 12 GB ... slow")`. Una 12 GB (12,0 GB) no recibe ninguna nota. | `setup.py:3142-3143` |
| RAM | `ram_gb()` (GiB; un "32 GB" lee ~31,9). Parada si `ram < 28` y ni el Coder cabe en modo low-RAM. | `setup.py:281-287`, `3144-3161` |
| Page file (Windows) | solo avisa si es < 4 GB. | `setup.py:3162-3166` |
| CPU | solo AVX2/AVX-512. No se ajustan hilos. | `setup.py:300-320`, `3167-3169` |
| Binario del motor | **descarga el zip prefabricado** (`strata-windows-x64.zip` / `strata-linux-x64.zip`) de `github.com/Niko1221/Strata/releases` (primero el tag `v<versión de CMakeLists>`, luego `latest`). Si el `BUILD.json` no tiene la arquitectura, compila. sm_86 está en la lista: el fixture de tests usa `archs [75,86,89,120]` (`tools/test_setup_choices.py`), el Dockerfile compila `75;80;86;89;120` (`Dockerfile:58`). No pude bajar el zip para ver su `BUILD.json`: **no verificado**. | `setup.py:101-103`, `1671-1752`, `3436` |
| Compilar | solo con `--build`, o sin zip, o sin arquitectura. Compila **solo para la arquitectura de la tarjeta** (`-DCMAKE_CUDA_ARCHITECTURES=86`); en Windows instala VS Build Tools + CUDA (20-40 min). | `setup.py:1965-2017`, `1835-1906` |
| `MIN_ENGINE` | 0.1.38. Un motor instalado prefabricado >= 0.1.38 **nunca** se vuelve a mirar, aunque el código del fork cambie. Solo los motores `source: local` se recompilan al cambiar el hash de fuentes. | `setup.py:107`, `1794-1802` |
| Flags del motor que escribe | `--pack --native --ple-gguf --expert-profile data/expert-profile[-coder].bin --expert-cache auto --prefill auto --spec 4 --spec-min-p 0.5 --mtp <rt> --max-context N` (+ `--kv int8` si ctx > 8K, `--kv-resident 32768` si ctx >= 64K y cabe, `--mmap-experts`/`--resident-experts` si low-RAM, `--vision --vram-reserve-mib 700` solo con imágenes). **No** escribe hilos, `--pcie-frac`, `--vram-reserve-mib` (sin imágenes), ni `--draft-vocab`/`env`. | `setup.py:3550-3624` |
| Prompt lookup / MTP | MTP siempre (`--mtp`, `--spec 4`). Prompt lookup: lo decide el motor (por defecto). | `setup.py:3552`; `docs/DETAILS.md:891-894` |
| Perfil de expertos | el distribuido (`expert-profile.bin`, o el del Coder). No se aprende del PC salvo opt-in `expert_profile_save`. | `setup.py:3551`, `docs/DETAILS.md:417-426` |
| Imágenes | por defecto **off** (con `--yes` y con Enter). Con `gpu`: 0,9 GB de descarga + ~1,4 GB de VRAM reservados. | `setup.py:190-191`, `3330-3337` |
| Calibración | solo se ofrece si hay alguien contestando (no con `--yes`), NVIDIA. | `setup.py:3670-3674` |

### 1a. RTX 3060 12 GB + 32 GB RAM + Windows (31,9 GiB) - (SIMULADO)

`START-HERE.bat --yes --no-start` (y también Enter en todas las preguntas):

```
model: Qwen3.8-Flash-Next        <- family "1" = qwen (setup.py:3196)
size: Q2_0                        <- rec = "1" porque ram < 60 (setup.py:3221)
context: 32768 tokens             <- min(32768 [VRAM<14], ram_ctx=131072)  (3266-3270)
low-RAM mode: Q2_0's experts (34 GB) are read ... through the OS file cache ... the GPU holds ~21% of them
[!] most of the experts are read from the SSD while it answers: expect it to be much slower ...
ARGS: --expert-cache auto --prefill auto --spec 4 --spec-min-p 0.5 --mtp ... --max-context 32768 --kv int8 --mmap-experts
```

- Modelo/tamaño: **qwen Q2_0**, modo **mmap** (`low_ram_needed` verdadero porque 31,9 < 34+10; `low_ram_resident` falso porque
  `34 - (12-5) = 27`, y `27 + 10 > 31,9`; `setup.py:2027-2052`). `low_ram_fits` da falso para Q2_0
  (`31,9 - 6 + 7 = 32,9 < 34`, `setup.py:2055-2058`), pero **nadie lo consulta** para el tamaño elegido: solo se usa para
  el tamaño más pequeño (`setup.py:3147`) y en `--check`/menú. Resultado: `--yes` instala Q2_0 mapeado sin parar.
- Contexto 32K, KV int8, sin streaming (ctx < 64K), sin imágenes, `--vram-reserve-mib` ausente (el motor usa 700 MiB).
- `--draft-vocab`: no se escribe; el default `cjk` (~348 MiB de VRAM) se queda. Solo sale un "Tip for a 12 GB card"
  (`setup.py:2757-2769`, `3539-3540`) que no cambia nada.
- Con `--family coder` (lo que dicen las docs) sí cambia: `IQ1_M`, `--resident-experts`, "la GPU guarda ~30 %, el resto
  (~16 GB) en RAM". Mismo resto de flags.
- Avisos/negativas que **sí** disparan: ninguna parada. Page file < 4 GB (aviso), driver < 580 (parada). La parada por
  RAM solo aparece por debajo de ~28 GiB sin low-RAM viable.
- Disco que exige: Coder + low-RAM = 58,4 GB de descarga + 8 + 23,4 + 1 ≈ **91 GB** libres (`setup.py:3409-3416`): en
  un SSD SATA de 240/250 GB es mucho.

### 1b. RTX 3060 12 GB + 64 GB RAM + Linux (62,7 GiB) - (SIMULADO)

- Familia `qwen`, **tamaño `IQ3_XXS`** (porque `62,7 >= 60`, `setup.py:3221`). No hay low-RAM (42,9+10 <= 62,7).
- Contexto **32K** (`rec_ctx=32768` por VRAM < 14 GB). KV int8. Sin `--kv-resident`. `--prefill auto --spec 4 --spec-min-p 0.5`.
- Si el usuario elige 128K en el menú: con `IQ3_XXS`/`IQ3_S` **no se activa el streaming** en 62,7 GiB (ver 3 del resumen) y
  no se imprime nada; con `Q2_0`/`IQ2_XS` sí (`kv-resident 32768`).
- Motor: zip `strata-linux-x64.zip` + ruedas CUDA 13.0 por pip (~0,4 GB) (`setup.py:105`, `3438`).

### 1c. RTX 3060 8 GB + 32 GB RAM - (SIMULADO)

- `[!] less than 12 GB of VRAM: ... slow` (`setup.py:3142`). Mismo camino que 1a: `qwen Q2_0` mmap, "la GPU guarda ~9 %".
- Con `--family coder`: `--resident-experts`, "GPU ~13 %, ~20 GB en RAM". Esa cifra es optimista: ver H8 (la fórmula
  `vram - 5` ignora reserva, cabeza del draft y escritorio; con 8 GB la caché real sería ~0,6 GiB, ESTIMACIÓN).
- Nota de tarjeta pequeña (`small_card_note`) solo para < 7,5 GB (`setup.py:2772`, `3617-3621`): una 3060 de 8 GB no la recibe.

### 1d. Otros casos que probé (SIMULADO)

| PC | `--yes` sin flags |
| --- | --- |
| 3060 12 GB + 16 GB | **para** (`RAM: 16 GB - the smallest model (the Coder) needs about 32 GB`); con `--family coder --model IQ1_M --yes` instala en mmap (16 GB: la GPU guarda ~30 %, todo lo demás del SSD) |
| 3060 12 GB + 48 GB (47,0) | `qwen Q2_0` en RAM, ctx 32K; sin streaming aunque se pida 64K (48+0,9+1 > 47) |
| 3060 12 GB + 64 GB | `qwen IQ3_XXS`, ctx 32K |

---

## 2. Hallazgos (con file:line)

**H1. Familia/tamaño por defecto no siguen la regla de RAM de las docs** (crítico para 32 GB).
`setup.py:3190-3196` (default familia fijo `"1"`), `3221` (`rec`: IQ3_XXS si `ram>=60`, si no `"1"` = Q2_0),
`3256-3263` (low-RAM se activa sin comprobar `low_ram_fits` del tamaño elegido), `3344-3368` (solo `warn`).
Contra: `README.md:101-104`, `docs/MODELS.md:12-21`, `docs/AI_SETUP.md:70-78` ("With `--yes` and no `--model`, setup picks the
recommended size for the RAM itself" - cierto para el tamaño, no para la familia).

**H2. Docs y código discrepan a 64 GB.** Código: IQ3_XXS (`setup.py:3221`). Docs: IQ2_XS "recommended"
(`README.md:103`, `docs/MODELS.md:16`, `docs/AI_SETUP.md:74`). En 12 GB con DDR4 la diferencia importa: en la 5070 IQ3_XXS va
a 62/49 tok/s (1K/128K) e IQ2_XS a 79/63 (MEDIDO, `docs/MODELS.md:25-33`, `docs/DETAILS.md:36-55`).

**H3. KV streaming: test de RAM mal calibrado y silencioso** (`setup.py:3564-3601`).
`stream_fits = ram >= MODELS[model]["ram_gb"] + kv_ram_gb + 1` usa el "needs" (48/48/60/62), no el arena (34/35,5/42,9/50,3).
(SIMULADO) 3060 12 GB, 62,7 GiB: IQ3_XXS a 128K -> sin `--kv-resident`; a 63,9 GiB -> con. IQ3_S a 64K y 128K -> sin, en ambos. Con 47 GiB y Q2_0 ->
sin streaming ni a 64K. Cuando el streaming se descarta en modo `auto` no se dice nada (`setup.py:3585-3601`: solo hay mensaje con
`--kv-streaming on`). Las docs afirman que el streaming es lo que se mide (`docs/DETAILS.md:17-20`, `69-74`).
En modo low-RAM el KV se queda en VRAM por diseño (`setup.py:2185-2191`, `docs/DETAILS.md:66-67`), incluso en la variante *resident*
donde sobra RAM.

**H4. Contexto por defecto conservador para <14 GB** (`setup.py:3266`). Según el motor, `--kv-resident N` guarda N celdas
por capa QSA en VRAM (mínimo 20 480) y todo el KV en RAM anclada (`generate.cpp:459-461`); la VRAM del KV a 128K con streaming es
la del contexto de 32K (13,7 KB/token, `setup.py:3567`). Inferencia (hay que medir en una 3060): 64K/128K son neutros en
VRAM. Para 12 GB y 64 GB de RAM el default razonable es 131072.

**H5. `low_ram_gpu_gb` optimista para 12 GB** (`setup.py:2033-2039`, `2058`): `vram - 5`. Con los registros de #620 (16 GB, sin
escritorio: 9,12 GiB libres tras cargar lo denso, con el codificador de imágenes) y `bench/results/2026-09-28-coder` (5070 12 GB: 2 570
slots del Coder = ~4,9 GB) lo no-experto en una tarjeta de 12 GB en Windows ronda ~7 GB: denso+KV32K (~5,4) + reserva 0,7 +
cabeza del draft 0,13-0,34 + escritorio 0-1,5 (ESTIMACIÓN derivada). Efecto: "la GPU guarda ~30 %" donde lo medido
es ~21 % (2 570 de 12 288); en 8 GB la decisión *resident* es dudosa.

**H6. Draft vocab por defecto `cjk`** (~348 MiB; `en` 133 MiB; `setup.py:2753`). Solo hay un consejo para < 14 GB
(`2757-2769`, regla "recomienda, no fuerza" #403/#406). En 12 GB son ~215 MiB = ~110-150 expertos (~4-5 % de la caché,
ESTIMACIÓN), a cambio de velocidad en respuestas CJK. No toca la calidad: solo qué tokens puede *proponer* el draft
(`docs/DETAILS.md:102-113`).

**H7. Imágenes en GPU** cuestan ~1,4 GB: en una 12 GB son ~700 expertos menos, ~25-30 % de la caché (ESTIMACIÓN con 1,97 MiB/slot de #620). El
doc mide 2-8 % de decode en una 5070 (`docs/DETAILS.md:784-794`). Default `off`: bien. Si se activan, `--vision cpu` merece
mencionarse para 12 GB.

**H8. Sin consulta de escritorio/PCIe/RAM/disco.**
`gpus()` (`setup.py:385-397`) no pide `memory.used`, `display_active`, `pcie.link.gen.gpumax/hostmax`, `pcie.link.width.max`. El motor
mide el espacio libre al arrancar, así que el escritorio ya se descuenta (`docs/INSTALL.md:201-202`), pero no el crecimiento posterior
(#533: WDDM mueve las asignaciones a RAM del sistema y el decode se hunde). Tampoco se detecta 1 solo DIMM (monocanal), XMP
apagado (`docs/TROUBLESHOOTING.md:45-47` lo cita como causa de "más lento que las tablas"), ni SATA/HDD (#605: el default `--ple-io direct`
se bloquea en disco rotacional).

**H9. Hilos y calibración.** `--pool-workers` por defecto = núcleos físicos (host incluido) (`generate.cpp:260`, `586-589`); en 6C/12T son 6. `calibrate.py`
prueba `[6, 4, 3]` (`worker_candidates`, `tools/calibrate.py:73-80`): nunca 5. Solo se ofrece interactivamente (`setup.py:3670-3674`).
`--spec-min-p 0.5` está medido en un Ryzen 5 7600 (`tools/calibrate.py:3-6`); dos configuraciones comunitarias en CPU solo-AVX2 usan
**0,70** (#519 5950X; #486 EPYC/AVX2 con `--pcie-frac 0.35 --pool-workers 9`). n=2, anecdótico.

**H10. Kernels fusionados de prompt.** `moe_fused.cu:396-408`: por defecto activos solo para el pack **Q2_0 canónico**
(el que setup construye únicamente con AVX-512, `setup.py:3501`); para los packs nativos IQ (y Q2_0 sin AVX-512, o sea Ryzen 3600/5600, i5-10400/12400) hay que
poner `STRATA_PF_FUSED=1`. `available()` pide cc >= 80, una 3060 cumple. En la 5090 con IQ3_XXS: +13-23 % de prompt (#519);
en la 5070: IQ2_XS +12 % a 4K, +3 % a 32K, IQ3 igual (`docs/DETAILS.md:26-28`). **No medido en sm_86 ni validado en calidad**.

**H11. Pagefile en Windows 32 GB** (`setup.py:3162-3166`, solo < 4 GB). Cada asignación en la GPU se carga también al *commit*
de Windows (`docs/DETAILS.md:862`): Coder resident = ~17-18 GB bloqueados + ~12 GB de VRAM + resto > 32 GB (ESTIMACIÓN), así que el page
file gestionado por el sistema es necesario, no opcional.

**H12. Disco:** low-RAM escribe `experts.bin` (+23,4 GB el Coder, `setup.py:3516-3519`) aunque el motor >= 0.1.31 puede mapear el GGUF
directamente (`docs/DETAILS.md:148-156`, "Setup does not use this yet"). Para un SSD SATA de 250-500 GB son 23-36 GB regalados (por confirmar que
`--resident-experts` también lo admita).

**H13. Binarios y fork.** `tools/make_release.py` (citado en `setup.py:96`) **no existe** en el repo; no hay `.github/workflows`.
Si el fork cambia el motor, `get_prebuilt` seguirá bajando el zip de upstream (mismo `version` 0.1.38, `CMakeLists.txt:11`); si el fork sube
la versión, el tag no existe y cae a `latest` de upstream (`setup.py:1662-1668`): motor ≠ fuente. Hay que publicar un release propio y
fijar `PREBUILT_URL` (`setup.py:101`) o forzar `--build`.

**H14. serve para una máquina lenta** (punto 5 del encargo):

| Ajuste | Valor hoy | Efecto en una 3060 | Dónde |
| --- | --- | --- | --- |
| Cola | un solo `threading.Lock` FIFO, cola **sin límite** y sin timeout de cola; `/status` cuenta `queued` | un cliente que reintenta apila peticiones; sin 429 | `serve/server.py:955`, `1447-1456` |
| `max_tokens` ausente | "el resto del contexto" (`0/-1`) | con 15-35 tok/s una respuesta sin tope puede durar >10 min | `serve/server.py:2420`, `2481` |
| `fit_max_tokens` | **off** (400 si prompt + max_tokens > ctx) | con ctx 32K, Claude Code/OpenCode (piden 32K de salida) reciben 400 siempre | `serve/server.py:2847-2849`, `docs/DETAILS.md:523-526` |
| Pensamiento | el modelo piensa en `high` por defecto; la web usa `thinking: "high"`; `reasoning_budget_tokens` = 0 (sin tope), opt-in | en 3060 un `high` largo cuesta minutos; tocar el tope **cambia respuestas** (no es "sin pérdida") | `serve/web/app.js:464`, `serve/server.py:991`, `docs/DETAILS.md:467-476` |
| Peticiones Anthropic sin `thinking` | piensan como la plantilla; `"anthropic_thinking": "on_request"` lo evita (opt-in) | las llamadas auxiliares de Claude Code (título) gastan pensamiento; el cambio altera comportamiento si el cliente no pide pensamiento | `docs/DETAILS.md:476-479` |
| Silencio del motor | 300 s (`ENGINE_SILENCE_S`), + `chunk/50 tok/s` hasta el primer `PP`, ×3 por chunk después | suficiente mientras el prompt se lea a >= ~100 tok/s | `serve/server.py:72-80`, `519`, `555` |
| Watchdog del motor | 60 s sin progreso (`STRATA_WATCHDOG_S`) | puede dispararse con disco lento/mmap; #605 | `docs/DETAILS.md:874`, `generate.cpp:5004-5007` |
| Keep-alive | cada 10 s en streaming | bien; en *no*-streaming el cliente espera mudo | `serve/server.py:526-541`, `2447` |

**H15. Tests actuales en Linux:** ver sección 5.

---

## 3. Evidencia de la comunidad (enlaces)

Limitación: el MCP de GitHub y `gh` están restringidos a `adrianbm96/strata3060` en esta sesión (no pedí `add_repo`: no se me encargó).
Leí títulos y **cuerpos** con la búsqueda de issues (sí devolvió cuerpo) y con `WebFetch` de las páginas públicas; `WebFetch` no devuelve los hilos
de comentarios, así que los issues con muchos comentarios (#74: 16, #28: 4, #6: 2) los conozco solo por el mensaje inicial.

**Directamente relevante (12 GB / 30-series / DDR4):**

- [#534](https://github.com/Niko1221/Strata/issues/534) RTX 3060 12 GB (CUDA) y RX 7800 XT 16 GB (HIP), Ryzen 7 7800X3D, 64 GB, Ubuntu 26.
  Swift 1.5 IQ3_XXS, 128K, KV q8: **~30-35 tok/s en la 3060**, estable (la 7800 XT 40-45, inestable por `systemd-oomd`). Único dato
  MEDIDO de 3060 en Strata. CPU con DDR5/AVX-512, no representativo del DDR4.
- [#610](https://github.com/Niko1221/Strata/issues/610) RTX A3000 12 GB (GA104, **sm_86, 32 SMs, 3 MB L2, 336 GB/s**), i7-12850HX, motor 0.1.38,
  Swift IQ3_XXS: esperas GPU 26-27 ms + pool CPU 27-29 ms por ventana, 2,10 tokens/ventana, aciertos de caché 52,6 % (frío) / 67-70 % (caliente).
  Las GEMV de proyección GDN (2,21 GB/ventana) corren a ~345 GB/s frente a 336 GB/s de pico: **ligadas al ancho de banda de la VRAM**. Dos tweaks
  de kernel probados salieron negativos (-0,43 % e2e). Es la mejor aproximación a la 3060 (misma arquitectura, mismo ancho de banda).
- [#74](https://github.com/Niko1221/Strata/issues/74) RTX 3080 Laptop 16 GB, i7-10870H, **64 GB DDR4-2666**, NVMe: IQ3_S **~17 tok/s**
  (motor 0.1.18, ya viejo), 3 219 expertos en VRAM (6,2 GB) con ctx 64K en VRAM e imágenes on. 16 comentarios (no legibles). Cerrado.
- [#6](https://github.com/Niko1221/Strata/issues/6) RTX 3500 Ada 12 GB (portátil), i7 13.ª, 64 GB DDR5: IQ2_XS **9-10 tok/s** (se preguntaba si era normal; cerrado, sin texto de la respuesta).
- [#331](https://github.com/Niko1221/Strata/issues/331) RTX 2060 12 GB, Xeon Gold 6136, **32 GB**, Windows: Coder IQ1_M, 8K, el motor 0.1.30 se colgaba en cada ventana de verificación (Turing).
  Cerrado; arreglos de Turing en 0.1.33+. Prueba de que existe el combo 12 GB/32 GB/Coder/Windows.
- [#141](https://github.com/Niko1221/Strata/issues/141) RTX 5060 Ti 16 GB + **Ryzen 5 5600 + 48 GB DDR4**, Windows: `strata.exe` 36 GB de RAM + 15 GB VRAM + 23 GB de "VRAM compartida", commit 64/72 GB,
  page file ~25 GB. Es la clase de CPU/RAM objetivo; respalda H11.
- [#467](https://github.com/Niko1221/Strata/issues/467) Windows **32 GB** + RX 7900 XTX 24 GB: la variante *resident* nunca se activaba (el chequeo de ajuste corría tras calentar la caché; disponible
  0,5 GiB tras arrancar). Cerrado (v0.1.35, `setup.py:107`). Cuando cae a mmap: ~47 tok/s incluyendo razonamiento, con 53 % de los expertos ya en una GPU de 24 GB. Con 32 GB
  de RAM la variante *resident* necesita ~(experts no-GPU + 4 GiB) **libres**.
- [#533](https://github.com/Niko1221/Strata/issues/533) (abierto) y [#516](https://github.com/Niko1221/Strata/issues/516) /
  [#560](https://github.com/Niko1221/Strata/issues/560) (cerrados, AMD/Linux; `--vram-reserve-mib 3072`/`4000` arregló los fallos del compositor): la reserva fija de 700 MiB
  es corta cuando el escritorio crece después; en Windows el síntoma es el decode hundido por WDDM.
- [#620](https://github.com/Niko1221/Strata/issues/620) (abierto) 5060 Ti 16 GB con el escritorio en la iGPU: sin `--kv-resident` el 128K falla con `native head upload: out of memory`; con `--kv-resident 32768`
  arranca ("9,12 GiB free, 700 MiB reserved (+218 MiB draft head) -> 3318 slots", luego 3771 slots / 7,23 GiB). Datos para el cálculo de caché y de por qué el streaming importa en VRAM justa.
- [#605](https://github.com/Niko1221/Strata/issues/605) (abierto) disco rotacional + `--ple-io direct` = el prefill se bloquea; arreglo `--ple-io ram` (solo Linux; en Windows el motor lo rechaza, `generate.cpp:1557-1558`).
- [#519](https://github.com/Niko1221/Strata/issues/519) 5090 + **Ryzen 9 5950X (AVX2)** + 96 GB **DDR4-3200**, Windows 11, PCIe gen 4 x16 (sonda 28,3 GB/s): config de producción con `--spec-min-p 0.70 --pcie-frac 0.20`
  y `STRATA_IQ_MT_MIN=1`; `STRATA_PF_FUSED=1` da +13-23 % de prompt con IQ3_XXS.
- [#486](https://github.com/Niko1221/Strata/issues/486) 2x RTX 2080 Ti, AVX2 sin AVX-512, 126 GB: `--pcie-frac 0.35 --spec-min-p 0.70 --pool-workers 9`; 262K puede agotar VRAM, 204 800 es estable.
- [#28](https://github.com/Niko1221/Strata/issues/28) (3080 Ti 12 GB vs 4070 Ti Super 16 GB, pregunta sobre canales/velocidad de RAM; 4 comentarios no legibles).
- `docs/MULTI_GPU.md:117` cita #253: un split 4090 + **3060** perdía 2/3 de velocidad de prompt por un tope de pin en WDDM (arreglado en 0.1.31, Linux).

**Repo local:** `docs/COMMUNITY_BENCHMARKS.md:15-17` solo lista una RTX 5090; `bench/results/` tiene 5070 12 GB (Coder: 2 570 expertos en VRAM, 1K-262K),
3090 + EPYC 7453 (AVX2; sondas PCIe 23-26 GB/s), 5080+3090, 5090 y `2026-09-30-tc-gemv-sm75` (i7-8700K + RTX 2070 8 GB, solo microbenchmark: no hay tok/s). Ninguna 3060.

**Externo (no es Strata):** [InsiderLLM, 8 sep 2026](https://insiderllm.com/guides/qwen3-8-flash-next-rtx-3090-3060-32gb/) con llama.cpp v0.4.0 (UD-IQ3_XXS 82 GB):
RTX 3060 12 GB + i7-7700 + 31 GB DDR4-2133 + NVMe = **8,7-11,5 tok/s** (prefill 31-70 tok/s, ~25 MiB/token desde el SSD); cita un vídeo de otra persona con 3060 + Ryzen 5 5600X + 64 GB DDR4 a 22-24 tok/s
(llama.cpp con un fork). Sirve de suelo de comparación para el hardware "típico de una 3060 usada". También: 6 vs 12 hilos en una caja de 6 núcleos = +3 % (ruido), 3 hilos = -27 %,
consistente con el default por núcleo físico. [AlphaSignal](https://alphasignal.ai/news/strata-runs-a-125b-ai-model-on-a-regular-gaming-pc) solo repite las cifras de 5070/3090.

### Qué esperar (ESTIMACIÓN, sin medir en 3060 + DDR4)

Referencia medida (5070 12 GB, Ryzen 5 7600, DDR5-5200, `docs/MODELS.md:25-33`): Q2_0 94/76, IQ2_XS 79/63, IQ3_XXS 62/49, Coder 55/43 tok/s (corto/128K).
Dato 3060 (#534): 30-35 frente a 49 de la 5070 con IQ3_XXS a 128K = 0,61-0,71. Aplico 0,55-0,70 por GPU y otro ×0,8-0,9 por DDR4-3200 (suposición mía):

| Tamaño | Decode estimado en 3060 12 GB + DDR4-3200 | Notas |
| --- | --- | --- |
| Q2_0 (64 GB) | ~35-50 tok/s | |
| IQ2_XS (64 GB) | ~30-45 tok/s | |
| IQ3_XXS (64 GB) | ~25-35 tok/s | consistente con #534 |
| Coder (32 GB, resident) | ~20-33 tok/s | |
| 8 GB de VRAM | **sin base**: la caché real sería ~0,6 GiB (~1-2 % de los expertos), casi todo en CPU | medir |

Prompts: 5070 IQ2_XS 1 256 (4K) / 2 092 (32K) tok/s; para la 3060 (28 SMs, 360 GB/s, PCIe 3.0 x16 ~12-13 GB/s) ESTIMO 0,35-0,55 de eso. Todo ±30 % o peor.

---

## 4. Perfil RTX 3060 propuesto

### 4.1 Detección (por rasgos, no por nombre)

| Rasgo | Fuente | Para qué |
| --- | --- | --- |
| `vram_gb` en [7,5 ; 14) = "tarjeta de presupuesto" (12 GB: [10,5 ; 14), 8 GB: [7,5 ; 10,5)) | `nvidia-smi memory.total` (ya lo hay) | variable dominante: `docs/DETAILS.md:208-211` ("more VRAM matters more than a faster GPU"; cada GB = ~700 expertos) |
| `compute_cap >= 8.0` | ya lo hay | Ampere+: kernels fusionados de prompt (`moe_fused.cu:393`), sm_86 en el zip |
| RAM total (banda 16 / 32 / 48 / 64) | `ram_gb()` | familia, tamaño, low-RAM |
| AVX-512 sí/no | `cpu_info()` | hoy decide también el pack Q2_0 (`setup.py:3501`) |
| Generación y ancho PCIe = min(gpumax, hostmax) | `nvidia-smi pcie.link.gen.gpumax,pcie.link.gen.hostmax,pcie.link.width.max` (**nuevo**, verificar nombres con `nvidia-smi --help-query-gpu` en un PC real) | solo informar: el motor ya ajusta `--pcie-frac` |
| `display_active`, `memory.used` | `nvidia-smi` (**nuevo**) | cuánta VRAM se come el escritorio (Windows: 0,5-1,5 GB); consejo de iGPU |
| SMs / ancho de banda | **no** salen de `nvidia-smi` (ni SMs ni ancho de bus). Opciones: (a) que el motor los imprima: `device.cu:251` ya lee `multi_processor_count`; `cudaDeviceProp` tiene `memoryClockRate`, `memoryBusWidth`, `l2CacheSize` -> ampliar `strata-device --list-devices` (`device_main.cpp:49-62`) y distribuirlo en el zip; (b) `ctypes` + `cudaDeviceGetAttribute` desde la rueda `nvidia-cuda-runtime`, pero se instala en el paso 4 (`setup.py:3438`), después del dimensionado del paso 2. | solo para el texto de "qué velocidad esperar"; **el dimensionado se queda en VRAM + RAM** |

Una tabla nombre -> (SMs, GB/s) solo sirve para mensajes; no debe decidir nada.

### 4.2 Valores por RAM y VRAM

Todo lo que no figura aquí no cambia (`--prefill auto --spec 4 --spec-min-p 0.5 --kv int8 --expert-cache auto`, perfil de expertos distribuido, imágenes off, MTP).

| | **12 GB VRAM** | **8 GB VRAM** |
| --- | --- | --- |
| **16 GB RAM** | **No soportado** (parada actual, `setup.py:3148-3159`, ya es correcta). Mensaje con la salida: 2x16 GB DDR4 (barato) | igual |
| **32 GB RAM** | **Coder IQ1_M**, `--resident-experts`, ctx **32 768** (65 536 permitido con streaming si cabe), KV int8, `--draft-vocab en` (salvo idioma CJK/cirílico), `fit_max_tokens: true`. Pagefile "gestionado por el sistema". Avisar: necesita ~21 GiB *libres* (experts no-GPU ~17-18 + 4 GiB), cerrar navegadores. `Q2_0`/`IQ2_XS`: **solo con `--model` explícito** (consiente el riesgo; van por mmap, GPU ~14-21 %) | **Coder**, mismo ajuste; ctx 32K (o 16K); `en`; esperar caché de ~0,6 GiB: decisión *resident* al límite (el motor cae a mmap con aviso). Sin medición: decir "lento" y nada más |
| **48 GB RAM** | `Q2_0` o `IQ2_XS` en RAM (hoy `Q2_0`, docs `IQ2_XS`); ctx 65 536 con streaming (con el test arreglado) | igual |
| **64 GB RAM** | `IQ3_XXS` como hoy (calidad primero, 30-35 tok/s medido en #534) con `IQ2_XS` como alternativa rápida (ESTIMACIÓN +25 %, ratio 5070); ctx **131 072** con `--kv-resident 32768`, KV int8; `en`; `fit_max_tokens: true` | `IQ2_XS`/`Q2_0` (decisión del dueño); ctx 131 072 con streaming (la VRAM es la de 32K); `en` |

Cruzados:

- **Calidad:** el perfil no baja `--kv` (int8), no activa `q4_0`/`k8v4` (`docs/DETAILS.md:76-79`: q4_0 pierde precisión) ni toca `--rope-scaling`. La familia Coder solo se
  recomienda donde la alternativa es mmap sobre SSD. El draft vocab `en` solo recorta lo que el draft puede proponer.
- **Reserva de VRAM:** dejar el default de 700 MiB. Con Windows + `display_active` + `memory.used >= 0,3 GB`: consejo (sin cambiar): conectar el monitor a la placa
  si tiene salida de vídeo (los i5 con iGPU, no los Ryzen 3600/5600 sin G) y desactivar la aceleración por hardware del navegador (+0,5-1 GB ≈ +250-500 expertos, ESTIMACIÓN). Subir la reserva a 1 024-1 536 MiB
  cuesta 170-440 slots (7-18 % de una caché de ~2 500), solo si el usuario juega/usa vídeo a la vez: dejarlo como flag, no como default.
- **Hilos:** dejar el default del motor (núcleos físicos). Proponer añadir `5` a `worker_candidates(6)` en `tools/calibrate.py:73-80` (`[6,5,4,3]`) y ofrecer `--calibrate` al final.
- **`--spec-min-p`:** mantener 0,5 hasta medir 0,5 vs 0,7 en Ryzen 5 3600/i5-10400 (#519 y #486 usan 0,70).
- **MTP/prompt lookup:** sin cambios (el motor decide).
- **Perfil de expertos:** el distribuido; recordar `expert_profile_save` (opt-in) tras unas semanas de uso (`docs/DETAILS.md:417-426`).
- **Imágenes:** off. Si se piden en 12 GB, ofrecer `--vision cpu` primero.
- **Servidor:** `fit_max_tokens: true` en <=128K; `engine_silence_s: 600` si el modo es mmap o el disco no es NVMe (opcional). No fijar tope de pensamiento por defecto (cambia respuestas).
- **Motor:** zip prefabricado de sm_86 como hoy; si el fork cambia código del motor, release propio + `PREBUILT_URL`, o `--build` (compila solo `86`).

---

## 5. Cambios concretos en setup.py + tests

Prototipo funcional en `05-setup-profile.setup.patch` (aplicado a una copia; todos los tests pasan). Posiciones de inserción en el `setup.py` original:

| # | Qué | Dónde (original) | Detalle |
| --- | --- | --- | --- |
| P0 | Bloque de ayudas | antes de `def mtp_corrupt` (`2813`) | `BUDGET_VRAM_GB=14.0`, `SSD_BOUND_SHARE=0.35`; `budget_card(v)` = `7.5 <= v < 14` (reutiliza `SMALL_CARD_GB`, `2772`: las < 7,5 GB siguen con su propio consejo y sus tests); `rec_family(ram, vram, low_ram_choice)`; `kv_stream_fits(...)`; `locale_draft_vocab(env, win_locale)`; `parse_gpu_extras`, `gpu_extras`, `budget_notes` |
| P1 | Familia por RAM | `3196` | `rec_fam = "qwen" if a.model else rec_family(ram, gpu["vram_gb"], a.low_ram)`; el default de `ask("Which model?")` pasa a `str(fams.index(rec_fam)+1)`. `rec_family` devuelve `coder` si `low_ram_needed(Q2_0) and not low_ram_resident(Q2_0) and low_ram_fits(IQ1_M)`. No toca `--model X` (si no, `--model IQ2_XS --yes` fallaría con "Coder has no IQ2_XS") |
| P2 | Parada por mmap ligado al SSD | en el `else` del modo mapeado, `3363-3368` | `confirm_risk(...)` si `budget_card and share < 0.35 and a.low_ram not in ("on","mmap")`; `explicit = bool(a.model)`: `--yes` solo para, `--model Q2_0 --yes` consiente (regla del dueño, `confirm_risk` `2154-2163`) |
| P3 | Contexto recomendado | tras `3270` | si budget, 1 GPU, sin low-RAM, sin `--kv-streaming off`, KV != k8v4, no WSL y `kv_stream_fits(modelo, ram, KV@128K, vram)`: `rec_ctx = max(rec_ctx, min(131072, ram_ctx(...)))` |
| P4 | Test de streaming | `3572` | `if budget_card(small) and not multi and budget is None: stream_fits = kv_stream_fits(model, ram, kv_ram_gb, small, low_ram, resident)`. Regla: `ram >= arena + KV + 10` (RAM); resident low-RAM: `ram >= (arena - GPU) + KV + 10 + 4`; mmap low-RAM: no |
| P5 | Draft vocab | `3536` | `if draft_vocab is None and budget_card(vram) and not multi: draft_vocab = locale_draft_vocab()` (`es_ES`, `en_US`, `fr`... -> `en`; `zh/ja/ko` -> `cjk`; `ru/uk/bg/sr/be/mk/kk` -> `cyrillic`; en Windows `GetUserDefaultLocaleName`) + `ok(...)`. Se guarda en `cfg["draft_vocab"]` (`3653`) como hoy |
| P6 | `fit_max_tokens` | antes de `if hip:` (`3632`) | `if budget_card(vram) and ctx <= 131072: cfg["fit_max_tokens"] = True` (el servidor ya lo lee, `serve/server.py:2940`) |
| P7 | `--check` | `3184` | imprime "Profile for a 12 GB card with 32 GB of RAM: recommended the Coder" + `budget_notes(...)` (escritorio, PCIe < gen 4/x16, AVX2 sin AVX-512) |

Cambios recomendados que **no** están en el prototipo (cada uno necesita decisión o medición):

1. `low_ram_gpu_gb` (`2033-2039`) y `low_ram_fits` (`2058`): para tarjetas budget restar ~2 GB más (reserva + draft + escritorio) en vez de `vram - 5` (H5).
2. Corregir `stream_fits` también para tarjetas grandes (`3572`) -> cambia los golden de IQ3_S/IQ3_XXS (re-grabar con `python tools/test_setup_golden.py --write` solo si el dueño lo aprueba). Es el mismo bug que P4, sin la condición de VRAM.
3. Umbral de `warn` `3142` (`< 11`) y su texto ("less than 12 GB"): decir las cifras medidas de la sección 3 en vez de un genérico; para 12 GB una línea de perfil.
4. `ram_modules()` (Windows): `Get-CimInstance Win32_PhysicalMemory | Select Speed, ConfiguredClockSpeed` -> avisar de **1 solo DIMM** (monocanal) y de `ConfiguredClockSpeed < Speed` (XMP apagado). Pura función de parseo + tests; formato no verificado aquí.
5. Pagefile (`3162-3166`): subir el umbral cuando el modo es resident y RAM <= 36 GB (p. ej. 8 GB) o leer `AutomaticManagedPagefile` por WMI y no avisar si es automático.
6. Aviso previo al arranque (`start()`, `2704-2724`): en Windows con `--resident-experts`, comparar `ullAvailPhys` (de `_memory_status`, `267-278`) con (complemento + 4 GiB).
7. `get_prebuilt` / `PREBUILT_URL` (`101-103`, `1662-1668`): release propio del fork (H13).
8. `tools/calibrate.py:73-80`: `worker_candidates(6)` -> `[6,5,4,3]` (+ test en `tools/test_calibrate.py`).
9. Opcional medir y, si pasa la comprobación de calidad, escribir `"env": {"STRATA_PF_FUSED": "1"}` en la config cuando `compute_cap >= 80`, no AVX-512 y paquete nativo (H10).
10. Dejar de escribir `experts.bin` en el modo resident del Coder si el motor lo admite sin él (H12).

### Tests

`tools/test_setup_budget.py` (nuevo, **19 tests, pasan**; usa `install()` de `test_setup_golden.py`, sin GPU ni red):

- `RecFamily`: 32 GB + 12 u 8 GB -> `coder`; 64/48/96 GB, 16/24 GB de VRAM, 16 GB de RAM y `--low-ram off` -> `qwen`; `budget_card` por VRAM (7,99 / 8 / 12 / 13,9 sí; 6 / 14 / 15,9 / 24 no).
- `WindowsLinux3060`: 12 GB + 32 GB `--yes` = Coder `--resident-experts`, ctx 32768, sin visión, `draft_vocab en`, `fit_max_tokens`, 0 preguntas; igual con Enter; `--family qwen` sin tamaño = **parada** ("would be SSD-bound ... --family coder");
  `--model Q2_0` / `--model IQ2_XS --yes` = consentimiento (mmap, dicho); 16 GB sigue parando; 12 GB + 64 GB = IQ3_XXS, ctx 131072, `--kv-resident 32768`, `--kv int8`;
  IQ3_XXS a 128K hace streaming en 62,7 y 63,9 GiB; 8 GB + 32 GB = Coder + `en`; opciones explícitas del usuario se respetan (`--context 32768`, `--draft-vocab cjk`);
  Coder en 32 GB: 64K hace streaming y 128K no.
- `OtherCardsUnchanged`: 16 GB, 24 GB y 32 GB de VRAM no reciben `fit_max_tokens`, `draft_vocab` ni el mensaje nuevo.
- `KvStreamFits`: cifras (IQ3_XXS 62,7 sí; IQ3_S a 256K no; mmap no; Coder resident 64K sí, 128K y 256K no).
- `Locale`: `es_ES`, `en_US`, `C`, vacío, `fr_FR` -> `en`; `zh_CN`, `ja_JP`, `ko_KR` -> `cjk`; `ru_RU`, `uk_UA` -> `cyrillic`; locales de Windows (`es-ES`, `zh-CN`, ...).
- `GpuExtras`: parseo de `nvidia-smi` (tarjeta gen 4 en placa gen 3 -> gen 3), basura -> `{}`, notas (escritorio, PCIe, AVX2) y `--check` con/sin perfil.

Test que tuve que tocar fuera de lo mío: **ninguno de los existentes** (`test_setup_choices::SmallCardTip` sigue pasando porque `budget_card` excluye < 7,5 GB).
Hay que arreglar `tools/test_setup_golden.py:normalize()` (línea ~59) para Linux: `v.replace(setup.EXE, "<EXE>")` -> reemplazar solo `.../<EXE>` al final de la ruta (incluido en el parche).

### Resultado de los tests existentes (Python 3.11.15, Linux, copia del repo)

| Módulo | Tests | Resultado |
| --- | ---: | --- |
| test_setup_amd | 21 | **1 fallo** `test_prebuilt_hip_zip` (Windows-only, ya existía: escribe `strata.exe` en el zip y `EXE` es `strata` en Linux) |
| test_setup_choices | 31 | OK |
| test_setup_draft_vocab | 5 | OK |
| test_setup_golden | 4 | **fallan 46 subtests** (ya existían: `normalize()` rompe `strata-<m>.log` en Linux); con el arreglo de una línea: **OK** |
| test_setup_lowram | 5 | OK |
| test_setup_pins | 16 | OK |
| test_setup_prompts | 7 | OK |
| test_setup_risk | 34 | OK |
| test_setup_rope | 19 | OK |
| test_setup_unsloth | 29 | OK |
| test_setup_update | 8 | OK |
| **test_setup_budget (nuevo)** | 19 | OK; todos los anteriores siguen igual con el parche aplicado |

---

## 6. Preguntas abiertas / qué medir

Decisiones del dueño del fork:

1. ¿64 GB -> IQ3_XXS (hoy) o IQ2_XS (docs)? Es calidad contra ~25 % de velocidad (ESTIMACIÓN); el perfil lo deja como está y lo expone.
2. ¿`draft_vocab en` por defecto en 8-14 GB? Va contra el "recomendar sin forzar" de `setup.py:2761-2764`, pero no cambia lo que escribe el modelo.
   Para texto en **español**: `en` es subconjunto de `cjk` y de `cyrillic`, así que el draft en español rinde igual con los tres; pero **no sé cuántos tokens latinos con tilde/ñ incluye la base**
   (el cirílico pasó de 142 a 18 580 tokens y de 1,4 a 2,1 tokens/ronda, `docs/DETAILS.md:107-109`). Un subconjunto `latin` es una oportunidad real para hablantes de español: medir con `tools/draft_vocab.py --stats` y aceptación de drafts en español.
3. ¿Aceptar `Coder` por defecto en 32 GB aunque rinda peor fuera de código, inglés y CJK (`docs/MODELS.md:99-101`, #438)? Alternativa: parar y pedir elegir.
4. ¿Publicar release propio del fork? (H13).

Mediciones necesarias en una 3060 12 GB real (ninguna existe hoy):

1. 32K sin streaming frente a 128K con `--kv-resident 32768`, prompt corto: tokens/s y "expert cache N slots" del log (confirma que es neutro en VRAM). Probar también `--kv-resident 20480` (mínimo del motor) en 8 GB.
2. `en` frente a `cjk`: slots y tok/s.
3. `--spec-min-p` 0,5 / 0,7 y `--pool-workers` 6 / 5 / 4 en Ryzen 5 3600 / 5600 e i5-10400 / 12400 (`--calibrate`).
4. `STRATA_PF_FUSED=1` en sm_86 (IQ2_XS / IQ3_XXS / Q2_0 nativo) + comprobación de KL contra el default.
5. Windows 32 GB: Coder resident (RAM disponible, paginación, tok/s, tiempo de arranque con SATA SSD) frente a IQ2_XS mapeado en NVMe y en SATA.
6. Reserva 700 contra 1 024 con Chrome/vídeo y la 3060 manejando el monitor (#533).
7. PCIe 3.0 x16: valor de la sonda (`PCIe probe: ... GB/s`), prompt tok/s a 4K/32K.
8. DDR4-3200 dual frente a monocanal y a XMP apagado (decode).
9. 8 GB: caché real (log), tamaño de chunk de prefill (#448 midió 512 tokens y prompts 6,2x más lentos en una 3080 de 10 GB junto a otra tarjeta), tok/s.
10. Publicar los resultados con la plantilla de `docs/COMMUNITY_BENCHMARKS.md:121-167` en `bench/results/<fecha>-community-rtx-3060/`.

Cosas que no pude verificar: contenido real de `BUILD.json` del zip prefabricado (sm_86 inferido de tests y Dockerfile); nombres exactos de los campos `nvidia-smi` en todas las versiones de driver;
el formato de salida de `Win32_PhysicalMemory`; los hilos de comentarios de #74, #28, #6, #534; si `--resident-experts` funciona sin `experts.bin`.
