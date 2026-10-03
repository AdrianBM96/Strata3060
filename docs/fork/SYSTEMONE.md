# System One en Strata — documentación completa

Estado: **en producción** · Fecha: 2026-10-03 · Motor: Strata 0.1.38 (rama `logprobs-endpoint`)

Strata no permitía leer la distribución del modelo. Ahora la expone, y sobre ella se
monta `ada-decide`, un endpoint compatible con **Jev / SystemOne**: recibe un estado y
preguntas tipadas, y devuelve probabilidades sobre las opciones — en una pasada, sin
generar texto.

```
cliente --HTTP--> ada-decide :8087  (/v1/systemone)
                    |
                    +-- Strata :8081  (--logprobs N)  -->  125B cargado
```

---

## 1. Por qué (y por qué no otro camino)

Strata **no** tenía:
- `logprobs` en la API (`/completion` y `/v1/completion` dan 404; `chat/completions` no lo emite).
- Un endpoint tipo Jev.
- Forma de leer los logits por petición en la ruta nativa.

Se evaluaron y **descartaron**:

| Alternativa | Por qué no |
|---|---|
| Portar `parallel-decision` (bifurcación de KV) al motor | Strata es **QSA + DeltaNet híbrido**: DeltaNet tiene estado recurrente, no cache de KV. Bifurcar por candidato = snapshot/restore del estado, semanas de trabajo en kernels |
| `Clef` / `Clef-Flash` de Cloudflare | Necesitan su cabeza custom (`joint_schema_model.py`), 18 GB en BF16, y en CPU son ~6 s/decisión. En la GPU no caben con Strata |
| `llama.cpp parallel-decision` en un fork | Es el sitio correcto para `/v1/decision`, pero **no** para el 125B (Strata no es llama.cpp) |
| `json_schema` de Strata | Funciona, pero **genera** JSON y lo valida: 6,6 s y sin probabilidades |

La clave que lo hizo posible: el motor **ya copiaba logits a host** en su bucle nativo
(`ver.copy_logits`), solo que sin exponerlos.

---

## 2. El cambio en el motor (`--logprobs N`)

**Fichero**: `src/program/generate.cpp` (rama `logprobs-endpoint`, 65 líneas)

### Qué hace

Emite, **una vez por petición**, una línea en stdout con la distribución de la **última
posición del prompt**:

```
strata logprobs: 5181:-0.649 33:-1.916 15666:-2.254 318:-2.628 ...
```

Son **log-probabilidades absolutas** (log-softmax sobre las 248.320 entradas del
vocabulario, temperatura 1). El llamador aplica su temperatura y normaliza sobre **sus**
opciones.

### Dónde

Tres piezas:

1. **Campo de opción** `int logprobs = 0;` + parseo `--logprobs N` (0..64) en la ayuda.
2. **Helper** `emit_logprobs_line(const float* row, int64_t n_vocab, int k)`: log-sum-exp
   sobre el vocabulario y `partial_sort` de los k mayores. Puro read-out.
3. **Dos puntos de emisión**, uno por ruta:
   - ruta batch (`while (produced.size() < o.max_new)`), tras la lectura de logits a host;
   - **ruta nativa de `--serve`** (`while (!cancelled && produced_n < max_new)`), justo
     después de `ver.run(...)` de la primera ventana, cuando `first_window` es cierto.

