# P05. Partición Pareto de expertos: Hot Tier estático + Dynamic Tier para la caché de VRAM

## 1. La idea en 2 frases
Dividir la memoria de la caché de expertos de la GPU en dos niveles: un *Hot Tier* fijo que mantiene permanentemente en VRAM los ~45 expertos más frecuentes de cada capa (obtenidos de un perfil de activación global Pareto), y un *Dynamic Tier* con los ~27 slots restantes por capa que rota según la localidad de la conversación.

## 2. De dónde sale
- **PowerInfer**: *PowerInfer: Fast Large Language Model Serving with a Commercial GPU and Sparsity* (Song et al., 2023, arXiv:2312.12456, SJTU-IPADS).
- Patrón de colocación híbrida de neuronas/expertos frecuentes de **PowerInfer-2** (arXiv:2406.06282).

## 3. Qué cuello de nuestro hardware ataca y por qué aplica
Ataca los **fallos de caché de expertos y el tiempo de espera PCIe en decode (`waitB` 6,5-10,2 ms)**.
En la RTX 3060 de 12 GB, el presupuesto de caché de expertos es de ~3.450 slots en total (~72 slots por capa para 512 expertos).
Hoy, Strata gestiona estos 72 slots con una política puramente dinámica y reactiva (`--adapt-every 2 --adapt-swaps 32`). En tareas de agentes, las transiciones entre código y razonamiento expulsan expertos estructurales globales para cargar expertos de ráfagas transitorias, provocando "thrashing" y dejando el acierto en **73-78 %** (mediana ~75 %, `C22_TECHO.md`).
Como demostró PowerInfer, en MoE el ~8-10 % de los expertos (~45 de 512) absorbe más del 65 % de todas las decisiones del router de forma estable. Blindar esos expertos en VRAM garantiza esa base y reduce las expulsiones destructivas.

## 4. Ganancia estimada con nuestras cifras
*(Cifras base: `C22_TECHO.md` y `ENTREGAS.md` orden 9: acierto actual 73-78 %, techo Belady en simulador ~85 % (`ORDENES.md` §14), waitB 6,5-10,2 ms con media 9,3 ms, ventana 44 ms con 2,3 tok/v en B1 = 50,70 tok/s).*
- Con 45 slots en Hot Tier estático y 27 slots en Dynamic Tier, el acierto sube del 75 % a un realista **81-82 %** (acercándose al techo asintótico de Belady de ~85 % sin pretender superarlo):
  - Los fallos de caché caen de 2,50 a 1,85 por capa-ventana (un **−26 % de fallos**).
  - La espera PCIe `waitB` baja proporcionalmente de 9,3 ms a **~6,9 ms** (ahorro neto de **~2,4 ms por ventana**).
  - El tiempo de ventana se reduce de 44 ms a **~41,6 ms**:
  $$\text{Decode B1: } \frac{2,3 \text{ tok}}{0,0416 \text{ s}} \approx \mathbf{55,3 \text{ tok/s}} \quad (\mathbf{+6 \text{ a } +8\% \text{ en decode general B1}}).$$

## 5. Riesgo para la calidad
**Ninguno (bit-exacto)**. No altera ningún vector, peso ni selección matemática del router; únicamente optimiza qué expertos residen en la VRAM física del acelerador.

## 6. Integración en Strata y esfuerzo
- **Punto de integración**: `src/core/expert_cache.cpp:180-260`. Marcar un flag `pinned_hot` en la tabla de slots de la caché para los IDs correspondientes al perfil global `hot_experts.bin` (generado previamente por `cachelog`), impidiendo que el algoritmo de swap (`adapt_swaps`) los elija como víctimas.
- **Esfuerzo**: **M** (añadir flag de inmutabilidad en la selección de víctimas de la caché).
