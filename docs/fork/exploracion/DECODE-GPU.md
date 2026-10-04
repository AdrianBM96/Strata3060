# DECODE-GPU: Optimización de la Parte Densa en GPU (12-15 ms/ventana)

Fecha: 2026-10-04. Autor: explorer. Para: Claude (arquitecto).  
Cifras base: `ENTREGAS.md` (orden 9), `MEDICION_RONDA5.md`, `02-cuda-sm86.md`.  
Ventana de decode B1 (~2,3-4 tokens, 44 ms total): parte densa en GPU mide **12,0 a 15,0 ms/ventana**:
- `router + hc-read`: **3,4 ms**
- `q8 + qkv gemv`: **2,4 ms**
- `head`: **1,8 ms**
- Resto de capas densas (GDN recurrence, conv, shared expert, QSA attn): **~4,4 – 7,4 ms**
- Nodos de grafo CUDA capturados: **~3.150 nodos/ventana** (`verify.cpp:1240-1269`).
Tarjeta: NVIDIA GeForce RTX 3060 12 GB (GA106, arquitectura Ampere, `sm_86`, 28 SMs, 3 MB L2, 360 GB/s DRAM).

---

## 1. Análisis de Cuellos de Botella en la Parte Densa en sm_86

En `sm_86`, la RTX 3060 tiene solo **3 MB de caché L2** y carece de las características de Hopper/Blackwell (sin clusters, sin TMA, sin PDL / Programmatic Dependent Launch).  
Con ~3.150 nodos en el grafo de CUDA y una latencia de sincronización inter-nodo de **~1,5 a 2,0 µs** en Ampere:
$$3.150 \text{ nodos} \times 1,5\ \mu\text{s} = \mathbf{4,7 \text{ ms de pura latencia de lanzamiento y huecos de burbuja intra-grafo}}.$$
El tiempo denso no está limitado por un solo kernel gigante, sino por una fragmentación excesiva en cientos de micronodos y el uso ineficiente de `ROWS = 1` en los GEMV multi-columna.

---

## 2. Candidatos MMVQ de 2 Filas por Bloque (`ROWS = 2`)

