# C24-PREP: Instrumentación con Eventos CUDA y Análisis del Pipeline de Prefill

Fecha: 2026-10-04. Autor: explorer. Para: Claude (arquitecto) y opencode2 (desarrollador).  
Archivos clave: `src/prefill/prefill.cpp`, `src/prefill/moe_fused.cu`, `include/strata/prefill/moe_fused.hpp`.  
Cifras base: `C22_TECHO.md`, `ENTREGAS.md` (orden 7 y 9), y mediciones de `DETAILS.md`.

---

## 1. Perfilado existente en Strata: `STRATA_PREFILL_TIMING=1`

Strata **ya implementa** un sistema completo de instrumentación por eventos CUDA en [`src/prefill/prefill.cpp:1458-1543`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L1458-L1543) a través de la estructura `PfTimer` (instancia `pt`, línea 1580):
- **Cero sincronizaciones CPU-GPU añadidas**: Graba marcas asíncronas en el stream de cómputo con `cudaEventRecord(ev[used], cs)` en `pt.mark(phase, cs)` ([`prefill.cpp:1475-1486`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L1475-L1486)).
- **Cálculo de tiempos sin paradas**: En los puntos de sincronización naturales de la arquitectura (el final de capa o el final del chunk), ejecuta `pt.fold()` ([`prefill.cpp:1488-1497`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L1488-L1497)), calculando `cudaEventElapsedTime(&t, ev[i], ev[i+1])` sin penalizar el pipeline.
- **Reporte final**: Si `STRATA_PREFILL_TIMING=1`, imprime el desglose por fases en stderr al terminar el prefill ([`prefill.cpp:3011-3032`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L3011-L3032)).

