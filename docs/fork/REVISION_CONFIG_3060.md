# Revisión de la configuración medida (RTX 3060) y de System One

Fecha: 2026-10-03. Revisa [CONFIG_MEDIDA_3060_Y_SYSTEMONE.md](CONFIG_MEDIDA_3060_Y_SYSTEMONE.md) (commit `e630c2f`)
con los informes del fork y los benchmarks del repo. Objetivo: **más tokens/s de decode y prefill sin perder contexto
ni inteligencia**, y un System One mejor.

Etiquetas: **[medido]** con dónde; **[est.]** estimación. Nada de esto está medido todavía en vuestra máquina: la
última sección dice cómo medirlo.

No he podido revisar `ada-decide.py`, `apply-tuning.sh`, `free-vram.sh` ni `SYSTEMONE.md`: no están en el repo. Lo
que digo de System One sale del documento y del parche.

## Resumen

| # | Cambio | Qué gana | Qué cuesta |
| --- | --- | --- | --- |
| 1 | **Quitar YaRN del uso diario**: perfil por defecto a 262K sin escalado; 512K como segundo perfil | recupera el modelo original en **todas** las peticiones | 512K solo bajo demanda (~25 s de cambio) |
| 2 | **KV de 8 bits en vez de `q4_0`** | recupera precisión en textos largos (perplejidad de documento +8-12 % con q4_0 [medido]) | ~5-8 % de decode [medido en una 5070] |
| 3 | `"draft_vocab": "en"` | +80-110 expertos en VRAM, +1-2 % [est.] | casi sin borradores en chino, japonés o coreano |
| 4 | System One: **estado en el prompt de sistema** + `--prompt-cache-root 256` | 2.ª y siguientes preguntas sobre el mismo estado: leer ~40 tokens en vez del estado entero [est.: de ~1,6-3,6 s a ~0,6-1 s] | nada |
| 5 | System One: **calibración** (permutar opciones + calibración contextual + temperatura ajustada) | ataca el fallo medido "billing 0,955 con un estado corto" | 1-2 peticiones extra por plantilla o por decisión, baratas con el punto 4 |
| 6 | System One: **escalar a "System Two"** cuando el margen es bajo | calidad del modelo completo solo donde hace falta | una generación normal en los casos dudosos |
| 7 | A/B de `--pool-workers 6`, `STRATA_ADAPT_NOWAIT=1`, `--vram-reserve-mib 500` y páginas de 2 MB | 0-7 % cada uno [est.] | minutos de medición |
| 8 | Motor: kernel AVX2 de IQ2_S por bloques (plan 2.1) | kernel x1,22-1,29, **idéntico bit a bit** [medido aquí]; +3-5 % de decode con Swift IQ2_XS [est.] | 1-2 semanas, recompilar |

Los puntos 1 y 2 **no aceleran**: devuelven la inteligencia que la configuración actual cede sin decirlo. Si se
aplican los dos, el decode baja algo (punto 2) y lo recuperan los puntos 3, 7 y 8.

## 1. YaRN a 512K cambia el modelo en cada petición

`--rope-scaling yarn --rope-scale 2` no se activa solo a partir de 262K. `bench/results/2026-09-28-rope-scaling`
lo dice así: *"yarn's mscale applies at every position (a 64-token greedy prompt diverges from the stock run after
~19 tokens); arm A is the stock model, arms B/C are not"*.

Es decir: con la configuración actual, **una pregunta de 500 tokens la responde un modelo ligeramente distinto del
original**, y nadie ha medido cuánto peor (solo que recupera una aguja a 512K). Eso afecta también a System One:
las probabilidades salen de ese modelo modificado.

**Propuesta:**

- `strata-swift-iq2_xs.json` (el de `ada-next`) a **262.144 sin `--rope-scaling`**: es el contexto entrenado del
  modelo, y con KV streaming ocupa en VRAM lo mismo que 32K.
- Un segundo config, por ejemplo `strata-swift-iq2_xs-512k.json`, con YaRN x2, para las pocas tareas que de verdad
  pasen de 262K. Se cambia como ya cambiáis de pila (~25 s). La conversación ya leída no se pierde si la guardáis en
  la caché de conversaciones del otro perfil.

Contexto: 262K por defecto, 512K disponible. Inteligencia: la del modelo original en el uso diario.

## 2. `q4_0` cuesta precisión, y más cuanto más largo es el texto

Medido en `bench/results/2026-09-27-kv-q4` (Q2_0, teacher-forced, fp16 como referencia):

| Texto | Mismo top-1 que fp16: int8 / q4_0 | Perplejidad frente a fp16 con q4_0 |
| --- | --- | --- |
| agente de código (512) | 98,4 % / 95,7 % | casi neutro |
| documento (1.024) | 95,6 % / 91,3 % | **+8 %** |
| documento a 8K | 92,3 % / 88,0 % | **+12 %** |

