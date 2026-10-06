# Oficina v5 — estado real de Herdr, push, y visual legible

Feature: `oficina-herdr-v5` · Rama: `claude/strata-rtx3060-optimization-zfgxq8` · Inicio: 2026-10-06

## Objetivo
Que la oficina web muestre **el estado real de cada agente** (idle · working · blocked · done · unknown) y que Herdr hable con la oficina por **eventos**, no por polling. Hoy el estado se destruye en el servidor y la visualización es decorativa.

## Problema (evidencia, verificada en el binario Herdr 0.9.3)
- `docs/fork/oficina/server.py:66` (`mkagent`): `st = "working" if status == "working" else "idle"` → `blocked`, `done`, `unknown` se pierden antes de llegar al navegador.
- `docs/fork/oficina/index.html:341` (`statusOf`): `blocked`/`waiting` solo se fabrican con un regex sobre la actividad de `opencode2`.
- `docs/fork/oficina/index.html:340,154` ya tienen LED ámbar/rojo y clases CSS para esos estados → lenguaje visual muerto.
- Consecuencia: un agente bloqueado esperando aprobación **camina a la mesa de café y se pinta gris**.
- `herdr_out` (`server.py:63`) devuelve solo `stdout` y descarta `stderr`. Herdr manda errores como JSON a stderr con exit 1 (`{"id":…,"error":{"code":…,"message":…}}`), así que los checks `server.py:256-258` son código muerto.
- `agent prompt` se lanza sin `--wait` (`server.py:346`) → no se observa si la entrega ocurrió.
- Herdr tiene `events.subscribe` con `PaneAgentStatusChangedEvent`, `PaneOutputMatchedEvent`, `PaneScrollChangedEvent`. La oficina sondea cada 5 s y nunca suscribe.
- `herdr api snapshot` (verificado: 6.6 KB) entrega `agents`, `panes`, `tabs`, `workspaces`, `layouts`, foco en **una** llamada. La oficina hace `agent list` + `workspace list` por separado.
- Hay **102 métodos de API**; la oficina usa 10 comandos CLI.
- La oficina **no tiene ningún test**.
- Concurrency: `get_state` es check-then-act (`server.py:110-111`); `CACHE["data"]["interval"]` (`server.py:111`) muta el dict que los hilos SSE iteran (`server.py:418`). Sin lock en `CACHE`, `SEEN`, `FAILS`, `MCACHE`, `KCACHE`, `JOBS`, `save_meta`.
- Multi-máquina: `agent start` de `ada-cli` descarta `mach` (`server.py:249`); `rhome` cachea `""` para siempre (`server.py:160`); la regex `/api/state?ws` (`server.py:401`) rechaza labels con mayúsculas/guiones bajos.
- Visual: `applyTheme` (`index.html:654-662`) reconstruye sala y materiales sin `dispose()` (fuga de GPU por cambio de tema); `placeLabels` (`index.html:627-639`) reconstruye DOM cada frame; `drawBoard` pinta blanco en tema oscuro (`index.html:394`); `metrics.jefe` no existe en `METRICAS.json` → el jefe del tablero está hardcodeado (`index.html:772`); `t.pasos` no existe en `TAREAS.json` (`index.html:708`); `G.cap` (`index.html:367`) crea `CapsuleGeometry` por llamada; el comentario `index.html:352` dice ~60 mallas, la realidad es 350–400.
- Datos que viajan y nunca se muestran: `ticker`, `bench`, `interval`, `updated`, `agents.color`, `ws`/`maquina`, `metrics.fuente`, `candidato.B1`, `brazo_def_B1`, `TAREAS.columnas`.
- Deriva documental: `README.md` dice 847/77 líneas (son 879/52); el `strata-oficina.service` del repo apunta a `wt-C30` y menciona un token que ya no existe.

## Por qué
La oficina existe para que Adrián vea qué está haciendo cada agente y qué necesita. Si el estado `blocked` se pierde, la herramienta falla en su función central. Y el polling cada 5 s subprocesos es consumo que compite con las mediciones de la RTX 3060.

## Alcance
`docs/fork/oficina/server.py`, `docs/fork/oficina/index.html`, `docs/fork/oficina/tests/` (nuevo), `docs/fork/oficina/README.md`, `docs/fork/oficina/strata-oficina.service`.

