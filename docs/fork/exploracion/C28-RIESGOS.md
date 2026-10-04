# C28-RIESGOS: Análisis de Riesgos y Bit-Exactitud para el Prefill Layer-Major (C28)

Fecha: 2026-10-04. Autor: explorer.  
Para: Claude (arquitecto) y opencode2 (desarrollador C28).  
Objetivo: Auditoría exhaustiva del código de Strata (`~/strata-explore`) para identificar qué puede romper el orden por capas o la equivalencia bit-idéntica en el prefill layer-major (C28), con archivo:línea y estrategia de mitigación.

---

## 1. Resumen Ejecutivo de Riesgos Críticos

| Área Auditada | Archivos y Líneas Clave | Nivel de Riesgo | ¿Rompe Bit-Exactitud si no se atiende? | Mitigación Inmediata |
|---|---|:---:|:---:|---|
| **1. Checkpoint Prompt-Cache / PLE** | `generate.cpp:507, 1090, 5339, 5393`, `conversation_state.cpp:154` | **CRÍTICO** | Sí (corrompe snapshots intermedios) | Desactivar checkpoints periódicos durante layer-major o snapshot bifásico en `root_at`. |
| **2. Visión (Tokens de Imagen Intercalados)** | `generate.cpp:5792, 6386`, `prefill.cpp:1607, 1943, 1969` | **ALTO** | Sí (inyección de embeddings raw y M-RoPE) | Respetar punteros `row_ptr` en capa 0 y retener tabla `d_mrope` en todas las capas QSA. |
| **3. Cuantización INT8 KV por Chunk** | `kv_q8.hpp:5-8`, `native_qsa_indexer.hpp:12-36`, `prefill.cpp:2063` | **BAJO** | No (es local por token/cabeza) | Comprobado matemáticamente: escala por bloque de 64 elementos; indexador C-2 es bit-idéntico. |
| **4. Estado GDN entre Chunks** | `prefill.cpp:2005-2018`, `kernels.cu:337-377, 896-904` | **ALTO** | Sí (si se usa scan asociativo o falla conv) | Bucle secuencial estricto en GPU (`gdn_rec_cols_kernel`); flush de cola conv1d al final de capa. |
| **5. Hiperconexiones ($HC=4$)** | `prefill.cpp:90, 1976-1995, 2901`, `kernels.cu:171-198` | **CRÍTICO** | Sí (desbordamiento de buffer por $4\times$) | Dimensionar $m.R$ como $T \times (4 \times 2560) = T \times 10.240$ floats (1,34 GB en 32K). |
| **6. RESUME de Prefijos** | `generate.cpp:6467-6500, 6684-6701`, `prefill.cpp:2005` | **MEDIO** | Sí (si se asume offset local en KV) | Dimensionar $m.R$ solo para $T_{\text{nuevo}}$; consultas QSA deben atender al KV previo ya residente. |
| **7. Préstamo de Caché y Repoblado** | `generate.cpp:7520-7555, 7580-7600`, `plan.hpp:125` | **ALTO** | No (pero puede causar OOM en >32K) | Limitar préstamo a VRAM libre; en >32K paginar $m.R$ en RAM o zig-zag; amortizar coste de refill. |

---

## 2. Análisis Detallado por Componente

---

