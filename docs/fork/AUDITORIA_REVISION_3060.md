# Auditoría de `REVISION_CONFIG_3060.md`, y lo que medimos

Fecha: 2026-10-03. Audita [REVISION_CONFIG_3060.md](REVISION_CONFIG_3060.md) (revisión de otro agente)
**verificando cada cita** contra los benchmarks del repo y **midiendo en la RTX 3060 real** lo que se
podía medir. Máquina: RTX 3060 12 GB, i5-12400F (AVX2), 62 GB, Strata 0.1.38.

Regla: **[medido]** en esta máquina · **[verificado]** la cita del repo dice eso · **[refutado]** la
medición no sostiene la afirmación.

---

## Veredictos

| Punto de la revisión | Veredicto |
| --- | --- |
| YaRN cambia el modelo en todas las peticiones | **[verificado]** + **[medido]** |
| `q4_0` pierde precisión en textos largos | **[verificado]** (las cifras son exactas) |
| `draft_vocab` no está fijado | **[verificado]** |
| La calibración nunca prueba más hilos | **[verificado]** |
| El coste fijo de System One viene del camino por lotes | **[verificado]** + **[medido]** |
| El top-32 puede perder opciones | **[verificado]**, corregido |
| `q4_0` es un 8 % más rápido → usar `int8` cuesta velocidad | **[refutado]** en la 3060 |
| `--pool-workers 6` puede dar 0-7 % | **[refutado]**: neutro |
| `--vram-reserve-mib 500` | coherente, pero el defecto ya es 700: ganancia ~0,5 % |
| Kernel AVX2 de IQ2_S (+3-5 %) | **sin verificar** (su medida es en un Xeon, no en el i5) |
| Páginas de 2 MB | **sin verificar** (real en `pinned.cu`, efecto no medido) |

---

## 1. YaRN: verificado y medido

La cita es textual (`bench/results/2026-09-28-rope-scaling/README.md`):

> yarn's mscale applies at every position (a 64-token greedy prompt diverges from the stock run after
> ~19 tokens); arm A is the stock model, arms B/C are not, by design.

**Medicion propia** (mismo prompt, `temperature 0`), con `--logprobs` para comparar la distribución:

| | Top-1 | Cola |
| --- | --- | --- |
| Con YaRN ×2 | token 1743, −0,025 | 6374 −4,425 · 16849 −5,646 |
| Sin YaRN (stock) | token 1743, −0,024 | 6374 −4,498 · 16849 −5,767 |

El **top-1 es idéntico**; la cola difiere ~0,1 nats. En una generación de ~200 palabras:

- texto idéntico en los **primeros 195 caracteres**, divergen después (~45 tokens);
- decode igual con y sin YaRN (**33,9 vs 34,8 tok/s**).

Conclusión: el efecto es **real** (las respuestas normales cambian) y **suave** (mismo top-1 en el
primer token). Sigue **sin medir si es peor**, como el propio revisor dice.

---

## 2. KV `int8` vs `q4_0`: la expectativa no se traslada

Las cifras de precisión son exactas (`bench/results/2026-09-27-kv-q4`): documento +0,078 nats a 1K,
+0,112 a 8K; en código **negativo** (−0,031, algo mejor).

Pero la velocidad la midió en una **RTX 5070**. En la 3060, 6 pasadas de cada una, mismas condiciones:

| KV | muestras (tok/s) | mediana | media |
| --- | --- | --- | --- |
| `q4_0` | 38,9 · 39,6 · 41,4 · 41,6 · 38,7 · 38,1 | **39,25** | 39,72 |
| `int8` | 45,7 · 39,7 · 41,0 · 39,1 · 40,2 · 38,0 | **39,95** | 40,62 |

**No hay diferencia medible.** El +8 % de `q4_0` es de una tarjeta con 672 GB/s; en una 3060 el cuello es
otro. Por tanto **`int8` recupera la precisión gratis** → es la configuración por defecto.

---

## 3. A/B de minutos

### `--pool-workers 6` — [refutado], neutro
`tools/calibrate.py:worker_candidates()` solo prueba el valor por defecto, ⅔ y ½ — **nunca más**
(verificado). Pero medido, 6 no ayuda:

| | mediana |
| --- | --- |
| 5 workers (defecto) | **39,60** |
| 6 workers | 39,60 (media algo peor) |