## Restricciones
1. **Cero dependencia externa** en `server.py`: solo stdlib de Python 3.12. Los tests pueden usar `pytest` (ya instalado, 9.1.1).
2. **No robar cómputo a la medición**: render bajo demanda intacto, ~0 % dGPU en reposo. Cada fase se verifica midiendo reposo.
3. **Seguridad intacta**: scrypt + cookie `sid` HttpOnly/SameSite=Strict + fail-closed + `shell=False` + cap 8192 B. No exponer fuera de localhost/tailnet.
4. **Verificación viva solo con agentes `agy`** (decisión de Adrián, 2026-10-06): no usar `claude`, `pi`, `opencode2` como sujetos de prueba en Herdr vivo. **Autorización permanente concedida por Adrián el 2026-10-06: "no me pidas más permiso, siempre hazlo solo con agy".** No se pide autorización por probe; se registran el workspace creado y su cierre en el documento. Sigue siendo prohibido tocar `claude`, `pi`, `opencode2` y la oficina `w1`.
5. Idioma: UI y docs de la oficina en español (convención del repo); identificadores siguiendo el estilo existente (`herdr_out`, `mkagent`, `build_state`).
6. Cerrar cada tarea con un commit de unidad de trabajo en la rama de feature. Push/PR/merge quedan en Adrián.

## Checklist
- [x] **T1** — Stub de Herdr a nivel argv + suite pytest (sin GPU ni Herdr vivo). Commit `0b1f224`.
- [x] **T2** — Contrato de estado real (`agent_status` + `pane_id`, `focused`, `interactive_ready`, `completion_seq`, `state_change_seq`, `agent`) en el payload. Commit: *(hash en el próximo commit)*
- [x] **T3** — `statusOf` es función pura del estado real; se elimina el regex. Commit: *(hash en el próximo commit)*
- [x] **T23** — Formas de Herdr **verificadas en vivo** y fijadas como fixtures verbatim (`workspace create`, `pane split`, `workspace close`, `agent start`/`agent prompt` y sus errores).
- [ ] **T26** — `pane split --cwd` / `workspace create --cwd` no se honran: los paneles caen en `$HOME`. La oficina pasa `--cwd` y su meta reporta ese cwd, pero Herdr pone `/home/bazzite`. Verificado con `pane run pwd` → `/home/bazzite`. Superficie: `server.py`.
- [ ] **T22** — Las clases `.st.done`/`.st.unknown` y el badge rojo no se consumen en el DOM: ningún elemento usa la clase `st`. Superficie: `index.html`. Verificado por navegador en T3 y en la prueba viva.
- [ ] **T24** — Ruta multi-máquina en `/api/offices` sin verificar en vivo: bajo HOME aislado `machines()` devuelve `[]` (no hay `~/.config/herdr/config.toml` en el scratch), así que `maquinas` quedó solo `['bazzite']` y cero llamadas remotas. En producción el servicio vivo ve `mac-mini` y `macbook-air`.
- [x] **T25** — Forma de éxito de `agent prompt --wait` capturada en vivo; `herdr_cmd` lee `result` primero y `send_outcome` usa ese `agent`.
- [x] **T4** — `herdr_out` captura `stderr` y parsea `{error:{code,message}}`; se eliminan los checks de substring muertos. Commit: *(hash en el próximo commit)*
- [x] **T5** — `agent prompt --wait --timeout` + manejo real de `agent_blocked` / `agent_prompt_stalled` con mensaje humano. Commit: *(hash en el próximo commit)*
- [x] **T6** — Un solo `api snapshot` por ciclo. Commit: *(hash en el próximo commit)*
- [x] **T7** — Locks + rebuild atómico de `CACHE`/`SEEN`/`FAILS`/`MCACHE`/`KCACHE`/`JOBS`/`meta`. Commit: *(hash en el próximo commit)*
- [x] **T8** — Baches multi-máquina: `mach` en `agent start`, `rhome` no cachea `""`, regex `ws` acepta labels reales. Commit: *(hash en el próximo commit)*
- [ ] **T9** — `events.subscribe` en un hilo; SSE alimentado por eventos; cero subprocesos en reposo.
- [ ] **T10** — `notification.show` cuando un agente pasa a `blocked`.
- [ ] **T11** — Mapeo visual `blocked` / `done` / `unknown` (LED, pose, badge).
- [ ] **T12** — Burbuja "TE NECESITA" sigue al agente bloqueado (deja de estar hardcodeada a `opencode2`).
- [ ] **T13** — Canvas theme-aware: `drawBoard` y `drawScreen` leen el tema.
- [ ] **T14** — `dispose()` en `applyTheme` + cache de `G.cap`.
- [ ] **T15** — `placeLabels` sin reconstruir DOM cada frame.
- [ ] **T16** — Código muerto y claims de mallas corregidos.
- [ ] **T17** — Mostrar datos que viajan y se ignoran: `ticker`, `interval`, `bench`, `metrics.fuente`, `candidato.B1`, `TAREAS.columnas`.
- [ ] **T18** — A11y: tabs con `role`/`aria-selected`/teclado, label de `#msg`, `role="status"` en toast, focus trap en modal, `prefers-reduced-motion` aplicado al bucle 3D.
- [ ] **T19** — Responsive compacto real: oficinas, KPIs y cola accesibles bajo 1150 px.
- [ ] **T20** — Estados de error/empty/retry en `/api/state`, "Salida" e "Hilo".
- [ ] **T21** — Deriva documental: `README.md` y `strata-oficina.service`.

