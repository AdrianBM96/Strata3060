# BEELLAMA-TRUCOS: Minería de Optimizaciones Medidas en RTX 3060 12GB + i5-12400F

Fecha: 2026-10-04. Autor: explorer. Para: Claude (arquitecto) y Adrián.  
Origen: Análisis estático (solo lectura) de `/home/bazzite/beellama/`: scripts `run_*.sh`, `models.ini`, `docs/qwopus-optimizacion-2026-09.md`, `evals/2026-09-24-qwopus-v2/` y `docs/sweep-sep2026/`.  
Hardware de medición: **RTX 3060 12 GB + i5-12400F (6P/12T) + 62 GB RAM DDR4** (la misma máquina del proyecto).

---

## 1. Identificación del motor y repositorios upstream

En `/home/bazzite/beellama/` y directorios hermanos residen los siguientes binarios y forks:
- **Upstream principal**: Fork de `https://github.com/ggerganov/llama.cpp.git` (commit `f3718514f8cba74e7085095ab2665e420f5f092a`, rama local `k2-merge`, fecha 2026-09-09, etiquetado en scripts como *BeeLlama.cpp v0.4.4 / build 10890*).
- **Variantes de investigación especializadas**:
  1. `/home/bazzite/llama-dcfr`: Base `c060ca97` (tag `b10603`) + parche experimental D-CFR (2.105 líneas, `kadenball`) + parche de kernels `mmvq` de `revv`.
  2. `/home/bazzite/llama-prism`: Commit `5d80cff` (utilizado para el récord de Qwopus V2 en producción).
  3. `/home/bazzite/llama-revv`: Enfoque en slot-save de estados recurrentes GDN.

---

## 2. Inventario exhaustivo de trucos con ganancia medida

