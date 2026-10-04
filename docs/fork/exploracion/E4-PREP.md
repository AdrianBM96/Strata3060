# E4-PREP: Diseño de Prefill Híbrido CPU+GPU (P11+P12) sobre el código real de Strata

Fecha: 2026-10-04. Autor: explorer. Para: Claude (arquitecto).
Cifras base: `C22_TECHO.md`, `ENTREGAS.md` (orden 7 y 9), y mediciones de `DETAILS.md`.

---

## 1. Cómo funciona hoy el prefill de expertos por chunk

En la rama actual de Strata, el prefill procesa el prompt en bloques secuenciales mediante la clase `strata::prefill::Prefill` (`src/prefill/prefill.cpp`):

1. **Tamaño de chunk**:
   - Configurado con `--prefill auto` (`src/program/generate.cpp:431-435, 1379-1387`). En nuestra máquina con RTX 3060 12 GB, el algoritmo de autosizing elige **$T = 6.144$ tokens** (`RESUMEN_FINAL.md:52`).
   - El bucle principal fragmenta el prompt en `c0 = 0; c0 < n; c0 += m.T` (`src/prefill/prefill.cpp:1618`), procesando $T$ tokens a través de las 48 capas (`src/prefill/prefill.cpp:1915`).

2. **Evaluación del router y conteos**:
   - En cada capa $L$, tras la atención (half 0), el router en GPU emite los $K=10$ IDs elegidos por token a `m.ids`.
   - Se copia `m.ids` a RAM con `cudaMemcpyAsync(m.ids_host.data(), ...)` y sincroniza con `cudaStreamSynchronize(m.cs)` (`src/prefill/prefill.cpp:2340-2345`).
   - El host construye el histograma de activación: `std::fill(m.cnt.begin(), m.cnt.end(), 0)` e incrementa `++m.cnt[e]` (`src/prefill/prefill.cpp:2351-2356`).

3. **Cómo se stremean los expertos por PCIe**:
   - De los 512 expertos de la capa, la GPU aloja ~72 en la caché de VRAM (`m.host_res >= 0`). Los ~440 restantes que tengan `m.cnt[e] > 0` se copian secuencialmente a través del staging ring (`m.ring`, `src/prefill/prefill.cpp:2403-2410`).
   - Si los expertos están fijados en RAM (nuestro caso con 62 GB), el stream `m.copy` ejecuta `cudaMemcpyAsync(m.stage_dev[sl], b, ...)` (`src/prefill/prefill.cpp:2673`). Cada blob pesa **~1,8 MB** (IQ2_S 1,44 MB + down q2_0 0,45 MB).
   - El stream de cómputo `m.cs` se detiene en `cudaStreamWaitEvent(m.cs, m.copied[...])` (`src/prefill/prefill.cpp:2710`) esperando a que los pesos lleguen por PCIe.

4. **Dónde se calcula**:
   - **El 100 % de los expertos se calcula en GPU**: las activaciones se cuantizan a `q8_1` en `m.Xq` (`src/prefill/prefill.cpp:2416`) y se lanzan en GPU mediante `m.mmq_ctx->run(gu)` y `m.mmq_ctx->run(dn)` (`src/prefill/prefill.cpp:2767, 2776`).
   - La CPU permanece ociosa durante todo el cómputo de la capa MoE.

---

## 2. Distribución de tokens por experto en un chunk típico (6.144 tokens)

En un chunk de $T = 6.144$ tokens con top-10 entre 512 expertos:
- **Total de asignaciones**: $6.144 \times 10 = \mathbf{61.440 \text{ pares (token, experto)}}$ por capa.
- **Media aritmética**: $61.440 / 512 = 120$ tokens por experto.

Sin embargo, los routers de MoE siguen una distribución marcadamente asimétrica (Power-law / log-normal):
1. **Cabeza (hot, ~50 expertos)**: Expertos muy generalistas que reciben entre **300 y 900 tokens** cada uno (~40 % de todas las asignaciones). La mayoría de estos residen en los ~72 slots de VRAM.
2. **Cuerpo (warm, ~260 expertos)**: Expertos que reciben entre **50 y 250 tokens** cada uno.
3. **Cola larga (cold, ~200 expertos no residentes)**:
   - ~40-60 expertos no reciben ningún token (`m.cnt[e] == 0`).
   - **~160-180 expertos reciben entre 1 y 20 tokens** en todo el chunk ($T_e \le 20$).

**La patología actual**: Para un experto con $T_e = 6$ tokens, Strata transfiere por PCIe 1,8 MB de matriz de pesos a 11 GB/s (tarda ~164 $\mu$s de cable) para calcular solo 6 filas de GEMV. La intensidad de transferencia es pésima: $1.800 \text{ KB} / 6 \text{ tok} = \mathbf{300 \text{ KB de pesos transferidos por cada token}}$.

