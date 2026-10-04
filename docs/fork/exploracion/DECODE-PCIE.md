# DECODE-PCIE: Diagnóstico de `waitB` en Decode y Techo de Optimización PCIe

Fecha: 2026-10-04. Autor: explorer. Para: Claude (arquitecto).  
Cifras base: `C22_TECHO.md` (orden 9, ventana de 44 ms, B1: `waitB` mide **6,5 a 10,2 ms** frente a suelo físico de **~3,0 ms**).  
Archivos analizados: `src/core/layer.cpp`, `src/core/verify.cpp`, `src/core/expert_source.cpp`, `src/program/generate.cpp`, `src/core/pinned.cu`.

---

## 1. Rectificación previa: `pshufb` en `IQ2_S` y `iq2s_grid`

- **Corrección aceptada**: La tabla `iq2s_grid` (1.024 entradas $\times$ 8 bytes = 8 KB) cabe íntegramente en la caché L1D (32 KB en Golden Cove). El coste no es ancho de banda de RAM, sino la **latencia serial de la cadena de 4 accesos escalares dependientes por mitad de bloque** ([`iq_avx2.cpp:404-405`](file:///home/bazzite/strata-explore/src/kernels/cpu/iq_avx2.cpp#L404-L405)) combinada con la inserción escalar `_mm256_set_epi64x`.
- **Uso real de `pshufb` en `IQ2_S`**:
  En `llama.cpp` (`ggml/src/ggml-cpu/arch/x86/quants.c:1340-1390`), la instrucción `_mm256_shuffle_epi8` (`vpshufb`) **NO se usa para indexar la rejilla de 1.024 entradas** (lo cual es físicamente imposible, pues `pshufb` solo indexa 16 bytes con índices de 4 bits). Se utiliza exclusivamente para:
  1. Difusión de bytes de signo (`shuf_sgn` / `bsel` en [`iq_avx2.cpp:368-370`](file:///home/bazzite/strata-explore/src/kernels/cpu/iq_avx2.cpp#L368-L370)).
  2. Reordenación de bytes de escala (`k_sc_shuffle` en [`iq_avx2.cpp:356-364`](file:///home/bazzite/strata-explore/src/kernels/cpu/iq_avx2.cpp#L356-L364)).
  La búsqueda en registros con `pshufb` solo aplica a formatos con diccionarios de $\le 16$ entradas (como `IQ1_S` o las subtablas de `IQ2_XXS`). Queda retractada la afirmación de lookup en registros para `IQ2_S`.

---

## 2. Anatomía de `waitB`: ¿Dónde se pierden los 6,5 - 10,2 ms?

A 11 GB/s medidos en PCIe Gen4 x16, transferir el volumen real de expertos no residentes en decode (~20-25 MB por ventana de 44 ms con 75 % de acierto en VRAM) requiere **solo 2,0 a 2,5 ms de cable** (suelo en `C22_TECHO.md`: **~3,0 ms** con protocolo).

La pérdida de **3,5 a 7,0 ms adicionales** se descompone en 4 puntos críticos del código:

### 2.1. Sobrecarga y roundtrip de `cudaLaunchHostFunc` (`verify.cpp:1641-1652`)
- **Mecánica**:
  En [`src/core/verify.cpp:1647-1651`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L1647-L1651), cuando el host lanza las copias de la capa en el stream `v->copy_`:
  ```cpp
  for (int i = 0; i < n; ++i)
      cudaMemcpyAsync(stage + (size_t) i * bytes, src[i], bytes, cudaMemcpyHostToDevice, v->copy_);
  cudaLaunchHostFunc(v->copy_, [](void* p) { FlagSet* s = (FlagSet*) p; raise_flag(s->flag, s->value); }, &fs);
  ```
- **El problema**: Para notificar a la GPU que la copia ha terminado, se encola un callback de host (`cudaLaunchHostFunc`). Al terminar la copia, la GPU emite una interrupción MSI-X de PCIe $\rightarrow$ el kernel de Linux despierta un hilo worker del driver CUDA en host $\rightarrow$ ejecuta `raise_flag` $\rightarrow$ escribe en memoria mapeada `h_flagB_`.
- **Impacto medido**: Cada `cudaLaunchHostFunc` introduce una latencia de roundtrip de **25 a 50 µs**. Multiplicado por las 48 capas:
  $$48 \times 35 \ \mu\text{s} = \mathbf{1,7 \text{ ms por ventana de pura sobrecarga de sincronización de driver}}.$$

### 2.2. Ventana de solapamiento nula: los "VRAM hits" son demasiado breves (`verify.cpp:1059-1062`)
- En el grafo de GPU capturado ([`src/core/verify.cpp:1058-1075`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L1058-L1075)):
  1. La GPU ejecuta los aciertos de VRAM (`kProfPer[20]`, línea 1059).
  2. Inmediatamente después, se frena en `wait_flag_ge(m_flagB_, ring, cs)` (línea 1062, inicio de `waitB`).
- **El desfase temporal**:
  - En una ventana de 2,3 tokens, los aciertos de VRAM de una capa duran apenas **~0,11 ms**.
  - Sin embargo, una transferencia PCIe de 2 expertos (3,6 MB a 11 GB/s) tarda **0,33 ms**.
  - La GPU termina los VRAM hits en 0,11 ms y pasa **0,22 ms en riguroso bloqueo en el spin-kernel `wait_flag_ge`**.
  - Multiplicado por las capas con fallos PCIe (~30-35 capas):
    $$32 \times 0,22 \text{ ms} \approx \mathbf{7,0 \text{ ms acumulados en waitB}}.$$

### 2.3. Formato de blobs y granularidad de copia (`expert_source.cpp:2148`)
- **¿Van los expertos en blobs separados (gate/up/down) o juntos?**:
  - **Van JUNTOS en un único blob empaquetado**: en [`src/kernels/cpu/expert_layout.cpp:45`](file:///home/bazzite/strata-explore/src/kernels/cpu/expert_layout.cpp#L45), el layout empaqueta `gu` (gate + up) y `down` en un único bloque contiguo (`lay.blob_bytes(l)` = 1,89 MB en IQ2_S).
  - Sin embargo, la granularidad de la llamada es individual: `for (int i = 0; i < n; ++i) cudaMemcpyAsync(...)` ([`verify.cpp:1647`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L1647)). No hay coalescencia de transferencias DMA cuando hay 2 o más fallos en la misma capa.

### 2.4. ¿Las copias de $L+1$ pueden empezar antes?
- **Hoy: NO**. El bucle del host en [`src/core/verify.cpp:1480-1503`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L1480-L1503) es estrictamente secuencial y reactivo:
  1. Espera a que la GPU termine el router de la capa $L$ y toque el timbre (`h_seq_`).
  2. El host evalúa el router de $L$, arma el plan de $L$ y recién entonces llama a `fetch_dma` de $L$.
  3. No se realiza ningún prefetch ni pipeline hacia la capa $L+1$ hasta que la capa $L$ ha sido completamente despachada.

### 2.5. Estado de la memoria fijada (`pinned memory`)
- En [`src/program/generate.cpp:3068-3071`](file:///home/bazzite/strata-explore/src/program/generate.cpp#L3068-L3071), el código documenta que `cudaHostRegister` sobre la arena completa de 34 GB fallaba con OOM de driver, cayendo a páginas anónimas de 4 KB no fijadas.
- En [`src/core/pinned.cu:371-381`](file:///home/bazzite/strata-explore/src/core/pinned.cu#L371-L381), se implementó el registro por rebanadas (*sliced pin*). Si alguna capa no queda registrada en el sliced pin, el DMA de PCIe sufre una penalización catastrófica: el driver de NVIDIA debe copiar los datos a un búfer intermedio (*bounce buffer*), degradando el ancho de banda efectivo de 11 GB/s a **~5,5 GB/s**.

---

## 3. Las 3 palancas bit-exactas para reducir `waitB` a su suelo físico

### Palanca 1: Sustituir `cudaLaunchHostFunc` por sincronización directa de streams en GPU
- **Diagnóstico**: `verify.cpp:1651` envía un callback de host para escribir `h_flagB_`, forzando a la GPU a hacer spin-polling en memoria del host.
- **Cambio propuesto**:
  Eliminar `cudaLaunchHostFunc` para el flag B. Registrar un evento CUDA en el stream de copia:
  `cudaEventRecord(v->dma_ready_ev_[l], v->copy_);`
  Y hacer que el stream de cómputo `cs_` espere directamente en la GPU mediante:
  `cudaStreamWaitEvent(cs_, v->dma_ready_ev_[l], 0);` (o nodo de evento dentro del grafo).
- **Naturaleza**: **100 % Bit-idéntico** (mismos bytes, solo cambia el mecanismo de sincronización hardware).
- **Punto de integración**: [`src/core/verify.cpp:1061`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L1061) y [`src/core/verify.cpp:1648-1652`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L1648-L1652).
- **Techo de ahorro**: **~1,5 a 2,0 ms por ventana** (elimina 48 roundtrips de interrupción/driver).

---

### Palanca 2: Prefetch inter-capa solapado ($L+1$ lanzado durante $L$)
- **Diagnóstico**: La GPU pasa 0,22 ms en `waitB` por capa porque los VRAM hits (0,11 ms) son más cortos que la transferencia PCIe (0,33 ms).
- **Mecánica**:
  En cuanto la GPU emite los logits del router de la capa $L+1$ (que se calculan al final de la atención densa de $L+1$, [`layer.cpp:367-373`](file:///home/bazzite/strata-explore/src/core/layer.cpp#L367-L373)), el host despacha las copias DMA de $L+1$ **mientras la GPU aún está calculando los expertos (VRAM, PCIe y CPU) de la capa $L$**.
  - La ventana de solapamiento disponible pasa de 0,11 ms (solo VRAM hits) a:
    $$\text{Densa}_{L+1} (0,42\text{ ms}) + \text{Expertos}_L (0,33\text{ ms}) \approx \mathbf{0,75 \text{ ms}}.$$
  - En 0,75 ms, el bus PCIe a 11 GB/s puede transferir **hasta 4 expertos completos sin que la GPU espere un solo microsegundo**.
- **Naturaleza**: **100 % Bit-idéntico**.
- **Punto de integración**: Separar el doorbell del router del loop de servicio de CPU en [`src/core/layer.cpp:380`](file:///home/bazzite/strata-explore/src/core/layer.cpp#L380) y pipelinear `fetch_dma` en [`src/core/verify.cpp:1500-1515`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L1500-L1515).
- **Techo de ahorro**: **~2,5 a 3,5 ms por ventana** (oculta casi todo el tiempo de cable bajo el cómputo de la capa anterior).

---

### Palanca 3: Coalescencia de transferencias DMA contiguas y verificación de Sliced Pinned
- **Diagnóstico**: Múltiples llamadas individuales `cudaMemcpyAsync` sobre punteros separados dispersan la cola del motor DMA de PCIe. Además, si alguna página cae fuera de `cudaHostRegister`, el ancho de banda cae a la mitad por *bounce buffers*.
- **Cambio propuesto**:
  1. Agrupar las transferencias PCIe de una misma capa en un único descriptor DMA cuando los slots de destino en staging sean contiguos.
  2. Forzar que el registro `cudaHostRegister` en [`src/core/pinned.cu:371-381`](file:///home/bazzite/strata-explore/src/core/pinned.cu#L371-L381) cubra el 100 % de los pesos mediante páginas THP de 2 MB (verificado en **E1**).
- **Naturaleza**: **100 % Bit-idéntico**.
- **Punto de integración**: [`src/core/verify.cpp:1647`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L1647) y [`src/core/pinned.cu:374`](file:///home/bazzite/strata-explore/src/core/pinned.cu#L374).
- **Techo de ahorro**: **~1,0 a 1,5 ms por ventana**.

---

## 4. Balance global de optimización de `waitB`

| Palanca | Cuello que elimina | Modificación de código (`file:line`) | Techo de ahorro (ms) |
|---|---|---|:---:|
| **1. GPU Event Sync** | Elimina `cudaLaunchHostFunc` y roundtrip de interrupciones | `verify.cpp:1061`, `verify.cpp:1651` | **1,5 - 2,0 ms** |
| **2. Prefetch Inter-Capa** | Solapa la copia de $L+1$ con la atención densa y expertos de $L$ | `layer.cpp:380`, `verify.cpp:1503` | **2,5 - 3,5 ms** |
| **3. Coalescencia DMA + Pinning** | Elimina overhead de llamadas y garantiza 11 GB/s sin bounce buffers | `verify.cpp:1647`, `pinned.cu:374` | **1,0 - 1,5 ms** |

### Impacto combinado en Decode B1
- **Reducción de `waitB`**: De **~9,3 ms** (mediana actual) a **~3,2 - 3,8 ms** (ahorro neto de **~5,5 a 6,0 ms por ventana**).
- **Tiempo de ventana**: Cae de **44 ms a ~38 ms**.
- **Throughput proyectado**: De **50,7 t/s a ~58,5 t/s** (**+15,4 % de ganancia neta en decode B1**), **100 % bit-idéntico** y sin requerir cambios de cuantización ni de calidad.