El punto que importa es el segundo: **los packs nativos (IQ) nunca pasan por la ruta
batch** (el propio motor lo documenta: *"a native pack never reaches the per-token dump
site"*). Por eso el hook va en el bucle de `--serve`, donde `ver.copy_logits(0, …)` da
la fila 0 de la primera ventana = la última posición del prompt.

### Por qué no rompe nada

- **Solo se activa con `--logprobs N > 0`.** Sin el flag, el código no se ejecuta.
- **No toca el muestreo, el KV, el commit ni el MTP.** Es una copia D2H y una lectura.
- **Verificado**: misma salida **byte a byte** con y sin el flag.
- Coste medido en decode: **38,2 tok/s** con el flag, dentro de la banda normal (35–42).

---

## 3. El cambio en el servidor

**Fichero**: `serve/server.py` (22 líneas)

- En `StrataEngine.generate`, rama nueva para `line.startswith("strata logprobs:")`:
  parsea `id:logprob` y lo guarda en `self.last_logprobs`.
- Reset por petición (`self.last_logprobs = None` al empezar `generate`).
- Se propaga en el evento `done` y se expone en la respuesta JSON y en el chunk final SSE
  como **`strata_logprobs`**.
- **No se filtra al texto generado**: el bucle de lectura ignora las líneas que no
  empiezan por `T `, `PP `, `RESUME `, `DONE` o `ERR`.

---

## 4. `ada-decide` — la API (Jev / SystemOne)

**Fichero**: `~/Strata/ada-decide.py` · Servicio: `ada-decide.service` · Puerto **8087**

### Contrato

```
POST /v1/systemone
{
  "state": "<string | objeto | array>",
  "questions": {
    "<id>": {
      "type": "choice",                       // choice | score | noul
      "instructions": "<qué se decide>",
      "criteria": { "<opcion>": "<desc>", ... }   // choice
      // choice: mapa opcion->descripcion (2..11)
      // score:  lista ordenada; el score esperado es sum(i * p_i)
      // noul:   "criteria" opcional; devuelve la probabilidad de true
    }
  }
}
```

Respuesta:

```json
{
  "model": "ada-next-systemone",
  "answers": {
    "<id>": {
      "type": "choice",
      "choice": "technical",
      "confidence": 0.893,
      "probabilities": {"billing": 0.086, "technical": 0.893, "sales": 0.02}
    },
    "<id>": {"type": "score", "score": 1.88, "confidence": 0.94,
             "legend": ["Can wait","This week","Today"],
             "probabilities": {"0":0.059,"1":0.001,"2":0.94}},
    "<id>": {"type": "noul", "value": true, "confidence": 0.962,
             "probabilities": {"true":0.962,"false":0.038}}
  }
}
```

### Ejemplo

```bash
curl -s http://127.0.0.1:8087/v1/systemone -H 'Content-Type: application/json' -d '{
  "state": "Our checkout returns 500 errors and orders are blocked since the deploy.",
  "questions": {
    "department": {"type":"choice","instructions":"Which team should handle it?",
                   "criteria":{"billing":"Payments","technical":"Bugs or outages","sales":"Commercial"}},
    "urgency": {"type":"score","instructions":"How urgent?","criteria":["Can wait","This week","Today"]},
    "outage": {"type":"noul","instructions":"Is a service down?"}
  }}'
```

### Cómo decide

1. Por cada pregunta renderiza:

```
State:
<state>

Question: <instructions>
Options:
(A) <desc>
(B) <desc>
Answer: (
```

2. Pide **una vez** a Strata con `max_tokens=1`, `temperature=0`, `reasoning_effort=none`.
3. Lee `strata_logprobs` y **normaliza sobre las opciones**: suma `exp(logprob)` de los
   tokens candidatos de cada etiqueta (prueba `A`, ` A`, `(A`, ` (A` para cubrir cómo
   tokeniza cada variante), y normaliza.
4. Devuelve `choice`/`score`/`noul` con `confidence` y `probabilities`.

---

## 5. Números medidos (RTX 3060 12 GB, i5-12400F)

| Medición | Valor |
|---|---|
| Decisión, estado ~200 tok | **2,10 s** |
| Decisión, estado ~1.000 tok | **3,56 s** |
| Decisión, estado ~2.000 tok | **3,63 s** |
| 3 preguntas (choice+score+noul) | **4,90 s** |
| Coste del flag en decode | **ninguno medible** (38,2 tok/s) |
| VRAM extra | **0** (usa el modelo ya cargado) |

Comparación honesta:

| | Latencia/decisión | Calibración | VRAM extra |
|---|---|---|---|
| Jev (nube) | ~524 ms | entrenada (Brier) | n/a |
| decisor pequeño local (12B, `/v1/decision`) | ~101 ms | según modelo | 5–8 GB |
| **ada-decide** | **2,1–3,6 s** | **no entrenada** (sobreconfía) | **0** |

**Conclusión**: es 4–7x más lento que Jev y no está calibrado. Su única ventaja, y es
decisiva aquí, es que es **el único System One que cabe junto al 125B en 12 GB**.

---

## 5b. Comparación medida con `ada-praxis` (nex-mini)

Misma pregunta, mismo estado, con el modelo ya caliente en cada caso:

| | Respuesta | Latencia (caliente) | Salida |
|---|---|---|---|
| **ada-decide** (125B, logprobs) | technical | **1,59 s** | distribución `{billing 0.056, technical 0.912, sales 0.032}` |
| `ada-praxis` (nex-mini 35B, JSON) | technical ("B") | **0,37–0,94 s** | `{"choice":"B","confidence":0.95}` |

Lectura honesta:

- **nex-mini es más rápido** (0,37 s vs 1,59 s): es 4x más pequeño.
- **ada-decide da una distribución real**; ada-praxis da una confianza autoinformada, que
  tampoco está calibrada y no es una probabilidad sobre las opciones.
- La comparación **depende de qué modelo esté residente**: si Strata ocupa la GPU (nuestro
  defecto), `ada-praxis` cuesta además el swap (~25 s + carga); si el residente es
  nex-mini, es `ada-decide` quien paga el swap.

### Aviso de calidad (medido, no teórico)

Con un estado **descriptivo** la decisión fue correcta (`technical`, 0,912). Con un estado
**corto** ("El checkout devuelve 500 desde el deploy.") y opciones en español, devolvió
**`billing` con 0,955** — **incorrecta**, y con confianza alta.

Es exactamente el problema de la calibración: el modelo expresa seguridad que no
corresponde. **Conclusión práctica: no decidas con umbral de confianza sin validar en tus
casos, y da al decisor un `state` lo más completo posible.**



## 6. Operación

```bash
# estado
systemctl --user status ada-decide.service strata.service
curl -s localhost:8087/health

# logs
journalctl --user -u ada-decide.service -f

# arrancar / parar / reiniciar
systemctl --user start|stop|restart ada-decide.service

# a mano (depurando)
python3 ~/Strata/ada-decide.py --port 8087 --strata http://127.0.0.1:8081 \
        --tokenizer /home/bazzite/Strata-data/packs/swift-iq2_xs/tokenizer
```

`ada-decide` necesita **Strata cargado** en `:8081`. Si el arbiter de litellm ha dado la
GPU a un modelo de llama.cpp, Strata está descargado y `ada-decide` no encontrará
`strata_logprobs`. En ese caso: `~/Strata/strata-switch.sh swift`.

---

## 7. Actualizar Strata sin perder esto  ⚠️

El cambio vive en `src/` y `serve/`, así que **cada actualización de Strata lo pisa**.
Procedimiento:

```bash
cd ~/Strata
cp -a patches data README-ADA.md ada-decide.py /tmp/strata-mios/    # fuera del repo
git stash                                    # o commit en la rama
git fetch --depth 1 origin main && git reset --hard FETCH_HEAD
git checkout -B logprobs-endpoint
git apply patches/systemone-logprobs.patch   # re-aplica el fork
./update.sh                                  # recompila el motor
cp build/strata engine/strata                # (update.sh ya lo hace si compila)
systemctl --user restart strata.service
./apply-tuning.sh                            # re-aplica el tuneo (setup borra claves)
```

Si `git apply` falla, el upstream cambió esas líneas: hay que mirar el conflicto. El
parche son **84 líneas**; los dos puntos de emisión son los que importan (§2).

---

## 8. Rollback completo

```bash
# 1) motor y servidor a como estaban
cp ~/Strata/engine/strata.bak-20261003-pre-logprobs ~/Strata/engine/strata
cp ~/Strata/serve/server.py.bak-20261003-logprobs     ~/Strata/serve/server.py

# 2) quitar --logprobs de los configs
python3 - <<'EOF'
import json,glob
for f in glob.glob('/home/bazzite/Strata/strata-*.json'):
    d=json.load(open(f)); a=d['args']
    if '--logprobs' in a:
        i=a.index('--logprobs'); del a[i:i+2]
    json.dump(d,open(f,'w'),indent=1)
print("listo")
EOF

# 3) parar el decisor y reiniciar
systemctl --user disable --now ada-decide.service
systemctl --user restart strata.service
```

O con git: `git checkout main && cp engine/strata.bak-20261003-pre-logprobs engine/strata`.

---

## 9. Problemas conocidos

| Síntoma | Causa / arreglo |
|---|---|
| `strata_logprobs` ausente | El motor arrancó sin `--logprobs`. Comprobar: `ps -o args= -p $(pgrep -f engine/strata)` |
| `ada-decide` responde con `error` | Strata descargado (`loaded:false`). `strata-switch.sh swift` |
| 404 en `/v1/systemone` | Puerto equivocado: `ada-decide` es **8087**, Strata es 8081 |
| `git apply` falla al actualizar | El upstream tocó esas líneas: resolver a mano (84 líneas) |
| Precios/etiquetas raras | Más de 11 opciones: el tope es el top-32 del motor |

---

## 10. Inventario

**Código (rama `logprobs-endpoint`, commit `070d006`)**
- `src/program/generate.cpp` — `--logprobs N`, `emit_logprobs_line`, 2 puntos de emisión
- `serve/server.py` — captura y expone `strata_logprobs`

**Nuevos**
- `~/Strata/ada-decide.py`, `~/.config/systemd/user/ada-decide.service`
- `~/Strata/patches/systemone-logprobs.patch` (re-aplicable)
- `~/Strata/README-ADA.md`, este `SYSTEMONE.md`
- `~/Strata/free-vram.sh`, `serve-strata.sh`, `strata-switch.sh`, `apply-tuning.sh`

**Backups**
- `engine/strata.bak-20261003-pre-logprobs`
- `serve/server.py.bak-20261003-logprobs`
- `strata-*.json.bak-20261003-{logprobs,profilelearn,convcache,perlayer,prefill,beforeload,argsfix}`
- `~/beellama/litellm/content_router.py.bak-20261003-*` (8)
- `~/beellama/litellm/config.yaml.bak-20260920-adaspeed`

**Documentos de referencia**
- `~/.opencode/plan/ADA-COMPUTER-USE-SYSTEMONE.md` (el plan original; §16, el patch de llama.cpp)
