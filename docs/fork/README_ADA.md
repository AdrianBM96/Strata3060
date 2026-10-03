# Ada-Next — Strata + litellm

`ada-next` (litellm `:4000`) = **Strata** en `:8081` = Qwen3.8-Flash-Next 125B MoE.
El stack de llama.cpp (`beellama`, `models.ini`, GGUF, quants, llama-prism) queda **intacto**
para regresión.

## Arquitectura

```
clientes → litellm :4000 → content_router (arbitraje de GPU)
                              ├─ ada-next  → Strata :8081   (125B)
                              └─ resto     → router :8080   (Qwopus/nex-mini/etc.)
```

La GPU (12 GB) no da para las dos pilas: **una sola está cargada a la vez**, y el
content_router decide cuál según lo que pidas.

## Los dos modos

Se eligen escribiendo en `~/Strata/.mode`:

### `auto` (por defecto) — swap automático, como antes

- Pides un modelo de llama.cpp (`ada-ethos`, `ada-praxis`…) → Strata suelta la GPU
  (`POST /unload`) y el router carga el modelo pedido.
- Pides `ada-next` → se aparta el modelo de llama.cpp y Strata recarga.
- **Con `model=auto`** (el router semántico) hay **histéresis**: no cambia de pila,
  se queda con quien tenga la GPU. Sin esto, alternar arquetipos por turno costaría
  16–25 s cada vez.
- **Imágenes**: van a `ada-praxis` (nex-mini, el único con visión); Strata Swift/Coder
  no leen imágenes.

### `only` — todo a Strata

Cualquier modelo local, sin importar quién tenga la GPU, se sirve con `ada-next`.

| Transición | Coste medido |
|---|---|
| Cargar Strata (33 GB a RAM) | **~25 s** |
| Swap de llama.cpp (Qwopus ↔ nex-mini) | **~16 s** |
| En la misma pila, sin cambio | 0,4–3 s |

## Uso

```bash
~/Strata/strata-switch.sh swift    # Swift 1.5 IQ2_XS — el de ada-next (4/6, 43-48 tok/s)
~/Strata/strata-switch.sh coder    # Coder IQ1_M — 512K, 23 GB RAM (3/6, 32-37 tok/s)
~/Strata/strata-switch.sh qwen     # Qwen IQ2_XS — el único con visión en Strata
~/Strata/strata-switch.sh status
~/Strata/strata-switch.sh stop     # libera la VRAM y vuelve a llama.cpp
```

Modo: `echo auto > ~/Strata/.mode` o `echo only > ~/Strata/.mode`.

## Servicios

`strata.service` (systemd de usuario, habilitado, linger activo). **Espera VRAM** en
vez de hacer crash-loop: si la GPU está ocupada, carga cuando queda libre.
`litellm-proxy.service` gestiona el proxy (no lanzarlo a mano, se duplican instancias).

## Rendimiento medido (RTX 3060 12 GB, i5-12400F, 63 GB)

| | Decode | Prefill 28K | Batería 6 tareas |
|---|---|---|---|
| ada-next (Swift) | 43–48 tok/s | 884 tok/s | **4/6** |
| ada-next (Coder) | 32–37 tok/s | 963 tok/s | 3/6 |
| ada-ethos (Qwopus V2) | 10 tok/s | 193 tok/s | **4/6** |
| ada-praxis (nex-mini) | 44 tok/s | 184 tok/s | 3/6 |

Contexto: **524.288** en Strata (yarn auto, el modelo entrenó a 262K). Verificado con
aguja: 469.742 tokens recuperados a 394 tok/s.

## El detalle que más muerde

El router `:8080` guarda el último modelo con `--cache-idle-slots` y **no lo suelta solo**.
El arbitraje del content_router lo mata (SIGKILL) cuando Strata necesita la GPU; el router
lo recrea en la siguiente petición. Es lo que permite el swap automático.

## Archivos tocados (todos con backup)

