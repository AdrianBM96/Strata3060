# P12. Descomposición aditiva split-reduce CPU+GPU en prefill (DeepSpeed-MoE)

## 1. La idea en 2 frases
Eliminar por completo el streaming de pesos por PCIe en prefill desacoplando la capa MoE en dos evaluaciones aditivas paralelas ($y = y_{\text{GPU}} + y_{\text{CPU}}$): la GPU computa exclusivamente los ~72 expertos residentes en VRAM (0 bytes de pesos transferidos) mientras la CPU calcula en paralelo los expertos no residentes en RAM, intercambiando únicamente los tensores de activación y sumando el resultado en GPU con un kernel aditivo.

## 2. De dónde sale
- **DeepSpeed-MoE**: *DeepSpeed-MoE: Advancing Mixture-of-Experts Inference and Training to Power Next-Generation AI Models* (Rajbhandari et al., Microsoft, ICML 2022).
- Patrón heterogéneo de reducción MoE en **ktransformers** (`kt-kernel/cpu/moe_reduce.cpp`).
- Principio análogo ya implementado en el **decode** de Strata (`moe_hit_add` en `src/core/verify.cpp:1091`).

## 3. Qué cuello de nuestro hardware ataca y por qué aplica
Ataca el **cuello de botella de transferir 38 GB de pesos por el cable PCIe Gen4 (11 GB/s) en cada chunk de prefill**.
En el diseño actual de prefill (`src/prefill/prefill.cpp`), la GPU asume el 100 % de los expertos, forzando a que ~440 expertos por capa crucen el bus PCIe chunk tras chunk (~38 GB por chunk, requiriendo 3,45 s de transferencia continua por el cable en cada chunk de 6.144 tokens).
Sin embargo, una capa MoE es intrínsecamente aditiva y conmutativa respecto a la combinación ponderada de los expertos:
$$y(t) = \sum_{e \in \text{top-10}} w_e(t) \cdot E_e(x(t)) = \underbrace{\sum_{e \in \text{VRAM}} w_e(t) E_e(x(t))}_{y_{\text{GPU}}(t)} + \underbrace{\sum_{e \notin \text{VRAM}} w_e(t) E_e(x(t))}_{y_{\text{CPU}}(t)}.$$
- La GPU calcula $y_{\text{GPU}}$ utilizando **exclusivamente sus 72 expertos residentes**, que ya están en VRAM y no requieren ni un solo byte de transferencia PCIe.
- La CPU (i5-12400F) calcula $y_{\text{CPU}}$ accediendo a los expertos restantes directamente en los 62 GB de memoria RAM local (a ~27 GB/s de ancho de banda).
- El tráfico por el bus PCIe pasa de mover matrices enteras de pesos a mover únicamente los tensores de activación:
  $$\text{Activación } x \text{ (chunk 6.144 tok)} = 6.144 \times 2.560 \times 2 \text{ B} = \mathbf{31,4 \text{ MB}}.$$
  $$\text{Salida parcial } y_{\text{CPU}} = 6.144 \times 2.560 \times 2 \text{ B} = \mathbf{31,4 \text{ MB}}.$$
  $$\text{Total PCIe por capa} = 62,8 \text{ MB} \quad (\text{en vez de } \sim 790 \text{ MB}, \mathbf{12,5\times \text{ menos tráfico}}).$$

## 4. Ganancia estimada con nuestras cifras
*(Cifras base: `RESUMEN_FINAL.md:99`: prefill actual a 990 tok/s en 128K; tráfico PCIe hoy en chunk de 6.144 tokens = ~38 GB por chunk = 3,45 s de transferencia a 11 GB/s).*
- Tráfico total por PCIe por chunk de 6.144 tokens:
  $$\text{Tráfico nuevo} = 48 \text{ capas} \times 62,8 \text{ MB} \approx \mathbf{3,0 \text{ GB por chunk}} \quad (\text{frente a 38 GB hoy, } \mathbf{-92\% \text{ de datos PCIe}}).$$
- A 11 GB/s medidos, el tiempo de transferencia PCIe cae en picado de 3,45 s a **~0,27 s por chunk** (ahorro directo de **~3,18 s por chunk** en el cable).
- Para un prompt de 32K tokens (5,3 chunks):
  - Ahorro de tiempo en PCIe: $5,3 \times 3,18 \text{ s} \approx \mathbf{16,8 \text{ s}}$.
  - Tiempo de prefill de 32K baja de ~22 s a **~11-12 s**:
  $$\text{Rendimiento prefill}: \text{de } 990 \text{ tok/s a } \sim \mathbf{1.800 \text{ tok/s}} \quad (\mathbf{+80\% \text{ en throughput de prefill}}).$$

## 5. Riesgo para la calidad
**Ninguno (bit-exacto)**. La suma vectorial es exacta dentro de la precisión de coma flotante estándar de acumulación en la GPU.

## 6. Integración en Strata y esfuerzo
- **Punto de integración**: `src/prefill/prefill.cpp:2400-2800`. Reutilizar el patrón de fork-join y combinación que el motor ya utiliza con éxito en decode (`src/core/verify.cpp:1085-1091`, `moe_hit_add`). Desactivar el streaming de pesos `m.ring` en prefill y sustituirlo por el lanzamiento asíncrono en `pool.cpp`.
- **Esfuerzo**: **L** (requiere reestructurar la pipeline de prefill para sincronizar streams de GPU y CPU con el kernel de adición final).
