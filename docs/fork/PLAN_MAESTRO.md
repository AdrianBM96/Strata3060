# Plan maestro: decode, prefill y System One

Fecha: 2026-10-03. Para Adrián y el agente del servidor. Parte de lo medido hasta la
[ronda 4](MEDICION_RONDA4.md) y de la [nota de decode](NOTA_DECODE_RONDA5.md). Etiquetas: **[medido]** con dónde,
**[est.]** estimación (las mías han salido optimistas dos veces: tomadlas como orden de magnitud).

## 0. Qué se puede garantizar y qué no

**No existe un plan que asegure "inmejorable" al 100 %.** Siempre habrá otra GPU, otro modelo u otra versión de
upstream, y ninguna estimación sustituye a medir. Lo que este plan sí construye:

| Parte | Garantía |
| --- | --- |
| Velocidad (decode y prefill) | Llegar al **techo medido de esta máquina**, con una regla de parada: se para cuando ninguna mejora pendiente puede superar lo que la medida distingue (~2 %) |
| Calidad del modelo | Ningún cambio que no sea idéntico bit a bit entra **sin pasar la puerta de calidad** (§1.3). Los idénticos bit a bit no la necesitan: dan los mismos tokens |
| System One | Una **garantía estadística del error** de lo que decide solo (predicción conformal, §4.4): por ejemplo "≤ 5 % de error en las decisiones automáticas, con un 90 % de confianza", sobre vuestras decisiones etiquetadas. Lo demás escala |
| Todo | **Sin regresiones**: CI, campeón/aspirante y versiones con vuelta atrás |

## 1. Método común

### 1.1 Banco fijo

Los mismos prompts siempre, `temperature 0`, máquina sin otra carga y GPU ya caliente:

| Id | Qué mide |
| --- | --- |
| B1 | Decode: respuesta de 1.024 tokens con razonamiento, contexto corto |
| B2 | Decode: turno de agente con 32K de contexto |
| B3 | Decode: turno de agente con 128K de contexto |
| P1 / P2 | Prefill: prompt nuevo de 24K y de 128K |
| S1 | System One: estado nuevo de ~500 tokens + 4 preguntas (latencia de la 1.ª y del total) |
| S2 | System One: 4 preguntas sobre un estado ya en caché |
| S3 | System One masivo: 200 ítems cortos con las mismas preguntas |

Cada medida va a `bench/results/<fecha>-<tema>/` con el config y el log, como hace upstream.

### 1.2 Estadística

- Vuestras pasadas varían ±5 % con la misma configuración: para efectos de ~2 %, **10+10 pasadas alternas**
  (A, B, A, B…), mediana con intervalo bootstrap o test de Mann-Whitney.
- Un cambio cada vez; al final todos juntos y `./setup.sh --calibrate`, porque los ajustes interactúan.

### 1.3 Puerta de calidad

Para todo cambio que no sea idéntico bit a bit:

1. `ops/logpos-compare.py` sobre **tres textos** (código, un documento y una conversación de agente; ~5.000 tokens
   cada uno), con suelo de ruido.
2. Criterio: **ΔNLL dentro de 2 errores estándar del ruido**, y acuerdo del top-1 no peor que el del ruido menos
   0,5 puntos.
3. Si toca el contexto: `tools/needle_bench.py` a 32K, 128K y al máximo.
4. Si toca System One: el conjunto etiquetado (acierto, Brier, ECE) no peor.

## 2. Decode

**Hoy** [medido]: ~40-42 tokens/s (Swift IQ2_XS, 512K con YaRN, KV int8, `--spec 8 --mtp-max-t 4`, borrador `en`,
kernel IQ2_S por bloques).

**Ventana de 48,5 ms:** P 21,4 ms (cadena densa en la GPU; la CPU espera) + C 18,3 ms (expertos en la CPU; la GPU
casi parada) + borrador 2,6 + resto. Acierto de la VRAM 74,6 %; 1,86 tokens por ventana.

**Techo realista ~50-55 tokens/s** [est.]: casi todo vendría de bajar P hacia su ideal de ancho de banda.