- `~/.config/systemd/user/strata.service`, `~/Strata/serve-strata.sh`, `~/Strata/strata-switch.sh`
- `~/Strata/.mode`, `~/Strata/.current-model`
- `~/.config/opencode/opencode.jsonc` — entrada `ada-next`
- `~/beellama/litellm/config.yaml` — `ada-next`, `context_window: 524288`, fallback → `ada-ethos`
- `~/beellama/litellm/content_router.py` — `ada-next` en 4 tablas, `collapse_to_strata`,
  `arbitrate_gpu`, histéresis, visión. Backups: `.bak-20261003-{strata-only,arbiter,fixmatcher,lock,vision,hysteresis,onlyfix}`

## Lo que NO se puede portar de llama.cpp

Los GGUF no corren en Strata: el formato de expertos está atado a Flash-Next (48 capas,
blob de 1.382.400 B) y necesita la tabla PLE de 28,8 GB derivada de su checkpoint.
Verificado: `iq_pack.py` rechaza `neohorse` (denso) y `Qwopus 27B` (MoE)
con *"no expert tensors"*.

## Visión

Strata la soporta **solo en la variante `qwen`** (el `--vision` del motor + `--vram-reserve-mib 700`
+ `--vision gpu` en setup). Está activada y probada: **9,5 s**, describe bien una captura real.

```bash
~/Strata/strata-switch.sh qwen     # Strata 125B CON visión
~/Strata/strata-switch.sh swift    # vuelve al mejor coder (sin visión)
```

Dos detalles que costaron encontrar:

- **`--lazy` es incompatible con visión** (`lazy loading is text-only`). `serve-strata.sh`
  lo detecta: con visión carga al arrancar y llama antes a `free-vram.sh`.
- **`--vision` es un flag real del motor**, no un marcador de setup. Quitarlo rompe las
  imágenes con *"this engine was started without --vision"*.

Coste medido con visión: ~7% de decode (38,2 vs ~41 tok/s) y 700 MiB reservados.

## Convivencia nativa con llama.cpp (sin parches)

La documentación de Strata trae el mecanismo correcto para compartir la GPU, y ahora
es el que manda:

| Clave en `strata-<model>.json` | Qué hace |
|---|---|
| `"before_load": "/home/bazzite/Strata/free-vram.sh"` | Strata aparta el modelo de llama.cpp **antes** de cargar |
| `"min_free_vram_mib": 10500` | si aun así no cabe, responde **503** en vez de reventar en `cudaMalloc` |
| `--lazy` (en `serve-strata.sh`) | el servidor escucha ya y carga el modelo en la primera petición |

Sin `--lazy` estas dos claves **no se usan** (el motor carga al arrancar y el hook nunca
corre). Coste: la primera petición tras arrancar paga la carga (~25 s).

## Caché de conversaciones (activado)

`--conversation-cache-mib 8192 --conversation-cache-slots 4` (off por defecto). La doc lo
describe como *"preserves controller/worker histories when their requests alternate"* —
nuestro patrón de varios clientes. Medido:

| | Antes | Con parking |
|---|---|---|
| Volver a conversación A | 5,0 s | **1,3 s** |
| Volver a conversación B | 4,3 s | **1,4 s** |

Solo cuesta RAM de host (nada de VRAM). El motor pasa de 38,5 GiB.

## System One (Jev) integrado — `ada-decide`

> **Documentación completa, con el cambio del motor, el parche re-aplicable, la
> actualización y el rollback: [`SYSTEMONE.md`](SYSTEMONE.md).**

`ada-decide` (:8087) expone el contrato **Jev/SystemOne** sobre el modelo que Strata tenga
cargado. Una pasada, probabilidades, sin generar texto.

```bash
curl -s http://127.0.0.1:8087/v1/systemone -H 'Content-Type: application/json' -d '{
  "state": "El checkout devuelve 500 desde el deploy.",
  "questions": {
    "department": {"type":"choice","instructions":"Which team?",
                   "criteria":{"billing":"Pagos","technical":"Bugs","sales":"Comercial"}},
    "urgency": {"type":"score","criteria":["Can wait","This week","Today"]},
    "outage": {"type":"noul","instructions":"Is a service down?"}
  }}'
```

Cómo funciona: por cada pregunta renderiza `State: … / Options: (A)… / Answer: (` y pide
**una vez** a Strata con `max_tokens=1`. El motor devuelve `strata_logprobs` (la
distribución de la última posición del prompt) y `ada-decide` **normaliza sobre las
opciones**. Servicio: `ada-decide.service` (habilitado).

