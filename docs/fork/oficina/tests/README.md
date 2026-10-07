# Tests de la oficina (stub de Herdr a nivel argv y socket Unix)

Suite pytest que emula `herdr` a nivel de argumentos **y** del socket de eventos, para probar
`server.py` sin Herdr vivo, sin GPU y sin red. Nace en T1 de la feature `oficina-herdr-v5` (ver
`odd/tasks/oficina-herdr-v5.md`); hoy fija las anclas de T2, T4, T5, T6, T7, T8, T9, T10, T17, T27 y T28.

## Cómo se corre

```
python3 -m pytest docs/fork/oficina/tests/ -q
```

Medido en esta máquina (Python 3.12.3, pytest 9.1.1, sin GPU ni Herdr vivo), el 2026-10-07, en dos runs:
**173 passed in 153,23 s** y **173 passed in 153,64 s**. Reparto: `test_herdr_stub.py` **134** tests,
`test_events.py` **39**. **No hay `xfail` ni `xpass`**: el `xfail(strict=True)` que T1 puso para `mkagent`
se quitó cuando T2 arregló el colapso, y las tres anclas de T1 se actualizaron al contrato nuevo.

La línea de hermeticidad que escribe el harness al final de la corrida:

```
T28 hermeticidad: tests=170  executables pedidos=['ada-cli', 'agy', 'herdr', 'opencode', 'pi', 'ssh']  binarios reales=0  pedidos tras el sellado=0  hilos del servidor al final=[]
```

- `tests=170`: los tests que instalaron el shim de `subprocess` (173 menos 3 que no piden el fixture
  `server` y solo leen fixtures: `test_agent_list_es_la_captura_real`,
  `test_campos_de_t2_existen_pero_no_en_todos_los_agentes`, `test_snapshot_entrega_todo_en_una_llamada`).
- `executables pedidos`: los seis nombres que el servidor pidió al shim. **Los seis están emulados** por
  el stub (`LOCAL` en `stub_herdr.py:17` más `herdr`), así que `binarios reales=0`.
- `pedidos tras el sellado=0`: ningún hilo del servidor pidió `subprocess` después del teardown.
- `hilos del servidor al final=[]`: la guardia de sesión no encontró ningún hilo de `server.py` vivo.

El harness no escribe bytecode en `docs/fork/oficina/` (`sys.dont_write_bytecode`), no arranca ni
reinicia `strata-oficina.service`, y no modifica el `HOME` real.

## Qué hace el harness

- `stub_herdr.py` (522 líneas) — emulador de `herdr` a nivel argv. Despacha por `(machine, args)`,
  devuelve la captura en `stdout`, y sabe responder errores
  (`{"id":…,"error":{"code":…,"message":…}}` en `stderr` con exit 1, y stdout vacío). Controla el estado
  de cada agente con `set_agent_state(name, status)` y registra cada invocación en `calls` / `exes`.
  Cualquier binario no emulado queda en `unknown`, y el fixture `server` exige al final que `unknown`
  esté vacío. `HerdrSocket` emula el socket Unix: responde el ack verbatim
  `{"id":..,"result":{"type":"subscription_started"}}`, emite los eventos capturados por la misma
  conexión, puede dejar la ruta sin socket (`refuse`), sustituir el ack para probar la degradación, o
  cortar la conexión justo tras el ack (el corte a mitad observado en el probe vivo).
- `conftest.py` (504 líneas) — fixture `server`: importa `server.py` aislado. `HOME` temporal,
  `STRATA_OFICINA_PW` temporal (scrypt con la forma que lee `pw_ok`), `FORK` y las cuatro rutas derivadas
  (`CHANGELOG`, `COLA`, `METRICAS`, `TAREAS`) apuntan al árbol de fixtures, `MOTOR` y `BENCH` a ficheros
  temporales, `sys.argv` reducido (con `pytest -q`, `server.py:6` `int(sys.argv[1])` reventaría el
  import), y `subprocess` del módulo sustituido por un shim. Los caches del módulo (`CACHE`, `SEEN`,
  `FAILS`, `MCACHE`, `AGCACHE`, `KCACHE`, `JOBS`, `LAST_AGENTS`, `LAST_SEND`, `SESS`) y el estado `EV`
  se resetean por test, porque son globales con TTL y sin ellos los tests dependen del orden.
  `HERDR_SOCKET_PATH` se fija a una ruta sin socket: `ev_path()` nunca cae en `~/.config/herdr/herdr.sock`.
- `fixtures/` — 21 ficheros de captura verbatim del binario real, más un agente `blocked` sintético
  (`agent_list_blocked.json` y `api_snapshot_blocked.json`, derivados de las capturas con `explorer`
  puesto a `blocked`), y el árbol `fork/` recortado a lo que `server.py` parsea (4 ficheros).

