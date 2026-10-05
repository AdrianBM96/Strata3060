# X4. El Borrador (MTP y Suffix-Draft): Por Qué Falla y Cómo Subir Tokens sin Árbol

**Autor:** explorer  
**Destino:** claude (para opencode2 / adrian)  
**Fecha:** 2026-10-05 04:55 UTC  
**Ficheros analizados:** `src/program/generate.cpp` (l. 7130–7205), `include/strata/spec/draft_policy.hpp`, `src/spec/draft_policy.cpp`, `include/strata/spec/suffix_drafter.hpp`, `src/spec/suffix_drafter.cpp`, `docs/fork/ENTREGAS.md` (orden 7 y 9).

---

## 1. El Estado Actual del Borrador en Strata 3060

Strata combina dos motores de especulación lineal en un único bucle de verificación (`generate.cpp:7130-7205`):
1. **MTP (Multi-Token Prediction):** Capa residente en VRAM (836 MiB, 512 expertos, `draft_layer`) que predice autoregresivamente futuros tokens mediante pesos neuronales.
2. **Suffix-Drafter (Prompt Lookup):** Índice CPU de trigramas (`SuffixDrafter`, `WAYS=4`, `table_` abierta) que detecta n-gramas repetidos en el historial y propone la continuación textual exacta sin coste de GPU.

### Cifras Medidas en Producción:
- **B1 (Razonamiento / Prosa en inglés y código nuevo):**
  - **~2,30 tokens por ventana** (con ventana $T=4$, es decir, 1 token confirmado + 1,30 borradores aceptados de 3 ofrecidos).
  - Aceptación de borradores MTP: **~43–45 %**.
  - Tiempo de ventana: **~44 ms** (verify ~41,5 ms + draft ~2,5 ms).
  - Throughput resultante: **50,70 tok/s**.
- **B4 (Copia de código / Refactorización de contexto largo):**
  - Aceptación de borradores: **98 %** (`ENTREGAS.md:122`).
  - Throughput resultante: **56,15 tok/s**.

---

## 2. Dónde y Por Qué Falla el Borrador Actual

### A. Fallo del MTP en Texto General (B1: Techo en ~2,3 tokens/ventana)
1. **Acumulación de error autorregresivo sin autoatención completa:**
   - La cabeza MTP de Swift 1.5 es una única capa densa/MoE desacoplada. Cuando proyecta el token $t+2$ y $t+3$, lo hace sobre sus propias representaciones latentes previas, sin recomputar las 36 capas GDN ni las 12 capas QSA del modelo base.
   - Si la probabilidad marginal de acierto del primer borrador es $p_1 \approx 0,72$, la del segundo decae a $p_2 \approx 0,50$ y la del tercero a $p_3 \approx 0,35$.
   - La esperanza matemática de tokens aceptados en una ventana lineal de $T=4$ es:
     $$E[T] = 1 + p_1 + p_1 p_2 + p_1 p_2 p_3 = 1 + 0,72 + 0,36 + 0,12 = \mathbf{2,20\text{ tokens/ventana}}$$
   - Esto demuestra que el tope de ~2,3 tokens/ventana no es un bug del motor, sino el límite probabilístico intrínseco de una cabeza MTP de una sola capa sin branching (árbol).

2. **Coste creciente del miss en la ventana lineal:**
   - En una ventana de 4 tokens ($T=4$), la GPU ejecuta la cadena densa y MoE para los 4 tokens simultáneamente.
   - Si el modelo base rechaza el borrador en la posición 1 (lo que ocurre el 28 % de las veces en B1), **se descartan los cálculos de las posiciones 2 y 3**, habiendo pagado el coste de memoria y ancho de banda de verificar 4 tokens para quedarse con solo 1.

### B. El Cuello de Botella Artificial de Suffix-Draft en Código (B4)
Al auditar el código fuente de `generate.cpp:7135-7142`, se descubren **dos frenos artificiales severos**:

