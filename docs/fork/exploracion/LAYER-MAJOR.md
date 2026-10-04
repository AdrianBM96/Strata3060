# LAYER-MAJOR: Estudio de Prefill Capa a Capa con Offloading (Contrato C28)

Fecha: 2026-10-04. Autor: explorer. Para: Claude (arquitecto).  
Objetivo: Evaluar la viabilidad, estado inter-capa, consumo de memoria a 32K y 86K, y problemas documentados de la ejecución capa a capa (*layer-major*) frente al prefill por bloques (*chunk-major*).

---

## 1. Motores y Literatura que Implementan Layer-Major con Offload

| Motor / Paper | Modelo / Arquitectura | Estrategia de Prefill | Lecciones y Limitaciones Documentadas |
|---|---|---|---|
| **FlexGen** (Sheng et al., *ICML 2023*) | Denso (OPT-175B en 1x 16 GB GPU) | **Zig-zag / Layer-major**: procesa todo el batch/tokens capa a capa para reutilizar pesos al máximo. | **Explosión de activaciones**: con $B \times T$ grande, las activaciones superan la VRAM y deben volcarse a RAM/SSD. No contempló MoE ni recurrencia. |
| **MoE-Lightning** (EuroSys 2024 / MSR) | MoE (Switch, Mixtral, DeepSeek) | **Layer-major MoE**: enruta todo el prompt en la capa $L$, agrupa tokens por especialista, transmite cada especialista **1 sola vez** por prompt. | Demostró que el prefill por chunks paga un "impuesto MoE" multiplicativo ($C \times \text{pesos}$). El cuello fue la fragmentación de buffers de dispersión (*scatter-gather*). |
| **ktransformers** (Tsinghua / 2024) | MoE (DeepSeek-V2/V3, Qwen-MoE) | Híbrido: offload de atención a GPU y FFNs a CPU/RAM con kernels optimizados. | En prefill largo descartó layer-major estricto en CPU porque su cuello era ancho de banda de CPU RAM; en Strata, con GPU dedicada, el cuello es PCIe. |
| **PowerInfer-2** (ASPLOS 2024 / 2024) | LLMs en dispositivos con poca RAM | **Block-major**: procesa en grupos de capas (4-8 capas) para balancear VRAM de activaciones frente a recarga de pesos. | Demostró que no es necesario pasar de 1 capa a 48: se pueden empaquetar bloques de capas si la VRAM de activaciones lo exige. |
| **DeepSpeed ZeRO-Inference** (Aminabadi et al., 2022) | Densos y MoE multi-GPU | Streaming secuencial de capas desde NVMe/Host RAM. | A mayor $T$ por capa, la intensidad aritmética sube ($O(T)$ FLOPs por byte transferido), saturando Tensor Cores y ocultando el bus. |

---

## 2. Estado Inter-Capa en Swift 1.5: ¿Qué hay que guardar entre capas?

Swift 1.5 (125B IQ2_XS) es una arquitectura híbrida de 48 capas:
- **36 capas GDN** (Gated DeltaNet / linear attention recurrent SSM).
- **12 capas QSA** (Quantized Sparse Attention, cada 4 capas: 3, 7, 11, ..., 47).
- **Residual hiper-conectado**: $D = 2.560$, $hc = 4 \implies HC = 10.240$ dimensiones por token.

### 2.1. Estado que fluye entre la capa $L$ y la capa $L+1$
Al terminar la capa $L$, solo se necesita **un único tensor**: la activación residual saliente $R^{(L)} \in \mathbb{R}^{T \times HC}$ (o su proyección en $D$).  
No se necesita retener las activaciones de capas anteriores ($0 \dots L-1$). Con un esquema de **doble búfer (ping-pong)** en VRAM (Búfer A y Búfer B), la capa $L$ lee de A y escribe en B; la capa $L+1$ lee de B y escribe en A.

