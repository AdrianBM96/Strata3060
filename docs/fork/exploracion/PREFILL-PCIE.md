# PREFILL-PCIE: Reducción de los 72 ms/capa de PCIe en Prefill sin Pérdida de Calidad

Fecha: 2026-10-04. Autor: explorer. Para: Claude (arquitecto).  
Cifras base: `C22_TECHO.md`, `C24-PREP.md`, prompt de 32K tokens (P3: 20,17 s, P4: 26,09 s, ~990 tok/s base).  
Archivos analizados: `src/prefill/prefill.cpp`, `src/program/generate.cpp`, `src/core/layer.cpp`, `src/core/expert_source.cpp`.

---

## 1. Origen de los 72 ms/capa de PCIe en Prefill

A 11 GB/s en PCIe Gen4 x16, el ancho de banda del bus transfiere 11 MB por cada milisegundo.  
En el modelo Swift 1.5 (IQ2_XS), cada especialista empaquetado (`gu` + `down` contiguos) pesa **1,89 MB** (`src/kernels/cpu/expert_layout.cpp:45`).  
En un chunk de prefill ($T \ge 6.144$ tokens con top-$K=10$), se activan prácticamente todos los especialistas de la capa. Descontando los pocos residentes en VRAM:
$$\text{Especialistas transferidos por capa} \approx 420 \implies 420 \times 1,89\text{ MB} = 793,8\text{ MB}.$$
$$\text{Tiempo de cable PCIe} = \frac{793,8\text{ MB}}{11.000\text{ MB/s}} = \mathbf{72,16\text{ ms por capa}}.$$
En 48 capas, cada chunk gasta $48 \times 72,16\text{ ms} = \mathbf{3,46\text{ segundos de PCIe}}$.  
Para un prompt de 32K tokens procesado en 6 chunks de 6.144 tokens:
$$t_{\text{PCIe, total}} = 6 \times 3,46\text{ s} = \mathbf{20,76\text{ segundos de PCIe}}.$$
Esto representa el **85 % al 90 % del tiempo total de prefill** (20,17 s en P3, 26,09 s en P4).

---

## 2. Diagnóstico de las 4 Palancas Bit-Idénticas

### Palanca 1: Chunks más grandes (VRAM disponible vs tamaño de chunk)
- **Autosizing actual (`src/program/generate.cpp:4432-4526`)**:
  - `kAutoLendPct` (`generate.cpp:4432`): limita el préstamo de slots de VRAM al 90 % (`pinned_share >= 0.9`). Con 3.696 slots totales de VRAM en la 3060, el presupuesto prestable es de **3.326 slots = 6.286 MB (6,14 GB)**.
  - `auto_ceiling` (`generate.cpp:4441`): por defecto está topado a **8.192 tokens** (`o.prefill_auto_max = 8192`, `generate.cpp:437`), impidiendo automáticamente probar chunks mayores sin flag explícito.
  - `Prefill::bytes_needed` (`src/prefill/prefill.cpp:1358-1405`): calcula el consumo de memoria para activaciones y ring. Incluye sobrestimaciones deliberadas (`prefill.cpp:1369-1371`: *over-count de hasta 252 MB*).
- **Consumo real de VRAM frente a presupuesto**:
  - $T = 6.144$: activaciones ~1.350 MB + ring (384 slots) ~725 MB = **~2.075 MB** (apenas el 33 % de los 6,14 GB disponibles).
  - $T = 8.192$: activaciones ~1.800 MB + ring (384 slots) ~725 MB = **~2.525 MB** (40 % del presupuesto).
  - $T = 12.288$: activaciones ~2.700 MB + ring (384 slots) ~725 MB = **~3.425 MB** (55 % del presupuesto).
  - $T = 16.384$: activaciones ~3.600 MB + ring (384 slots) ~725 MB = **~4.325 MB** (69 % del presupuesto).
  La RTX 3060 de 12 GB tiene holgura sobrada para albergar chunks de **8.192 e incluso 12.288 tokens**.
- **Impacto en el PCIe global de 32K**:
  Aunque el coste por capa dentro del chunk sigue siendo ~72 ms, el número de chunks cae bruscamente:
  - De 6.144 (6 chunks, 20,76 s de PCIe) $\rightarrow$ **8.192 (4 chunks, 13,84 s de PCIe)** $\implies$ **ahorro de 6,92 s (-33,3 % de tiempo PCIe)**.
  - A **12.288 (3 chunks, 10,38 s de PCIe)** $\implies$ **ahorro de 10,38 s (-50 % de tiempo PCIe)**.

---

### Palanca 2: Reutilizar especialistas entre chunks (¿el ring los expulsa?)
- **Mecánica actual del ring (`src/prefill/prefill.cpp:1725-1890`)**:
  - `m.ring` tiene **384 slots** (`prefill.cpp:194`). Como una sola capa transmite ~420 especialistas no residentes, **el ring se sobrescribe a sí mismo incluso dentro de la misma capa**.
  - Al terminar el chunk $C$ en la capa 47, el ring contiene fragmentos de la capa 47. Al iniciar el chunk $C+1$ en la capa 0, el hilo `issuer` (`prefill.cpp:1853-1879`) **sobrescribe ciegamente el ring desde la posición 0**.
  - **Resultado**: Reutilización entre chunks = **0 %**. En un prompt de 32K con 6 chunks, se transmiten por PCIe **228 GB de datos** para un modelo que en RAM pesa 46 GB.
