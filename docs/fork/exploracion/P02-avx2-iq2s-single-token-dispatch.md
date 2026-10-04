# P02. Despacho directo del kernel AVX2 IQ2_S para un solo token (nt=1)

## 1. La idea en 2 frases
Habilitar el kernel optimizado AVX2 `row_dot_iq2s<1>` en `native_gu_rows` cuando `nt == 1` para capas de tipo IQ2_S (tipo 22), eliminando la caída al genérico escalar/AVX1 de ggml. Hoy, casi todos los expertos evaluados en decode tienen un solo token (`nt=1`) y el código los desvía por defecto a la rutina genérica lenta.

## 2. De dónde sale
- **ik_llama.cpp**: *ikawrakow/ik_llama.cpp* (kernels AVX2 especializados con FMA unrolled para IQ2_S).
- **Código del repo**: `src/kernels/cpu/iq_avx2.cpp:468` (donde ya existe `gu_rows<TY, 1>` y `row_dot_iq2s<1>`), puenteado erróneamente en `src/kernels/cpu/native_expert.cpp:112-123`.

## 3. Qué cuello de nuestro hardware ataca y por qué aplica
Ataca el **tiempo de cómputo de expertos en CPU en decode (14-19 ms por ventana)**.
El modelo Swift 1.5 tiene 34 de sus 48 capas (~71 %) en formato `IQ2_S`. En el perfil `perf` (`PERF_EXPERTOS.md`), `ggml_vec_dot_iq2_s_q8_K` consumía el **11,59 % de todas las muestras**, mientras que nuestro kernel de bloque (`gu_rows<22,2>`) solo consumía el 1,27 %.
La causa en código (`src/kernels/cpu/native_expert.cpp:112`):
```cpp
if (nt >= mt_min && (iq512_supported(...) || (!cpu512 && iq256_supported(...))))
```
Como `mt_min = 2` por defecto, cuando un experto solo es elegido por 1 token de la ventana de verificación (`nt == 1`, el caso en más del 85 % de las evaluaciones individuales de CPU), **se salta el kernel AVX2** y cae en el `traits(f.gu_type)->vec_dot` de ggml (compilado para baseline conservador).

## 4. Ganancia estimada con nuestras cifras
- En nuestro i5-12400F (6 P-cores con doble puerto FMA 256-bit), `row_dot_iq2s` con bloques sin permutas es ~30 % más rápido por fila que el `vec_dot` genérico de ggml.
- Con 4,35 expertos CPU/capa-ventana en 34 capas IQ2_S = ~148 evaluaciones de expertos por ventana:
  - Tiempo actual en IQ2_S: ~10-12 ms de los 16 ms de CPU.
  - Con el kernel directo para `nt=1`: baja a ~7-8,5 ms (ahorro de **~3 ms de CPU por ventana**).
- Como el modelo de ventana (`C22_TECHO.md`) es $t = \text{densa} + \max(\text{CPU}, \text{PCIe} + \text{aciertos})$, bajar la CPU de 16 ms a 13 ms reduce directamente el tiempo de ventana en ~2,5 ms (de 44 ms a 41,5 ms):
  $$\text{Decode: } \frac{2,3 \text{ tok}}{0,0415 \text{ s}} \approx \mathbf{55,4 \text{ tok/s}} \quad (\mathbf{+7 \text{ a } +9\% \text{ en decode general B1}}).$$

## 5. Riesgo para la calidad
**Ninguno (bit-idéntico)**. El test unitario `iq2s_avx2_test` ya demostró equivalencia bit a bit estricta contra la referencia de ggml.

## 6. Integración en Strata y esfuerzo
- **Punto de integración**: `src/kernels/cpu/native_expert.cpp:112-124`. Cambiar la guarda para permitir `nt == 1` cuando `f.gu_type == 22` en CPUs AVX2:
  ```cpp
  if ((nt >= mt_min || (nt == 1 && f.gu_type == 22)) && avx2 && iq256_supported(f.gu_type)) {
      iq256_gu_rows(f.gu_type, blob, f.gu_row, f.up_off, (int) f.n_embd, act, nt, ff, r0, r1);
      return;
  }
  ```
- **Esfuerzo**: **S** (≤ 5 líneas en C++, no requiere nuevos buffers ni dependencias).