---

## 3. Diseño concreto: partir 'Pesos por PCIe' vs 'Activaciones a CPU'

### 3.1. Umbral analítico de transferencia (Breakeven point)
- **Vector de activación de un token**: Dimensión $N = 2.560$. En formato FP16/BF16, $x$ pesa $5.120 \text{ bytes}$.
- **Salida proyectada de un token**: $y_{\text{CPU}}$ pesa $5.120 \text{ bytes}$.
- **Tráfico PCIe total de ida y vuelta para evaluar un token en CPU**: $5.120 + 5.120 = \mathbf{10.240 \text{ bytes} \approx 10,24 \text{ KB/token}}$.

Comparación frente a transferir el peso del experto ($1,8 \text{ MB} = 1.800 \text{ KB}$):
$$\text{Coste PCIe Pesos} = \frac{1.800 \text{ KB}}{11 \text{ GB/s}} = \mathbf{163,6 \ \mu\text{s}} \quad (\text{fijo por experto}).$$
$$\text{Coste PCIe Activaciones} = T_e \times \frac{10,24 \text{ KB}}{11 \text{ GB/s}} = T_e \times \mathbf{0,93 \ \mu\text{s}}.$$
$$\text{Punto de corte de volumen} = \frac{1.800 \text{ KB}}{10,24 \text{ KB}} \approx \mathbf{175 \text{ tokens}}.$$
*Conclusión de volumen*: Para cualquier experto con **menos de 175 tokens**, se mueven MENOS BYTES por el cable PCIe transfiriendo las activaciones a la CPU que transfiriendo los pesos a la GPU.

### 3.2. Punto de corte para equilibrio de tiempos (Terminar a la vez)
El objetivo de E4 es que el cómputo/transferencia de la GPU y el cómputo de la CPU terminen simultáneamente:
- **GPU**: Procesa los ~72 expertos residentes en VRAM (0 bytes PCIe) más $N_{\text{stream}}$ expertos transferidos por PCIe.
  - Tiempo de transferencia PCIe para $N_{\text{stream}}$ expertos:
    $$t_{\text{PCIe}} = N_{\text{stream}} \times 163,6 \ \mu\text{s}.$$
- **CPU**: Procesa los $N_{\text{CPU}} = (440 - N_{\text{stream}})$ expertos no residentes directamente en la RAM (27 GB/s de ancho de banda).
  - Medido en `C22_TECHO.md`: 4,35 expertos en 0,33 ms $\implies \tau_{\text{CPU}} \approx \mathbf{75 \ \mu\text{s}}$ por experto en RAM con 6 hilos P.
  - Como son expertos con pocos tokens ($T_e \le 30$), el cálculo es aún más rápido: $\tau_{\text{CPU}} \approx \mathbf{60 \ \mu\text{s}}$ por experto.

Igualando los tiempos para que finalicen en paralelo:
$$N_{\text{stream}} \times 163,6 \ \mu\text{s} = (440 - N_{\text{stream}}) \times 60 \ \mu\text{s}$$
$$N_{\text{stream}} \times (163,6 + 60) = 440 \times 60 \implies N_{\text{stream}} \times 223,6 = 26.400$$
$$\mathbf{N_{\text{stream}} \approx 118 \text{ expertos en GPU}} \quad \Longleftrightarrow \quad \mathbf{N_{\text{CPU}} \approx 322 \text{ expertos en CPU}}.$$

### 3.3. Regla de decisión en runtime (Punto de corte $T_{\text{cut}}$)
Al calcular el histograma `m.cnt` en `src/prefill/prefill.cpp:2355`:
1. Los ~72 expertos residentes (`m.host_res >= 0`) se asignan **siempre a la GPU**.
2. Los expertos no residentes con `m.cnt[e] > 0` se ordenan por `m.cnt[e]` descendente:
   - Los **120 expertos no residentes más pesados** ($T_e \ge 32$) se asignan a **GPU por streaming PCIe**.
   - Los **~280-320 expertos no residentes restantes** ($T_e < 32$) se asignan a **CPU in situ en RAM**.

### 3.4. Impacto en latencia de capa
- Tiempo de transferencia PCIe hoy: $440 \times 163,6 \ \mu\text{s} \approx \mathbf{72,0 \text{ ms por capa}}$.
- Tiempo de transferencia PCIe con E4: $120 \times 163,6 \ \mu\text{s} + (320 \times 12 \text{ tok} \times 0,93 \ \mu\text{s}) \approx 19,6 + 3,5 = \mathbf{23,1 \text{ ms por capa}}$.
- **Ahorro neto por capa**: $72,0 - 23,1 = \mathbf{48,9 \text{ ms por capa}}$.
- **Ahorro por chunk de 6.144 tokens**: $48 \text{ capas} \times 48,9 \text{ ms} \approx \mathbf{2,35 \text{ segundos por chunk}}$.
- En un prompt de 32K tokens (5,3 chunks): ahorro de **~12,4 segundos** (el prefill total baja de ~22 s a **~9,6 s**, **+129 % tokens/s**).

