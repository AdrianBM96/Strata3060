# Configuración medida en una RTX 3060, y System One (`ada-decide`)

Fecha: 2026-10-03. Sobre Strata **0.1.38** (commit `99f3dbd`), con **una modificación propia** en el motor.
Máquina: **RTX 3060 12 GB · i5-12400F (6P/12T, AVX2 sin AVX-512) · 62 GB DDR4 · Ubuntu 24.04 · driver 580.173.02**.

> **Este documento existe porque el plan del fork dice: "Ninguna cifra está medida en una RTX 3060".**
> Aquí están las primeras medidas reales, con la configuración concreta que las produce, más la
> modificación de System One que sí toca el motor.

Regla de etiquetado, como en el resto del fork: **[medido]** en esta máquina, **[est.]** estimación.

---

## 1. Qué modelo y con qué configuración

| | |
| --- | --- |
| Modelo | **Swift 1.5 IQ2_XS** (`swift-1.5-iq2_xs`), 125B MoE |
| Contexto | **524.288** (512K, con `--rope-scaling yarn --rope-scale 2`) |
| KV | `--kv q4_0` + `--kv-resident 32768` (streaming; la KV vive en RAM) |
| Expertos | `--expert-cache auto` → **~3.700 en VRAM** de 24.576, resto en RAM + CPU |
| Perfil de expertos | `expert_profile_save` activo (aprendido de uso real, se guarda cada 5 min) |
| Caché de conversaciones | `--conversation-cache-mib 8192 --conversation-cache-slots 4` (off por defecto en upstream) |
| `--prefill` | `auto` (el motor elige el chunk: **6.144** tokens en esta tarjeta) |
| MTP | activo, `--spec 4` |
| `--pcie-frac` | **0,22–0,23** (lo pone el sondeo del motor; ver §3) |
| `--pool-workers` | **5** (calibrado) |
| `--logprobs 32` | **modificación propia**, ver §5 |

Datos por modelo (los tres instalados):

| Config | Contexto | RAM del motor | Uso |
| --- | --- | --- | --- |
| `strata-swift-iq2_xs.json` | 524.288 | 38,5 GiB | **por defecto** (`ada-next`) |
| `strata-coder-iq1_m.json` | 524.288 | ~30 GiB | más RAM libre, mejor prefill |
| `strata-iq2_xs.json` | 131.072 | ~39 GiB | el único con **visión** |

---

## 2. Medidas reales [medido]

### Decode y prefill (Swift 125B, 512K)

| Medición | Valor |
| --- | --- |
| **Decode**, razonamiento `high`, 1.024 tokens | **39,5 tok/s** |
| **Decode**, razonamiento `low`, 1.024 tokens | **39,2 tok/s** |
| **Prefill**, prompt de **24.561 tokens** (sin caché) | **890 tok/s** |
| Prefill, prompt corto (93 tokens) | 57,7 tok/s (manda el coste fijo) |
| Aceptación MTP | 62–71 % (436–487 de ~650 borradores) |

Rango observado en varias tandas del mismo día: **decode 35–48 tok/s**, prefill 845–890 tok/s.
La variación es de la máquina compartida, no del motor.

### Contexto largo

| Prueba | Tokens | Resultado |
| --- | --- | --- |
| Aguja en el heno, 131K | 105.423 | ✅ recuperada, prefill 900 tok/s |
| Aguja, ~128K | 133.315 | límite real: 131.072 en esa config |
| Aguja, **512K** | **469.742** | ✅ recuperada, prefill 394 tok/s |

### Comparación con el stack llama.cpp de la misma máquina [medido]

| Modelo | Decode | Prefill 28K | Batería 6 tareas |
| --- | --- | --- | --- |
| **Qwopus V2 27B** (`ada-ethos`) | 10 tok/s | 193 tok/s | **4/6** |
| **nex-mini 35B-A3B** (`ada-praxis`) | 44 tok/s | 184 tok/s | 3/6 |
| **Swift 125B** (Strata) | 39–48 tok/s | 890 tok/s | **4/6** |
| **Coder IQ1_M** (Strata) | 32–37 tok/s | 963 tok/s | 3/6 |

---

## 3. Calibración: qué dijo y por qué la clave importa

`./setup.sh --calibrate` sobre la configuración final (512K, `q4_0`) concluyó:

```
[ok] tuned for this PC: the default settings are already the fastest here (36.4 tok/s)
```

Con el barrido a la vista: `pcie-frac` 0,20 (36,7) > 0,35 (36,1) > 0,55 (29,5) > 0,75 (27,5);
`5 workers` (36,4) > `3` (28,2) > `2` (25,3).