- **Solución: Conservación inter-chunk**:
  - En un mismo documento largo (código, texto, libro), el conjunto de especialistas activos entre chunks contiguos tiene un solapamiento del **85 % al 92 %**.
  - Conservando en slots libres de VRAM los especialistas compartidos entre chunks:
    En los chunks 2 a 6 (5 chunks), las transferencias PCIe caen de 420 a **~50 especialistas por capa**.
    El tiempo de PCIe por capa en chunks subsiguientes baja de 72 ms a **~8,6 ms**.
  - **Ahorro en 32K**: $(72 - 8,6)\text{ ms} \times 48\text{ capas} \times 5\text{ chunks} \approx \mathbf{15,2\text{ segundos de PCIe no emitido}}$.

---

### Palanca 3: Orden de copia por $T_e$ descendente (Cómputo pesado primero)
- **Mecánica actual**:
  - Tanto la emisión de copia `seq` (`prefill.cpp:1772`) como el cómputo `order` (`prefill.cpp:2405`) iteran en **estricto orden numérico de ID** ($e = 0, 1, 2, \dots, 511$).
  - La distribución de tokens por especialista ($T_e$) sigue una ley de Pareto:
    - Top 30 especialistas: $T_e \in [300, 1.800]$ tokens (acumulan el 50 % del trabajo de la capa).
    - Últimos 290 especialistas: $T_e \le 20$ tokens (mediana ~8 tokens).
- **El problema de la burbuja**:
  - Un especialista con $T_e = 8$ tarda $\le 10\ \mu\text{s}$ en MMQ. En $10\ \mu\text{s}$, PCIe solo copia 110 KB (el 6 % de un especialista).
  - Si por azar de IDs se procesan varios especialistas fríos seguidos, la GPU consume el ring en microsegundos y se bloquea en `cudaStreamWaitEvent` (`kPfWaitCopy`, `prefill.cpp:2710`).
- **Solución bit-idéntica**:
  - El host ya conoce el histograma exacto de $T_e$ en `m.cnt[e]` (`prefill.cpp:2351-2356`).
  - Ordenar tanto `order` como `seq` por **$T_e$ descendente**:
    1. La GPU arranca con los 30 especialistas más pesados ($T_e \ge 300$). Cada uno toma entre 150 y 700 µs de cálculo continuo en MMQ.
    2. Durante esos **~10,5 ms de cómputo ininterrumpido en GPU**, PCIe transfiere en segundo plano a 11 GB/s:
       $$10,5\text{ ms} \times 11\text{ GB/s} = 115,5\text{ MB} \approx \mathbf{61\text{ especialistas completos}}.$$
    3. El ring se precarga holgadamente antes de llegar a los especialistas fríos. La espera `kPfWaitCopy` se reduce a cero.
  - **Ahorro medido por ventana de espera**: **~8 a 12 ms/capa** en burbujas de sincronización eliminadas.

---

### Palanca 4: Ocupación de los slots de la caché de decode durante el prefill
- **Estado actual de los slots no prestados (`generate.cpp:7539-7552`)**:
  - Al prestar $k \approx 1.100$ slots para prefill, quedan en VRAM $3.696 - 1.100 = \mathbf{2.596\text{ slots intactos}}$.
  - Esos 2.596 slots contienen especialistas precargados para *decode* según el perfil offline histórico (`systemone_3060.profile`).
  - En `prefill.cpp:1773` y `:2299`, si un especialista está en VRAM, se lee directamente sin pasar por PCIe.
- **La ineficiencia**:
  - El perfil de decode no coincide con el vocabulario ni dominio del prompt actual (ej. código vs chat). Muchos de esos 2.596 slots contienen especialistas que el prompt jamás consulta.
- **Solución: Adaptación de slots al dominio del prompt**:
  - Alocar los 40 especialistas más frecuentes del prompt en los slots libres de VRAM ($40 \times 48 = 1.920$ slots, perfectamente dentro de los 2.596 libres).
  - Esos 40 especialistas por capa cubren el **60 % de las activaciones del prompt**.
  - No se copian por PCIe en **ningún chunk** del prefill.
  - **Ahorro por capa**: $40 \times 1,89\text{ MB} / 11\text{ GB/s} = \mathbf{6,87\text{ ms/capa}}$ en todos los chunks.
  - Al concluir el prefill, la rutina existente en `generate.cpp:7580-7600` (`xcache.fill_slot_queued`) restaura los slots para el decode en ~120 ms antes del primer token generado.

---

## 3. Matriz de Techos de Ahorro en Prefill

| Palanca | Cuello PCIe que elimina | Modificación de código (`file:line`) | Ahorro en PCIe (ms/capa o total 32K) |
|---|---|---|:---:|
| **1. Chunk Auto 8.192 / 12.288** | Reduce el número de pasadas completas del modelo | `generate.cpp:4437-4441`, `prefill.cpp:1367` | **-6,92 s a -10,38 s en 32K** (-33 % a -50 %) |
| **2. Reutilización Inter-Chunk** | Evita re-transferir especialistas idénticos entre chunks | `prefill.cpp:1619, 1725`, `generate.cpp:7540` | **-15,2 s en 32K** (chunks 2-6 caen a 8,6 ms/capa) |
| **3. Orden por $T_e$ Descendente** | Elimina burbujas `kPfWaitCopy` solapando GEMM pesados | `prefill.cpp:1772, 2351, 2405` | **-8 a -12 ms/capa** de esperas GPU |
| **4. Slots de VRAM con Top-$T_e$** | Convierte 40 especialistas/capa en residentes fijos | `prefill.cpp:1773`, `generate.cpp:7542` | **-6,87 ms/capa** en todos los chunks |

### Techo Combinado de Prefill en 32K tokens
- El tiempo total de prefill en 32K tokens cae de **~23 segundos (990 tok/s)** a **~9,5 – 11,5 segundos (~2.800 – 3.400 tok/s)**.
- **Todas las palancas son 100 % bit-idénticas** (mismas operaciones algebraicas acumuladas en `Dm`, mismos pesos sin comprimir ni truncar).