### RIESGO 1: Checkpoint de Prompt-Cache y PLE
- **Archivos y Líneas**:
  - [`src/program/generate.cpp:505-509`](file:///home/bazzite/strata-explore/src/program/generate.cpp#L505-L509): `prompt_cache_every = 16384`, `prompt_cache_root = 2048`.
  - [`src/program/generate.cpp:5337-5373`](file:///home/bazzite/strata-explore/src/program/generate.cpp#L5337-L5373): `checkpoint_at(L)` que invoca `checkpoint_save(c, ss, g)`.
  - [`src/program/generate.cpp:5393-5411`](file:///home/bazzite/strata-explore/src/program/generate.cpp#L5393-L5411): Disparo de checkpoint cada `prompt_cache_every` tokens.
  - [`src/core/conversation_state.cpp:154-180`](file:///home/bazzite/strata-explore/src/core/conversation_state.cpp#L154-L180): `conversation_checkpoint_save` copia el bloque completo de 36 capas GDN (`ss.gdn_state`), el historial PLE (`ss.ple_hist`) y colas de QSA.
  - [`src/prefill/prefill.cpp:1920-1975`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L1920-L1975): El bloque PLE solo corre en la **Capa 1**, actualizando `ss.ple.hist`.
- **Mecanismo de Fallo**:
  - En el prefill por chunks tradicional, todas las capas $0 \dots 47$ avanzan sincronizadas al unísono. Al terminar un chunk de longitud $L$, todas las capas están en la posición $L$, por lo que `checkpoint_save` obtiene una instantánea global coherente.
  - En el prefill layer-major (C28), la Capa 0 procesa los $T$ tokens completos antes de que la Capa 1 comience.
  - Si `generate.cpp:5393` intenta disparar `checkpoint_at(16384)` durante la ejecución, **las capas están en estados temporales completamente disjuntos** (la capa 0 en 32K, la capa 1 en 0, etc.).
  - Peor aún: al finalizar todas las capas, `ss.gdn_state` y `ss.ple.hist` se encuentran en el token final $T$. Los estados intermedios en $L=2048$ (`prompt_cache_root`) o $L=16384$ habrán sido **sobreescritos y destruidos**, impidiendo que futuras peticiones reutilicen el prefijo del prompt de sistema.
- **Cómo Evitarlo**:
  1. **Prefill Bifásico para Prompt de Sistema**: Si la petición empieza desde el token 0 y `root_at >= prompt_cache_root`:
     - Fase 1: Ejecutar layer-major sobre $[0, \text{root\_at})$. Al terminar todas las capas, capturar el checkpoint de sistema coherentemente con `checkpoint_save`.
     - Fase 2: Ejecutar layer-major sobre $[\text{root\_at}, T)$ reutilizando la fase 1.
  2. **Desactivar Checkpoints Periódicos en Layer-Major**: Configurar `prompt_cache_every = 0` cuando C28 esté activo, ya que en una sola pasada el prefill de 32K toma solo ~3,5 s (frente a 23 s), haciendo innecesario el guardado incremental.

---

### RIESGO 2: Visión (Tokens de Imagen Intercalados y M-RoPE)
- **Archivos y Líneas**:
  - [`src/program/generate.cpp:5792`](file:///home/bazzite/strata-explore/src/program/generate.cpp#L5792): Token especial `kImagePad = 248056`.
  - [`src/program/generate.cpp:6349-6395`](file:///home/bazzite/strata-explore/src/program/generate.cpp#L6349-L6395): Inyección de `img_rows` vía `row_ptr[(size_t)(i + j)]` y cálculo de rejilla 3D M-RoPE.
  - [`src/prefill/prefill.cpp:1607-1613, 1697`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L1607-L1613): Generación de filas n-gram de PLE y subida a GPU de `ple_emb`.
  - [`src/prefill/prefill.cpp:1943, 1969`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L1943-L1969): Inyección de `ple_emb` en el stream residual en la Capa 1.
  - [`src/prefill/prefill.cpp:2037, 2088`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2037-L2088): Aplicación de RoPE tridimensional usando `d_mrope`.
- **Mecanismo de Fallo**:
  - Para los tokens de imagen (`<|image_pad|>`), la Capa 0 no lee la matriz de embedding `w_te`, sino que debe copiar directamente los floats del encoder de visión desde `row_ptr`. Si la Capa 0 de C28 asume un array plano de `int32_t tokens`, las imágenes se corromperán por completo.
  - En la Capa 1, el bloque PLE requiere `m.ple_emb` de tamaño $T \times N \times 4\text{ B}$. A 32K tokens, esto representa **335,5 MB**.
  - Si C28 intenta alojar 335,5 MB de golpe en la VRAM de la 3060 para `ple_emb`, sumado a los 1,34 GB de $m.R$, competirá agresivamente con los slots de caché.
- **Cómo Evitarlo**:
  1. En la Capa 0, preservar la rama condicional `sp.embd_rows != nullptr ? sp.embd_rows[t] : ...`.
  2. Para el PLE de la Capa 1, procesar la proyección de `ple_emb` en sub-bloques (`SB = 4096` tokens) reutilizando memoria de scratch (`m.region_bytes`, [`prefill.cpp:1931-1935`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L1931-L1935)) y liberar ese buffer de inmediato al terminar la Capa 1.
  3. Mantener el buffer de dispositivo `d_mrope` cargado para que todas las 12 capas QSA apliquen la rotación 3D en las posiciones correctas.

---

### RIESGO 3: Cuantización INT8 del KV por Chunk (¿Depende del Orden?)
- **Archivos y Líneas**:
  - [`include/strata/kernels/kv_q8.hpp:5-8, 22`](file:///home/bazzite/strata-explore/include/strata/kernels/kv_q8.hpp#L5-L8): Fórmula de cuantización INT8 y tamaño de grupo (`KV_Q8_GROUP = 64`).
  - [`src/prefill/prefill.cpp:2061-2084`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2061-L2084): `kv_append` y rotación FWHT256.
  - [`include/strata/kernels/native_qsa_indexer.hpp:12-36`](file:///home/bazzite/strata-explore/include/strata/kernels/native_qsa_indexer.hpp#L12-L36): Agrupación en bloques de 4 tokens (`idx_block = 4`).
- **Auditoría Matemática de Dependencia de Orden**:
  - **Fórmula de cuantización**:
    $$\text{scale} = \text{fp16}\left(\frac{\max |x|}{127}\right), \quad \text{code} = \text{clamp}\left(\text{rint}\left(\frac{x}{\text{scale}}\right), -127, 127\right)$$
  - Esta cuantización se realiza de manera **estrictamente local por token, por cabeza y por bloque de 64 elementos de canal**.
  - **Conclusión**: **NO depende en absoluto del orden de chunks ni de los tokens circundantes**. Un token $t$ genera idénticos bytes en INT8 ya sea procesado en un chunk de 8K o en un bloque de 32K.
  - **El Indexador QSA**: Agrupa cada 4 tokens contiguos. El kernel por lotes `native_qsa_indexer_append_batch` está verificado para ser bit-idéntico con el append secuencial (`perf-review C-2`).
- **El Verdadero Riesgo en Atención Causal**:
  - En la Capa $l$ (QSA), al procesar los $T$ tokens a la vez, todo el KV de la Capa $l$ se escribe en memoria antes de la atención.
  - El kernel de atención (`qsa_prompt_attn_batch`) **debe garantizar la máscara triangular estricta $j \le t$**. Si por error se usa un kernel de atención densa bidireccional sin máscara causal, los tokens anteriores atenderán a tokens futuros, arruinando la salida.
- **Cómo Evitarlo**:
  - Asegurar que `qsa_prompt_attn_batch` tenga activa la máscara causal para $j > t$ en todas las consultas de la secuencia completa.

---

### RIESGO 4: Estado GDN entre Chunks (Recurrencia y Convolución 1D)
- **Archivos y Líneas**:
  - [`src/prefill/prefill.cpp:2005-2018`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2005-L2018): Invocación de `gdn_conv` y `gdn_recurrence`.
  - [`src/prefill/kernels.cu:337-377`](file:///home/bazzite/strata-explore/src/prefill/kernels.cu#L337-L377): `gdn_rec_cols_kernel`.
  - [`src/prefill/kernels.cu:896-904`](file:///home/bazzite/strata-explore/src/prefill/kernels.cu#L896-L904): `gdn_conv`, `gdn_conv_tiled_kernel`, `gdn_conv_hist_kernel`.
- **Mecanismo de Fallo**:
  - **Recurrencia**: En `gdn_rec_cols_kernel:351`, el kernel recorre $t = 0 \dots T-1$ ejecutando:
    `s[r] = fmaf(g, s[r], sk[...] * delta);`
    Como el cálculo se hace en registros secuencialmente, ejecutar $T=32.768$ en un solo lanzamiento produce **exactamente los mismos bits** que ejecutar 4 chunks de 8.192.
    *Riesgo*: Si se intentara reemplazar por un scan paralelo en árbol (asociativo), se rompería la bit-exactitud debido a la no-asociatividad de la suma en FP32.
  - **Convolución 1D**: `gdn_conv` filtra con un FIR de 4 tomas sobre el canal $C=10.240$. Al finalizar la capa, `gdn_conv_hist_kernel` guarda las últimas 3 entradas en `history`.
    *Riesgo*: Si el kernel layer-major no actualiza `history` al final de la secuencia completa, la primera ventana de decode recibirá un historial de convolución en ceros o desalineado.
- **Cómo Evitarlo**:
  1. Mantener `gdn_rec_cols_kernel` ejecutando secuencialmente sobre todo el rango $T$ (su coste es despreciable: ~0,15 ms por capa en Ampere).
  2. Comprobar que `gdn_conv_hist_kernel` se invoque con `T = T_total` para que el estado de convolución que hereda el decode sea exactamente el de los tokens $[T-3, T-1]$.

---

### RIESGO 5: Hiperconexiones ($HC=4$, Dimensión $D = 10.240$)
- **Archivos y Líneas**:
  - [`src/prefill/prefill.cpp:90`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L90): `constexpr int64_t N = 2560, HC = 4, D = N * HC = 10240;`.
  - [`src/prefill/prefill.cpp:1976-1995`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L1976-L1995): Lectura y normalización del flujo hiperconectado (`gr_norm_rs`, `gr_mix_r`).
  - [`src/prefill/prefill.cpp:2888-2909`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2888-L2909): Escritura residual con inyección hiperconectada (`gr_write_norm_rs`, `gr_write`).
  - [`src/prefill/kernels.cu:171-198`](file:///home/bazzite/strata-explore/src/prefill/kernels.cu#L171-L198): `gr_mix_kernel` y `gr_write_kernel`.
- **Mecanismo de Fallo**:
  - En Swift 1.5, el residual inter-capas **NO es de tamaño $N=2560$**, sino de $HC=4$ flujos paralelos con dimensión $D = 10.240$ floats (40 KB/token).
  - Si el prototipo de C28 dimensiona el buffer de transferencia inter-capas usando la dimensión de embedding $N$ en lugar de $D$, se producirá un **desbordamiento catastrófico de memoria** (escribiendo $4\times$ más allá del final del buffer) o se perderán 3 de los 4 flujos hiperconectados.
  - **Huella de VRAM para $m.R$**:
    - Para $T = 32.768$ tokens: $32.768 \times 10.240 \times 4\text{ B} = \mathbf{1,342\text{ GB}}$.
    - Para $T = 86.000$ tokens: $86.000 \times 10.240 \times 4\text{ B} = \mathbf{3,523\text{ GB}}$.
  - *Ventaja clave descubierta en el código*: `gr_write_kernel` actualiza $R$ mediante FMA in-place (`R[i] = fmaf(..., R[i])`). **No se requiere doble buffer ping-pong**. Un único buffer de 1,34 GB es suficiente.
- **Cómo Evitarlo**:
  - Asegurar que la reserva de memoria para el residual use estrictamente $D = g.hc \times g.n\_embd$ ($10.240$ floats por token).

---

### RIESGO 6: RESUME de Prefijos (Reúso de Caché de Conversación)
- **Archivos y Líneas**:
  - [`src/program/generate.cpp:6467-6500`](file:///home/bazzite/strata-explore/src/program/generate.cpp#L6467-L6500): Detección de prefijo coincidente (`resume > 0`).
  - [`src/program/generate.cpp:6684-6701`](file:///home/bazzite/strata-explore/src/program/generate.cpp#L6684-L6701): `checkpoint_restore` en la posición `resume` y fijado de `read_from = resume`.
  - [`src/program/generate.cpp:6704-6725`](file:///home/bazzite/strata-explore/src/program/generate.cpp#L6704-L6725): Bifurcación por longitud corta (`short_read = 64`).
- **Mecanismo de Fallo**:
  - Cuando se reanuda una sesión desde un prefijo de $L_{\text{res}}$ tokens:
    1. El buffer $m.R$ solo debe procesar $T_{\text{nuevo}} = N - L_{\text{res}}$ tokens.
    2. En las capas QSA, las claves y valores de $[0, L_{\text{res}}-1]$ ya residen en las páginas del KV cache. La Capa $l$ solo debe generar y adjuntar claves/valores para $[L_{\text{res}}, N-1]$, pero la atención debe ejecutarse contra el rango completo $[0, N-1]$.
    3. Si la Capa $l$ asume que el token 0 de su buffer local $m.R$ tiene posición absoluta 0 en lugar de $L_{\text{res}}$, las rotaciones RoPE se aplicarán con ángulos incorrectos y la atención buscará en bloques vacíos.
- **Cómo Evitarlo**:
  - Pasar el offset de posición `p0 = resume` explícitamente a todas las operaciones de la capa (RoPE, indexer append y atención QSA).

---

### RIESGO 7: Préstamo de Huecos de la Caché de Expertos y Repoblado a Decode
- **Archivos y Líneas**:
  - [`src/program/generate.cpp:7520-7555`](file:///home/bazzite/strata-explore/src/program/generate.cpp#L7520-L7555): `plan_lend`, toma de slots de `xcache` (`borrow = xcache.device_slot(first)`).
  - [`src/program/generate.cpp:7580-7600`](file:///home/bazzite/strata-explore/src/program/generate.cpp#L7580-L7600): Repoblado de slots prestados (`lent`, `xcache.fill_slot_queued`).
  - [`src/program/generate.cpp:6881-6890`](file:///home/bazzite/strata-explore/src/program/generate.cpp#L6881-L6890): Interacción con `--batch` y decode concurrente.
  - [`include/strata/plan/plan.hpp:125-127`](file:///home/bazzite/strata-explore/include/strata/plan/plan.hpp#L125-L127): Límite del pool de VRAM: `vram_pool_bytes() = 5,943,000,000` (~5,94 GB).
- **Mecanismo de Fallo**:
  1. **Conflicto en Concurrencia con Decode**:
     Si mientras se ejecuta el prefill por capas hay sesiones decodificando concurrentemente (modo `--batch`), esas sesiones sufrirán un 100 % de misses en los slots prestados, sobrecargando la CPU y bloqueando los tiempos de respuesta.
  2. **Sobrecoste de Repoblado (Refill Latency)**:
     Prestar 1,34 GB equivale a ~970 slots de expertos.
     Repoblar 970 slots por DMA PCIe al terminar el prefill toma:
     $$t_{\text{refill}} = \frac{1,34\text{ GB}}{12\text{ GB/s}} \approx \mathbf{110\text{ ms}}.$$
     Si el prompt es corto (< 2048 tokens), el repoblado tarda más que el prefill mismo.
  3. **Inviabilidad en 86K Tokens (OOM del Pool de VRAM)**:
     A 86K tokens, el KV cache ocupa ~3,5 GB. En el pool de 5,94 GB solo restan 2,44 GB para la caché de expertos.
     Como $m.R$ a 86K requiere **3,52 GB**, **no cabe en los slots prestables de VRAM**. Forzar el préstamo causará un desbordamiento catastrófico.
- **Cómo Evitarlo**:
  - Para $T \le 32\text{K}$: Tomar prestados slots de la caché de expertos solo si no hay slots en decode concurrente activo.
  - Para $T > 32\text{K}$ (hasta 86K): No prestar slots de VRAM para todo el buffer $m.R$. Aplicar **Prefill Bloque-Capa (Zig-Zag)** procesando bloques de 32K a través de las capas, o descargar el buffer $m.R$ a memoria RAM fijada (*pinned host memory*) por PCIe DMA asíncrono.