En velocidad, q4_0 ganó **+8 %** de decode a 128K con streaming (67,4 frente a 62,4 tokens/s, RTX 5070) por 146
expertos más y menos lectura de KV.

**Propuesta: `--kv int8`.** Para un agente de código la pérdida de q4_0 es pequeña, pero vuestra regla es no
sacrificar inteligencia, y en documentos largos la pérdida es medible. La RAM alcanza: a 262K la KV de 8 bits ocupa
3,6 GB (q4_0: 2,0); a 512K, 7,2 GB (q4_0: 3,9). Con 38,5 GiB del motor y 8 GiB de caché de conversaciones cabe en
62 GB.

Si preferís la velocidad: q4_0 es defendible **solo** para el perfil de agente de código. Documentadlo como decisión,
no como configuración neutra.

## 3. Subconjunto de borradores `en`

Vuestro config no fija `draft_vocab`, así que usa el de por defecto (`cjk`, 106.299 tokens). Con
`"draft_vocab": "en"` en `strata-swift-iq2_xs.json` (setup lo aplica al arrancar, `setup.py:2578`), el cabezal del
borrador ocupa ~110 MiB menos (`docs/DETAILS.md:105`): ~80 expertos más y un cabezal que lee menos por ronda.

El borrador solo propone; el modelo decide cada token. El texto no cambia. El español usa los mismos tokens que el
subconjunto `en` (el `cjk` es el `en` más los tokens CJK). Con texto en chino, japonés o coreano se pierde casi toda
la aceleración del MTP.

## 4. System One: la latencia es la lectura del prompt, y se puede reutilizar

**Por qué una pregunta de 100 tokens tarda 1,9 s.** En `--serve`, una parte del prompt de más de `--short-read` (64)
tokens va por el camino por lotes. El propio motor documenta que ese camino cuesta ~300 ms por ejecución aunque haya
pocos tokens: envía por PCIe todos los expertos que el trozo usa y no están en VRAM, y luego rellena los huecos
prestados, ~180 ms (`src/program/generate.cpp:5388-5394`). Encima van la plantilla, la primera ventana de
verificación, el commit y el borrador.

**Cómo reutilizarlo.** Si las preguntas sobre el mismo estado comparten prefijo, el motor puede leer el estado una
vez y para las siguientes solo la cola. Se guarda un punto de control al final del prompt de sistema cuando mide al
menos `--prompt-cache-root` tokens (por defecto 2.048, `generate.cpp:410-412`). Así que:

1. **El estado va en el mensaje de sistema**, y la pregunta (instrucciones, opciones, `Answer: (`) en el mensaje de
   usuario:

   ```
   system: <reglas fijas del decisor>\n\nState:\n<estado>
   user:   <instrucciones de la pregunta>\nOptions: (A) ... (B) ...\nAnswer with the letter.
   assistant: (            <- sin razonamiento
   ```

2. **`--prompt-cache-root 256`** en los args del motor, para que un estado de 256 tokens o más se guarde.
3. Las preguntas **en serie** sobre el mismo estado. La 1.ª lee todo; la 2.ª y siguientes leen solo su pregunta.
   Si cabe en `--short-read` (64 tokens), va por las ventanas de verificación (~16 ms por token en la 5070) sin el
   coste fijo del camino por lotes. Subid `--short-read` a ~96 si las preguntas son algo más largas, y medidlo: el
   cruce exacto depende de la 3060.

Estimación: de 1,9-3,6 s por pregunta a **~0,6-1 s** desde la 2.ª [est.]; las 3 preguntas de vuestro ejemplo, de
4,9 s a ~2,5-3 s. Se ve en el log: la línea `RESUME n` dice cuántos tokens se reutilizaron.

**Dos mejoras del parche** (pequeñas):

- **Pedir los ids de las opciones, no el top-32.** Si la letra de una opción no está entre las 32 más probables, hoy
  desaparece de la normalización (cuenta como 0). Una clave por petición (`lp_ids 32,33,34`) que imprima la logprob
  de esos ids exactos lo evita. Es otra lectura de la misma fila.
- **Cerrar las rutas que no son `--serve`.** El tercer bloque del parche (`generate.cpp`, tras
  `STRATA_DUMP_FIRST_LOGITS`) imprime en otra ruta de la primera ventana. Si en algún modo se ejecutan las dos, salen
  dos líneas por petición (la última pisa a la primera; es la misma fila, así que hoy es inofensivo). Conviene dejar
  una sola.

## 5. System One: calibración