| Paso | Qué | Quién | Esfuerzo |
| --- | --- | --- | --- |
| D1 | A/B de sistema de [NOTA_DECODE_RONDA5 §1](NOTA_DECODE_RONDA5.md): relojes de GPU, gobernador, SMT, recalibrar, modo PCIe, `--kv-resident`, páginas de 2 MB, `STRATA_ADAPT_NOWAIT` | servidor | 1-2 días |
| D2 | Desglose de P: `STRATA_VERIFY_PROFILE=1` + `STRATA_DECODE_TIMING=1` | servidor | minutos |
| D3 | Motor, según lo que pese más en D2. Kernels de P idénticos bit a bit (MMVQ de 2 filas, ramas paralelas en el CUDA graph, fusión de lanzamientos). Pesos GR en 8 bits, solo si pasan §1.3. Top-k sin clusters, solo si los turnos de ≥128K son habituales. Caché sembrada con el prompt, solo si el acierto tras un prompt largo es menor que el de régimen | yo (código) + servidor (medir) | 1-6 semanas |
| D4 | Más tokens por ventana: `--suffix-draft 2`; si vuestros clientes muestrean (temperatura > 0), medir la aceptación y probar `STRATA_SPEC_COUPLED=1` | servidor | horas |
| D5 | Upstream: cada versión nueva, reaplicar el parche, CI, banco; se adopta si gana fuera del ruido | yo + servidor | continuo |

**Parar** cuando B1 pase de ~48 tokens/s, o cuando todas las candidatas que queden tengan un techo menor del 2 %.

**La velocidad que se nota no son solo los tokens/s:** es el tiempo hasta terminar una tarea. Un System One que
elige el esfuerzo de razonamiento de cada paso del agente (§4.6) puede ahorrar más tiempo que cualquier kernel.

## 3. Prefill

**Hoy** [medido]: ~865-890 tokens/s a 24K y ~394 a 470K, en trozos de 6.144. Un prompt **nuevo** de pocos cientos o
miles de tokens tiene un coste fijo de ~1,5-3 s: cada trozo envía por PCIe casi todos los expertos que no están en
VRAM, ~29 GB a ~25 GB/s, ≈ 1,2 s [est.].

| Paso | Qué | Calidad | Esfuerzo |
| --- | --- | --- | --- |
| R1 | `STRATA_PREFILL_TIMING=1`: el tiempo por fase | - | minutos |
| R2 | `STRATA_PF_FUSED=1` (en la 5070: IQ2_XS +12 % a 4K) | pasa por §1.3 | horas |
| R3 | ¿`gdn_rec_kh_kernel` cae al kernel lento con 28 SM? (`cuobjdump --dump-resource-usage`; informe 02, P2) | idéntica | horas; si cae, días |
| R4 | Más VRAM libre (`--kv-resident 16384`) para que `--prefill auto` elija un trozo mayor | idéntica | horas |
| R5 | **Lo que más ahorra en un agente es no hacer prefill.** Medir la reutilización (`RESUME n`) en sesiones reales; poner lo estable primero en los prompts; ajustar `--prompt-cache-every` y los huecos de la caché de conversaciones | idéntica | días |

Misma regla de parada que el decode.

## 4. System One

### 4.1 Dónde estamos frente a Jev

Lo que publican sobre Jev (fuentes al final): devuelve decisiones tipadas con probabilidades **entrenadas para estar
calibradas**. Lee el estado una vez y responde todas las preguntas en paralelo en **70-500 ms por lote**, con estados
de hasta ~32K tokens. Cuesta **$0,042 por millón de tokens de entrada**. Debilidades que señalan: caja negra, flojo con
números, fechas y contenido adversario, y elige una opción aunque ninguna encaje.

| | Jev | Nuestro System One hoy | Con este plan |
| --- | --- | --- | --- |
| Latencia, estado en caché | 70-500 ms por lote | 0,37 s **por pregunta** [medido] | ~0,2 s por pregunta, 1 pasada [est.] |
| Latencia, estado nuevo | 70-500 ms por lote | 3,0-4,6 s [medido] | igual, pero oculta si el estado se precarga (§4.2) |
| Volumen (muchos ítems) | 724 anuncios en 40 s | ~1,5-3 s por ítem [est.] | ~10x con el modo masivo [est.] |
| Calibración | entrenada | sesgo de letra corregido (ronda 4) | aprendida de **vuestras** decisiones, con error garantizado (§4.4) |
| Contexto del estado | ~32K tokens | 512K | 512K |
| "Ninguna encaja" | elige igual | `escalate` por acuerdo y masa | conjunto conformal: si no hay una opción clara, escala o responde "ninguna" |
| Por qué decidió | no | no | `explain: true` (System Two) |
| Aprende de vuestros datos | no consta | no | **sí** (§4.5) |
| Privacidad / coste | API, de pago | local, gratis | local, gratis |