**Aviso que costó encontrar:** la calibración se guarda con clave que **incluye el contexto**
(`...|swift-1.5-iq2_xs|131072|text`). Al pasar de 131K a 512K, la clave deja de coincidir y la
calibración **no se aplica**; el motor cae a su sondeo de PCIe (que da 0,22–0,23, casi idéntico) y al
`spec-min-p` por defecto. Medido: a 512K el default (0,5) **es mejor** que el 0,70 calibrado a 131K.
No hay regresión, pero conviene saberlo al cambiar de contexto.

---

## 4. Convivencia con llama.cpp (mecanismo nativo del motor)

Strata y los modelos de llama.cpp **no caben juntos** en 12 GB. Se resuelve con lo que ya trae el motor:

| Clave en `strata-<model>.json` | Qué hace |
| --- | --- |
| `"before_load": "<ruta>/free-vram.sh"` | aparta el modelo de llama.cpp **antes** de cargar |
| `"min_free_vram_mib": 10500` | si aun así no cabe, responde **503** en vez de fallar en `cudaMalloc` |
| `--lazy` (server) | el servidor escucha ya; carga en la primera petición |

**Detalle crítico:** sin `--lazy`, `before_load` y `min_free_vram_mib` **no se usan** (el motor carga al
arrancar y el hook nunca corre). Sintoma si falta: `session state allocation failed`.

**`--lazy` es incompatible con visión** (`lazy loading is text-only`): la variante con imágenes carga al
arrancar y llama antes a `free-vram.sh`.

Coste medido de un cambio de pila: **~25 s** cargar Strata (33 GB de disco a RAM a 3,4 GiB/s) y
**~16 s** un swap de llama.cpp.

---

## 5. System One (Jev) — la modificación del motor

### 5.1. Qué resuelve

Strata no exponía la distribución del modelo: ni `logprobs` en la API (los endpoints `/completion` y
`/v1/completion` **dan 404**), ni endpoint tipo Jev, ni forma de leer los logits por petición en la ruta
nativa. Sin eso, un System One no se puede montar.

La vía barata que el propio plan del fork describe (leer `logprobs` de las letras de las opciones) **no
era posible**: no hay `logprobs`.

### 5.2. El cambio

`src/program/generate.cpp` (65 líneas) + `serve/server.py` (22 líneas):

1. **`--logprobs N`** (0..64). Tras la verificación de la **primera ventana** en el bucle nativo de
   `--serve`, copia la fila 0 de logits (`ver.copy_logits(0, …)` = **la última posición del prompt**) y
   emite una línea:
   `strata logprobs: 5181:-0.649 33:-1.916 …`
   Son **log-probabilidades absolutas** (log-softmax sobre las 248.320 entradas, temperatura 1).
2. **El servidor** la captura y la expone en `/v1/chat/completions` como **`strata_logprobs`**
   (`{token_id: logprob}`).

**Por qué va en el bucle de `--serve` y no en el del dump**: los packs **nativos (IQ)** nunca pasan por la
ruta batch. El propio motor lo dice: *"a native pack never reaches the per-token dump site"*. El hook
tiene que ir donde el modelo realmente genera.

**Por qué no rompe nada:**

- Solo se activa con `--logprobs N > 0`.
- No toca el muestreo, el KV, el commit ni el MTP: es una copia D2H y una lectura.
- **Verificado: salida byte a byte idéntica** con y sin el flag.
- Coste en decode: **38,2 tok/s** con el flag, dentro de la banda normal.

Parche re-aplicable: [`patches/systemone-logprobs.patch`](patches/systemone-logprobs.patch)
(`git apply`, 84 líneas).

### 5.3. `ada-decide`: el contrato Jev

Servicio en **:8087** (`ada-decide.py`), contrato Jev/SystemOne:

```
POST /v1/systemone
{"state": "<str|obj>", "questions": {
   "<id>": {"type":"choice","instructions":"...","criteria":{"<op>":"<desc>"}},
   "<id>": {"type":"score","instructions":"...","criteria":["...","..."]},
   "<id>": {"type":"noul","instructions":"..."}}}
→ {"model":…, "answers": {"<id>": {choice|score|value, confidence, probabilities}}}
```

Por cada pregunta renderiza `State: … / Options: (A)… / Answer: (` y pide **una vez** a Strata con
`max_tokens=1`. Normaliza los logprobs **sobre las opciones**.

**Ejemplo real [medido]:**

```
department  choice = technical   conf 0,893   {billing 0,086, technical 0,893, sales 0,020}
urgency     score  = 1,88        conf 0,940   {0: 0,059, 1: 0,001, 2: 0,94}
outage      noul   = true        conf 0,962
```

### 5.4. Latencia medida [medido]

| Estado | Por pregunta | 3 preguntas |
| --- | --- | --- |
| ~100 tokens | ~1,9 s | — |
| ~500 tokens | ~2,5 s | — |
| ~1.000 tokens | ~3,5 s | — |
| ~2.000 tokens | ~3,6 s | — |
| ejemplo de 3 preguntas | — | **4,90 s** |