Coherente: 6 workers + el hilo host sobre 6 núcleos es sobre-suscripción. **Se queda el defecto.**

### `--vram-reserve-mib 500`
El defecto del motor ya es **700** (`generate.cpp:348`), así que la propuesta es bajar 200 MiB
(~140 expertos, ~0,5 %) a cambio de menos margen. **No aplicado**: relación mala.

### `draft_vocab "en"`
Verificado: no está fijado, y el motor usa el de defecto `cjk` (**106.299** tokens, el log lo dice).
`docs/DETAILS.md:105`: `en` = **40.525** ids, ~110 MiB menos de VRAM, 1-2 % más rápido en inglés.

**No aplicado todavía** porque **no es una clave de config**: setup elige *qué fichero* de borrador usa
(`setup.py:DRAFT_VOCABS` → `draft_vocab.bin` / `draft_vocab_en.bin`), y el de `en` **no está
descargado**. Requiere `./setup.sh --draft-vocab en` (que reescribe el config; hay que reaplicar
`apply-tuning.sh`).

---

## 4. System One: latencia — medido, y mejor de lo estimado

Se implementó lo propuesto (estado en el mensaje de **sistema**, `--prompt-cache-root 256`, preguntas en
serie) y se midió:

| | Antes | Ahora |
| --- | --- | --- |
| 2.ª pregunta en adelante (mismo estado) | 1,9-3,6 s | **0,37 s** |
| 4 preguntas sobre un estado | ~8-14 s | **1,48 s** |
| 1.ª pregunta con estado nuevo | — | 3,0-4,6 s (lee el estado; sin cambios) |

El log lo confirma: `prompt 311 tokens = 304 reused + 7 read in 342 ms`. **Solo 7 tokens leídos** por
pregunta. La estimación era ~0,6-1 s; la realidad es 0,37 s.

Contra `ada-praxis` (nex-mini), en caliente: 0,37 s frente a 0,37-0,94 s — **ya no es más lento**.

---

## 5. System One: ids exactos — corregido

Era un **fallo real**: si la letra de una opción no caía en el top-32, contaba como 0 y deformaba la
distribución. Implementado con una clave por petición, `lpids=a,b,c`:

- El motor imprime la logprob de **exactamente esos ids** (misma fila, otra lectura).
- El servidor la reenvía desde `strata_lpids` del cuerpo de la petición.
- Un motor viejo **salta** la clave desconocida → compatible hacia atrás.
- Se eliminó la emisión del camino batch para dejar **una sola** ruta, como pedía la revisión.
- `ada-decide` envía ahora los ids de las etiquetas y **reporta** `missing_label_ids` si alguna faltara.

---

## 6. Lo que queda sin verificar (honestamente)

- **Kernel AVX2 de IQ2_S** (×1,22-1,29, +3-5 %): el prototipo se midió en un **Xeon de 2,6 GHz**, no en
  el i5-12400F. Es el cambio con más retorno potencial del plan y el único que justifica semanas.
- **Páginas de 2 MB**: el mecanismo es real (`src/core/pinned.cu`, `MAP_HUGETLB`), el efecto está sin
  medir por su autor en hardware real.
- **Calibración** (permutar opciones, calibración contextual, temperatura ajustada): son técnicas
  estándar; sin un conjunto etiquetado de decisiones vuestras no hay número. **Sigue pendiente y es
  importante**: el fallo medido (`billing` 0,955 en un caso técnico) no está resuelto.

---

## 7. Estado de la configuración tras esta auditoría

| | Antes | Ahora |
| --- | --- | --- |
| KV | `q4_0` | **`int8`** (precisión gratis, medido) |
| `--pool-workers` | defecto (5) | defecto (probado 6: neutro) |
| `--prompt-cache-root` | defecto (2048) | **256** (System One) |
| `--logprobs` | — | **32** + `lpids` por petición |
| Perfiles | uno (512K YaRN) | **dos**: `swift` (512K, YaRN, diario) y `swift262` (**262K sin YaRN**) |
| System One | 1,9-3,6 s/pregunta | **0,37 s** (2.ª en adelante), ids exactos |

El perfil de **262K sin YaRN** queda disponible con `~/Strata/strata-switch.sh swift262` (~25 s de
cambio). Es la recomendación de la revisión para el uso diario: devuelve el modelo original en todas
las peticiones. **No se cambió el defecto** para no alterar el contexto que se eligió a propósito.