**Honestamente:** en latencia y en volumen, Jev gana por diseño. Es un modelo dedicado, no autorregresivo, en sus
servidores; un 125B en una 3060 no lo va a igualar. Donde se le puede ganar, y medirlo, es en acierto en vuestro
dominio (porque aprende de vuestras decisiones), calibración con garantía, contexto largo, abstención, explicaciones,
privacidad y coste.

### 4.2 Más rápido

| Id | Qué | Esperado [est.] | Dónde |
| --- | --- | --- | --- |
| V1 | **Medir los 342 ms** de una pregunta en caché (`STRATA_TRACE=1`). El trabajo real esperado es ~150 ms: 1-2 ventanas y restaurar el punto de control. El resto es sobrecoste que recortar | objetivo ≤ 200 ms por pregunta | motor + servidor |
| V2 | **Leer los logprobs de la última ventana que lee el prompt.** Esa ventana ya calcula los logits de todas sus filas; hoy se abre una ventana de decode más solo para leer la fila 0 | −1 ventana, ~50 ms | motor, pequeño |
| V3 | **`POST /v1/systemone/warm {state}`**: leer el estado en cuanto se conoce, antes de que lleguen las preguntas | la 1.ª pregunta pasa de 3-4,6 s a la latencia en caché, si el estado llega con antelación | `ada-decide` |
| V4 | **La cabeza propia (§4.3) quita las permutaciones**: una pasada por pregunta en vez de 2-3 | ÷2-3 si hoy permutáis | `ada-decide` |
| V5 | **Modo masivo.** (a) Ya posible: la pregunta en el prefijo que se guarda en caché y el ítem al final, así cada ítem lee solo sus tokens. (b) Motor: muchos ítems en **una** pasada por lotes, leyendo los logits de las etiquetas en las filas marcadas | (b) ~10x de rendimiento [est.] | (a) `ada-decide`; (b) motor, 2-3 semanas, con puerta: acuerdo con el modo individual ≥ 98 %, porque cada ítem "ve" los anteriores |

### 4.3 Mejor: una cabeza de decisión propia

Es lo que Jev dice hacer (probabilidades entrenadas para estar calibradas), pero entrenada con **vuestras**
decisiones:

- **Motor:** una clave por petición (`emb=1`) que devuelve el vector que entra en la cabeza del modelo en la fila de la
  respuesta: 2.560 floats, ~10 KB. Ya está en GPU (`Verifier::head_mixed_`; `final_R(t)` da el residual de 10.240).
  Es el mismo patrón que `lpids`, ~40 líneas.
- **Cabeza:** por plantilla de pregunta, regresión logística con pérdida log o Brier. En plantillas con pocos datos,
  regularizada hacia una cabeza global (PCA a 256 dimensiones antes). Se entrena en la CPU en segundos.
- **Memoria kNN:** los vectores de las decisiones etiquetadas; votan las 16 más parecidas. **Aprende en el instante**
  en que llega una etiqueta, sin reentrenar.
- **Mezcla:** letras (lo de hoy) + cabeza + kNN, con pesos aprendidos por plantilla. Con 0 etiquetas es exactamente
  lo de hoy.

### 4.4 Abstención con garantía (predicción conformal)

Con las decisiones etiquetadas recientes se calcula el umbral que garantiza **error ≤ α en lo que decide solo, con
confianza 1−δ** (Learn-then-Test con cota de Clopper-Pearson).

- Cada respuesta trae `prediction_set` (las opciones que no se pueden descartar) y `auto: true/false`. Si el conjunto
  tiene más de una opción o ninguna, escala o responde "ninguna". Esto sustituye las reglas a mano de `margin` y
  `option_mass`.
- Por plantilla si hay datos; global si no. Se recalcula cada día.
- **La garantía vale respecto a la fuente de las etiquetas:** con etiquetas humanas, respecto a la verdad; con las de
  System Two, respecto a lo que decidiría System Two. Y supone que lo que viene se parece a lo reciente: por eso la
  alarma de deriva (§4.5).

### 4.5 Autoaprendizaje: el bucle

```
decisión ──> registro (SQLite: id, plantilla, estado, letras, vector, salida, ¿escaló?)
                │
   etiquetas <──┤  (a) feedback explícito: POST /v1/systemone/feedback {id, label}   -> ORO
                │  (b) System Two: toda decisión escalada se resuelve con razonamiento -> PLATA
                │  (c) auditoría: un 5 % de las automáticas va a System Two en segundo plano,
                │      con la GPU libre (para no aprender solo de los casos difíciles) -> PLATA
                v
   aprendices (cada N etiquetas o cada noche, CPU, segundos):
       calibración por plantilla · cabeza · kNN · umbral conformal
                v
   campeón / aspirante: el nuevo solo entra si mejora en datos retenidos por tiempo
   (Brier, NLL y acierto con la cobertura fijada); versión guardada; vuelta atrás con un comando
                v
   vigilancia: tasa de escalado, acuerdo con System Two en la auditoría, deriva -> alarma
```

