# P08. Layout entrelazado de 2 filas (RTR) para GEMV de expertos en AVX2

## 1. La idea en 2 frases
Reorganizar los pesos de los expertos en RAM en un formato entrelazado por pares de filas (Row-Transposed Representation, `-rtr` de ik_llama). Permite que una única carga vectorial de la activación calcule 2 filas simultáneamente en dos acumuladores FMA independientes, reduciendo a la mitad las lecturas de activación y saturando los dos puertos FMA de los núcleos P del i5-12400F.

## 2. De dónde sale
- **ik_llama.cpp**: *ikawrakow/ik_llama.cpp*, flag `-rtr` (Row-Transposed Representation para aceleración de GEMV en AVX2/AVX-512).
- **llamafile**: microkernels AVX2 GEMV desenrollados a 2 filas por Justine Tunney (`mozilla-Ocho/llamafile`).

## 3. Qué cuello de nuestro hardware ataca y por qué aplica
Ataca el **tiempo de cómputo de expertos en CPU durante el decode (14-19 ms por ventana, media ~16 ms)**.
En la implementación actual (`src/kernels/cpu/iq_avx2.cpp:446-450`):
```cpp
for (int r = r0; r < r1; ++r) {
    row_dot_any<TY, NT>(blob + (size_t) r * gu_row, nb, y, g);
    row_dot_any<TY, NT>(blob + up_off + (size_t) r * gu_row, nb, y, u);
    ...
}
```
El bucle itera estrictamente fila a fila ($r$). Para cada una de las 1.280 filas de gate y 1.280 filas de up de cada experto, se vuelve a leer el vector de activación `y` desde el inicio (2.560 pasadas por experto).
Aunque `y` resida en L1, el procesador se ve limitado por el puerto de carga de L1 y por la latencia de 4 ciclos de las instrucciones FMA (`_mm256_fmadd_ps` / `_mm256_madd_epi16`) sobre un único acumulador.
El i5-12400F (núcleos Golden Cove) dispone de **dos puertos FMA independientes de 256 bits** (puerto 0 y puerto 1). Entrelazando los pesos de la fila $r$ y $r+1$ en bloques contiguos de 32 bytes (`W[r][0..15]`, `W[r+1][0..15]`), una sola carga de activación alimenta dos multiplicaciones en paralelo, eliminando burbujas de latencia y duplicando el uso de los puertos de cálculo.

## 4. Ganancia estimada con nuestras cifras
*(Cifras base: `C22_TECHO.md` y `ENTREGAS.md` orden 9: CPU expertos 14-19 ms [media 16 ms], ventana 44 ms, 2,3 tok/v en B1 = 50,70 tok/s).*
- En los benchmarks de ik_llama y llamafile, el empaquetado RTR de 2 filas rinde un **25-30 % más de GFLOPS en AVX2** frente a la evaluación fila a fila clásica.
- El tiempo de cómputo de expertos en CPU por ventana se reduce de ~16 ms a **~11,8 ms** (ahorro directo de **~4,2 ms de CPU por ventana**).
- Aplicando el modelo de ventana de `C22_TECHO.md` ($t = \text{densa} + \max(\text{CPU}, \text{PCIe} + \text{aciertos})$):
  - Al bajar la CPU a ~11,8 ms, queda equilibrada con la rama de aciertos+PCIe (~12-14 ms).
  - La ventana total se reduce de 44 ms a **~40,2 ms**:
  $$\text{Decode B1: } \frac{2,3 \text{ tok}}{0,0402 \text{ s}} \approx \mathbf{57,2 \text{ tok/s}} \quad (\mathbf{+12,8\% \text{ en decode B1}}).$$

## 5. Riesgo para la calidad
**Ninguno (bit-exacto)**. Es estrictamente una transposición de memoria contigua en la inicialización; las operaciones aritméticas de punto flotante y acumulación entera son idénticas en valor y orden de reducción.

## 6. Integración en Strata y esfuerzo
- **Punto de integración**: `src/kernels/cpu/expert_layout.cpp` (reempaquetado de las matrices de pesos al cargar el archivo de modelo en RAM) y `src/kernels/cpu/iq_avx2.cpp:430-460` (añadir especialización `row_dot_2rows<TY, NT>`).
- **Esfuerzo**: **M** (requiere función de transposición en carga de memoria de la arena de RAM y kernel desenrollado a 2 acumuladores).