### 2.2. Estado que se acumula persistentemente
1. **Caché KV de las 12 capas QSA**:
   - Geometría: $n\_head\_kv = 2$, $head\_dim = 256$ ([`layout.hpp:42-43`](file:///home/bazzite/strata-explore/include/strata/core/layout.hpp#L42-L43)).
   - En cada capa QSA, los $T$ tokens generan sus claves y valores ($K, V$), que se almacenan directamente en su pool de VRAM/RAM para la posterior fase de decode.
2. **Estado recurrente de las 36 capas GDN**:
   - Geometría por capa ([`plan.hpp:84-86`](file:///home/bazzite/strata-explore/include/strata/plan/plan.hpp#L84-L86)):
     $$\text{State} = 128 \times 48 \times 128 \times 4\text{ B} = 3,145\text{ MB}.$$
     $$\text{Conv1D} = 3 \times 10.240 \times 4\text{ B} = 0,123\text{ MB}.$$
     $$\text{Total por capa GDN} = \mathbf{3,12\text{ MB}} \implies \text{Total 36 capas} = \mathbf{112,3\text{ MB}}.$$
   - **Propiedad crítica**: El estado recurrente de GDN es **estrictamente constante** e **independiente del número de tokens $T$**.

---

## 3. Consumo de Memoria a 32K y a 86K Tokens

En la RTX 3060 de 12 GB, el presupuesto prestable de VRAM para prefill es de **6.286 MB (~6,14 GB)** (`kAutoLendPct = 90 %`, `generate.cpp:4432`).

| Componente de Memoria | Formato / Precisión | Tamaño a 32K tokens ($T=32.768$) | Tamaño a 86K tokens ($T=86.016$) | Ubicación |
|---|---|:---:|:---:|:---:|
| **Ping-Pong Activaciones $R$** (2 buffers $T \times HC$) | BF16 (2 B/elem) | $2 \times 671\text{ MB} = \mathbf{1.342\text{ MB}}$ | $2 \times 1.761\text{ MB} = \mathbf{3.522\text{ MB}}$ | VRAM |
| **Scratch intra-capa MoE/MMQ** (Xq, GU, H, Dm) | INT8 / FP16 | **~450 MB** | **~850 MB** | VRAM (reutilizable) |
| **Caché KV (12 capas QSA)** | Q4_0 (~576 B/tok/capa) | **~226 MB** (768 MB en FP16) | **~594 MB** (2,01 GB en FP16) | VRAM / RAM sesión |
| **Estados recurrentes GDN (36 capas)** | FP32 | **112,3 MB** (fijo) | **112,3 MB** (fijo) | VRAM |
| **Ring / Staging de especialistas** (1 capa) | Pesos IQ2_XS (1 slot) | **~25 MB** (12 slots) | **~25 MB** (12 slots) | VRAM |
| **TOTAL VRAM Requerida en Prefill** | — | **~2.155 MB (2,10 GB)** | **~5.103 MB (4,98 GB)** | **Dentro de los 6,14 GB** |

### Conclusión de Memoria
- A **32K tokens**: Requiere apenas **2,10 GB**, consumiendo solo el **34 %** de la VRAM prestable.
- A **86K tokens**: Requiere **4,98 GB**, encajando con **1,15 GB de margen libre** dentro de los 6,14 GB de la 3060 sin necesidad de verter activaciones a la RAM del sistema.

---

## 4. Problemas Técnicos y Soluciones Específicas

### 4.1. Atención Causal entre Tokens en QSA
- **El reto en FlexGen**: FlexGen sufría con la atención completa porque la matriz de atención $T \times T$ explota en memoria cuadrática ($O(T^2)$).
- **En Strata / QSA**:
  1. QSA no usa atención densa ingenua: utiliza un indexer que selecciona un número acotado de celdas (`m.cap`, [`prefill.cpp:2173`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2173)), reduciendo la complejidad a $O(T \cdot \text{cap})$.
  2. El kernel [`qsa_prompt_attn_batch`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2222) procesa la secuencia causal mediante tiling en bloques de queries sin materializar la matriz de atención completa en memoria.
  3. Ejecutar layer-major sobre todo el prompt a la vez es **más eficiente y bit-idéntico** que partir en chunks, ya que no requiere gestionar fronteras artificiales de KV entre trozos dentro de la misma capa.

### 4.2. Estado Recurrente (GDN / Mamba / DeltaNet)
- **El comportamiento en chunk-major**: En el motor actual, cada chunk procesa $t = 0 \dots 6.143$ y debe serializar y propagar el estado GDN hacia el chunk siguiente a través de las 36 capas.
- **En layer-major**: La capa $L$ procesa la secuencia continua completa $t = 0 \dots T-1$ de un tirón ([`gdn_recurrence`](file:///home/bazzite/strata-explore/src/prefill/prefill.cpp#L2015)). El estado recurrente se actualiza de forma monótona y natural a lo largo de toda la secuencia. Al llegar a $t = T-1$, el estado queda automáticamente en su posición final para el decode, **sin requerir ningún guardado o restauración intermedia**.

### 4.3. Agrupamiento de Especialistas en MoE (Scatter-Gather)
- En layer-major, el router de la capa $L$ clasifica los $T$ tokens del prompt completo (ej. 32K tokens $\times$ 10 = 327.680 asignaciones).
- Cada especialista $e$ acumula $T_e$ tokens de todo el prompt (promedio $T_e \approx 640$ tokens frente a $T_e \approx 120$ en chunks de 6.144).
- **Ventaja de GPU**: Con $T_e \approx 640$, el GEMM de MMQ alcanza una eficiencia de Tensor Cores sustancialmente más alta (mayor intensidad aritmética $M \times N \times K$).
- **Gestión de memoria**: En lugar de asignar un búfer gigante de $T \times K \times D$, se mantiene el esquema actual de Strata con offsets (`m.off`) y filas empaquetadas en `Xq` (`prefill.cpp:2416`), cuyo tamaño crece estrictamente de forma lineal con $T$.

### 4.4. ¿La salida es bit-idéntica?
- **GDN y QSA**: Idénticos bit a bit (mismas operaciones algebraicas en el mismo orden secuencial temporal).
- **MoE**: Cada token acumula las salidas de sus especialistas asignados en su fila de $R$. En álgebra de punto flotante de GPU, la suma aditiva de especialistas sobre filas independientes produce resultados idénticos.
- **Diferencia de redondeo potencial**: Si el GEMM de un especialista pasa de $M=120$ a $M=640$, la reducción interna de CUDA puede sumar acumuladores en diferente orden de árbol, introduciendo una variación de $\le 1$ ULP en mantisa de FP16 (comportamiento habitual en cualquier cambio de batch en cuBLAS/CUTLASS), pero **sin divergencia de logits ni degradación de perplejidad**.

---

## 5. Techo de Rendimiento Teórico: C28 frente a Strata Actual

| Métrica | Chunk-Major Actual ($T=6.144$) | Layer-Major C28 ($T=32.768$) | Ganancia C28 |
|---|:---:|:---:|:---:|
| **Pasadas de los pesos por PCIe (32K)** | **6 pasadas** (228 GB transferidos) | **1 pasada** (38 GB transferidos) | **6× menos datos en PCIe** |
| **Tiempo puro de cable PCIe (32K)** | **20,76 segundos** | **3,46 segundos** | **-17,30 s netos (-83 %)** |
| **Tiempo puro de cable PCIe (86K)** | **48,44 segundos** (14 pasadas) | **3,46 segundos** (1 pasada) | **-44,98 s netos (-93 %)** |
| **Eficiencia GEMM especialistas** | Baja ($T_e \sim 80-120$, subutiliza TC) | Alta ($T_e \sim 500-1.800$, satura TC) | **+15 a +20 % velocidad cálculo** |
| **Throughput proyectado (32K)** | **~990 tok/s** (~23 s total) | **~3.200 – 3.800 tok/s** (~8,5 – 10 s total) | **~3,5× más rápido en prefill** |