| # | Truco / Técnica | Mecánica exacta | Ganancia medida en esta 3060/12400F | Fuente (fichero:línea) | Estado en Strata |
|---|---|---|---|---|:---:|
| **1** | **Cuantización asimétrica de FFN en CPU** | Modelo base Q3_K_S con FFN `gate/up` de capas en CPU en **Q2_K**, dejando `down` en **Q3_K** (imatrix mradermacher). Q2_K alivia el cuello de cálculo de CPU; Q3_K en down preserva calidad. | **Decode: 7,2 → 10,2 t/s (+41 %)**.<br>HE-50: 0.94 → 0.96. | `evals/2026-09-24-qwopus-v2/RESULTADO.md:26-30`<br>`models.ini:32-34` | **NO LO TIENE** *(planificado en E2)* |
| **2** | **Offload quirúrgico de tensores (`-ot`)** | `-ngl 99 -ot 'blk.(0..33).ffn_*=CPU'`. En llama.cpp, mandar capas enteras a CPU desactiva los kernels GDN fusionados; `-ot` mantiene GDN en GPU y solo externaliza FFN a RAM. | **Ahorra ~40 ms/tok en decode**.<br>Prefill: **+13 %**. | `evals/2026-09-24-qwopus-v2/RESULTADO.md:23-25`<br>`models.ini:39` | **NO APLICA** *(Strata ya desacopla denso en GPU y MoE en RAM por diseño)* |
| **3** | **Afinidad estricta a P-Cores (`-t 6`, `--cpu-range 0-5`)** | Fijar `--threads 6 --cpu-range 0-5 --cpu-strict 1` para decode (solo los 6 núcleos P físicos, sin HT). Usar `--threads-batch 12 --cpu-range-batch 0-11` solo en prefill. | **+10 % en decode**.<br>Evita que la CPU suba a 86 °C (los hilos HT generan contención en L1/L2). | `evals/2026-09-24-qwopus-v2/RESULTADO.md:22`<br>`run_bee_q25_512k.sh:45-49`<br>`models.ini:40-41` | **LO TIENE PARCIALMENTE** *(`--pool-workers 6`, pero sin affinity estricto en C++)* |
| **4** | **Offload de Embeddings y Cabezas a CPU** | `-ot "output.weight=CPU,token_embd.weight=CPU"`. Mueve la matriz de vocabulario a RAM del host. | Libera **~1.100 MiB de VRAM**, permitiendo subir `ngl` de 46 a 52 capas en GPU. | `docs/sweep-sep2026/sweep2.sh:54-55`<br>`run_bee_q25_512k.sh:29` | **NO APLICA** *(Strata mantiene embedding y shared en GPU optimizados)* |
| **5** | **D-CFR (Deferred-Commit Factor Replay)** | `LLAMA_GDN_TRANSACTIONAL_REPLAY=1`. Difiere la confirmación de estados recurrentes GDN/SSM durante el muestreo especulativo hasta que se aceptan tokens. | **Decode: 9,7 → 10,5 t/s (+8 %)**.<br>Permite MTP a profundidad 3. | `docs/qwopus-optimizacion-2026-09.md:11-18, 64`<br>`run_profile_qwopus_dcfr.sh:2-9` | **NO APLICA** *(Strata usa su propio gestor de estados GDN en CUDA)* |
| **6** | **Tuning de profundidad MTP (`draft-mtp`)** | `--spec-type draft-mtp --spec-draft-n-max 3 --draft-p-min 0.75`. Ajuste de profundidad: depth 2 = 9,23 t/s, depth 3 = 10,52 t/s, depth 4 = 10,34 t/s. | **+14 % decode** vs depth 2.<br>Aceptación del **90-93 %**. | `docs/qwopus-optimizacion-2026-09.md:64, 95`<br>`run_profile_qwopus_dcfr.sh:6, 23` | **LO TIENE PARCIALMENTE** *(`STRATA_SPEC_COUPLED=1`, sin depth dinámico)* |
| **7** | **Micro-batching agresivo en prefill (`-b 128 -ub 128`)** | Reducir el batch y micro-batch a 128 en lugar de 512/2048 para prompts masivos en la 3060. | En prompt 20K: **60 → 176 tok/s (~3×)**.<br>VRAM: **-412 MiB**. | `docs/qwopus-optimizacion-2026-09.md:18, 63`<br>`models.ini:49-50` | **NO LO TIENE** *(Strata usa chunk fijo de 6.144 por autosize)* |
| **8** | **Especulación N-gram modificada (`ngram-mod`)** | `--spec-type ngram-mod --spec-ngram-mod-n-match 8`. Especulación basada en n-gramas del historial de contexto para modelos sin MTP. | **Decode: 21,7 t/s** (Praxis 27B) y **62 t/s** (NeoHorse 4B). | `docs/qwopus-optimizacion-2026-09.md:72, 81`<br>`models.ini:106-109, 130-133` | **NO LO TIENE** *(Propuesto en P07)* |
| **9** | **Descarte del Drafter Externo DFlash2** | Probar modelo drafter externo `DFlash2` (z-lab/incoai) vs MTP integrado del fichero. | **FRACASO**: aceptación 43 % vs 90 % MTP; +1,14 GB VRAM forzó bajar capas y cayó a **6,4 t/s (-35 %)**. | `docs/qwopus-optimizacion-2026-09.md:80` | **NO APLICA / LECCIÓN** *(No meter drafters externos en 12 GB)* |
| **10** | **Polling de espera activa en CPU (`--poll 100`)** | Sondeo de 100 µs en el scheduler de threads antes de ceder al kernel de Linux. | **+5 a +8 % estabilidad de latencia** (evita dormir hilos y ahorra wake-up C-states). | `models.ini:23`<br>`run_bee_q25_512k.sh:50` | **LO TIENE PARCIALMENTE** *(`STRATA_POOL_SPIN_US` en `pool.hpp:200`)* |
| **11** | **Compresión KV asimétrica (`-ctk q4_0 -ctv q4_0`)** | Cuantizar las claves y valores a 4 bits (`q4_0`) para contextos largos. Intentar `q8_0` daba OOM por superar los 5,5 GB de arena KV. | Permite operar a **131K / 512K de contexto** ocupando solo 11,2 GB en la 3060. | `docs/qwopus-optimizacion-2026-09.md:13, 74`<br>`run_bee_q25_512k.sh:34-36` | **LO TIENE** *(Soporta KV cache `q4_0` / `int8`)* |
| **12** | **Descarte del combo MTP + N-Gram** | Intentar combinar `draft-mtp` y `ngram-mod` simultáneamente en Qwopus. | **FRACASO**: decode cayó a **7,9 t/s** (la N-gram contamina el árbol de alta calidad del MTP). | `docs/qwopus-optimizacion-2026-09.md:72`<br>`docs/sweep-sep2026/sweep.sh:64` | **LECCIÓN CRÍTICA** *(No combinar MTP y N-gram ingenuamente; usar P07)* |

---

## 3. Lecciones transferibles directamente a Strata

1. **La confirmación de E2 (Cuantización Asimétrica en CPU)**:
   El hallazgo de BeeLlama de pasar `gate/up` a `Q2_K` manteniendo `down` en mayor precisión (`Q3_K` o `IQ2_S`) demostró en esta misma CPU un salto de **7,2 a 10,2 t/s (+41 %)**. La verificación de tokens en CPU es limitada por cálculo; rebajar la aritmética de `gate/up` es la palanca con mayor retorno.
2. **Afinidad estricta de 6 hilos a P-Cores (`--cpu-range 0-5`)**:
   BeeLlama demostró que involucrar los hilos Hyper-Threading en decode satura térmicamente el i5-12400F (llegaba a 86 °C) y frena el throughput por contención de puertos. Strata debe forzar la afinidad de sus workers en C++ a los núcleos 0-5 (apoyando **E1**).
3. **Peligro de drafters externos en 12 GB de VRAM**:
   DFlash2 demostró que en una tarjeta de 12 GB, cualquier drafter externo que consuma >1 GB de VRAM destruye el rendimiento neto al obligar a expulsar capas de la GPU. La especulación debe ser **MTP interno** o **N-gram sin pesos**.