El fallo medido (§5.6 del documento: `billing` con 0,955 sobre un estado corto, cuando la respuesta era `technical`)
es el conocido sesgo de las probabilidades de un LLM puntuando opciones: por la letra, por la posición y por las
palabras del prompt. Tres arreglos estándar, de menor a mayor coste:

1. **Calibración contextual** (Zhao et al., 2021, *Calibrate Before Use*): por cada plantilla de pregunta, una
   petición con un estado vacío (`State: N/A`) da el sesgo `p_cf` de cada opción. Después,
   `p_cal(o) ∝ p(o) / p_cf(o)`. Cuesta una petición por plantilla, no por decisión, y se guarda.
2. **Permutar las opciones**: preguntar 2-3 veces con las opciones en otro orden (A/B/C rotadas) y promediar por
   opción. Quita el sesgo de letra y de posición. Con el punto 4, cada permutación extra solo lee la cola (~0,5 s).
3. **Temperatura ajustada**: con 100-200 decisiones etiquetadas de vuestro uso real, ajustar un único `T` que
   minimice el Brier o la log-loss (`p ∝ exp(logprob / T)`). Esto es lo más cercano a la "cabeza entrenada con
   Brier" que echáis en falta, sin entrenar nada.

Y una regla de producto: **si el margen entre las dos primeras opciones calibradas es bajo** (por ejemplo < 0,2),
**escalar a System Two**: una petición normal con razonamiento a Strata, o a `ada-ethos`. System One responde rápido
los casos fáciles, y los difíciles los decide el modelo completo pensando.

## 6. Ajustes A/B sin tocar código

Cada uno se mide con 3 pasadas alternas, como hicisteis con `--expert-cache-per-layer`:

| Ajuste | Por qué | Esperado [est.] |
| --- | --- | --- |
| `--pool-workers 6` (y 11) | `--calibrate` solo prueba el valor por defecto y menos hilos; el i5-12400F tiene 6 núcleos de rendimiento y ninguno de eficiencia | 0-7 % |
| `STRATA_ADAPT_NOWAIT=1` | no bloquear la ventana mientras llegan las copias del tier adaptativo (#463) | 0,5-1,5 % en PCIe 4.0 |
| `--vram-reserve-mib 500` | sin escritorio en la 3060 sobran ~0,5 GiB libres con todo cargado; comprobar que el log sigue diciendo ≥ 256 MiB libres (`generate.cpp:4950`) | +140 expertos, ~+0,5 % |
| Páginas de 2 MB | la arena solo usa `MAP_HUGETLB` (`src/core/pinned.cu:204-226`), que necesita `vm.nr_hugepages`: ~18.200 páginas para 35,5 GB. Se pueden reservar en `free-vram.sh` (antes de cargar) y liberar al parar, para no quitárselas a llama.cpp. A/B con `STRATA_NO_LARGEPAGES=1` | 0-3 % (sin efecto en una VM; sin medir en hardware real) |
| `--spec-min-p` 0,4 / 0,6 a 262K | la calibración ya mostró que el óptimo depende del contexto | 0-3 % |

Recordad: la clave de la calibración incluye el contexto, así que hay que volver a lanzar `./setup.sh --calibrate`
después de los puntos 1 y 2.

## 7. El cambio de motor que más rinde en esta máquina

Swift IQ2_XS guarda el gate/up de 34 de sus 48 capas en IQ2_S, y la CPU calcula el ~85 % de los expertos (3.700 de
24.576 están en VRAM). El prototipo de `informes/prototipos-cpu/iq2s_proto.cpp` vectoriza por bloque la
decodificación de IQ2_S. **Medido aquí** (Xeon de 2,6 GHz con AVX2): x1,22-1,29 por núcleo, 0 floats distintos. En
el i5-12400F se estima **+3-5 % de decode** [est.], y lo mismo para la lectura de prompts cortos (camino de ventanas).
Extenderlo a IQ2_XXS/IQ3_\* (plan 2.1) y el kernel Q2_0 en planos (plan 2.2, el `down` de varios packs) suma más.

## Cómo medirlo

Antes de cada cambio y después, con la misma batería:

1. `tools/needle_bench.py --lengths 32k,128k,262k` (contexto).
2. Decode a 1.024 tokens con razonamiento `high`, 3 pasadas; prefill con el prompt de 24.561 tokens.
3. Calidad: `tools/kv_precision_compare.py` (int8 frente a q4_0), y vuestra batería de 6 tareas con y sin YaRN.
4. System One: latencia por pregunta (1.ª y siguientes, mirando `RESUME n`) y **acierto y Brier sobre un conjunto
   etiquetado** de decisiones reales, con y sin cada paso de calibración.

Orden sugerido: 1 + 2 + 3 (configuración, minutos), recalibrar, medir. Después, 4 + 5 (System One, horas). Por
último, el 7 (motor, semanas).