## Criterios de aceptación
1. Un agente `blocked` en Herdr se ve rojo/ámbar en el diorama y en el inspector, **no** como inactivo. Verificado con stub y con un agente `agy` vivo.
2. En reposo, el servidor no lanza subprocesos Herdr (T9) y la dGPU queda ~0 %.
3. Los errores de Herdr aparecen como mensaje legible en la UI, no como "herdr no responde".
4. La suite pytest corre sin GPU, sin Herdr vivo y sin red.
5. Ninguna verificación viva toca `claude`, `pi` ni `opencode2`.

## Verificación
- **Regresión**: `python3 -m pytest docs/fork/oficina/tests/ -q` con stub de Herdr.
- **Servicio**: `systemctl --user restart strata-oficina.service` + `journalctl --user -u strata-oficina.service -n 40`.
- **Puertas sin sesión**: `/api/*` → 401, `/` → 302, `/login` → 200.
- **Reposo**: `nvidia-smi` (dGPU %) y `ps -o %cpu` del servicio tras 60 s sin cambios.
- **Viva**: un agente `agy` en un panel de prueba, transición `idle → working → blocked`, y `notification.show`.

## Sondeos de eventos (autorización permanente, solo `agy`)
Adrián autorizó el 2026-10-06: **«no me pidas más permiso, siempre hazlo solo con agy»**. Se registran los workspaces creados y cerrados; `w1` (Strata3060) y los agentes `claude`/`pi`/`opencode2` nunca se tocan. Workspaces usados: `wJ` (probe-v5), `wK` (probe-v5b), `wM`, `wN`, `wP`, `wQ`, `wR` — **todos cerrados**; `workspace list` queda solo con `w1`.

Protocolo del socket verbatim (`~/.config/herdr/herdr.sock`, JSON newline-delimited):
- Requiere campo `id`; `ping` → `{"id":"p1","result":{"type":"pong","version":"0.9.3","protocol":22,"capabilities":{...}}}`.
- `events.subscribe` con `{"subscriptions":[...]}` → ack `{"id":..,"result":{"type":"subscription_started"}}`. Los eventos llegan por la misma conexión, newline-delimited, forma `{"data":{...},"event":"..."}`.
- **Suscripciones globales (solo `type`)**: `pane.agent_detected`, `pane.created`, `pane.closed`, `pane.exited`, `pane.updated`, `pane.focused`, `pane.moved`, y todos los `workspace.*`. `pane.agent_status_changed` y `pane.scroll_changed` **requieren `pane_id`**; `pane.output_matched` requiere `pane_id + source + match`.
- `OutputMatch` es `{type: substring|regex, value: string}` — **`value`**, no `text`.
- El evento de estado **no trae el nombre del agente**: trae `pane_id` y `workspace_id`. Por eso T2 (propagar `pane_id`) era prerrequisito de T9. `pane_agent_detected` emite `event` con guiones (`pane_agent_detected`), `pane.agent_status_changed` con punto — hay que manejar ambos.
- Eventos capturados: `{"data":{"agent":"agy","pane_id":"wR:p2","type":"pane_agent_detected","workspace_id":"wR"},"event":"pane_agent_detected"}`, `{"data":{"agent":"agy","agent_status":"idle","pane_id":"wR:p2","workspace_id":"wR"},"event":"pane.agent_status_changed"}`, luego `working`, luego `blocked`. Y `workspace_created` / `workspace_closed` (el de cierre incluye `workspace_id` duplicado en `data`).
- `agent start` éxito verbatim: `{"id":"cli:agent:start","result":{"agent":{...,"agent_status":"idle","interactive_ready":true},"argv":["agy"],"type":"agent_started"}}`.
- `agent prompt --timeout` **requiere** `--wait`: sin `--wait` es **rc=2** (`--timeout requires --wait`). `--wait` puede asentarse en `blocked` (observado), no solo `working`/`done`.

**Defecto real descubierto (entra en el alcance, T26):** `pane split --cwd` **no se honra**. En el pane dividido, `pane run pwd` imprimió `/home/bazzite`, no el `cwd` pedido; y `workspace create --cwd` también responde `cwd: "/home/bazzite"`. La oficina crea la oficina en `~/nombre`, pasa `--cwd`, y **los agentes terminan en la home**. El `cwd` que `/api/offices` reporta viene de su propia meta, no de Herdr: la confinación es cosmética.

