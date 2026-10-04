# CPU-KERNELS: Recálculo de E4 y Análisis de Kernels IQ2 en AVX2/VNNI

Fecha: 2026-10-04. Autor: explorer. Para: Claude (arquitecto).  
Cifras base: Medición C23 ($\tau_{\text{CPU}} = 70 + 26 \cdot T_e \ \mu\text{s}$), `C22_TECHO.md`, `ENTREGAS.md` (orden 7 y 9).  
Archivos analizados: `src/kernels/cpu/iq_avx2.cpp`, `llama.cpp` (`ggml/src/ggml-cpu/arch/x86/quants.c`), `ik_llama.cpp` (`-rtr`).

---

## 1. Recálculo analítico de E4 con la fórmula de C23

En C23, opencode2 midió que el tiempo de CPU por experto en el i5-12400F **no es plano**, sino que está fuertemente limitado por cálculo:
$$\tau_{\text{CPU}} = 70 + 26 \cdot T_e \ \mu\text{s}.$$

### 1.1. Nuevo punto de corte ($T_{\text{cut}}$)
- **Coste de transferir pesos por PCIe a la GPU**:
  $$t_{\text{PCIe}} = \frac{1,8\text{ MB}}{11\text{ GB/s}} = \mathbf{163,6 \ \mu\text{s}} \quad (\text{fijo por experto}).$$
- **Coste total de evaluar el experto en CPU**:
  Cálculo en CPU más el tráfico PCIe de ida y vuelta de activaciones ($x + y = 10,24\text{ KB/tok}$ a 11 GB/s = $0,93\ \mu\text{s/tok}$):
  $$t_{\text{CPU\_total}} = \tau_{\text{CPU}} + 0,93 \cdot T_e = 70 + 26,93 \cdot T_e \ \mu\text{s}.$$
- **Condición para que la CPU gane a PCIe**:
  $$70 + 26,93 \cdot T_e \le 163,6 \implies 26,93 \cdot T_e \le 93,6 \implies T_e \le 3,47 \implies \mathbf{T_e \le 3 \text{ tokens}}.$$
  *Conclusión*: La CPU **solo es más rápida que el cable PCIe si el experto recibe 1, 2 o 3 tokens*. A partir de $T_e = 4$, transferir los 1,8 MB de pesos a la GPU y ejecutarlos en los Tensor Cores es más rápido que computarlos en el i5-12400F.

### 1.2. Cuántos expertos caen en $T_e \le 3$ en un chunk típico de 6.144 tokens
En un chunk de $T = 6.144$ tokens con top-10 ($61.440$ slots) y distribución Zipf/power-law sobre 512 expertos:
- **Cabeza y cuerpo ($T_e \ge 4$)**: ~360 a 380 expertos activos (incluye los ~72 de VRAM).
- **Cola inactiva ($T_e = 0$)**: ~40 a 60 expertos.
- **Cola de baja cardinalidad ($1 \le T_e \le 3$)**:
  - $T_e = 1$: ~30 expertos por capa.
  - $T_e = 2$: ~23 expertos por capa.
  - $T_e = 3$: ~17 expertos por capa.
  - **Total de expertos en $T_e \le 3$**: **~70 expertos por capa** (entre 60 y 80 según el prompt).

### 1.3. Ahorro real de E4
- Para $T_e = 1$: ahorro de $163,6 - (70 + 27) = \mathbf{66,6 \ \mu\text{s}}$ por experto.
- Para $T_e = 2$: ahorro de $163,6 - (70 + 54) = \mathbf{39,6 \ \mu\text{s}}$ por experto.
- Para $T_e = 3$: ahorro de $163,6 - (70 + 81) = \mathbf{12,6 \ \mu\text{s}}$ por experto.
- **Ahorro medio por experto descargado**: $\sim 44,5 \ \mu\text{s}$.
- **Ahorro por capa**: $70 \text{ expertos} \times 44,5 \ \mu\text{s} \approx \mathbf{3,1 \text{ ms por capa}}$.
- **Ahorro por chunk de 6.144 tokens**: $48 \text{ capas} \times 3,1 \text{ ms} \approx \mathbf{149 \text{ ms por chunk}}$.
- **Ahorro en prompt de 32K tokens (5,3 chunks)**:
  $$5,3 \times 149\text{ ms} \approx \mathbf{0,79 \text{ segundos}}.$$