## Formas verificadas contra el binario herdr 0.9.3

Todo lo que sigue está capturado en vivo en esta máquina (2026-10-06) y fijado verbatim en
`fixtures/`. Las formas de los comandos mutantes (`workspace create`, `pane split`, `workspace close`,
`agent start`, `agent prompt` y `notification show`) **están capturadas**: la lista de inferencias que
abrió T1 se cerró con T23, T25 y el hueco de T10. Queda una brecha de harness anotada abajo: el stub
sigue emitiendo para tres de esos comandos el `type` inventado, no el capturado.

- `agent list`, `api snapshot`, `machine list --json`, `workspace list`, `pane list`, `agent get`
  (`agent_list.json`, `api_snapshot.json`, `machine_list.json`, `workspace_list.json`, `pane_list.json`,
  `agent_get.json`).
- `machine list --json` es una **lista desnuda** en el nivel superior, no va envuelta en `result`.
- El JSON real es **compacto** (sin espacio tras los dos puntos); el stub emite JSON compacto.
- Los errores van a **stderr** con **exit 1**; stdout vacío. Los códigos capturados: `timeout`,
  `agent_not_ready`, `agent_blocked`, `agent_prompt_stalled`, `agent_name_not_found`, `agent_not_found`.
- `agent_status` es exactamente `idle | working | blocked | done | unknown`.
- `workspace create` → `result` = `{root_pane, tab, type:"workspace_created", workspace}`
  (`workspace_create.json`).
- `pane split` → `result` = `{pane, type:"pane_info"}` (`pane_split.json`). Un pane nuevo real trae
  `agent_status:"unknown"` y **sin** `name`.
- `workspace close` → `result` = `{type:"ok"}` (`workspace_close.json`).
- `agent start` éxito → `result` = `{agent:{…,"agent_status":"idle","interactive_ready":true},
  argv:[…], type:"agent_started"}`. `agent_start.json` guarda ese `result` verbatim, sin el envoltorio
  `id`: los tests lo sirven como `stdout` con `script_response`.
- `agent prompt --wait` éxito → `result` = `{agent:{…,"agent_status":"done","completion_seq":1091,
  "pane_id":"wK:p3"}, type:"agent_prompted"}`; `agent_prompt.json` guarda el sobre entero
  (`{"id":"cli:agent:prompt","result":{…}}`). **`type` y `agent` viven dentro de
  `result`**, no en la raíz: leer `type` de la raíz da `None` y un envío entregado se lee como 502.
- `notification show` éxito → rc=0 y `result` = `{reason:"shown", shown:true,
  type:"notification_show"}`, stderr vacío (`notification_show.json`).
- `pane read --source recent-unwrapped --format text` devuelve **texto plano**, no JSON
  (`agent_read.txt`): `api_log` parte por líneas y toma las 30 últimas.
- `api_snapshot.json` (**6.640 bytes** con `wc -c`), `result.snapshot` con `agents`, `workspaces`,
  `tabs`, `panes`, `layouts`, `focused_pane_id`, `focused_tab_id`, `focused_workspace_id`, `protocol`,
  `version`.
- Protocolo del socket: los pedidos llevan `id`; `events.subscribe` → ack `subscription_started`; los
  eventos llegan por la misma conexión, newline-delimited, `{data, event}`. `pane.agent_status_changed`
  y `pane.scroll_changed` requieren `pane_id`; `pane.output_matched` requiere `pane_id + source + match`,
  y `OutputMatch` es `{type: substring|regex, value: string}` (la clave es `value`, no `text`).
- Reglas del CLI, verificadas en vivo: `--cwd` se honra **solo si la ruta existe**; `agent prompt
  --timeout` **requiere** `--wait` (sin `--wait` es rc=2, `--timeout requires --wait`); `--wait` puede
  asentarse en `blocked`, no solo en `working`/`done`.

## Reglas que los fixtures pruevan falsas en `server.py`

1. `agent list` **puede traer un agente sin la clave `name`** (la captura real trae 6 agentes y uno no
   la tiene). La versión anterior lo convertía en `"?"` y lo metía en `agents`. Hoy se omite
   (`server.py:224-225`), y `test_agente_sin_name_se_omite` y `test_agente_sin_name_remota_se_omite` lo fijan.
2. `interactive_ready` y `completion_seq` **son opcionales por agente**: `opencode2` y `claude` no traen
   `interactive_ready`; `tester` y el agente sin nombre no traen `completion_seq`. Leerlos con `a["..."]`
   reventaría. `test_campos_de_t2_existen_pero_no_en_todos_los_agentes` fija el hecho.
3. `machine list --json` **no** tiene `result`: se parsea como lista con `herdr_out`
   (`server.py:87`), y `hj()` (`server.py:338`) no serviría para ese comando.