## Verificación viva autorizada (2026-10-06)
Adrián autorizó una prueba viva en Herdr con **un solo workspace descratch y un solo agente `agy`**, para convertir en verificado lo inferido. Ejecutada y **cerrada**: `wJ` (`probe-v5`) creado, sondeado, y `herdr workspace close wJ` ejecutado; `workspace list` queda solo con `w1`.

Formas capturadas verbatim del binario 0.9.3 (copiadas a `tests/fixtures/`):
- `workspace create` → `result` = `{root_pane, tab, type:"workspace_created", workspace}`. `server.py` lee `result.workspace.workspace_id` y `result.root_pane.pane_id` → **coincide**. El `type` inventado era `workspace_create`; el real es `workspace_created`.
- `pane split` → `result` = `{pane, type:"pane_info"}`. `server.py` lee `result.pane.pane_id` → **coincide**. El fixture inventaba `type:"pane_split"` y un pane con `name`/`agent`; un pane nuevo real trae `agent_status:"unknown"` y **sin** `name`.
- `workspace close` → `result` = `{type:"ok"}`. El fixture inventaba `{type:"workspace_close", workspace_id, closed}`.
- `agent start` (fracaso) → **stderr**, rc=1: `{"error":{"code":"timeout","message":"timed out waiting for agent startup"},"id":"cli:agent:start"}` y `{"error":{"code":"agent_not_ready","message":"agent probeagy is blocked during startup and is not ready for prompts"},"id":"cli:agent:start"}`.
- `agent prompt --wait` sobre agente bloqueado → **stderr**, rc=1: `{"error":{"code":"agent_blocked","message":"agent probeagy is blocked and requires interactive input"},"id":"cli:agent:prompt"}`, stdout **vacío**. Confirma T4.
- `pane read --source recent-unwrapped` devuelve **texto plano**, no JSON → `api_log` (`splitlines()[-30:]`) está bien.

Prueba end-to-end viva (Chromium headless 1187 + Herdr real, puerto 8099, 0 comandos mutantes):
- `/api/offices` lista `probe-v5` con `probeagy`; `/api/state?ws=wJ` → `status:"blocked"`, `activity:"esperando confirmación"`, `pane_id:"wJ:p3"`, `agent:"agy"`.
- **LED del escritorio rojo**: 34 px rojos en radio 14, centroide a **4.2 px** del punto proyectado, núcleo `(224,52,49)`. Control en `w1` con 5 agentes `idle`: **0 px rojos**.
- **En su escritorio**: pill a **12.9 px** del asiento proyectado; la mesa de café más cercana a **186.8 px**.
- **Render bajo demanda en vivo**: 18 rAF en 90.002 s (0.2 fps), gaps **4999.7–4999.9 ms constantes** → cero frames de caminata.
- 5/5 agentes de `w1` coinciden con Herdr; el agente sin `name` (`w1:pR`) se omite. 0 errores de consola, 0 requests externos, leyenda de 5 estados.
- 379 llamadas herdr del servidor scratch: 355 `agent list`, 18 `workspace list`, 6 `machine list --json`; **0 mutantes**. Servicio vivo 8095 intacto (`MainPID` 1645893 y `ActiveEnterTimestamp` sin cambio).

## Progreso
- 2026-10-06: **T8 completada.** Writer delegado: `server.py` +87/-11, `test_herdr_stub.py` +314/-1, 16 tests. `_start` lleva `mach` en **ambas** ramas; ada-cli remoto se **rechaza** (`ADA_REMOTE`: el shim `pi`→ada-cli vive en la home de bazzite), coherente con que `kinds(mach)` ya excluye ada-cli remoto. `rhome` cachea `(home, marca)` con TTL negativa de 60 s: el éxito no expira, el fallo se re-sondea. `state_for` + `WSRE` aceptan labels `[A-Za-z0-9][A-Za-z0-9_-]{0,31}`; label malformado → 400, label desconocido → 404, **nunca** fallback silencioso al estado local. `api_hilo` declara `audit:"local"`, `maquina`, `scope` en vez de fingir historial remoto. `AGCACHE` con TTL explícita e invalidada al crear y borrar.
  - **7.º defecto encontrado por los tests nuevos:** `_crear` claveaba la meta de agentes por `wid` mientras las oficinas remotas viven bajo `oid` → **toda creación de oficina remota terminaba en `estado: error`** y sus agentes nunca llegaban a `oficinas.json`.
  - RED observado: `10 failed, 120 passed` (incluido `KeyError: 'w2'`). Evidencia del padre: **133 passed in 17.92 s**. `grep shell=True` = 0, imports nuevos = 0, cap 8192, scrypt, cookie, `log_message`, orden de locks de T7 intactos.
  - Honestidad: 3 tests son guardas que pasan en la base, no anclas RED. Registrado.
  - **Gap grande**: la ruta remota está probada **solo contra el stub**. La captura viva tenía **0 agentes remotos** (mac-mini: 1 workspace `w8`, 1 pane, 0 agentes). Es T24. El dominio de labels con `_`/mayúsculas se probó con labels sintéticos; los labels reales son minúscula-guion.
  - `RHOME_NEG = 60` s es elección de la oficina, no medida. Una máquina muerta cuesta una sonda ssh de 15 s cada 60 s, dentro de `MLOCK`.
