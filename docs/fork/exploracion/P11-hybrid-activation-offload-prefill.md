# P11. Prefill híbrido por activación: expertos de baja cardinalidad a CPU (Fiddler)

## 1. La idea en 2 frases
En lugar de transferir por PCIe los ~1,8 MB de pesos de un experto no residente para calcular un puñado de tokens en prefill, transferir únicamente las activaciones ($x$, ~5 KB/token) a la CPU y calcular el experto directamente en RAM (27 GB/s). La GPU solo calcula los expertos residentes y aquellos no residentes que concentren un lote grande de tokens ($T_e \ge 32$).

## 2. De dónde sale
- **Fiddler**: *Fiddler: CPU-GPU Orchestration for Fast Inference of Mixture-of-Experts Models* (Kamahori et al., Univ. Washington, 2024, arXiv:2402.07033), principio de *"Compute-centric offloading: transfer activations instead of weights"*.
- Modelado de intensidad de transferencia en **MoE-Lightning** (Schafhalter et al., ICML 2024).

## 3. Qué cuello de nuestro hardware ataca y por qué aplica
Ataca el **volumen masivo de datos que satura el bus PCIe Gen4 (11 GB/s) en prefill (~38 GB por chunk de 6.144 tokens)**.
En cada chunk de prefill, el histograma de asignación de tokens por experto (`m.cnt` en `src/prefill/prefill.cpp:2406`) presenta una distribución asimétrica:
- Algunos expertos concentran $T_e \ge 64$ tokens. En ellos, la intensidad aritmética justifica transferir el blob de pesos de 1,8 MB a la GPU para ejecutar tensor cores.
- Sin embargo, aproximadamente **~180-220 expertos no residentes por capa** son activados por un número muy reducido de tokens ($T_e \le 16$).
Para un experto llamado por 8 tokens, transferir 1,8 MB de pesos por PCIe (11 GB/s = ~160 $\mu$s) para calcular apenas 8 productos fila es un desperdicio del bus (se transfieren 225 KB de datos por cada token procesado).
Si en cambio la GPU envía las activaciones de esos 8 tokens a la CPU por PCIe:
$$8 \text{ tokens} \times 2.560 \text{ dim} \times 2 \text{ bytes (BF16)} = \mathbf{40 \text{ KB de transferencia}} \quad (\mathbf{45\times \text{ menos datos que transferir los pesos}}).$$
El i5-12400F procesa esos 8 tokens directamente en la memoria RAM (donde ya residen los 512 expertos a ~27 GB/s de ancho de banda local) en paralelo, mientras la GPU avanza con los lotes pesados.

## 4. Ganancia estimada con nuestras cifras
*(Cifras base: `RESUMEN_FINAL.md:99`: prefill a 990 tok/s en 128K; tráfico PCIe hoy en chunk de 6.144 tokens = 48 capas × 440 expertos no residentes × 1,8 MB = ~38 GB por chunk = 3,45 s de transferencia a 11 GB/s).*
- Desviando a CPU los ~200 expertos/capa con $T_e \le 16$ tokens:
  - Los expertos cuyos pesos cruzan por PCIe se reducen de 440 a ~240 por capa (**−45 % de pesos transmitidos**).
  - El volumen de pesos por PCIe baja de 38 GB a **~20,7 GB por chunk**.
  - El volumen de activaciones enviadas a la CPU (y resultados devueltos a GPU) añade solo:
    $$48 \text{ capas} \times 200 \text{ expertos} \times 8 \text{ tokens} \times 5 \text{ KB} \times 2 \approx \mathbf{768 \text{ MB por chunk}}.$$
  - El tiempo neto de transferencia PCIe cae de 3,45 s a **~1,95 s por chunk** (ahorro directo de **~1,5 s por chunk**).
- En un prompt largo de 32K tokens (5,3 chunks):
  $$\text{Tiempo prefill 32K}: \text{de } \sim 22 \text{ s a } \sim 14,0 \text{ s} \quad (\text{velocidad de prefill sube a } \sim \mathbf{1.350 \text{ tok/s}}, \mathbf{+36\%}).$$

## 5. Riesgo para la calidad
**Ninguno (bit-exacto)**. La formulación matemática es idéntica; la suma ponderada del router combina los mismos sumandos con idénticos pesos.

## 6. Integración en Strata y esfuerzo
- **Punto de integración**: `src/prefill/prefill.cpp:2405-2430`. En la clasificación de expertos, segregar en tres listas: `order_gpu_resident`, `order_gpu_streamed` (si `cnt >= 16`) y `order_cpu_offload` (si `cnt < 16`). Encolar la transferencia de activaciones hacia el pool de CPU y recibir los resultados parciales antes del residual.
- **Esfuerzo**: **M** (orquestación asíncrona de buffers entre `prefill.cpp` y `pool.cpp`).