---

## 4. ¿Sería bit-idéntico de verdad?

**NO. No es bit-idéntico.** Hay que ser tajantes:

1. **Divergencia de redondeo hardware (GPU vs CPU)**:
   - La GPU Ampere (sm_86) ejecuta el GEMV de expertos con instrucciones de producto escalar entero y reducción en registros (`mma.sync` / `dp4a` / FP16 tensor-core arithmetic en `moe_mmq.cu:320-410`).
   - La CPU i5-12400F ejecuta el cálculo con AVX2/FMA (`_mm256_fmadd_ps` / `vpmaddwd` en `iq_avx2.cpp:446`), que suma en un orden horizontal distinto y aplica truncamiento IEEE-754 de 32 bits en diferentes fases de la reducción.

2. **Precedente medido en Strata (`DETAILS.md:87-100` e Issue #152)**:
   - Strata ya documenta que la GPU y la CPU redondean un mismo experto de manera ligeramente diferente.
   - En el fork `architectds`, la opción de "CPU assist" en prefill fue probada y descartada (`RESUMEN_FINAL.md:84`) porque cambiaba la mezcla de redondeo y degradaba la calidad de código en pruebas sensibles.

3. **Criterio de adopción**:
   - E4 **no puede ser bit-exacto por física de arquitecturas**.
   - Solo es viable si la divergencia en logits se mantiene dentro de la banda de ruido de cuantización:
     - Teacher-forced KL divergence contra la ejecución 100 % GPU: $\text{KL} \le 0,015$ nats.
     - $\Delta\text{NLL} < 0,005$ en `logpos-compare`.
     - Batería de agentes 25/25 sin regresiones en generación de sintaxis estricta (JSON/Python).
   - Debe implementarse con variable opt-in (`STRATA_PREFILL_HYBRID=1`) y corte ajustable (`STRATA_PREFILL_CPU_MAX_TOKENS=32`).

---

## 5. Diseño de integración en el código existente

Lo más valioso de Strata es que **la infraestructura de bifurcación de activaciones y combinación ya existe**: se diseñó para multi-GPU sin P2P (`!P.p2p`) en `src/prefill/prefill.cpp:2440-2863`.

Puntos exactos de integración:

1. **Clasificación y partición (`src/prefill/prefill.cpp:2403-2410`)**:
   En lugar de meter todos los expertos con `m.cnt[e] > 0` en `order`, separar según el umbral:
   ```cpp
   std::vector<int32_t> order_gpu, order_cpu;
   for (int32_t e = 0; e < m.g->n_expert; ++e) {
       if (m.cnt[e] == 0) continue;
       const bool resident = (m.host_res && m.cache && m.host_res[l * m.g->n_expert + e] >= 0);
       if (resident || m.cnt[e] >= g_pf_cpu_max_tok) {
           order_gpu.push_back(e);
       } else {
           order_cpu.push_back(e);
       }
   }
   ```

2. **Envío de activaciones a CPU (`src/prefill/prefill.cpp:2442`)**:
   Reutilizar el búfer fijado `m.pp->host_x`:
   ```cpp
   cudaMemcpyAsync(m.cpu_pf_act_host, m.mixed, (size_t) T * N * sizeof(float), cudaMemcpyDeviceToHost, m.cs);
   ```

3. **Lanzamiento en el pool de CPU (`src/kernels/cpu/pool.cpp`)**:
   Disparar el cálculo de los `order_cpu` en el `ExpertPool` existente en paralelo mientras la GPU ejecuta el bucle de streaming de `order_gpu`.

4. **Combinación aditiva (`src/prefill/prefill.cpp:2860-2863`)**:
   La CPU deposita sus resultados en un búfer pinned `m.cpu_pf_rows_host`.
   El stream `m.cs` los sube a la GPU en un búfer `m.Dm_cpu` y ejecuta un kernel de suma de vectores antes del residual:
   ```cpp
   cudaMemcpyAsync(m.Dm_cpu, m.cpu_pf_rows_host, cpu_rows_bytes, cudaMemcpyHostToDevice, m.cs);
   moe_combine_hybrid(m.Dm, m.Dm_cpu, m.slot_dev, m.w, m.shared, m.sg, m.bo, T, m.cs);
   ```

**Veredicto**: Viabilidad técnica muy alta (**Esfuerzo M**), ganancia de prefill masiva (~**+120 % tokens/s**), pero sujeta estrictamente a la puerta de calidad por la divergencia de redondeo CPU-GPU.