### El motor: `--logprobs N`

Strata no tenía forma de leer la distribución del modelo. Ahora `--logprobs N` emite
`strata logprobs: <id>:<logprob> …` con la distribución de la **última posición del
prompt**, en la ruta nativa del `--serve` (`ver.copy_logits(0, …)`).

- **Aditivo y detrás del flag**: no toca el muestreo, el KV ni la generación. Verificado
  con salida **byte a byte idéntica** con y sin el flag.
- Expuesto en `/v1/chat/completions` como `strata_logprobs` (token id → logprob).
- Coste medido en decode: **38,2 tok/s** con el flag, dentro de la banda normal (35–42).

### Latencia medida (RTX 3060)

| Estado | Decisión completa (3 preguntas) |
|---|---|
| ~200 tokens | 2,10 s |
| ~1.000 tokens | 3,56 s |
| ~2.000 tokens | 3,63 s |
| 3 preguntas del ejemplo | 4,90 s |

### Límites honestos

- **No es un decisor entrenado.** Son las preferencias del 125B, no una cabeza con
  pérdida de Brier: la interfaz es Jev, la **calibración no**. Tiende a ser sobreconfiado.
- **~2–3,6 s por decisión** (~4–7x más lento que Jev, ~524 ms; ~20–35x más lento que un
  decisor pequeño local, ~100 ms). La ventaja: **0 VRAM extra**, va sobre el modelo que
  ya está cargado. Es lo único que cabe junto al 125B en 12 GB.
- Hasta ~11 opciones por pregunta (etiquetas A–K, tope del top-32 del motor).
- Necesita Strata cargado en `:8081` (no funciona si el arbiter ha dado la GPU a llama.cpp).

### Ficheros

- `src/program/generate.cpp` — `--logprobs N` + `emit_logprobs_line()` (rama `logprobs-endpoint`)
- `serve/server.py` — captura y expone `strata_logprobs`
- `~/Strata/ada-decide.py` + `~/.config/systemd/user/ada-decide.service`
- Backups: `engine/strata.bak-20261003-pre-logprobs`, `serve/server.py.bak-20261003-logprobs`,
  `strata-*.json.bak-20261003-logprobs`

## Optimización auditada (2026-10-03)

Engine **0.1.38** (actualizado desde 0.1.34). Lo medido, con A/B:

| Cambio | Resultado | ¿Se queda? |
|---|---|---|
| `--calibrate` a 512K | "the default settings are already the fastest here (36.4 tok/s)" | sí (defaults) |
| `--expert-cache-per-layer` | 39,1 vs 39,2 tok/s de media (3 pasadas) | **no**: neutro |
| `--prefill auto:16384` | el motor lo capa a 6144 (VRAM) y da 788 vs 818 tok/s | **no**: peor |
| `expert_profile_save` | guarda el perfil aprendido; arranca desde él | **sí** |

El perfil aprendido (`expert-profile-learned-swift.bin`) se guarda cada 5 min entre
peticiones y al salir. Se entrena con el uso real: al principio no aporta nada.

### Perfiles por proyecto

El propio motor documenta un perfil por proyecto: apunta `expert_profile_save` a otro
fichero por proyecto. Útil si saltas entre repos muy distintos.

### Qué NO merece la pena

- **Fork del motor**: es C++/CUDA, con releases cada pocos días (0.1.34 → 0.1.38 en una
  sesión) y ya está tuneado para AVX2 sin AVX-512 y para tarjetas de 12 GB. Todo lo
  medible dio igual o peor que sus defaults. Un fork se quedaría atrás.
- **Ganancia real pendiente**: una GPU de 24 GB. Cada GB guarda ~700 expertos más, que
  son expertos que la CPU no calcula.

## Pendiente / no resuelto

- Las claves cloud de Tencent (`hy4`) dan **401** por su cuenta: los fallbacks que
  acaban en `hy4` fallan. No es de este cambio.
- Sin perfil de expertos propio (`make_profile.py`): ganancia incierta, no compensó.