La decisión es **prefill-bound**: cuesta leer el estado entero. Hay ~1,7 s de coste fijo (se ve en que
un estado de 100 tokens ya tarda 1,9 s).

### 5.5. Comparación con `ada-praxis` (nex-mini 35B) [medido]

| | Respuesta | Latencia (caliente) | Salida |
| --- | --- | --- | --- |
| **ada-decide** (125B) | technical | **1,59 s** | distribución real |
| `ada-praxis` (35B, JSON) | technical | **0,37–0,94 s** | `{"choice":"B","confidence":0.95}` |

nex-mini es más rápido (4x más pequeño). Cada uno es rápido cuando **su** modelo es el residente: con
Strata ocupando la GPU, `ada-praxis` paga además el swap.

### 5.6. Aviso de calidad — medido, no teórico ⚠️

Con un estado **descriptivo**: `technical`, 0,912. Correcto.
Con un estado **corto/repetitivo**: **`billing`, 0,955**. **Incorrecto**, y con confianza alta.

Es la **calibración**: es el 125B puntuando la opción siguiente, **no una cabeza entrenada con pérdida de
Brier**. La interfaz es Jev; las probabilidades no.

**Conclusión práctica: no decidir por umbral de confianza sin validar, y dar al decisor un `state`
completo.**

### 5.7. Por qué no se portó `parallel-decision`

El patch de `thecodacus/llama.cpp` (bifurcación de KV, `POST /v1/decision`) **no** se portó al motor:
Strata es **QSA + DeltaNet híbrido**, y DeltaNet tiene **estado recurrente**, no cache de KV. Bifurcar por
candidato exige snapshot/restore del estado, no copiar páginas: semanas de trabajo en kernels donde un
error da probabilidades mal sin avisar. El sitio natural de ese patch es un fork de **llama.cpp**.

---

## 6. Otras funciones del motor activadas

| Función | Estado | Medido |
| --- | --- | --- |
| **Caché de conversaciones** (`--conversation-cache-mib 8192`) | activada (off en upstream) | volver a una conversación: **5,0 s → 1,3 s** |
| **Perfil de expertos aprendido** (`expert_profile_save`) | activado | neutro al principio; madura con uso real |
| **Visión** (variante `qwen`) | activada | **9,5 s** por imagen, describe bien; ~7 % de decode |
| `--kv k8v4` | descartado a 512K | no hace streaming de KV → no cabe |
| `--expert-cache-per-layer` | descartado | A/B 3 pasadas: **39,1 vs 39,2** tok/s (neutro) |
| `--prefill auto:16384` | descartado | el motor lo capa a 6.144 y da **788 vs 818** tok/s |

---

## 7. Qué NO se puede portar al motor (verificado)

Los GGUF de llama.cpp **no** corren en Strata: el formato de expertos está atado a Flash-Next (48 capas,
blob de 1.382.400 B) y necesita la tabla PLE de 28,8 GB derivada de su checkpoint. Comprobado con
`tools/iq_pack.py`:

```
neohorse-4B-Q8_0.gguf            → the model has no expert tensors
Qwopus3.8-27B-Flash-V2...gguf    → the model has no expert tensors
```

---

## 8. Resumen en una tabla

| | Valor [medido] |
| --- | --- |
| Decode (razonamiento) | **39,2–39,5 tok/s** (rango 35–48) |
| Prefill (24K tokens de código) | **890 tok/s** |
| Contexto | **524.288** (aguja recuperada a 469.742) |
| Decide (System One, por pregunta) | **~1,9–3,6 s** |
| VRAM libre con todo cargado | ~0,5 GiB |
| RAM del motor | 38,5 GiB |
| Cambio de pila (Strata ↔ llama.cpp) | 16–25 s |

**La palanca que falta sigue siendo la VRAM**, no el software: 3.700 de 24.576 expertos (15 %) es lo que
cabe, y el resto lo calcula una CPU AVX2 de 6 núcleos. Todo lo demás (kernels, cuantizaciones, System
One) se mueve alrededor de ese techo.

---

## 9. Reproducir esto

```bash
# 1) el motor con la modificación
git apply docs/fork/patches/systemone-logprobs.patch
./update.sh                      # recompila (compila sm_86 en esta tarjeta)

# 2) el tuneo de configuracion (setup.py borra las claves nuestras al reescribir el config)
./apply-tuning.sh

# 3) el decisor
python3 ada-decide.py --port 8087 --strata http://127.0.0.1:8081 \
        --tokenizer <ruta>/packs/swift-iq2_xs/tokenizer
```

Ver también `SYSTEMONE.md` (documentación completa: actualización, rollback, problemas conocidos).