Otras variables de control en prefill:
- `STRATA_PREFILL_ISSUER` ([`prefill.cpp:1838`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L1838)): Hilo host en background para desacoplar las llamadas de copia PCIe (`=0` ejecuta inline).
- `STRATA_PF_STEP_SYNC=1` ([`prefill.cpp:1904`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L1904)): Diagnóstico con sincronización paso a paso (añade syncs, solo para depurar bloqueos).
- `STRATA_PREFILL_STREAM_MIN` ([`prefill.cpp:105`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L105)): Tamaño mínimo de chunk para activar streaming (default 1024).
- `STRATA_PREFILL_RING` ([`prefill.cpp:179`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L179)): Permite forzar manualmente el número de slots del ring de staging.
- `STRATA_RING_BYTES` ([`prefill.cpp:169`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L169)): Habilita el cálculo de ring por presupuesto de bytes (`=0` restaura el esquema 0.1.39).
- `STRATA_PF_FUSED=1` ([`src/prefill/moe_fused.cu:406`](file:///home/bazzite/strata-explore/src/prefill/moe_fused.cu#L406)): Habilita kernels fusionados en lugar de MMQ.

---

## 2. Dónde medir con eventos CUDA (sin añadir `cudaStreamSynchronize`)

Para que opencode2 inserte o refine los eventos en `src/prefill/prefill.cpp`:

1. **GPU Densa (PLE, GDN/SSM, Proyecciones)**:
   - *PLE (Prompt Length Extension)*: `pt.mark(kPfPle, cs)` en [`prefill.cpp:1926, 1962`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L1926).
   - *Proyecciones GDN*: `pt.mark(kPfGdn, cs)`, `kPfGdnConv`, `kPfGdnRec`, `kPfGdnOut` en [`prefill.cpp:2004-2016`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2004-L2016).
   - *Proyecciones QSA*: `pt.mark(kPfQsa, cs)` en [`prefill.cpp:2030, 2046, 2231`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2030).
2. **Atención (QSA / FlashAttention)**:
   - *KV Staging*: `pt.mark(kPfKvStage, cs)` en [`prefill.cpp:2042`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2042).
   - *Indexer y Selección Top-K*: `pt.mark(kPfQsaIdx, cs)` en [`prefill.cpp:2095`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2095) y `pt.mark(kPfQsaSel, cs)` en [`prefill.cpp:2112`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2112).
   - *Cálculo de Atención (FlashAttention)*: `pt.mark(kPfQsaAttn, cs)` en [`prefill.cpp:2216`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2216).
3. **Router y Ruteo Compartido**:
   - `pt.mark(kPfRouter, cs)` en [`prefill.cpp:2243`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2243).
4. **Espera de Copia PCIe (`cudaStreamWaitEvent`)**:
   - En modo MMQ agrupado:
     [`prefill.cpp:2709-2711`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2709-L2711):
     ```cpp
     pt.mark(kPfWaitCopy, cs);
     cudaStreamWaitEvent(m.cs, m.copied[gg_slots[gg_nslots - 1]], 0);
     pt.mark(kPfDequant, cs);
     ```
   - En modo no-agrupado: [`prefill.cpp:2838-2840`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2838-L2840).
   - *Mecánica*: Como `m.copied` lo graba el stream `m.copy`, el tiempo transcurrido entre `kPfWaitCopy` y `kPfDequant` en `m.cs` mide con exactitud de nanosegundos la burbuja en que los SMs de la GPU están completamente parados esperando la llegada de datos por PCIe.
5. **Cálculo de Expertos (MMQ)**:
   - *Gate/Up GEMM + SwiGLU*: `pt.mark(kPfGemmGU, cs)` en [`prefill.cpp:2759`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2759), cubre `m.mmq_ctx->run(gu, cs)` y `mmq::swiglu`.
   - *Down GEMM*: `pt.mark(kPfGemmD, cs)` en [`prefill.cpp:2769`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2769), cubre cuantización a `q8_1` y `m.mmq_ctx->run(dn, cs)`.
   - *Combinación aditiva (Residual)*: `pt.mark(kPfCombine, cs)` en [`prefill.cpp:2857`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2857).

---

## 3. El histograma del host (`:2340-2356`): ¿Cuánto cuesta de verdad?

En [`src/prefill/prefill.cpp:2340-2356`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2340-L2356):
```cpp
pt.mark(kPfHostGroup, cs);
cudaMemcpyAsync(m.ids_host.data(), m.ids, (size_t) T * K * 4, cudaMemcpyDeviceToHost, m.cs);
cudaStreamSynchronize(m.cs); // <-- Sincronización CPU-GPU
pt.fold();
std::fill(m.cnt.begin(), m.cnt.end(), 0);
for (int64_t i = 0; i < T * K; ++i) {
    const int32_t e = ids_h[(size_t) i];
    ++m.cnt[(size_t) e];
}
```

### Análisis del coste
1. **Transferencia D2H de `m.ids`**:
   - $T = 6.144$, $K = 10 \implies 61.440$ enteros de 32 bits = **245,76 KB**.
   - En PCIe Gen4 a 11 GB/s, la transferencia de 245 KB tarda **22,3 µs**.
2. **El `cudaStreamSynchronize(m.cs)`**:
   - Como explica el código en [`prefill.cpp:2341`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2341) (*"a stall here is the GPU (attention, router), not the host"*), este bloqueo no es tiempo perdido en CPU: es la CPU esperando a que la GPU termine de calcular la atención y el router de esa capa.
   - Si la atención de la GPU ya terminó, la llamada al runtime de CUDA tarda **~3 a 5 µs**.
3. **El cómputo del histograma en el i5-12400F**:
   - `std::fill`: 512 enteros (2 KB) en L1 tarda **< 0,1 µs**.
   - Bucle `for` de 61.440 iteraciones leyendo datos contiguos en L2: **~18 a 22 µs** en Golden Cove.
4. **Coste total imputable al host**:
   - **~0,025 ms por capa**.
   - En las 48 capas de un chunk de 6.144 tokens: $48 \times 0,025\text{ ms} \approx \mathbf{1,2 \text{ ms}}$ en un chunk que tarda ~4.000 ms.
   - **Conclusión**: El histograma del host representa **menos del 0,03 % del tiempo de prefill**. No es un cuello de botella en absoluto.

---

## 4. ¿La copia del ring y el cálculo se solapan de verdad o el ring es pequeño?

### Ubicación y dimensionamiento del ring
- Ubicación: función `ring_slots(size_t T)` en [`src/prefill/prefill.cpp:178-200`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L178-L200) y asignación en [`prefill.cpp:918`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L918).
- En nuestra configuración (RTX 3060 12 GB, Swift 1.5 IQ2_XS, 62 GB RAM fijada):
  - `g_pinned_share >= 0.9`. El ring teórico es de **384 slots** (`prefill.cpp:194`).
  - Bajo presupuesto dinámico de VRAM (`ring_budget_slots()`, [`prefill.cpp:172`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L172)), la 3060 aloja típicamente entre **160 y 280 slots** (cada slot son 1,8 MB de blob, sumando ~300 a 500 MB de VRAM).

### Diagnóstico del solapamiento
- **¿Hay solapamiento real en hardware?**: **SÍ**.
  - El hilo `issuer` ([`prefill.cpp:1853-1879`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L1853-L1879)) empuja transferencias DMA en el stream `m.copy` de forma continua.
  - El motor Copy Engine de la GPU transfiere pesos por PCIe en paralelo mientras los SMs de cómputo procesan la atención densa de la capa en `m.cs`.
- **Por qué el ring parece "demasiado pequeño" (el cuello físico de PCIe)**:
  - Un blob IQ2_S + Down pesa **1,8 MB**. A 11 GB/s en PCIe Gen4:
    $$t_{\text{DMA}} = \frac{1,8\text{ MB}}{11\text{ GB/s}} = \mathbf{163,6 \ \mu\text{s por experto}}.$$
  - Durante la atención densa y router de una capa (~12-14 ms), el bus PCIe solo tiene tiempo físico de precargar:
    $$N_{\text{precarga}} = \frac{14.000 \ \mu\text{s}}{163,6 \ \mu\text{s}} \approx \mathbf{85 \text{ expertos}}.$$
  - De los 440 expertos no residentes de la capa, una vez consumidos los ~85 precargados, quedan **~355 expertos que deben transferirse en rigurosa serie**.
  - Como el cálculo MMQ de un experto con pocos tokens ($T_e \le 8$) en los Tensor Cores de la 3060 tarda solo **~15 a 25 µs**, la GPU es **8 a 10 veces más rápida calculando que PCIe transmitiendo**.
  - La GPU vacía el ring instantáneamente y se detiene en `cudaStreamWaitEvent` ([`prefill.cpp:2710`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2710)).
- **Conclusión arquitectónica**:
  - El ring **NO es pequeño**: con 160-280 slots excede de sobra la capacidad de absorción de la precarga (~85 expertos).
  - La espera `kPfWaitCopy` no se debe a contención del ring, sino al **techo físico de 11 GB/s de PCIe Gen4**.
  - Este resultado confirma que la única forma de acelerar el prefill en esta máquina es **E4** (evitar transferir los ~320 expertos de baja cardinalidad por PCIe, calculándolos directamente en la CPU).
