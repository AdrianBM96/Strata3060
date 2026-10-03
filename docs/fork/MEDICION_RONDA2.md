# Ronda 2: verificación de las correcciones y medición en la 3060

Fecha: 2026-10-03. Verifica el commit `0583327` (correcciones de otro agente sobre
[REVISION_CONFIG_3060.md](REVISION_CONFIG_3060.md)) y mide en la RTX 3060 lo que allí solo se
comprobó sin GPU. Etiquetas: **[verificado]** cita/código · **[medido]** esta máquina.

---

## 1. Sus cuatro correcciones: verificadas

| Corrección | Veredicto |
| --- | --- |
| `apply-tuning.sh` no reponía `--kv int8`, `--prompt-cache-root 256`, `--logprobs 32` | **[verificado]** mi versión tenía **0 ocurrencias** de esos flags: un `setup.py` los habría revertido en silencio. Su arreglo los repone y es **idempotente** (2.ª ejecución: 0 cambios) [medido] |
| `draft_vocab` **sí** es clave de config, y `data/draft_vocab_en.bin` viene en el repo | **[verificado]** y **me corrige**: yo dije que requería un `setup` completo. El fichero `en` (162.100 B) está en `data/`, y `refresh_draft_vocab` solo lo copia. Su `serve-strata.sh` lo hace al arrancar |
| `ada-decide` tomaba el **máximo** de las variantes de cada letra en vez de **sumarlas**; la variante `"A)"` daba el id del paréntesis | **[verificado]** mi código tenía `max(cands)` mientras su propio comentario decía "suma", y `ids[-1]` sobre `"(A"` podía devolver el paréntesis. Corregido con `logsumexp` y filtrando las variantes que no distinguen |
| El parche copiaba logits D2H en el camino sin `--serve` sin usarlos | **[verificado]** era mi `\|\| o.logprobs > 0` sobre `read_logits`, muerto al quitar la emisión del batch. Su parche lo limpia y **conserva** el gancho de `--serve` |

Sus **11 tests** (`test_ada_decide.py`) pasan aquí: `Ran 11 tests ... OK` [medido].

---

## 2. `draft_vocab "en"`: medido, y funciona

```
antes:  strata mtp: draft head over 106299 tokens
ahora:  strata mtp: draft head over  40525 tokens
```

El fichero `Strata-data/mtp/rt/draft_vocab.bin` pasa a 162.100 B (el `en`). **Confirmado en el log
del motor** [medido].

---

## 3. `--spec 8 --mtp-max-t 4`: +5,1 % de decode

Su A/B sugerido, con la base aislada (mismo KV `int8`, mismo `draft_vocab en`):

| | muestras (tok/s) | mediana |
| --- | --- | --- |
| `--spec 4` (6 pasadas) | 40,3 · 41,4 · 40,2 · 39,8 · 39,6 · 40,7 | **40,25** |
| **`--spec 8 --mtp-max-t 4`** (3) | 41,1 · 42,7 · 42,3 | **42,30** |

El mínimo de la variante nueva (41,1) queda por encima de la mediana de la vieja (40,25). **Prefill
sin daño**: 864 tok/s con 84.129 tokens [medido]. **Aplicado** y añadido a `ENGINE_ARGS` de
`apply-tuning.sh` para que `setup.py` no lo revierta.

---

## 4. Calibración: honestidad sobre lo que NO se pudo probar

**El fallo original (`billing` 0,955) no reproduce** con la estructura de prompt nueva (estado en el
mensaje de sistema). Con el mismo estado repetitivo y las mismas opciones en español, las cuatro
variantes dan `technical` correctamente:

| | respuesta | confianza |
| --- | --- | --- |
| por defecto | technical | 0,984 |
| calibración contextual | technical | 0,984 |
| 3 permutaciones | technical | 0,988 |
| 3 permutaciones + calibración | technical | 0,963 |

No se puede **atribuir** la mejora a la calibración: cambiaron a la vez el KV, la estructura del
prompt y la redacción de las opciones. Lo correcto es decir que **el fallo ya no se reproduce**, sin
afirmar cuál de los cambios lo arregló.

### `escalate` se comporta al revés en el modelo real ⚠️

Medido, con el umbral por defecto (`escalate_margin 0.2`, `min_mass 0.2`):

| Caso | Respuesta | margin | `escalate` |
| --- | --- | --- | --- |
| Vago ("no sé qué pasó") | a | **0,722** | false |
| Estado vacío | a | **0,911** | false |
| **Claro (error 500)** | b (correcto) | **0,083** | **true** |

Marca para escalar el caso que un humano ve claro y no los ambiguos. La maquinaria está bien
(comprobada con el modelo simulado en sus tests), pero **el umbral y el margen no valen para este
modelo**: hay que ajustarlos con decisiones etiquetadas (que es lo que él mismo dice).

---

## 5. Configuración final tras las dos rondas

| | |
| --- | --- |
| KV | **`int8`** (precisión gratis, medido 6 vs 6) |
| `--spec` / `--mtp-max-t` | **8 / 4** (medido +5,1 %) |
| `--logprobs` / `--prompt-cache-root` | 32 / 256 |
| `draft_vocab` | **`en`** (40.525 tokens, medido) |
| Perfiles | `swift` (512K, YaRN, diario) y `swift262` (**262K sin YaRN**) |
| System One | 0,37 s por pregunta desde la 2.ª; `lpids` exactos; calibración disponible |

**Decode**: ~42 tok/s · **Prefill**: ~865 tok/s · **Decide**: 0,37 s (2.ª en adelante).

---

## 6. Lo que sigue abierto

1. **Ajustar la calibración con datos etiquetados** (`--log` + `fit-calibration.py`), y **revisar el
   umbral de `escalate`**, que hoy dispara al revés.
2. **Batería de 6 tareas sin YaRN** para decidir si `swift262` pasa a diario. Con 6 tareas la
   potencia estadística es baja; conviene verla como un indicio, no como una prueba.
3. **Kernel AVX2 de IQ2_S** (+3-5 % est.): el único cambio de motor con retorno real, y sigue sin
   medirse en el i5-12400F.
