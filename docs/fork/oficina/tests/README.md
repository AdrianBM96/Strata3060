# Tests de la oficina (stub de Herdr a nivel argv)

Suite pytest que emula `herdr` a nivel de argumentos, para probar `server.py` sin Herdr vivo, sin GPU
y sin red. Tarea T1 de la feature `oficina-herdr-v5` (ver `odd/tasks/oficina-herdr-v5.md`).

## Cómo se corre

```
python3 -m pytest docs/fork/oficina/tests/ -q
```

Medido en esta máquina (Python 3.12.3, pytest 9.1.1, sin GPU ni Herdr vivo): 37 tests, 36 passed y
1 xfailed, 2,6-2,7 s. Dos runs seguidos dan el mismo resultado (solo cambia el tiempo). El harness
no escribe bytecode en `docs/fork/oficina/` (fuera de su superficie) y no toca el servicio.

No toca el servicio: `strata-oficina.service` no se arranca ni se reinicia, y el `HOME` real no se
modifica.

## Qué hace el harness

- `stub_herdr.py` — emulador de `herdr` a nivel argv. Despacha por `(machine, args)`, devuelve la
  captura en `stdout`, y sabe responder errores (`{"id":…,"error":{"code":…,"message":…}}` en `stderr`
  con exit 1). Controla el estado de cada agente con `set_agent_state(name, status)` y registra cada
  invocation en `calls` / `exes`. Cualquier binario no emulado queda en `unknown`, y el fixture
  `server` exige al final que `unknown` esté vacío: ningún test puede lanzar `herdr`, `tailscale`,
  `ssh`, `opencode`, `pi`, `agy`, `ada-cli` ni `nvidia-smi`.
- `conftest.py` — fixture `server`: importa `server.py` aislado. `HOME` temporal,
  `STRATA_OFICINA_PW` temporal (scrypt con la forma que lee `pw_ok`), `FORK` y las cuatro rutas
  derivadas (`CHANGELOG`, `COLA`, `METRICAS`, `TAREAS`) apuntan al árbol de fixtures, `MOTOR` y
  `BENCH` a ficheros temporales, `sys.argv` reducido (con `pytest -q`, `server.py:6`
  `int(sys.argv[1])` reventaría el import), y `subprocess` del módulo sustituido por el stub. Los
  caches del módulo (`CACHE`, `SEEN`, `FAILS`, `MCACHE`, `AGCACHE`, `KCACHE`, `JOBS`, `LAST_AGENTS`,
  `LAST_SEND`) se resetean por test, porque son globales con TTL y sin ellos los tests dependen del
  orden.
- `fixtures/` — las formas JSON capturadas del binario real, más un agente `blocked` sintético y el
  árbol `fork/` recortado a lo que `server.py` parsea.

## Formas verificadas contra el binario herdr 0.9.3

`agent_list.json`, `api_snapshot.json`, `machine_list.json`, `workspace_list.json`,
`pane_list.json`, `agent_get.json` son capturas vivas de esta máquina (2026-10-06). Lo verifican:

- `machine list --json` es una **lista desnuda** en el nivel superior, no va envuelta en `result`.
- El JSON real es **compacto** (sin espacio tras los dos puntos), y por eso el literal
  `"interactive_ready":true` de `server.py:255` puede coincidir. El stub emite JSON compacto.
- Los errores van a **stderr** con **exit 1**; `herdr_out` (`server.py:63`) devuelve solo `stdout`.
- `agent_status` es exactamente `idle | working | blocked | done | unknown`.
- `agent start` → `{"type":"agent_started","agent":{…},"argv":[…]}`; `agent prompt` →
  `{"type":"agent_prompted","agent":{…}}`.

Inferido (no verificado en vivo, marcado en el código): las formas de `result` de
`workspace create`, `pane split` y `workspace close`, el campo `matched_status` de `--wait`, y cómo
Herdr agrega `agent_status` en tabs y workspaces. Son reglas del stub, pensadas para que T5/T6 las
ajusten contra el binario.

## Línea base y anclas

Un run verde hoy es lo que T2 tiene que mantener verde. Las anclas que cambian de color:

- `test_mkagent_colapsa` — hoy solo `working` sobrevive; `blocked`, `done` y `unknown` se pintan
  `idle` (`server.py:66`).
- `test_build_state_pierde_el_bloqueado` — el fixture `blocked` (agy `explorer`) llega al navegador
  como inactivo.
- `test_state_ws_contradice_estado_y_actividad` — `state_ws` (`server.py:197-198`) dice
  `activity: "esperando confirmación"` y `status: "idle"` en el mismo payload.
- `test_t2_objetivo_el_bloqueado_sobrevive` — `xfail(strict=True)`: hoy falla y el suite está verde.
  Cuando T2 arregle `mkagent`, pasa a `XPASS(strict)` y el suite se pone rojo: hay que quitar el
  marker y actualizar las tres anclas de arriba al contrato nuevo.
- `test_herdr_out_solo_stdout`, `test_checks_de_substring_de_start_estan_muertos`,
  `test_api_log_sin_respuesta_es_el_error_actual` — anclas de T4: con el error en stderr, los checks
  de substring de `server.py:255-256` y el `if not out` de `server.py:117` están ciegos.
- `test_send_no_observa_la_entrega`, `test_send_a_bloqueado_da_el_mensaje_actual` — anclas de T5: sin
  `--wait` la entrega se confirma por texto, y `agent_blocked` llega como 502 "herdr no responde".
- `test_snapshot_emulado_tiene_la_forma_de_la_captura`, `test_build_state_no_pide_snapshot`,
  `test_api_offices_linea_base` — anclas de T6: hoy `build_state` hace 1 llamada y `api_offices`
  hace `agent list` + `workspace list` por máquina; `api snapshot` da todo en una.

## Supuestos de `server.py` que los fixtures pruevan falsos

1. `agent list` **puede traer un agente sin la clave `name`** (la captura real trae 6 agentes y uno
   no tiene `name`). `server.py:74` lo convierte en `"?"`, que entra en `agents` y en `LAST_AGENTS`
   como agente fantasma. `state_ws` (`server.py:197`) exige `a["name"]` tras filtrar por
   `a.get("name")`, así que nunca lo ve; `valid_agent` (server.py:113) rechaza `"?"`. T2 tiene que
   decidir el tratamiento, no esconder el caso.
2. `interactive_ready` y `completion_seq` **son opcionales por agente**: `opencode2` y `claude` no
   traen `interactive_ready`; `tester` y el agente sin nombre no traen `completion_seq`. Leerlos con
   `a["..."]` reventaría. `test_campos_de_t2_existen_pero_no_en_todos_los_agentes` fija el hecho.
3. `machine list --json` **no** tiene `result`: `server.py:149` lo parsea como lista (acertado), pero
   `hj()` (`server.py:140`) no serviría para ese comando.
4. La captura real **no tiene ningún agente `blocked`**. El estado del que trata la feature se
   construye con el fixture sintético `agent_list_blocked.json` y con `set_agent_state`, y el agente
   elegido es `agy` (`explorer`), conforme a la restricción de verificar solo con `agy`.