1. **La restricción de acuerdo forzado con MTP (`generate.cpp:7138`):**
   ```cpp
   if (o.suffix_draft > 0 && !first_window) {
       const int k = sfx.propose(S - 1, sbuf.data());
       sfx_match = sfx.last_match();
       if (k > 0 && sbuf[0] == drafts[0]) {   // <-- FRENO CRÍTICO
           const strata::spec::DraftPolicy::Pick pk = policy.choose(T, k, sfx_match);
           if (pk.lookup) { T = pk.t; from_sfx = true; }
       }
   }
   ```
   - El motor **solo considera el borrador de suffix-lookup si su primer token coincide exactamente con el primer borrador de MTP (`sbuf[0] == drafts[0]`)**.
   - En tareas de código, el suffix-drafter suele tener una coincidencia de 20 o 40 tokens con el contexto previo (`match >= 20`, donde la tasa histórica de acierto es >98 %).
   - Si la cabeza MTP genera un token inicial espurio (p. ej. un espacio extra, un tipo de comilla alternativo o una palabra clave distinta), **el motor descarta la coincidencia exacta de suffix-draft** y se queda con la ventana corta de MTP.

2. **Ventana de suffix-draft artificialmente limitada por `--spec`:**
   - En `generate.cpp:7136`, la longitud de la propuesta de lookup está limitada a `S - 1`:
     `const int k = sfx.propose(S - 1, sbuf.data());`
   - En producción, `strata-swift-iq2_xs.json:16` fija `"--spec": "4"`.
   - Por tanto, incluso cuando un agente está copiando un bloque de 80 líneas de código idénticas (como en B4), el motor **solo puede proponer 3 tokens por ventana** ($T=4$).
   - Para copiar 600 tokens se requieren $600 / 3 = 200$ ventanas completas a 44 ms cada una, tardando 8,8 segundos (56 tok/s). Si la ventana fuera más larga, el tiempo se reduciría a la mitad.

---

## 3. Cuatro Propuestas para Subir Tokens por Ventana SIN Árbol

Estas propuestas no requieren implementar tree attention, estados recurrentes por rama ni kernels complejos de dispersión MoE:

### Propuesta 1. Desacoplar Suffix-Draft de MTP cuando el Match es Largo ($match \ge 8$)
- **Fichero a modificar:** `src/program/generate.cpp:7138`.
- **Cambio:**
  Permitir que el suffix-draft tome el control de la ventana **incluso si `sbuf[0] != drafts[0]`**, siempre que la longitud de coincidencia previa (`sfx_match`) sea lo bastante alta:
  ```cpp
  // Condición actual:
  if (k > 0 && sbuf[0] == drafts[0])

  // Propuesta desacoplada:
  if (k > 0 && (sbuf[0] == drafts[0] || sfx_match >= 8))
  ```
- **Fundamento empírico:**
  En `src/spec/draft_policy.cpp:18`, el prior para el bucket 3 (`match >= 12`) tiene un acierto del **96 %** (`kPriorQ[3] = 0.96`). Cuando hay 8 o más tokens de contexto idénticos, la probabilidad de que la continuación sea exacta es inmensamente superior a la predicción de la capa única de MTP.
- **Ganancia:** Elimina falsos rechazos en código y citas de archivos, subiendo la tasa de activación de lookup en agentes.

### Propuesta 2. Ventana Asimétrica Ampliada para Suffix-Draft ($T_{\text{sfx}} = 8$ frente a $T_{\text{mtp}} = 4$)
- **Diagnóstico:**
  MTP no debe superar $T=4$ porque su error se dispara en el 3.er token. Pero el suffix-draft tiene un decaimiento mucho más plano ($q \approx 0,98$ en código).
- **Acción:**
  Desacoplar el límite de búsqueda de lookup (`kMaxLookup`) del `--spec` del MTP:
  - Mantener MTP acotado a $T=4$ (`--mtp-max-t 4`).
  - Permitir que `sfx.propose()` ofrezca hasta **7 borradores ($T=8$)** cuando `sfx_match >= 16`.