4. La captura real **no tiene ningún agente `blocked`**. El estado que trata la feature se construye con
   el fixture sintético y con `set_agent_state`, y el agente elegido es `agy` (`explorer`), conforme a la
   restricción de verificar solo con `agy`.
5. Los checks de substring sobre stdout (`"interactive_ready":true`, `"agent_started"`,
   `agent_not_ready`) estaban ciegos: el error vive en stderr. Se eliminaron y se parsea el sobre
   (`server.py:1007-1011`).

## Línea base y anclas

Un run verde hoy es lo que hay que mantener verde. Cada ancla es una aserción del contrato, no una
especie: las de T1 que describían el comportamiento roto se renombraron al contrato nuevo.

- **T2** — `test_mkagent_conserva_el_estado_real`, `test_mkagent_keyed_en_el_estado_real`,
  `test_build_state_conserva_el_bloqueado`, `test_done_sobrevive_a_build_state`,
  `test_estado_fuera_de_dominio_es_unknown`, `test_agente_sin_clave_agent_status_es_unknown`,
  `test_state_ws_alinea_estado_y_actividad`, `test_t2_objetivo_el_bloqueado_sobrevive` (sin `xfail`).
- **T4** — `test_herdr_out_solo_stdout`, `test_start_parsea_el_error_de_stderr`,
  `test_api_log_sin_respuesta_es_el_error_actual`, `test_herdr_cmd_captura_los_tres_canales`,
  `test_json_malformado_en_stderr_no_reventa`, `test_texto_en_stdout_con_stderr_ruido_no_es_error`,
  `test_stub_script_error_llega_como_sobre_verificado`.
- **T5** — `test_send_observa_la_entrega_con_wait`, `test_argv_lleva_wait_y_timeout_en_ms`,
  `test_el_plazo_de_subprocess_deja_que_herdr_devuelva_su_sobre`,
  `test_bloqueado_dice_aprobar_en_el_panel_y_no_se_registra_como_enviado`,
  `test_stalled_no_afirma_que_el_mensaje_fue_enviado`, `test_un_stalled_no_se_reintenta_solo`,
  `test_envios_log_registra_el_resultado_de_cada_envio`, `test_send_a_bloqueado_es_legible`.
- **T6** — `test_snapshot_emulado_tiene_la_forma_de_la_captura`, `test_build_state_pide_un_snapshot`,
  `test_api_offices_linea_base`, `test_el_snapshot_del_ciclo_se_reutiliza_en_offices`,
  `test_get_state_frio_lanza_un_solo_snapshot`, `test_office_create_invalida_el_snapshot`,
  `test_office_delete_invalida_el_snapshot`, `test_raw_agents_y_agmap_salen_del_snapshot`.
- **T7** — 13 tests de concurrencia: `test_el_dict_publicado_no_se_mutan_despues` (la invariant
  determinante), `test_get_state_cache_e_intervalo`, `test_mkagent_una_transicion_por_agente_bajo_builds_concurrentes`,
  `test_mkagent_registra_las_dos_transiciones_serializadas`, `test_job_serializa_una_copia_estable_del_job`,
  `test_job_view_es_copia_y_no_el_objeto_vivo`, `test_save_meta_mantiene_todas_las_oficinas_bajo_creacion_y_borrado`,
  `test_la_escritura_de_meta_es_atomica_y_no_trunca`, `test_sesiones_tambien_es_escritura_atomica`,
  `test_limit_de_intentos_correcto_bajo_fallos_concurrentes`, `test_la_ventana_de_15min_se_poda_con_el_lock`,
  `test_consumidor_sse_no_reventa_mientras_se_reconstruye`,
  `test_caches_de_herdr_no_reventan_bajo_lecturas_concurrentes`. Las dos últimas **pasan también en la
  base**: son guardas, no anclas RED. Registrado en el documento de la feature.
- **T8** — 16 tests añadidos en `test_herdr_stub.py` (registro de la feature, 2026-10-06). Los que fijan
  el contrato: `test_start_remota_lleva_machine_en_todos_los_kinds`,
  `test_ada_cli_remota_se_rechaza_y_no_lanza_el_binario_local`,
  `test_rhome_no_cachea_el_fallo_para_siempre`, `test_state_ws_acepta_los_labels_reales_de_machine_list`,
  `test_state_ws_label_desconocido_es_error_y_no_estado_local`, `test_api_hilo_declara_el_ambito_del_auditoria`,
  y el defecto 7: `test_api_office_recupera_la_maquina_que_volvio_a_responder`.
- **T9** — 21 tests en `test_events.py`: el set de suscripciones verbatim, la forma que la oficina no
  manda, `test_el_socket_nunca_manda_un_metodo_mutante`, `test_en_repojo_un_ciclo_de_estado_no_lanza_subprocesos`,
  la reconciliación de 30 s, el overlay de estado, la correspondencia pane→nombre, la re-suscripción
  idempotente, la degradación (`socket refusado` → `polling`, ack que no es `subscription_started`,
  conexión cortada a media transmisión), el backoff acotado, y la salud de la ruta (`live|polling|unavailable`).