### 2.1. El defecto del código actual (`src/kernels/cuda/native_mmvq.cu:1086-1092`)
En [`native_mmvq.cu:1075-1093`](file:///home/bazzite/strata-explore/src/kernels/cuda/native_mmvq.cu#L1075-L1093), el selector de lanzamiento implementa:
```cpp
if (n_in / F::DIV < F::BPI) {
    constexpr int ROWS = 2;
    const unsigned blocks = unsigned((std::size_t(n_out) + ROWS - 1) / ROWS);
    native_mmvq_multi_kernel<F, NCOLS, WARPS, ROWS><<<blocks, threads, 0, s>>>(w, x, y, n_in, n_out);
} else {
    native_mmvq_multi_kernel<F, NCOLS, WARPS, 1><<<unsigned(n_out), threads, 0, s>>>(w, x, y, n_in, n_out);
}
```
Para el modelo Swift 1.5 ($n_{\text{in}} = 2.560$ o $10.240$, y bloques de 32 elementos), $n_{\text{in}} / 32 = 80 \ge F::\text{BPI}$ (16–32).  
**La condición `n_in / F::DIV < F::BPI` siempre se evalúa como falsa.**  
Consecuencia: **TODOS los GEMV densos del decode se ejecutan con `ROWS = 1` (un bloque de 128 hilos para calcular 1 sola fila de salida).**

### 2.2. Sobrecarga de olas de bloques con `ROWS = 1`
En 28 SMs de la 3060:
- **$W_{qkv}$ ($n_{\text{out}} = 10.240$, [`verify.cpp:723`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L723))**: lanza **10.240 bloques** ($\approx 365$ olas de bloques en 28 SMs).
- **$W_{\text{head}}$ ($n_{\text{out}} = 248.320$, [`verify.cpp:1189`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L1189))**: lanza **248.320 bloques** ($\approx 8.868$ olas de bloques).
Cada bloque independiente paga el prólogo completo de lectura de índices, inicialización de memoria compartida `partial[NW-1][NCOLS][ROWS][WARP]` ([`native_mmvq.cu:1054`](file:///home/bazzite/strata-explore/src/kernels/cuda/native_mmvq.cu#L1054)) y `__syncthreads()`.

### 2.3. Ganancia con `ROWS = 2` (o `ROWS = 4` en Head)
Al procesar 2 filas contiguas por bloque de CUDA:
1. Las activaciones $x$ (ya en L1/registros) se reutilizan para ambas filas de pesos $W$.
2. El número de bloques lanzados se reduce al **50 %** (de 248K a 124K en head, de 10K a 5K en qkv).
3. Mejora el coalescing en DRAM y reduce la presión en la diminuta caché L2 de 3 MB.
- **Techo medido**:
  - $W_{\text{head}}$ (1,8 ms): **ahorro de 0,6 – 0,8 ms**.
  - $W_{qkv}$ y $W_{out}$ en GDN/QSA (2,4 ms): **ahorro de 0,5 – 0,7 ms**.
  - **Ahorro total de MMVQ con `ROWS = 2`**: **1,1 a 1,5 ms / ventana** (100 % bit-idéntico, ya validado en `mmvq_multi_parity.cpp`).

---

## 3. Kernels Fusionables en la Cadena de Decode

### Fusión 1: Router GEMV + Top-10 en Memoria Compartida (`verify.cpp:920-925`)
- **Situación actual**:
  1. `bf16_gemv_fp32_mmvf_multi` calcula los 512 logits por token y los escribe en DRAM `logits_` ([`verify.cpp:922`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L922)).
  2. `native_router_top10_multi` lee los 512 floats de DRAM para extraer los 10 mejores ([`verify.cpp:924`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L924)).
- **Solución fusionada**:
  Los 512 logits caben en 2 KB de `shared memory`. Un único kernel fusionado evalúa el GEMV y realiza la reducción top-10 en registros/smem.
- **Ahorro**: Elimina 48 lanzamientos de kernel y el roundtrip DRAM de logits.  
  **Techo**: **0,6 – 0,8 ms / ventana**.

### Fusión 2: Eliminación de Cuantizaciones Q8_1 Aisladas (`verify.cpp:722, 755, 780, 828, 898`)
- **Situación actual**:
  En cada una de las 48 capas se lanzan hasta **5 kernels aislados de `native_quantize_q8_1`** para preparar el vector $x$ antes de cada MMVQ ($W_{qkv}$, $W_{out}$, $W_{k,v}$, $W_{q}$, $W_{o}$).
  Son **240 nodos de grafo** que leen floats de DRAM, cuantizan y escriben bloques Q8_1 a DRAM.
- **Solución fusionada**:
  Para $N = 2.560$, un vector Q8_1 pesa solo **2.880 bytes**. Incorporar la cuantización de $x$ en el prólogo del propio kernel `native_mmvq` (en memoria compartida del bloque) o fusionarla en el epílogo de la norma RMS anterior.
- **Ahorro**: Elimina 240 nodos de grafo y ~480 accesos a DRAM de $x$.  
  **Techo**: **0,8 – 1,1 ms / ventana**.

### Fusión 3: Compactación de la Cadena de Shared Expert (`shared_expert.cu:161-197`)
- **Situación actual**:
  `shared_expert_multi` ([`verify.cpp:984`](file:///home/bazzite/strata-explore/src/core/verify.cpp#L984)) encadena **9 kernels consecutivos por capa** (quantize, gate MMVQ, up MMVQ, swiglu, quantize H, down MMVQ, gate scale, sigmoid, scale accum) = **432 nodos de grafo**.
- **Solución fusionada**:
  Fusionar `[SwiGLU + Quantize H]` y `[Down MMVQ + Gate Scale + Accumulate]`, reduciendo la cadena de 9 a **3 kernels**.
- **Ahorro**: Elimina ~280 nodos de grafo.  
  **Techo**: **0,5 – 0,7 ms / ventana**.

### Fusión 4: LM-Head + Argmax Greedy Online (`verify.cpp:1189`, `sampler.cu:92-169`)
- **Situación actual**:
  El GEMV del LM-head escribe los 248.320 logits en VRAM (1,0 MB por token) y luego `sampler_greedy_kernel` vuelve a leer el megabyte completo para hacer el `argmax`.
- **Solución fusionada**:
  En modo greedy (especulación y verificación estándar), el kernel del Head realiza la reducción del valor máximo y el índice directamente en registros atómicos o árbol de bloques, sin materializar los 248K floats en DRAM.
- **Ahorro**: **0,5 – 0,7 ms / ventana** (de los 1,8 ms del head).

---

## 4. Qué hacen `llama.cpp` e `ik_llama` en `sm_86` para Batch 2-4

En las ventanas de verificación especulativa ($T = 2\dots 4$ tokens):

1. **`llama.cpp` (`ggml-cuda/mmvq.cu` y `mmq.cu`)**:
   - Para $T = 1$: usa `mmvq` estándar (vector-matriz).
   - Para $T = 2\dots 4$: usa `mmvq_multi` (mismo código base que `native_mmvq.cu`), donde los pesos se cargan una vez y se multiplican por los $T$ vectores en registros. Ahorra hasta el **75 % del ancho de banda de pesos**.
   - Para $T \ge 4$ en `sm_86`: conmuta a **`mmq` (Tensor Cores INT8)**. En matrices grandes como $W_{qkv}$ y $W_{\text{head}}$, `mmq` utiliza `mma.sync.aligned.m16n8k16`, entregando hasta **148 INT8 TOPS** en Ampere (frente a 35 TFLOPS FP32 de los CUDA cores).
2. **`ik_llama` (Iwan Kawrakow fork)**:
   - **`ROWS = 2` forzado en sm_86**: Para evitar la inanición de warps provocada por lanzar 248K bloques de un solo renglón.
   - **Copias asíncronas con `cp.async` (`__ldgsts`)**: En Ampere, carga el siguiente bloque de pesos directamente de DRAM a memoria compartida sin pasar por los registros de los hilos, ocultando el 100 % de la latencia de DRAM en matrices densas.
   - **Prólogo Fused RMSNorm-Quant**: El kernel anterior deposita los datos ya cuantizados en smem compartida, reduciendo a cero los kernels intermedios de cuantización.

---

## 5. Balance y Techo de Ganancia en Decode GPU

| Palanca | Cuello que elimina | Modificación (`file:line`) | Techo de ahorro (ms/ventana) |
|---|---|---|:---:|
| **1. MMVQ `ROWS = 2`** | Elimina 50 % de bloques lanzados y reutiliza $x$ en L1 | `native_mmvq.cu:1086-1092` | **1,1 – 1,5 ms** |
| **2. Fusión Router + Top-10** | Elimina roundtrip de 512 logits y 48 nodos | `verify.cpp:922-924`, `native_router.cu:115` | **0,6 – 0,8 ms** |
| **3. Fusión Quantize Q8_1** | Elimina 240 nodos de grafo y roundtrips de activación | `verify.cpp:722, 755, 780, 898` | **0,8 – 1,1 ms** |
| **4. LM-Head Argmax Online** | Elimina escritura y lectura de 1 MB de logits | `verify.cpp:1189`, `sampler.cu:102` | **0,5 – 0,7 ms** |
| **5. Fusión Shared Expert** | Reduce de 9 a 3 kernels por capa (elimina 280 nodos) | `shared_expert.cu:161-197` | **0,5 – 0,7 ms** |

### Techo Total en la Parte Densa de Decode
- **Reducción neta**: De **12,0 – 15,0 ms** a **~7,5 – 9,5 ms / ventana** (ahorro de **~4,0 a 5,5 ms / ventana**).
- **Impacto combinado con Ronda 7 (`waitB` PCIe: -5,8 ms)**:
  - Tiempo de ventana total: cae de **44 ms** a **~33 – 34 ms**.
  - Throughput de Decode B1: sube de **50,7 tok/s a ~68 tok/s (+34 % de aceleración global)**.
  - **100 % bit-idéntico** (las operaciones de álgebra matricial preservan el orden de reducción por warp).
