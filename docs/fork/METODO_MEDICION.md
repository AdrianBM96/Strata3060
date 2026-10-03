# Método de medición: la deriva térmica invalida los A/B por bloques

Fecha: 2026-10-03. Corrige el método de los A/B de velocidad de esta máquina. Es un fallo mío,
no de Claude: **su §1.2 ya decía lo correcto y yo no lo seguí.**

## Qué pasó

Medí una misma configuración en dos momentos distintos del día:

| Medida de `swift` sin cambios | Passadas | Mediana |
| --- | --- | ---: |
| Primera (GPU caliente, 83 °C) | 6 | **40,25** |
| Segunda (misma config, GPU **86 °C** tras más trabajo) | 4 | **41,50** |

**La misma configuración dio 40,25 y 41,50: un 3 % de diferencia.** La GPU está limitada por
potencia (170 W) y temperatura (objetivo 83 °C, fan al 81 %), así que su reloj efectivo cambia
con el estado térmico, y con él los tokens/s.

## Por qué invalida mis A/B

Yo medí **bloques**: todas las pasadas de A, luego todas las de B (por ejemplo, `--spec 8` primero
y `--spec 4` después). Cualquier deriva térmica entre los dos bloques se atribuye al cambio. Es
exactamente lo que Claude advierte en su §1.2: **hay que alternar** (A, B, A, B…) y usar 10+10
pasadas para efectos de ~2 %.

**Afectadas (dentro de la deriva, no concluyentes):**

| Cambio | Lo que dije | Realidad |
| --- | --- | --- |
| `--spec 8 --mtp-max-t 4` | +5,1 % | **sin verificar**; en remedición interleaved |
| KV `int8` vs `q4_0` | 39,95 vs 39,25 (mediana) | dentro de la deriva |
| `--pool-workers 10` (SMT) | 40,05 vs 40,25 | dentro de la deriva |
| `--pcie-mode dma` | 40,20 vs 40,25 | dentro de la deriva |
| `STRATA_ADAPT_NOWAIT=1` | 42,25 vs 40,25 (+5 %) | **era térmico**: la base de ese momento era 41,50 |

**No afectadas (siguen en pie):**

- `draft_vocab en`: **106.299 → 40.525** en el log del motor. Un hecho, no una medida de velocidad.
- **YaRN**: `logpos-compare` con **error estándar y suelo de ruido** por texto. El veredicto (neutro
  en código/documento, mejor en conversación) es sólido porque cada comparación lleva su incertidumbre.
- Kernel **IQ2_S bit-idéntico**: es una propiedad, no una media.
- **Relojes de GPU**: la GPU no pasa de ~1880 MHz ni bloqueada a 2100, y la potencia sube de 109 a
  148 W. Limitada por **potencia/térmica**. Hecho reproducible.

## Protocolo a partir de ahora

1. **Alternar** A y B, nunca bloques.
2. **Reiniciar antes de cada medida** cuando el cambio es de proceso (`--spec`, `--kv`): así cada
   medida arranca con la GPU en el mismo estado térmico.
3. **10+10 pasadas** para efectos de ~2 % (su §1.2); 6+6 como mínimo, con mediana y mín./máx.
4. Anotar la **temperatura** de cada pasada (lo hace el script `/tmp/opencode/spec-ab.sh`).
5. Para lo que se pueda, usar medidas con **error estándar** (`logpos-compare`) en vez de promedios.

## En curso

Remedición de `--spec 8` con reinicio por medida y orden alterno (4,8 / 8,4), 6 parejas. El
resultado entra en el commit siguiente.