- 2026-10-06: **T7 completada.** Writer delegado: `server.py` +190/-63, `test_herdr_stub.py` +301/-1, 13 tests de concurrencia. Seis locks nuevos con orden de adquisición documentado (`server.py:44-50`): `STLOCK` (RLock, rebuild: `CACHE`, `SEEN`, `SNAP`, `LAST_AGENTS`), `MLOCK` (RLock, `MCACHE`/`HOMES`/`AGCACHE`), `KLOCK` (`KCACHE`), `FLOCK` (`FAILS`), `JLOCK` (`JOBS`), `MFLOCK` (`oficinas.json`); `SELOCK` pasa a RLock. Orden: `MLOCK`→`STLOCK` (solo `agmap`), `KLOCK`→`MLOCK` (solo `kinds` remoto); `STLOCK` nunca toma `MLOCK`/`KLOCK` → sin ciclo, sin deadlock.
  - `get_state` ya no es check-then-act: un solo hilo construye por intervalo, `interval` se fija **antes** de publicar, y se publica con una sola asignación; el dict publicado nunca se muta después. `save_meta`/`save_sess` usan `_atomic` (temp en el mismo directorio, mode 0600, `os.replace`), así un crash a mitad no trunca el fichero. `/api/office/job` serializa una **copia** estable del job.
  - RED observado: `11 failed, 106 passed` (8 hilos → 8 `build_state`; `mutados == ["interval"]`; 4 escrituras `SEEN` para 1 cambio; `AttributeError` en `job_view`/`meta_edit`/`FLOCK`; `pytest.raises(OSError)` sin `os.replace`). Base verificada: `git show HEAD:server.py` tiene **0** ocurrencias de los locks nuevos.
  - Evidencia del padre: **117 passed** en **tres runs** (10.66/10.67/10.65 s). `grep shell=True` = 0, `grep asyncio` = 0, cap 8192, scrypt, cookie, límite 3 s, condición SSE `body != prev or quiet >= 6`, `log_message` intactos.
  - Honestidad del writer: `test_consumidor_sse_no_reventa_mientras_se_reconstruye` y `test_caches_de_herdr_no_reventan_bajo_lecturas_concurrentes` **pasan también en la base**; son guardas, no anclas RED. La invariant determinante la fija `test_el_dict_publicado_no_se_mutan_despues`.
  - **Riesgo real**: `STLOCK` se sostiene durante el subproceso `api snapshot` (timeout 15 s), y `MLOCK` durante `machine list --json` (10 s) y `rhome` (15 s ssh). Con Herdr muerto los lectores esperan hasta 15 s. Es la semántica pedida (un constructor por intervalo), y es la motivación de T9.
- 2026-10-06: **T6 completada.** Writer delegado: `server.py` +50/-8, `test_herdr_stub.py` +230/-26, 12 tests netos. Fuente de `build_state` y `raw_agents` pasa a `api snapshot` (verificado rc=0, 6.6 KB, y `--machine mac-mini api snapshot` funciona igual); `api_offices` toma `workspaces` del mismo snapshot por máquina; `SNAP` cachea por máquina con la TTL del intervalo, así el snapshot que construyó el estado es el que leen `api_offices`, `agmap`, `strata_ws` y `state_ws`. `machine list --json` sigue con parseo de lista desnuda vía `herdr_out`. Se invalida `SNAP` al crear y al borrar oficina.
  - **Llamadas herdr por ciclo, medido con el stub:** `/api/offices` en estado caliente **7 → 3**; `/api/state?ws=w1` **1 → 0** (lee el snapshot cacheado); `/api/offices` frío **8 → 4**; carga completa de página **9 → 4**. Ciclo de estado: 1 subproceso (snapshot en vez de `agent list`).
  - RED observado: `14 failed, 88 passed`; segunda RED al comentar las dos invalidaciones de `SNAP` → `2 failed, 102 passed` (la oficina creada quedaba oculta por la cache). Evidencia del padre: **104 passed in 9.89 s**.
  - Riesgo aceptado: `SNAP` es un cache global **sin lock** (documentado en el código, es T7). Un cambio remoto aparece en el límite del ciclo (≤ 5 s) en vez de instantáneo.
  - `api_office_delete` conserva `workspace list` (una llamada por acción del usuario, no por ciclo).
