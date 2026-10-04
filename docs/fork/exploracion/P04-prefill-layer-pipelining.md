# P04. Solape inter-capas en prefill: precarga PCIe asíncrona con doble buffer

## 1. La idea en 2 frases
Solapar el streaming PCIe de los expertos no residentes de la capa $L+1$ con el cómputo denso de la capa $L$ (atención QSA, recurrencia GDN y proyecciones) utilizando un ring buffer en doble buffer (ping-pong). Así, cuando la GPU termina la atención de la capa $L+1$, sus expertos ya han cruzado el bus PCIe sin frenar el flujo.

## 2. De dónde sale
- **MoE-Lightning**: *MoE-Lightning: High-Throughput MoE Inference on Memory-constrained GPUs* (Schafhalter et al., ICML 2024), arquitectura del pipeline **CGOPipe** (CPU-GPU-IO pipelining).
- Patrón de doble búfer de streaming en **ktransformers** (*asynchronous scheduling of expert prefetching*).

## 3. Qué cuello de nuestro hardware ataca y por qué aplica
Ataca el **tiempo muerto de espera a PCIe en prefill (`kPfWaitCopy`)**.
En `src/prefill/prefill.cpp:1915-2710`, el bucle recorre secuencialmente las 48 capas:
1. Computa atención GDN/QSA de la capa $L$ (~3,5 ms).
2. Entra en el bloque MoE (`half == 1`), donde el host calcula el router de $L$ y encola la copia PCIe de los expertos en `m.copy`.
3. El stream de cómputo `m.cs` se frena en `cudaStreamWaitEvent(m.cs, m.copied[...])` (`line 2710`) esperando a que los blobs de esa capa terminen de cruzar.
Durante los ~3,5 ms de la fase de atención densa de cada capa, el bus PCIe está completamente infrautilizado. Como en prefill el texto ya se conoce al completo desde el inicio del chunk, el router de la capa $L+1$ puede evaluarse de inmediato o aproximarse, permitiendo lanzar los DMA de $L+1$ mientras la GPU aún calcula la capa $L$.

## 4. Ganancia estimada con nuestras cifras
- En cada una de las 48 capas, la atención densa (GDN o QSA + indexer) tarda entre 2,8 y 4,2 ms en la 3060 (media ~3,5 ms).
- Solapando la copia de los primeros 25-30 expertos de la capa siguiente durante esa ventana de atención:
  $$\text{Tiempo ocultado por capa} \approx 3,0 \text{ ms}.$$
  $$\text{Ahorro por chunk de prefill} = 48 \text{ capas} \times 3,0 \text{ ms} = \mathbf{144 \text{ ms por chunk}}.$$
- Para un prompt largo de 32K tokens (5,3 chunks): ahorro de $\approx \mathbf{0,8 \text{ a } 1,0 \text{ s}}$ de tiempo total de prefill.
- Elimina la mayor parte de las paradas registradas en el perfilador interno `stats_.ms_wait_copy`.

## 5. Riesgo para la calidad
**Ninguno (bit-exacto)**. Los pesos y activaciones operados son idénticos; únicamente cambia el momento relativo de emisión del `cudaMemcpyAsync` en el stream de copia frente al stream de cómputo.

## 6. Integración en Strata y esfuerzo
- **Punto de integración**: `src/prefill/prefill.cpp:1915-2040`. Dividir `m.stage_dev` en dos particiones (`STAGE_A` y `STAGE_B`). Al arrancar `l`, disparar el encolado de DMA de $l+1$ en la partición alterna antes de ejecutar el bloque de atención.
- **Esfuerzo**: **M** (gestión de sincronización de eventos entre los streams `m.copy` y `m.cs` sin incurrir en deadlocks de ring).