- **T10** — 10 tests: la transición a `blocked` avisa y el `blocked` repetido no,
  `blocked→idle→blocked` y la ventana de 20 s, el argv verbatim, el pane sin nombre, el aviso que falla,
  el bloqueado en masa bajo la cota de spawn, el aviso no bloquea la lectura del socket, el worker solo
  lanza `notification show`, y `test_forma_de_exito_de_notification_show_es_la_capturada_en_vivo`.
  El defecto de T9 encontrado aquí: `s.makefile("r")` bufferiza el chunk entero y se perdían todas las
  líneas tras la primera de un chunk; se arregló con un buffer propio (`_ev_read`).
- **T17** — `test_columnas_viajan_en_el_payload`: `TAREAS.json` sí tiene `columnas`
  (`EN COLA`, `EN CURSO`, `ESPERA OK`, `HECHO`, `DESCARTADO`) y `tasks_list` la descartaba.
- **T27** — 8 tests: `test_sin_ws_el_stream_es_la_oficina_de_strata_exacta`,
  `test_el_stream_lleva_la_oficina_pedida_y_el_bloqueado_llega_como_frame`,
  `test_un_cliente_que_mira_A_no_recibe_el_estado_de_B`,
  `test_un_ws_desconocido_en_events_se_refusa_y_no_sirve_el_estado_local`,
  `test_el_handler_SSE_sale_al_irse_el_cliente_y_deja_de_sondear`,
  `test_sin_canal_live_el_respaldo_de_la_UI_sigue_con_el_quantum_de_hoy`,
  `test_un_cliente_de_una_oficina_remota_no_recibe_eventos_locales`,
  `test_en_repojo_el_stream_de_otra_oficina_no_lanza_subproceso`.
- **T28** — la guardia está en `conftest.py`, no en un test: el fixture de sesión `hermetic` conserva el
  shim durante toda la sesión y solo devuelve el `subprocess` real al final, después de comprobar que no
  queda ningún hilo de `server.py` (`HEARTBEAT_DRAIN = 35 s` de margen) y de comprobar que ningún binario
  pedido está fuera de `EMULATED`. El teardown de `server` hace el orden de la fuga: neutralizar el
  socket → apagar los dos hilos (`ev_stop()` + `join(12 s)`) → comprobar que no queda ninguno → comprobar
  los ejecutables → **sellar** el shim. Sellado, un pedido se registra en `post` y reventa, y nunca llega
  al binario. El defecto medido en la base: el `monkeypatch` restauraba el `subprocess` real al teardown
  y un handler de SSE que quedaba vivo sondeaba el CLI real de Herdr; **25 lanzamientos reales de
  `api snapshot` en la suite entera**, y el guard `stub.unknown == []` no lo detectaba porque `herdr` es
  un nombre legítimo que el stub conoce. Hoy: `binarios reales=0` y `pedidos tras el sellado=0`.

## Cobertura que sigue sin prueba

- La **ruta remota** está probada solo contra el stub: la captura viva tenía 0 agentes remotos
  (mac-mini: 1 workspace, 1 pane, 0 agentes). Es T24.
- Los labels con `_` y mayúsculas se probaron con labels sintéticos; los reales son minúscula-guion.
- **El stub emite el `type` inventado para tres comandos mutantes**, aunque la forma capturada está
  fijada en `fixtures/`: `stub_herdr.py:260` devuelve `workspace_create`, `stub_herdr.py:267`
  `pane_split` y `stub_herdr.py:272` `workspace_close` con `workspace_id` y `closed`, mientras las
  capturas verbatim dicen `workspace_created`, `pane_info` y `{type:"ok"}`. Ningún test compara la salida
  del stub con esos tres fixtures. Lo que `server.py` lee de esos sobres (`workspace_id` y
  `pane_id`) **sí coincide** con la captura, así que el camino de la oficina funciona; la brecha es de
  harness, y está marcada en el código como `forma INFERIDA`. Cerrarla toca `stub_herdr.py`.
- El stub añade `matched_status` cuando `--wait` está en el argv (`stub_herdr.py:249`), un campo que
  **no existe** en la captura verificada de T25. `server.py` nunca lo usa para decidir.
- `RHOME_NEG = 60 s`, `NOTIF_WIN = 20 s`, `NOTIF_BURST = 4/10 s`, `RECONCILE = 30 s`, `SSE_MAX = 1800 s`
  y `SSE_TIMEOUT = 2 s` son **elección de la oficina, no medidas**: los tests fijan que el código hace lo
  que dice el número, no que el número sea el óptimo.