- 2026-10-06: **T25: la forma de éxito de `--wait` se captura y rompe T5.** Adrián autorizó un segundo probe (`wK`/`probe-v5b`, un solo `agy`). `agy` no arranca si el pane no lleva `PATH` con `/snap/bin`; el `--env` del `workspace create` **no** se propaga a los paneles de `pane split` (la oficina pasa `--env` en el split para opencode y ada-cli). El agente quedó **blocked** en el prompt de confianza; con autorización de Adrián se envió `Enter` (`agent send-keys probeagy2 enter` → `{"type":"ok"}`), luego `agent wait --until idle` → `interactive_ready:true`, y `agent prompt --wait --timeout 30000` devolvió:
  ```
  {"id":"cli:agent:prompt","result":{"agent":{...,"agent_status":"done","completion_seq":1091,"pane_id":"wK:p3"},"type":"agent_prompted"}}
  ```
  - **`type` y `agent` viven dentro de `result`**, no en la raíz. `herdr_cmd` leía `env.get("type")` de la raíz → `None` → `send_outcome` daba `no_response` → **502 con `ok:false` ante un envío realmente entregado.** El fixture `agent_prompt.json` era la forma inventada, y por eso los tests de T5 pasaban. Arreglado: `herdr_cmd` lee `result` primero y expone `agent`; `send_outcome` usa ese `agent`. Fixture verbatim, tests actualizados con `_prompt_envelope`.
  - `--wait` devolvió **`done`** (el agente terminó el turno), no `working`. `matched_status` **no existe** en la forma real; T5 ya lo ignoraba.
  - Código capturado nuevo: `agent get` de un agente inexistente → `agent_not_found` (el fixture inventaba `agent_name_not_found`). Ambos en `HERDR_MSG`.
  - El probe se **cerró**: `herdr workspace close wK` → `{"result":{"type":"ok"}}`; `workspace list` queda solo con `w1`.
  - Evidencia del padre: **92 passed** en dos runs (8.27 s y 8.14 s).
- 2026-10-06: **T5 completada.** Writer delegado: `server.py` +75/-9, `test_herdr_stub.py` +305/-30, 12 tests netos nuevos. argv: `herdr [--machine m] agent prompt <tgt> "<prefix+texto>" --wait --timeout <ms>`. `ms = max(5000, min(5000 + 25·len + (0 si ready else 4000), 20000))`; timeout del subprocess `ms/1000 + 10` s para que **Herdr gane la carrera** y devuelva su sobre (un `TimeoutExpired` de Python dejaría stdout y stderr vacíos → se leería como socket muerto). 5000 ms y la unidad están verificadas en la captura viva; 25 ms/caracter, 4000 de margen y el tope 20000 son **elección de la oficina, no medida** (documentado en `server.py:120-129`).
  - Contracto: `ok:true` **solo con entrega observada**. Clasificación `STALLED = (agent_prompt_stalled, timeout)` vs `PANEL = {agent_blocked: blocked, agent_not_ready: not_ready}` — los códigos de panel **no** son stall: el agente está vivo y espera aprobación; decir "no se entregó" mandaría al humano a reenviar en vez de aprobar. **Sin reintento automático**: reenviar a un panel a medias es la única forma de duplicar un mensaje.
  - `envios.log` registra el **resultado** (`outcome`, `confirmed`, `code`, `state`, `pane_id`, `timeout_ms`), no la intención. `api_hilo` muestra solo líneas `confirmed:true`.
  - RED observado por el writer: `21 failed, 68 passed`. Evidencia del padre: **92 passed in 7.91 s** (dos runs del writer: 7.91/7.93). `grep shell=True` = 0; límite 3 s, 1..4000, scrypt, cookie, cap 8192, `log_message` intactos.
  - **Riesgo real**: `/api/send` puede bloquear hasta ~20 s dentro del hilo HTTP. La UI debe mostrar un estado de espera durante un envío lento. Pendiente de verificar en navegador.
  - **Gap honesto del writer**: la forma de **éxito** de `agent prompt --wait` no está capturada en vivo (solo la de fallo sobre agente bloqueado). `matched_status` es inferido y **no se usa para decidir**; solo se usa el estado del agente, que sí está verificado.