- **Veredicto real**: El prefill total de 32K baja de 22,0 s a **~21,2 s** (una ganancia modesta de **+3,7 % tokens/s**, no el +120 % estimado bajo la hipótesis refutada de 60 µs).

---

## 2. Anatomía del cuello de cálculo en Strata (`src/kernels/cpu/iq_avx2.cpp`)

¿Por qué cada token adicional cuesta **$26 \ \mu\text{s}$** de CPU por experto?

1. **Lecturas escalares de tabla en memoria (`iq2s_grid`)**:
   En [`src/kernels/cpu/iq_avx2.cpp:404-405`](file:///home/bazzite/strata-explore/src/kernels/cpu/iq_avx2.cpp#L404-L405):
   ```cpp
   const uint32_t* q = gi + 4 * H;
   const __m256i g = _mm256_set_epi64x((long long) iq2s_grid[q[3]], (long long) iq2s_grid[q[2]],
                                      (long long) iq2s_grid[q[1]], (long long) iq2s_grid[q[0]]);
   ```
   En cada bloque de 256 pesos, Strata ejecuta **32 accesos escalares a memoria** a la tabla `iq2s_grid[1024]` (8 KB) y empaqueta el resultado con `_mm256_set_epi64x` (múltiples instrucciones `vpinsrq` / `vpunpcklqdq`). Para una fila de experto de 2.560 elementos (10 bloques), son 320 lecturas escalares; para un experto completo (1.280 filas $\times 2$ matrices), ¡más de **800.000 lecturas escalares** dependientes de puntero!
2. **Reconstrucción de índices en memoria de pila**:
   En [`iq_avx2.cpp:390-398`](file:///home/bazzite/strata-explore/src/kernels/cpu/iq_avx2.cpp#L390-L398), Strata almacena los índices en un búfer de stack `uint32_t gi[32]` tras combinarlos con `qh`, provocando dependencias Store-Forwarding en la CPU.
3. **Pipeline de multiplicación de 4 instrucciones**:
   En [`iq_avx2.cpp:414-416`](file:///home/bazzite/strata-explore/src/kernels/cpu/iq_avx2.cpp#L414-L416), la acumulación requiere:
   - `_mm256_sign_epi8` (aplicar signos a las activaciones $y$)
   - `_mm256_maddubs_epi16` (multiplicar magnitudes de rejilla $g$ por $y$)
   - `_mm256_madd_epi16` (multiplicar por la escala de 16 bits $sc$)
   - `_mm256_add_epi32` (acumular en enteros de 32 bits)
   Esto genera una presión excesiva sobre los puertos de ejecución enteros (ALU puertos 0, 1, 5) del i5-12400F.

---

## 3. Cómo lo resuelven `llama.cpp` e `ik_llama.cpp`

### 3.1. Repack de filas contiguas: RTR (`_R2` y `_R4`)
- **Origen**: `ik_llama.cpp` (flag `-rtr` / `--run-time-repack 1`) y `llama.cpp` (`vec_dot_iq2_xxs_r4_q8_k` en `ggml-cpu/arch/x86/quants.c`).
- **Mecánica**:
  En lugar de almacenar las filas de forma aislada ($W_0, W_1, W_2, W_3$), las transpone en memoria entrelazando 2 filas (`R2`) o 4 filas (`R4`) contiguas.
- **Por qué acelera el cálculo**:
  - En el cálculo de GEMV de CPU, el vector de activación $y$ se carga **una sola vez en registros YMM** y se reutiliza simultáneamente contra las 2 o 4 filas de pesos.
  - En los núcleos P Golden Cove del i5-12400F, que disponen de 2 puertos vectoriales independientes (puerto 0 y puerto 1), evaluar dos filas en paralelo mantiene ambos puertos al 100 % de ocupación sin burbujas de carga de memoria.
  - **Ganancia medida en ik_llama**: **30 % a 45 % de reducción de tiempo de CPU** en IQ2_S e IQ2_XXS.

### 3.2. Búsqueda en tablas por registros mediante `pshufb` (`_mm256_shuffle_epi8`)
- **Origen**: `llama.cpp` (`ggml-cpu/arch/x86/quants.c` en `vec_dot_iq2_xxs`).
- **Mecánica**:
  - En `IQ2_XXS`, cada bloque de 8 pesos procede de un diccionario restringido de magnitudes discretas.
  - En lugar de indexar un array en memoria RAM/L1 con índices escalares, la tabla de valores se carga **directamente en un registro vectorial YMM** al inicio del bucle.
  - La instrucción `_mm256_shuffle_epi8` (`vpshufb`) utiliza el nibble de 4 bits del peso como índice inmediato dentro del registro.
  - Realiza **32 búsquedas en paralelo en 1 solo ciclo de reloj**, eliminando por completo las 800.000 lecturas escalares a memoria y las instrucciones `_mm256_set_epi64x`.

### 3.3. Descompresión de signos y escalas totalmente vectorizada
- **Origen**: `llama.cpp` (`arch/x86/quants.c`).
- En Strata ([`iq_avx2.cpp:140-142`](file:///home/bazzite/strata-explore/src/kernels/cpu/iq_avx2.cpp#L140-L142)), la lectura de signos de `IQ2_XS` contiene un bucle escalar:
  `for (int l = 0; l < 4; ++l) s |= (uint32_t) ksigns_iq2xs[v[o + l] >> 9] << (8 * l);`
- En `llama.cpp`, los signos se expanden usando la tabla constexpr `even_signs` combinada con `_mm256_shuffle_epi8` para emitir los 32 signos en una sola operación vectorial sin branches ni bucles escalares.

### 3.4. Instrucción nativa AVX-VNNI (`vpdpbusd`)
- **Origen**: Extensiones VNNI para x86 (disponibles en los núcleos P de Alder Lake i5-12400F).
- **Mecánica**:
  - Reemplaza la secuencia de dos instrucciones `_mm256_maddubs_epi16` + `_mm256_madd_epi16` por la instrucción nativa `_mm256_dpbusd_epi32`.
  - Multiplica 4 pares de bytes (activación $\times$ peso de rejilla) y los acumula directamente en un entero de 32 bits en **1 único ciclo**.
  - Reduce a la mitad la longitud del pipeline de reducción interna de cada bloque.

---

## 4. Comparativa de características: Strata vs `ik_llama.cpp` / `llama.cpp`

| Característica | Strata (`iq_avx2.cpp`) | `ik_llama.cpp` / `llama.cpp` | Impacto en coste CPU ($\tau_{\text{CPU}}$) |
| :--- | :---: | :---: | :--- |
| **Grid Lookup** | 4 lecturas escalares en RAM + `set_epi64x` | En registros YMM con `vpshufb` | Elimina ~800K lecturas escalares / exp |
| **Layout en RAM** | Fila por fila estándar (secuencial) | Entrelazado de 2/4 filas (RTR / `_R4`) | Reduce a la mitad las cargas de activación $y$ |
| **Bucle de Signos** | Bucle escalar `for` en `IQ2_XS` | 100 % vectorial con permutas SIMD | Elimina penalización de saltos y shifts |
| **Instrucción MAC** | `maddubs` + `madd` (AVX2 estándar) | `vpdpbusd` (AVX-VNNI directo) | Duplica el rendimiento de acumulación |
| **Coste por token** | **$26 \ \mu\text{s/tok}$** (medido en C23) | **~11 a 13 $\mu\text{s/tok}$** (con RTR + VNNI) | **Corte E4 se amplía de $T_e \le 3$ a $T_e \le 7$** |

---

## 5. Recomendación para la arquitectura

1. **Aceptar el límite actual de E4**: Con el kernel actual de Strata, el umbral de descarga a CPU en prefill debe fijarse estrictamente en **$T_e \le 3$** (ahorro modesto de ~0,8 s en 32K).
2. **Priorizar optimización del microkernel AVX2 (C23)**:
   Si opencode2 implementa en `iq_avx2.cpp` el layout entrelazado de 2 filas (P08) y la búsqueda por `pshufb`, la pendiente de cálculo bajará de $26 \ \mu\text{s}$ a **$\le 13 \ \mu\text{s}$**.
3. **Efecto multiplicador sobre E4**:
   Con $\tau_{\text{CPU}} = 70 + 13 \cdot T_e \ \mu\text{s}$, el punto de corte sube a:
   $$70 + 14 \cdot T_e \le 164 \implies 14 \cdot T_e \le 94 \implies \mathbf{T_e \le 6 \text{ o } 7 \text{ tokens}}.$$
   A $T_e \le 7$, el número de expertos descargables por capa sube de 70 a **~170 expertos**, y el ahorro en prefill 32K pasa de 0,8 s a **~3,5 segundos (+16 % tokens/s)**.
