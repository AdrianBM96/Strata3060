# P05. Partición Pareto de expertos: Hot Tier estático + Dynamic Tier para la caché de VRAM

## 1. La idea en 2 frases
Dividir la memoria de la caché de expertos de la GPU en dos niveles: un *Hot Tier* fijo que mantiene permanentemente en VRAM los ~45 expertos más frecuentes de cada capa (obtenidos de un perfil de activación global Pareto), y un *Dynamic Tier* con los ~27 slots restantes por capa que rota según la localidad de la conversación.

## 2. De dónde sale
- **PowerInfer**: *PowerInfer: Fast Large Language Model Serving with a Commercial GPU and Sparsity* (Song et al., 2023, arXiv:2312.12456, SJTU-IPADS).
- Patrón de colocación híbrida de neuronas/expertos frecuentes de **PowerInfer-2** (arXiv:2406.06282).

## 3. Qué cuello de nuestro hardware ataca y por qué aplica
Ataca los **fallos de caché de expertos y el tiempo de espera PCIe en decode (`waitB` 6,5-10 ms)**.
En la RTX 3060 de 12 GB, el presupuesto de caché de expertos es de ~3.450 slots en total (~72 slots por capa para 512 expertos).
Hoy, Strata gestiona estos 72 slots con una política puramente dinámica y reactiva (`--adapt-every 2 --adapt-swaps 32`). Cuando una petición realiza llamadas a herramientas o salta entre código y razonamiento, la política de adaptación expulsa expertos estructuralmente necesarios para cargar expertos de ráfaga temporal, provocando "thrashing" y dejando el acierto de caché en **73-78 %**.
En los modelos MoE (como demostró PowerInfer), la activación de expertos sigue una ley de potencias (distribución de Pareto): en torno al 8-10 % de los expertos (~40-50 de 512) absorben más del 65 % de todas las decisiones del router en cualquier texto. Blindar esos expertos evita que la adaptación los expulse innecesariamente.

## 4. Ganancia estimada con nuestras cifras
- Hoy tenemos 72 slots/capa con 73-78 % de acierto (mediana ~75 %).
  - Fallos por capa = $10 \text{ elecciones} \times (1 - 0,75) = 2,5 \text{ fallos/capa}$.
  - Los fallos generan `waitB` (6,5-10 ms) y cómputo CPU (14-19 ms).
- Con 45 slots en Hot Tier estático (cubren ~68 % de aciertos garantizados) y 27 slots dinámicos (que cubren el 50 % de las llamadas restantes):
  $$\text{Tasa de acierto estimada} \approx 68\% + (32\% \times 0,52) \approx \mathbf{84,6\%} \quad (\text{muy próxima al techo Belady de } 87\%).$$
  - Los fallos caen de 2,5 a 1,54 por capa (un **−38 % de fallos**).
  - La espera PCIe `waitB` se reduce en ~3,5 ms (de ~9 ms a ~5,5 ms).
  - El tiempo de ventana se reduce de 44 ms a ~40,5 ms:
  $$\text{Decode: } \frac{2,3 \text{ tok}}{0,0405 \text{ s}} \approx \mathbf{56,8 \text{ tok/s}} \quad (\mathbf{+12\% \text{ en decode general B1}}).$$

## 5. Riesgo para la calidad
**Ninguno (bit-exacto)**. No altera ningún vector, peso ni selección matemática del router; únicamente optimiza qué expertos residen en la VRAM física del acelerador.

## 6. Integración en Strata y esfuerzo
- **Punto de integración**: `src/core/expert_cache.cpp:180-260`. Marcar un flag `pinned_hot` en la tabla de slots de la caché para los IDs correspondientes al perfil global `hot_experts.bin` (generado previamente por `cachelog`), impidiendo que el algoritmo de swap (`adapt_swaps`) los elija como víctimas.
- **Esfuerzo**: **M** (añadir flag de inmutabilidad en la selección de víctimas de la caché).