- 2026-10-06: **T4 completada.** Writer delegado: `server.py` +71/-13 (`herdr_cmd`, `_env`, `HERDR_MSG`, `SIN_RESPUESTA`, `herdr_msg`, `herdr_error`; call sites `build_state`, `api_log`, `hj`, `_start`, `api_send`, `api_office_delete`), `test_herdr_stub.py` +251/-32 con 21 tests nuevos. RED observado por el writer: `14 failed, 57 passed`. Tabla `code→mensaje`: `agent_blocked` → "el agente está esperando una aprobación en su panel: aprueba y vuelve a enviar"; `agent_not_ready` → "espera confirmación en su panel"; `agent_prompt_stalled` → "el mensaje no se entregó"; `agent_name_not_found`, `timeout`, `usage`; default muestra el código y conserva el texto original de Herdr.
  - **Dos defectos encontrados y arreglados por el padre inline:**
    1. `stub_herdr.py:_herdr` indexaba el payload scripted como `scripted[0]` (dict) → `KeyError: 0`, tragado por el servidor como salida vacía. La evidencia T1 de "herdr no responde" salía de un KeyError, no de la forma verificada. Arreglado: `isinstance(scripted, dict)` → stderr + exit 1. Fijado por `test_stub_script_error_llega_como_sobre_verificado`.
    2. `api_office_delete` quitaba la meta de `oficinas.json` **antes** de comprobar que Herdr cerró el workspace → oficina sin `cwd`/`perfiles` con el workspace vivo. Movido tras el éxito. Fijado por `test_office_delete_no_quita_la_meta_si_herdr_fallo`.
  - Evidencia observada por el padre: **73 passed** en dos runs (7.25 s y 7.16 s). `grep shell=True` = 0; cap 8192, scrypt, cookie HttpOnly/SameSite=Strict, `log_message` silencioso intactos. Servicio `active`, no reiniciado. Ningún comando mutante de Herdr ejecutado.
  - Pendiente: `envios.log` solo se escribe cuando `type == "agent_prompted"`; `api_send` devuelve 200 con `confirmed:false` si Herdr responde un stdout que no es `agent_prompted`.
- 2026-10-06: **T3 completada y verificada en navegador real** (Chromium headless 1187 + Playwright 1.63, instancia descratch en puerto 8099, HOME aislado, `STRATA_OFICINA_PW` generado, `herdr` stub no mutante, `tailscale` stub → 127.0.0.1). Solo `index.html` (12+/8-).
  - VERIFICADO: `blocked` pinta rojo en LED del diorama (`#ff937e` claro, `#ff7163` oscuro), en pill (`rgb(239,68,68)` = `#ef4444` exacto), en selector de chat y en menú. El agente bloqueado **se queda en su escritorio** (pill en (447.6,295.1) = posición de `working`; `idle` en (723.2,344.6), mesa de café, Δ 275.6 px). Leyenda con **5** estados, 481.7×28.7 px dentro de 1280×720, sin overflow. **Render bajo demanda intacto: 2 rAF ticks en 90.004 s** (0.0222 fps, gaps 34.9/35.2 s por heartbeat SSE); la caminata transitoria fue 71 frames en los primeros 4 s y **0 frames** en los 8 s siguientes. 0 errores de consola, 0 requests externos, canvas WebGL sano. `working`/`idle` idénticos a base en pixel de LED y pill.
  - Evidencia: `/tmp/oficina-verify/shots/*.png`, `results*.json`, `renderloop.json`. 554 `agent list`, 40 `workspace list`, 0 comandos mutantes.
  - **Hallazgo nuevo → T22**: ninguna clase `st` se usa en el DOM. T3 agregó `.st.done`/`.st.unknown` y existe `.st.blocked{color:var(--bad)}`, pero el inspector muestra el texto "esperando confirmación" en gris (`rgb(100,116,139)`), **no rojo**. El badge de estado necesita un elemento que consuma la clase.
  - Deviación registrada: el verificador envió dos GET read-only al servicio vivo 8095 al final (`/login` 200, `/` 302) para probar que seguía sirviendo. Ningún POST ni mutación. Servicio `active` antes y después, `MainPID` y `ActiveEnterTimestamp` sin cambio.
