# P09. Layout contiguo Gate-Up para fusión SwiGLU en registros (ktransformers)

## 1. La idea en 2 frases
Reorganizar en RAM la matriz de cada experto para que la fila $r$ de `gate` y la fila $r$ de `up` queden físicamente contiguas (`[Gate_r, Up_r, Gate_{r+1}, Up_{r+1}...]`), eliminando la separación actual de ~700 KB entre matrices. Esto convierte dos flujos de lectura concurrentes en un único flujo de streaming lineal, evitando la saturación del prefetcher L2 de los núcleos P y permitiendo resolver SwiGLU directamente en registros.

## 2. De dónde sale
- **ktransformers**: repo `kvcache-ai/ktransformers`, kernel `kt-kernel/cpu/moe_gate_up.cpp` (SwiGLU fused interleaved layout para CPUs x86).
- **FastLLM**: repo `ztxz16/fastllm`, `src/devices/cpu/cpudevice.cpp` (técnica de empaquetado adyacente para capas SwiGLU).

## 3. Qué cuello de nuestro hardware ataca y por qué aplica
Ataca la **ineficiencia de memoria RAM (DDR4-2133 sin XMP, ~27 GB/s reales) y fallos de prefetcher en CPU**.
En la implementación actual (`src/kernels/cpu/native_expert.cpp:125-126` y `iq_avx2.cpp:446-448`):
```cpp
const uint8_t* gr = blob + (size_t) r * f.gu_row;
const uint8_t* ur = blob + f.up_off + (size_t) r * f.gu_row;
```
En cada fila $r$, el procesador lee de `gr` (offset relativo 0) y de `ur` (offset `up_off` a ~700 KB de distancia en RAM).
En los 6 P-cores del i5-12400F (con 1,25 MB de L2 por núcleo), alternar a alta velocidad entre dos punteros distantes provoca contención en los stream prefetchers del hardware de Intel (L2 Streamer / Spatial Prefetcher). Esto satura la cola de peticiones de línea de caché y reduce el ancho de banda efectivo aprovechado de la RAM a solo ~18-20 GB/s (lejos de los 27 GB/s medidos con streaming puro en `03-host-cpu-ram-pcie.md`).
Agrupando `[Gate_r, Up_r]` de forma contigua, la CPU ejecuta una lectura 100 % secuencial en memoria, alimentando los registros vectoriales con máxima tasa de acierto de prebúsqueda L2.

## 4. Ganancia estimada con nuestras cifras
*(Cifras base: `C22_TECHO.md` y `ENTREGAS.md` orden 9: CPU expertos 14-19 ms [media 16 ms], ventana 44 ms, 2,3 tok/v en B1 = 50,70 tok/s, RAM medible en streaming ~27 GB/s).*
- Con streaming contiguo unificado, el aprovechamiento del ancho de banda de la DDR4-2133 sube de ~19 GB/s a ~25,5 GB/s (+34 % de rendimiento de carga de pesos en los expertos de CPU).
- El tiempo de cálculo de expertos en CPU baja de ~16 ms a **~13,6 ms** (ahorro directo de **~2,4 ms por ventana**).
- En el balance intra-capa ($t = \text{densa} + \max(\text{CPU}, \text{PCIe} + \text{aciertos})$):
  - El tiempo de ventana se reduce de 44 ms a **~42,2 ms**:
  $$\text{Decode B1: } \frac{2,3 \text{ tok}}{0,0422 \text{ s}} \approx \mathbf{54,5 \text{ tok/s}} \quad (\mathbf{+7,5\% \text{ en decode B1}}).$$
- Combinado con P08 (2 filas entrelazadas), el streaming unificado evita cualquier cuello de ancho de banda en DDR4.

## 5. Riesgo para la calidad
**Ninguno (bit-exacto)**. No se altera ningún valor de peso ni redondeo intermedio; únicamente se permuta el orden físico de las direcciones de memoria donde residen los bytes de los pesos en la RAM.

## 6. Integración en Strata y esfuerzo
- **Punto de integración**: `src/kernels/cpu/expert_layout.cpp:120-180` (en la función que organiza los blobs de expertos nativos en RAM) y `src/kernels/cpu/iq_avx2.cpp:440-455` (iterar sobre el bloque contiguo `gu_pair`).
- **Esfuerzo**: **M** (modificación del layout en la carga de la arena y ajuste del stride en el kernel AVX2).