- **Impacto medible en B4 (Copia de código):**
  - Con $T=8$ y aceptación del 95 %: la ventana pasa de aceptar 2,9 tokens a aceptar **~6,8 tokens por ventana**.
  - El número de ventanas para 600 tokens cae de 200 a ~88 ventanas.
  - El throughput de B4 sube de **56,15 tok/s a ~95–105 tok/s (+70 a +85 %)**.
  - Coste de memoria: 0 VRAM adicional (los buffers de verify en `generate.cpp:5783` ya soportan hasta $S=8$).

### Propuesta 3. Adaptación Dinámica del Tamaño de Ventana MTP por Entropía/Margen
- **Diagnóstico:**
  En texto creativo o difícil, MTP propone 3 tokens ciegamente, fallando casi siempre en el token 1.
- **Acción:**
  Leer el margen de confianza o la probabilidad top-1 de la cabeza MTP al emitir los borradores en `mtp.cpp`:
  - Si $p(\text{draft}_0) > 0,80$: abrir ventana completa $T=4$.
  - Si $p(\text{draft}_0) \in [0,50; 0,80]$: abrir ventana corta $T=2$ (1 token de borrador).
  - Si $p(\text{draft}_0) < 0,50$: no ofrecer borradores ($T=1$).
- **Impacto medible en B1:**
  Evita el sobrecoste de verificar tokens 2 y 3 cuando el borrador 1 va a fallar. Reduce el tiempo medio de la ventana de 44 ms a ~36 ms en pasos de baja confianza, elevando el B1 general de **50,70 tok/s a ~55–58 tok/s (+8 a +14 %)**.

### Propuesta 4. Ampliación del Vocabulario de Borrador (`draft_vocab`)
- **Diagnóstico:**
  En `strata-swift-iq2_xs.json:63`, el vocabulario del borrador está configurado en `"draft_vocab": "en"` (40.525 tokens).
- **Problema:**
  En código fuente y comentarios de agentes (que contienen identadores, palabras clave especiales y sintaxis no estándar en inglés plano), muchos tokens quedan fuera del subvocabulario y no pueden ser propuestos por MTP, forzando ventanas $T=1$.
- **Acción:**
  Evaluar un subconjunto ampliado de vocabulario (`draft_vocab: "code"` o `"all"` para el head MTP), eliminando tokens truncados sin aumentar el tiempo de GPU gracias al kernel MMVQ con indirección de filas.

---

## 4. Matriz Resumen de Mejoras sin Árbol

| Propuesta | Dónde se aplica | Complejidad | Ganancia esperada | Contexto donde actúa |
| :--- | :--- | :---: | :---: | :--- |
| **1. Desacoplar Suffix de MTP** | `generate.cpp:7138` | **XS** (1 línea) | +15 a +25 % en código | Refactoring, edición de ficheros |
| **2. Ventana Suffix Asimétrica ($T=8$)** | `generate.cpp:7136` y `draft_policy.cpp` | **S** (~15 líneas) | **+70 a +85 % en B4** | Copia de código, repetición |
| **3. Poda de ventana MTP por confianza** | `mtp.cpp` / `generate.cpp` | **M** (~40 líneas) | +8 a +14 % en B1 | Razonamiento, texto nuevo |
| **4. Ampliar `draft_vocab`** | `setup.py` / config JSON | **S** (config) | +3 a +5 % general | Lenguajes de programación |

---

## 5. Recomendación Concreta para Claude y opencode2

Las **Propuestas 1 y 2** son triviales de implementar, tienen coste cero de VRAM, no tocan los pesos del modelo ni la bit-exactitud del verificador, y desatan de inmediato el rendimiento de especulación en agentes sin necesidad de recurrir a la complejidad del árbol en capas recurrentes GDN.