- 2026-10-06: **T2 completada.** Writer delegado modificó solo `server.py` (45 líneas) y `test_herdr_stub.py` (218). `mkagent` deja de colapsar: dominio verificado `idle|working|blocked|done|unknown`, lo fuera de dominio es `unknown`, nunca `idle`; `SEEN` keyed en estado real. Campos de Herdr propagados con `.get()` → `None` si ausentes. Agente sin `name` se omite (se elimina el fantasma `"?"`). `tester` prefiere Herdr y el lock queda como texto de actividad. `state_ws` deriva actividad del estado normalizado (`ACTS`), sin contradicción.
  - Evidencia observada por el padre: `git diff` revisado línea por línea y **`50 passed in 3.28 s`**. El writer reportó RED `16 failed, 29 passed` antes del fix. Payload +659 B sobre 5 agentes (~132 B/agente). Herdr calls por ciclo: solo `agent list` (sin cambio).
  - Anclas renombradas: `test_mkagent_conserva_el_estado_real`, `test_build_state_conserva_el_bloqueado`, `test_state_ws_alinea_estado_y_actividad`, `test_state_ws_remota_conserva_el_bloqueado`. Dos anclas T1 adicionales (`test_build_state_linea_base`, `test_api_offices_linea_base`) se actualizaron por la omisión del agente sin `name`.
  - Decisión registrada: agente **ausente** de `agent list` queda `idle`; agente **presente** con estado fuera de dominio queda `unknown`. Si se prefiere ausente→`unknown`, es una línea en `g`.
  - Wording nuevo elegido por el writer: `done`→"terminado", `unknown`→"estado desconocido". Adrián puede preferir otra palabra.
  - **Pendiente de reiniciar el servicio**: sigue corriendo el `server.py` viejo. El contrato nuevo entra en el próximo restart.
- 2026-10-06: **T1 completada.** Writer delegado creó `docs/fork/oficina/tests/` (31 ficheros): `stub_herdr.py` (emulador argv por `(machine,args)`, errores en stderr+exit 1), `conftest.py` (import aislado de `server.py`: HOME, `STRATA_OFICINA_PW`, `FORK`+4 rutas derivadas, `MOTOR`/`BENCH`, `sys.argv`, `subprocess` shim, reset de `CACHE`/`SEEN`/`FAILS`/`MCACHE`/`AGCACHE`/`KCACHE`/`JOBS`/`LAST_AGENTS`/`LAST_SEND`), `test_herdr_stub.py` (37 tests), `README.md`, `fixtures/` (copias verbatim de capturas vivas + `blocked` sintético sobre `explorer`/agy).
  - Evidencia observada por el padre: `python3 -m pytest docs/fork/oficina/tests/ -q` → **36 passed, 1 xfailed in 2.63 s**. `git status --short` muestra solo `?? docs/fork/oficina/tests/` y `?? odd/`; `server.py`, `index.html`, `login.html`, `README.md`, `strata-oficina.service` intactos. Servicio `active` (no reiniciado).
  - Anclas que obligan a T2: `test_mkagent_colapsa` (`server.py:66`), `test_build_state_pierde_el_bloqueado`, `test_state_ws_contradice_estado_y_actividad` (`server.py:197-198`), `test_herdr_out_solo_stdout` (`server.py:63`), `test_send_no_observa_la_entrega` (`server.py:346`), `test_build_state_no_pide_snapshot`. `test_t2_objetivo_el_bloqueado_sobrevive` es `xfail(strict=True)`: cuando T2 arregle `mkagent` pasa a XPASS y pone la suite roja, forzando a quitar el marker y actualizar las anclas.
  - Hallazgos nuevos que entran en el alcance: `agent list` puede traer un agente **sin clave `name`** (la captura viva trae 6, uno sin `name`) → `server.py:74` lo convierte en `"?"` fantasma; `interactive_ready`/`completion_seq` son **opcionales por agente**; `machine list --json` es lista desnuda sin `result`.
  - Formas **inferidas** (no capturadas en vivo, a confirmar en T5/T6): `result` de `workspace create`, `pane split`, `workspace close`, `matched_status` de `--wait`, agregación de `agent_status` en tabs/workspaces.
- 2026-10-06: Plan aprobado por Adrián (Fases 0→5 completas, fase por fase). Verificación: stub para regresión **y** Herdr vivo, **solo con agentes `agy`**. Documentación leída: `docs/fork/oficina/README.md`, `docs/fork/AUDITORIA-OFICINA.md`. Dos scouts de exploración mapearon `server.py` e `index.html` con evidencia `path:line`; el binario Herdr 0.9.3 se inspeccionó en vivo (grupo help, `api snapshot`, `api schema --json`, `machine list --json`).
- Ninguna tarea iniciada.

## Espejo Engram
Pendiente: no hay herramientas de memoria (`mem_context`/`mem_search`/`mem_get_observation`/save) disponibles en esta sesión. El espejo local `odd/oficina-herdr-v5/tasks` se mantiene como copia; se resincronizará cuando Engram esté disponible.

## Próximo paso
T2: propagar el estado real (`idle|working|blocked|done|unknown`) y los campos de Herdr (`pane_id`, `focused`, `interactive_ready`, `completion_seq`, `state_change_seq`, `agent`) al payload; `mkagent` deja de colapsar; `state_ws` alinea; se quita el `xfail(strict)` de T1. Superficies: `docs/fork/oficina/server.py` + `docs/fork/oficina/tests/test_herdr_stub.py`.
