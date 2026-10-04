# P06. Microkernel AVX-VNNI para la proyección Down (q2_0) en CPU

## 1. La idea en 2 frases
Reemplazar la secuencia clásica AVX2 de 2 etapas (`_mm256_maddubs_epi16` + `_mm256_madd_epi16`) en la proyección `down` (`q2_0`) por la instrucción nativa AVX-VNNI de 256 bits (`_mm256_dpbusd_epi32`). Esto duplica el rendimiento de operaciones multiply-accumulate en los 6 núcleos P del procesador i5-12400F.

## 2. De dónde sale
- **FastLLM**: repo `ztxz16/fastllm` (kernels optimizados para cuantizaciones de enteros con VNNI de 256 bits).
- **llamafile**: microkernels AVX-VNNI desarrollados por Justine Tunney en `mozilla-Ocho/llamafile` para CPUs Alder Lake / Raptor Lake sin AVX-512.

## 3. Qué cuello de nuestro hardware ataca y por qué aplica
Ataca el **tiempo de CPU en decode invertido en la proyección `down` (8,21 % de todas las muestras)**.
En el perfil `perf` (`PERF_EXPERTOS.md`), la función `q2_0_gguf_rows_multi_avx2` ocupa el **8,21 % del tiempo total del proceso** (unos ~3,5 ms de los 14-19 ms de CPU por ventana).
En el modelo Swift 1.5, **las 48 capas sin excepción** utilizan `q2_0` para la proyección `down` de los expertos (pesa ~451 KB por experto).
Nuestro procesador (i5-12400F, arquitectura Alder Lake P-cores "Golden Cove") no tiene AVX-512, pero **sí implementa AVX-VNNI** (`avx_vnni` en `/proc/cpuinfo`).
Hoy, el archivo `src/kernels/cpu/q2_avx2.cpp` emula el producto escalar de enteros de 8 bits con dos instrucciones vectoriales AVX2 encadenadas (`vpmaddubsw` + `vpmaddwd`). Con AVX-VNNI, la instrucción `vpdpbusd` realiza 4 multiplicaciones y acumulaciones en un entero de 32 bits en **un único ciclo de reloj con throughput de 2 instrucciones por ciclo**.

## 4. Ganancia estimada con nuestras cifras
- En los 6 P-cores a 4,0-4,4 GHz, `_mm256_dpbusd_epi32` reduce las instrucciones de cálculo del producto escalar de `down` a la mitad y libera presión sobre los puertos de ejecución 0 y 1.
- Tiempo de `down` en CPU hoy: ~3,5 ms por ventana de decode.
- Tiempo de `down` con AVX-VNNI: baja a ~1,8-2,0 ms (ahorro de **~1,5 a 1,7 ms de CPU por ventana**).
- Al reducirse el tiempo de CPU agregado de 16 ms a ~14,3 ms, la ventana de decode se reduce de 44 ms a ~42,5 ms:
  $$\text{Decode: } \frac{2,3 \text{ tok}}{0,0425 \text{ s}} \approx \mathbf{54,1 \text{ tok/s}} \quad (\mathbf{+4 \text{ a } +5\% \text{ en decode general B1}}).$$

## 5. Riesgo para la calidad
**Ninguno (bit-idéntico)**. `vpdpbusd` efectúa exactamente la misma suma de productos enteros que la cadena `maddubs + madd`, acumulando en `int32` sin pérdida de precisión ni redondeos intermedios.

## 6. Integración en Strata y esfuerzo
- **Punto de integración**: `src/kernels/cpu/q2_avx2.cpp:80-160`. Añadir una variante `q2_0_down_rows_vnni` que utilice `_mm256_dpbusd_epi32` condicionado a `cpu_has_avx_vnni()`:
  ```cpp
  #if defined(__AVX_VNNI__)
  acc = _mm256_dpbusd_epi32(acc, q_bytes, a_bytes);
  #endif
  ```
  y compilar dicho módulo con `-mavxvnni`.
- **Esfuerzo**: **S** (~30 líneas de C++ intrinsics en `q2_avx2.cpp`).