**Riesgos y cómo se cubren:**

- Aprender los errores de System Two: oro y plata separados. La evaluación y la garantía usan oro cuando lo hay, y
  una muestra humana periódica.
- Bucle cerrado sesgado: la auditoría aleatoria (c).
- Privacidad: todo local; el estado se guarda con una retención configurable.
- El techo de calidad lo pone System Two (o un modelo mayor que elijáis como "profesor" para la auditoría).

### 4.6 Usos que aceleran todo lo demás

- **Elegir el esfuerzo de razonamiento de cada paso del agente** (low / high). Un usuario de Jev cuenta que así
  redujo a la mitad el coste de su agente. Medidlo en vuestra batería: tiempo por tarea y tasa de éxito, con y sin.
- **Enrutar:** decidir si un paso necesita el 125B o basta nex-mini.

### 4.7 Debilidades de Jev que podéis cubrir

| Debilidad | Cómo |
| --- | --- |
| Números y fechas | un preprocesador determinista que extrae y normaliza números y fechas del estado y añade los derivados ("hace 12 días", "importe > 1.000") |
| Elección forzada | el conjunto conformal (§4.4): "ninguna" o escalar |
| Caja negra | `explain: true`: una justificación corta generada por System Two (1-3 s) |

### 4.8 Benchmark contra Jev ("mejor" significa medido)

- **Conjunto:** 300-500 decisiones reales etiquetadas, de 5-10 plantillas (salen del registro de §4.5).
- **Métricas:** acierto, Brier, ECE, AUROC de la confianza, cobertura con error ≤ 5 %, latencia p50/p95 (estado en
  caché y estado nuevo) y coste.
- Jev por su API, si tenéis acceso (cuesta céntimos); el nuestro, local. Hasta ese momento, "mejor que Jev" es una
  hipótesis.

## 5. Orden y responsables

| Fase | Qué | Quién | Sale cuando |
| --- | --- | --- | --- |
| F0 | Banco fijo (§1.1) como script reproducible | yo lo escribo; servidor lo ejecuta | la línea base está en `bench/results/` |
| F1 | D1, D2, R1 | servidor | desgloses de decode y prefill entregados |
| F2 | System One, base del bucle: SQLite, ids, `feedback`, plantillas, System Two para escalados, auditoría, calibración por plantilla, conformal, `warm`, modo masivo (a) | yo, sin GPU, con tests; servidor despliega | tests en verde y las primeras etiquetas entran |
| F3 | Motor para System One: `emb=1` y logits de la última ventana de lectura (V2) | yo; servidor compila y mide | V1/V2 medidos |
| F4 | Cabeza propia + kNN + campeón/aspirante | yo | ≥ 100-200 etiquetas y el aspirante gana en datos retenidos |
| F5 | Motor de decode y prefill según los desgloses (D3, R2-R4) | yo + servidor | regla de parada (§2) |
| F6 | Benchmark contra Jev (§4.8) | servidor + yo | tabla publicada |
| F7 | Upstream | los dos | continuo |

## 6. Límites

- Una 3060 con un 125B tiene un techo físico. Más allá hace falta más VRAM (`docs/SECOND_GPU.md`) o un modelo más
  pequeño para System One.
- La garantía conformal necesita etiquetas y que el futuro se parezca al pasado reciente.
- Las etiquetas de System Two enseñan a imitar al propio modelo pensando: su calidad es el techo.
- Lo que no sea idéntico bit a bit, siempre por la puerta de §1.3.

## Fuentes sobre Jev

- [Simon Willison: Jev](https://simonwillison.net/2026/Sep/21/jev/)
- [dsebastien: Jev and System One models - a semantic if statement for your code](https://www.dsebastien.net/jev-and-system-one-models-a-semantic-if-statement-for-your-code/)
- [Apidog: What Is Jev?](https://apidog.com/blog/what-is-jev/)
- [LLM Gateway: System One typed decisions](https://llmgateway.io/changelog/system-one-typed-decisions)
- [DigitalOcean: System One API](https://docs.digitalocean.com/products/inference/how-to/use-system-one-api/)
