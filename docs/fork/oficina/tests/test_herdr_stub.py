"""Suite de la oficina con Herdr emulado a nivel argv: sin GPU, sin Herdr vivo, sin red.

El proposito es doble:
  1. Probar que el stub reproduce las formas que `server.py` parsea. Un run verde es la linea base
     que cada tarea tiene que mantener verde.
  2. Dejar anclas visibles para el RED->GREEN de las tareas T2..T6: despues de T2 quedan
     `test_herdr_out_solo_stdout` (linea base de T4: `herdr_out` sigue viendo solo stdout) y la linea
     base del regimen de herdr, hoy `test_build_state_pide_un_snapshot` (antes
     `test_build_state_no_pide_snapshot`).
     T4 volvio verdes sus tres anclas (`test_start_parsea_el_error_de_stderr`,
     `test_api_log_sin_respuesta_es_el_error_actual`, `test_send_a_bloqueado_es_legible`) y anadio
     la seccion `T4: el error de herdr vive en stderr`.
     T5 volvio verde su ancla (`test_send_no_observa_la_entrega` ->
     `test_send_observa_la_entrega_con_wait`) y anadio la seccion `T5: la entrega se observa`.
     T6 volvio verdes sus anclas (`test_build_state_no_pide_snapshot` ->
     `test_build_state_pide_un_snapshot`, `test_api_offices_linea_base`,
     `test_api_offices_con_oficina_remota`, `test_api_offices_remota_vacia`, y las dos anclas que
     scripteaban `agent list`: `test_herdr_no_responde_no_reventa` y
     `test_json_malformado_en_stdout_no_reventa_el_estado`) y anadio la seccion
     `T6: un solo `api snapshot` por ciclo de estado`.

T2 (contrato de estado) fija el dominio `idle | working | blocked | done | unknown` y los campos
que viajan por agente: `pane_id`, `focused`, `interactive_ready`, `completion_seq`,
`state_change_seq`, `agent`. Las anclas de T2 (`test_mkagent_conserva_el_estado_real`,
`test_build_state_conserva_el_bloqueado`, `test_state_ws_alinea_estado_y_actividad`,
`test_agente_sin_name_se_omite`, `test_state_ws_remota_conserva_el_bloqueado`) afirman hoy el
contrato nuevo, no el colapso.

Las formas verificadas contra el binario herdr 0.9.3 estan en `fixtures/` (ver README.md).
"""
from __future__ import annotations
import json
from pathlib import Path

from stub_herdr import FIXTURES, STATUSES

CAMPOS_AGENTE = {"agent", "agent_status", "completion_seq", "cwd", "focused", "foreground_cwd",
                 "name", "pane_id", "revision", "state_change_seq", "tab_id", "terminal_id",
                 "terminal_title", "terminal_title_stripped", "workspace_id"}
CAMPOS_T2 = {"pane_id", "focused", "interactive_ready", "completion_seq", "state_change_seq"}


def _script_error(server, code, message, match, mach=None, cid="cli:agent:prompt"):
    """Programa un error de herdr con la forma VERIFICADA: stdout vacio, JSON en stderr, exit 1.

    El stub tiene un arreglo en `_herdr` (2026-10-06, T4): cuando el payload scripted es un dict lo
    manda a stderr con exit 1. Antes indexaba `scripted[0]` -> KeyError, que el servidor traga como
    salida vacia, asi que la evidencia de `herdr no responde` salia de un KeyError y no de la forma
    capturada del binario 0.9.3. `test_stub_script_error_llega_como_sobre_verificado` lo fija.
    """
    payload = json.dumps({"id": cid, "error": {"code": code, "message": message}}, separators=(",", ":")) + "\n"
    return server.script_response(match, "", payload, 1, mach)


def _agentes(state):
    return {a["name"]: a for a in state["agents"]}


def _send(server, text="hola", agent="claude", mode="direct"):
    """`api_send` con el limite de 3 s reseteado: el limite es por envio humano, no por test."""
    server.mod.LAST_SEND = 0.0
    return server.mod.api_send(json.dumps({"agent": agent, "mode": mode, "text": text}))


def _log(server):
    """Lineas de `envios.log` como dicts (el audit trail del resultado, no de la intencion)."""
    p = Path(server.mod.ENVLOG)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


def _prompts(server):
    """Llamadas `agent prompt` reales, con su argv completo."""
    return [c for c in server.calls() if c[:2] == ("agent", "prompt")]


def _herdr(server):
    """Todas las llamadas herdr registradas por el stub: (maquina, args). `server.calls(mach)` filtra por
    maquina (None = local), asi que el regimen total se cuenta aqui."""
    return [(m, tuple(a)) for m, a in server.stub.calls]


def _snaps(server, mach="__todos__"):
    """Llamadas `api snapshot`: `mach=None` solo la maquina local, `"__todos__"` todos los targets."""
    return [c for c in _herdr(server) if c[1][:2] == ("api", "snapshot") and (mach == "__todos__" or c[0] == mach)]


def _solo_mac_mini(server):
    """Deja un solo target remoto: la prueba de regimen es local + 1 remota, no local + 2."""
    server.stub.machines = [m for m in server.stub.machines if m["label"] == "mac-mini"]
    return server.stub.machines


def _ms(argv):
    return int(argv[argv.index("--timeout") + 1])


def _prompt_envelope(status=None):
    """Sobre de exito de `agent prompt --wait` en la forma VERIFICADA en vivo (2026-10-06, agente agy
    del probe): `{"id":"cli:agent:prompt","result":{"agent":{...},"type":"agent_prompted"}}`.
    `type` y `agent` viven DENTRO de `result`. La forma inventada del fixture T23 (`{"type":..,
    "agent":..}` en la raiz) hizo pasar los tests de T5 y produjo 502 ante un envio realmente entregado.
    """
    env = json.loads((FIXTURES / "agent_prompt.json").read_text(encoding="utf-8"))
    if status is not None:
        env["result"]["agent"]["agent_status"] = status
    return json.dumps(env, separators=(",", ":")) + "\n"


# ---------- formas del binario, verificadas en vivo ----------

def test_agent_list_es_la_captura_real():
    d = json.loads((FIXTURES / "agent_list.json").read_text(encoding="utf-8"))
    assert d["id"] == "cli:agent:list"
    assert d["result"]["type"] == "agent_list"
    a = d["result"]["agents"][0]
    assert set(a) == CAMPOS_AGENTE
    assert all(a["agent_status"] in STATUSES for a in d["result"]["agents"])


def test_campos_de_t2_existen_pero_no_en_todos_los_agentes():
    """ANCLA T2: `interactive_ready` y `completion_seq` son opcionales por agente en la captura real.

    `opencode2` y `claude` no traen `interactive_ready`; `tester` y el agente sin nombre no traen
    `completion_seq`. server.py tiene que leerlos con `.get()`, nunca con `a["..."]`.
    """
    ags = json.loads((FIXTURES / "agent_list.json").read_text(encoding="utf-8"))["result"]["agents"]
    for f in CAMPOS_T2:
        assert any(f in a for a in ags), f
    assert all("pane_id" in a and "focused" in a for a in ags)
    assert {a.get("name", "<no-name>"): sorted(CAMPOS_T2 - set(a)) for a in ags} == {
        "opencode2": ["interactive_ready"], "tester": ["completion_seq"], "suplente": [],
        "claude": ["interactive_ready"], "explorer": [], "<no-name>": ["completion_seq", "interactive_ready"]}


def test_t2_objetivo_el_bloqueado_sobrevive(server):
    """Especie de T2: el `blocked` de un agente `agy` llega al payload intacto."""
    server.set_state("explorer", "blocked")
    assert _agentes(server.build_state())["explorer"]["status"] == "blocked"


def test_machine_list_es_una_lista_desnuda(server):
    """`machine list --json` NO va envuelto en `result`: server.py:149 lo parsea como lista."""
    ms = json.loads((FIXTURES / "machine_list.json").read_text(encoding="utf-8"))
    assert isinstance(ms, list) and all("result" not in m for m in ms)
    assert server.machines() == [{"label": "mac-mini", "target": "nicoaisdr@100.98.211.55"},
                                  {"label": "macbook-air", "target": "31017423Z@100.99.86.60"}]


def test_snapshot_entrega_todo_en_una_llamada():
    s = json.loads((FIXTURES / "api_snapshot.json").read_text(encoding="utf-8"))
    snap = s["result"]["snapshot"]
    assert s["id"] == "cli:api:snapshot" and snap["version"] == "0.9.3" and snap["protocol"] == 22
    assert {"agents", "panes", "tabs", "workspaces", "layouts", "focused_pane_id",
            "focused_tab_id", "focused_workspace_id"} <= set(snap)
    assert len(snap["agents"]) == len(snap["panes"]) == 6


def test_workspace_list_forma(server):
    r = server.mod.hj(["workspace", "list"])
    assert r["workspaces"][0]["workspace_id"] == "w1" and r["workspaces"][0]["label"] == "Strata3060"


def test_agent_get_forma(server):
    r = server.mod.hj(["agent", "get", "tester"])
    assert r["agent"]["pane_id"] == "w1:pH" and r["type"] == "agent_info"


# ---------- lineas base: lo que server.py hace hoy ----------

def test_build_state_linea_base(server):
    st = server.build_state()
    assert set(st) == {"agents", "metrics", "ticker", "queue", "lock", "bench", "suplencia",
                       "herdr", "tareas", "updated"}
    assert st["herdr"] is True and st["lock"] is False and st["bench"] is False
    assert st["queue"] == {"done": 3, "total": 9}
    assert [a["name"] for a in st["agents"]] == ["claude", "opencode2", "tester", "explorer",
                                                 "suplente"]   # T2: el agente sin `name` se omite
    assert st["metrics"]["base"]["B1"] == 50.7
    assert st["tareas"] and st["ticker"][-1] == "COLA 3/9"


def test_build_state_pide_un_snapshot(server):
    """T6 (ancla de T1 vuelta verde): un ciclo de estado lanza UNA sola llamada herdr para el target
    local, y es `api snapshot`, no `agent list`.

    La medida que se reduce esta en la verificacion viva de T23: 379 llamadas del servicio scratch, 355
    `agent list` y 18 `workspace list`, 6 `machine list --json`, 0 mutantes. Cada `agent list` es un
    subproceso que compite con las mediciones de la RTX 3060."""
    server.build_state()
    assert server.calls() == [("api", "snapshot")]
    assert ("agent", "list") not in server.calls()


def test_mkagent_conserva_el_estado_real(server):
    """T2: `mkagent` deja de colapsar. El dominio verificado en herdr 0.9.3 pasa tal cual.

    Lo que esta fuera del dominio (clave ausente, `None`, un string de un Herdr futuro) cae en
    `unknown`, nunca en `idle`.
    """
    for status, esperado in [("working", "working"), ("idle", "idle"), ("blocked", "blocked"),
                             ("done", "done"), ("unknown", "unknown"), ("waiting", "unknown"),
                             ("", "unknown"), (None, "unknown")]:
        assert server.mkagent("tester", status)["status"] == esperado
    a = server.mkagent("agy-obrero", "done")
    assert a["status"] == "done" and all(a[k] is None for k in CAMPOS_T2) and a["agent"] is None


def test_mkagent_keyed_en_el_estado_real(server):
    """Las transiciones de `SEEN` (`since`/`ts` que lee index.html) se keyean en el estado real."""
    server.mod.SEEN.clear()
    a = server.mod.mkagent("explorer", "blocked", "actividad", "#fb7185", "rol")
    assert server.mod.SEEN["explorer"][0] == "blocked" and a["since"] == server.mod.SEEN["explorer"][1]
    b = server.mod.mkagent("explorer", "blocked", "actividad", "#fb7185", "rol")
    assert b["since"] == a["since"] and b["ts"] == a["ts"]   # sin transicion no se reescribe
    c = server.mod.mkagent("explorer", "working", "actividad", "#fb7185", "rol")
    assert server.mod.SEEN["explorer"][0] == "working" and c["ts"] >= a["ts"]


def test_build_state_conserva_el_bloqueado(server):
    """T2: el fixture sintetico `blocked` (agy `explorer`) llega al navegador bloqueado."""
    server.set_state("explorer", "blocked")
    ag = _agentes(server.build_state())
    assert ag["explorer"]["status"] == "blocked"
    assert ag["explorer"]["activity"] == "investigando"   # la actividad sigue viniendo de ENVIADOS
    assert ag["claude"]["status"] == "idle" and ag["opencode2"]["status"] == "idle"   # linea base


def test_done_sobrevive_a_build_state(server):
    """T2: `done` no se pinta inactivo."""
    server.set_state("explorer", "done")
    ag = _agentes(server.build_state())
    assert ag["explorer"]["status"] == "done" and ag["explorer"]["activity"] == "investigando"


def test_estado_fuera_de_dominio_es_unknown(server):
    """T2: un `agent_status` de un Herdr futuro no se traduce a `idle`."""
    server.stub.find("explorer")["agent_status"] = "waiting"
    ag = _agentes(server.build_state())
    assert ag["explorer"]["status"] == "unknown"


def test_agente_sin_clave_agent_status_es_unknown(server):
    """T2: la clave `agent_status` puede faltar en un agente presente; el estado es `unknown`."""
    a = server.add_agent(name="agy-misterio", kind="agy", workspace_id="w1")
    del a["agent_status"]
    ag = _agentes(server.state_ws("w1"))
    assert ag["agy-misterio"]["status"] == "unknown"
    assert ag["agy-misterio"]["activity"] == server.mod.ACTS["unknown"]


def test_campos_de_herdr_opcionales_y_none(server):
    """T2: los campos de Herdr llegan por agente; los que no vienen son `None`, no un default falso."""
    ag = _agentes(server.build_state())
    assert all(set(CAMPOS_T2) | {"agent"} <= set(a) for a in ag.values())
    assert ag["opencode2"]["interactive_ready"] is None and ag["opencode2"]["completion_seq"] == 1083
    assert ag["tester"]["completion_seq"] is None and ag["tester"]["interactive_ready"] is True
    assert ag["claude"]["focused"] is False and ag["claude"]["pane_id"] == "w1:pE"
    assert ag["tester"]["agent"] == "pi" and ag["suplente"]["agent"] == "agy"


def test_agy_bloqueado_llega_con_sus_campos(server):
    """La unica verificacion viva permitida es con `agy`: el stub prepara el caso con `explorer`.

    Estado, tipo de agente y panel tienen que llegar juntos, para que T3/T10 puedan pintar y avisar
    sin adivinar.
    """
    server.set_state("explorer", "blocked")
    ag = _agentes(server.build_state())["explorer"]
    assert ag["status"] == "blocked" and ag["agent"] == "agy" and ag["pane_id"] == "w1:pM"
    assert ag["state_change_seq"] > 1077 and ag["interactive_ready"] is True


def test_state_ws_alinea_estado_y_actividad(server):
    """T2: `state_ws` ya no contradice su propio estado: la actividad se deriva del status real."""
    server.set_state("explorer", "blocked")
    ag = _agentes(server.state_ws("w1"))
    assert ag["explorer"]["status"] == "blocked" and ag["explorer"]["activity"] == "esperando confirmación"
    assert all(a["activity"] == server.mod.ACTS[a["status"]] for a in ag.values())
    assert ag["opencode2"]["status"] == "idle" and ag["opencode2"]["activity"] == "en reposo"


def test_captura_no_tiene_bloqueados_y_el_fixture_sintetico_si(server):
    idle = json.loads((FIXTURES / "agent_list.json").read_text(encoding="utf-8"))["result"]["agents"]
    assert all(a["agent_status"] == "idle" for a in idle)
    bloq = json.loads((FIXTURES / "agent_list_blocked.json").read_text(encoding="utf-8"))["result"]["agents"]
    assert [(a.get("name"), a["agent_status"]) for a in bloq if a.get("agent_status") == "blocked"] == [("explorer", "blocked")]
    server.load("agent_list_blocked.json")
    assert _agentes(server.build_state())["explorer"]["status"] == "blocked"   # T2: sobrevive


def test_agente_sin_name_se_omite(server):
    """T2: la captura real trae un agente SIN `name` y se omite, no se convierte en "?" fantasma.

    `state_ws` (server.py:197) ya exigia `a.get("name")`; `build_state` lo toleraba y `valid_agent`
    nunca aceptaria "?". El agente sin nombre no entra en `agents` ni en `LAST_AGENTS`.
    """
    st = server.build_state()
    names = [a["name"] for a in st["agents"]]
    assert "?" not in names and names == ["claude", "opencode2", "tester", "explorer", "suplente"]
    assert "?" not in server.mod.LAST_AGENTS
    assert server.valid_agent("?") is False
    assert "?" not in server.mod.agmap()
    assert all(a.get("name") for a in server.state_ws("w1")["agents"])


def test_valid_agent_linea_base(server):
    for n in server.mod.FIXED:
        assert server.valid_agent(n) is True
    assert server.valid_agent("no-existe") is False
    assert server.valid_agent("") is False
    assert server.valid_agent("claude") is True   # regex de server.py:113


def test_api_log_con_agente_conocido(server):
    server.set_agent_log("explorer", ["linea 1", "linea 2"])
    r = server.api_log("explorer")
    assert r["agent"] == "explorer" and r["lines"] == ["linea 1", "linea 2"]
    assert server.calls()[-1][:3] == ("agent", "read", "explorer")
    assert server.calls()[-1] == ("agent", "read", "explorer", "--source", "recent-unwrapped",
                                   "--lines", "45", "--format", "text")


def test_api_log_agente_desconocido(server):
    assert server.api_log("no-existe") == {"error": "agente desconocido"}
    assert ("agent", "read", "no-existe") not in server.calls()   # ni siquiera se llama a herdr


def test_api_log_sin_respuesta_es_el_error_actual(server):
    """T4 (ancla vuelta verde): el error de herdr estaba en stderr y `herdr_out` lo tiraba.

    El inspector recibe el codigo traducido y el mensaje real de herdr, no el generico.
    """
    _script_error(server, "agent_name_not_found", "no agent named explorer", ["agent", "read"],
                    cid="cli:agent:read")
    r = server.api_log("explorer")
    assert r["agent"] == "explorer"
    assert "ese agente no existe en herdr" in r["error"]
    assert "no agent named explorer" in r["error"]   # el mensaje real de herdr llega
    assert "herdr no responde" not in r["error"]


def test_herdr_out_solo_stdout(server):
    """Linea base: `herdr_out` se queda para lo que solo necesita stdout, y sigue siendo ciego al error."""
    _script_error(server, "agent_blocked", "pendiente de aprobacion", ["agent", "prompt"],
                    cid="cli:agent:prompt")
    assert server.mod.herdr_out(["agent", "prompt", "explorer", "hola"], 30) == ""


def test_start_parsea_el_error_de_stderr(server):
    """T4 (ancla vuelta verde): `agent_not_ready` vive solo en stderr; el parseo lo traduce.

    Los checks de substring de `server.py:268-269` no lo veian nunca porque miraban stdout.
    """
    _script_error(server, "agent_not_ready", "pane is not at an interactive shell prompt", ["agent", "start"],
                    cid="cli:agent:start")
    assert server.mod._start("tester", "pi", "", "w1:pH", "/tmp", False) == "espera confirmación en su panel"
    err = json.loads((FIXTURES / "error_agent_not_ready.json").read_text(encoding="utf-8"))
    assert err["error"]["code"] == "agent_not_ready"      # el codigo real vive solo en stderr
    assert '"agent_started"' not in json.dumps(err, separators=(",", ":"))


def test_start_listo_cuando_el_stub_devuelve_agent_started(server):
    """T4: el sobre verificado `agent_started` se parsea y no cae en la rama de error."""
    assert server.mod._start("tester", "pi", "", "w1:pH", "/tmp", False) == "listo"
    assert '"interactive_ready":true' in json.dumps(
        json.loads((FIXTURES / "agent_start.json").read_text(encoding="utf-8")),
        separators=(",", ":"))
    assert json.loads((FIXTURES / "agent_start.json").read_text(encoding="utf-8"))["type"] == "agent_started"


def test_send_observa_la_entrega_con_wait(server):
    """T5 (ancla de T1 vuelta verde): el prompt se lanza con `--wait` y la entrega se confirma por
    el estado observado en el sobre, no por texto en stdout."""
    code, r = _send(server)
    prompt = _prompts(server)[0]
    assert prompt[:3] == ("agent", "prompt", "claude")
    assert "--wait" in prompt
    assert r["confirmed"] is True and r["state"] == "working"   # el estado observado, no el texto


# ---------- T4: stdout, stderr y exit status ----------

def test_herdr_cmd_captura_los_tres_canales(server):
    """El helper ve lo que `herdr_out` tiraba: el JSON de `stderr` y el exit 1 verificados."""
    raw = (FIXTURES / "error_agent_blocked.json").read_text(encoding="utf-8")
    server.script_response(["agent", "prompt"], "", raw, 1)
    r = server.mod.herdr_cmd(["agent", "prompt", "explorer", "hola"], 30)
    assert r["out"] == "" and r["rc"] == 1
    assert r["code"] == "agent_blocked" and r["msg"] == json.loads(raw)["error"]["message"]
    assert r["dead"] is False
    assert json.loads(r["err"]) == json.loads(raw)   # la captura verbatim del binario, en stderr
    assert json.loads(raw)["id"] == "cli:agent:prompt"


def test_herdr_cmd_parsea_el_sobre_de_exit0(server):
    server.set_state("explorer", "idle")
    r = server.mod.herdr_cmd(["agent", "prompt", "explorer", "hola"], 30)
    assert r["rc"] == 0 and r["code"] is None and r["type"] == "agent_prompted"
    assert r["env"]["agent"]["name"] == "explorer" and r["dead"] is False


def test_herdr_cmd_socket_muerto_es_dead(server):
    """Socket muerto: stdout y stderr vacios, exit != 0. No es un error de agente."""
    server.script_response(["agent", "prompt"], "", "", 1)
    r = server.mod.herdr_cmd(["agent", "prompt", "explorer", "hola"], 30)
    assert r["out"] == "" and r["err"] == "" and r["rc"] == 1
    assert r["dead"] is True and r["code"] is None and r["env"] is None


def test_tabla_de_codigos_cubre_los_fixtures(server):
    """Todo codigo capturado en `fixtures/error_*.json` tiene su mensaje en espanol."""
    ficheros = ("error_agent_blocked.json", "error_agent_not_ready.json", "error_agent_name_not_found.json",
                "error_agent_prompt_stalled.json", "error_timeout.json")
    for f in ficheros:
        raw = (FIXTURES / f).read_text(encoding="utf-8")
        err = json.loads(raw)["error"]
        assert server.mod.HERDR_MSG[err["code"]], f
        assert "herdr no responde" not in server.mod.HERDR_MSG[err["code"]]
        server.script_response(["agent", "prompt"], "", raw, 1)   # stdout vacio, JSON en stderr, exit 1
        server.mod.LAST_SEND = 0.0   # el limite de 3 s es por envio, no por test
        code, r = _send(server, text="sigue", agent="explorer")
        assert code == 502 and r["code"] == err["code"]
        assert server.mod.HERDR_MSG[err["code"]] in r["error"] and err["message"] in r["error"]
        assert r["error"] != "herdr no responde"
        assert r["ok"] is False and r["confirmed"] is False   # T5: nada se afirma sin entrega observada
        panel = err["code"] in ("agent_blocked", "agent_not_ready")   # accion: aprobar en el panel
        assert r["stalled"] != panel                       # `stalled` = no se observo un turno
        assert server.api_hilo("explorer")["envios"] == []   # ninguno queda registrado como enviado
        assert all(d["confirmed"] is False for d in _log(server))   # el log dice lo que paso, no lo intentado


def test_codigo_desconocido_muestra_el_codigo(server):
    """Un codigo de un herdr futuro se ve, no se traga."""
    m = server.mod.herdr_msg("pane_locked", "pane is locked")
    assert "pane_locked" in m and "pane is locked" in m
    assert server.mod.herdr_msg(None, "") == "respuesta de herdr sin sobre JSON"


def test_socket_muerto_se_distingue_de_un_error_de_agente(server):
    server.script_response(["agent", "prompt"], "", "", 1)
    code, r = _send(server, text="sigue", agent="explorer")
    assert code == 502 and r["code"] == "no_response"
    assert r["ok"] is False and r["confirmed"] is False   # T5: sin entrega observada no hay ok
    assert r["stalled"] is True                          # y la UI no puede decir "enviado"
    assert "herdr no responde" in r["error"] and "aprobación" not in r["error"]


def test_api_log_socket_muerto_dice_el_socket(server):
    server.script_response(["agent", "read"], "", "", 1)
    r = server.api_log("explorer")
    assert "herdr no responde" in r["error"] and "aprobación" not in r["error"]


def test_json_malformado_en_stderr_no_reventa(server):
    server.script_response(["agent", "prompt"], "", "herdr: connect: No such file or directory\n", 1)
    code, r = server.mod.api_send(json.dumps({"agent": "explorer", "mode": "direct", "text": "x"}))
    assert code == 502 and r["code"] == "no_response" and "No such file" in r["error"]


def test_json_malformado_en_stdout_no_reventa_el_estado(server):
    server.script_response(["api", "snapshot"], "herdr: socket closed\n", "", 1)   # T6: la fuente es el snapshot
    st = server.build_state()
    assert st["herdr"] is False and all(a["status"] == "idle" for a in st["agents"])


def test_texto_en_stdout_con_stderr_ruido_no_es_error(server):
    """`agent read --format text` es texto, no JSON: el ruido en stderr no lo vuelve error."""
    server.script_response(["agent", "read"], "linea 1\nlinea 2\n", "herdr: aviso\n", 0)
    r = server.api_log("explorer")
    assert r["lines"] == ["linea 1", "linea 2"] and "error" not in r


def test_start_parsea_la_captura_agent_started(server):
    server.script_response(["agent", "start"], (FIXTURES / "agent_start.json").read_text(encoding="utf-8"), "", 0)
    assert server.mod._start("tester", "pi", "", "w1:pH", "/tmp", False) == "listo"


def test_send_parsea_la_captura_agent_prompted(server):
    raw = (FIXTURES / "agent_prompt.json").read_text(encoding="utf-8")
    assert json.loads(raw)["result"]["type"] == "agent_prompted"   # forma verbatim: type dentro de `result`
    server.script_response(["agent", "prompt"], raw, "", 0)
    code, r = _send(server)
    assert code == 200 and r["ok"] is True and r["confirmed"] is True
    assert r["state"] == "done" and r["pane_id"] == "wK:p3"   # T5: estado observado en el sobre (--wait devolvio done, captura viva)


def test_exit0_sin_sobre_no_confirma_el_envio(server):
    """T5: `ok` solo cuando la entrega esta observada. Un exit 0 sin sobre no es entrega."""
    server.script_response(["agent", "prompt"], "", "", 0)
    code, r = _send(server, text="x", agent="explorer")
    assert code == 502 and r["code"] == "no_response"
    assert r["ok"] is False and r["confirmed"] is False and r["stalled"] is True
    assert Path(server.mod.ENVLOG).exists() is False   # nada se registra como enviado


def test_envio_exitoso_deja_el_resultado_en_envios_log(server):
    code, r = _send(server, text="revisa la cola")
    assert code == 200 and r["confirmed"] is True
    d = _log(server)[0]
    assert d["agent"] == "claude" and d["mode"] == "direct" and d["text"] == "revisa la cola"
    assert d["outcome"] == "working" and d["confirmed"] is True   # el resultado, no la intencion
    assert d["code"] is None and d["timeout_ms"] == r["timeout_ms"]


def test_limite_de_3s_rechaza_antes_de_llamar_a_herdr(server):
    code, r = _send(server, text="primero")
    assert code == 200 and r["confirmed"] is True
    code2, r2 = server.mod.api_send(json.dumps({"agent": "claude", "mode": "direct", "text": "segundo"}))
    assert code2 == 429 and r2["error"] == "limite 1 envio/3s"
    assert len(_prompts(server)) == 1                     # el segundo ni llega a herdr
    assert len(Path(server.mod.ENVLOG).read_text(encoding="utf-8").splitlines()) == 1


def test_validaciones_de_send_se_mantienen(server):
    """Los rechazos de `api_send` no llegan a herdr y no dejan huella en `envios.log`."""
    for body, err in ((json.dumps({"agent": "no-existe", "mode": "direct", "text": "x"}), "agente desconocido"),
                      (json.dumps({"agent": "claude", "mode": "no-mode", "text": "x"}), "modo invalido"),
                      (json.dumps({"agent": "claude", "mode": "direct", "text": ""}), "texto 1..4000 chars"),
                      (json.dumps({"agent": "claude", "mode": "direct", "text": "x" * 4001}), "texto 1..4000 chars")):
        code, r = server.mod.api_send(body)
        assert code == 400 and r["error"] == err
    assert ("agent", "prompt") not in [tuple(c[:2]) for c in server.calls()]
    assert Path(server.mod.ENVLOG).exists() is False


def test_error_de_start_llega_al_log_del_job(server):
    """El paso del job dice en espanol lo que hay que hacer, no "herdr no respondió"."""
    _script_error(server, "agent_not_ready", "pane is not at an interactive shell prompt", ["agent", "start"],
                    cid="cli:agent:start")
    code, r = server.mod.api_office(json.dumps(
        {"name": "prueba agy", "equipo": [{"perfil": "obrero", "kind": "agy", "model": "gemini-pro", "n": 1}]}))
    j = server.job(r["job"])
    pasos = " ".join(j["pasos"])
    assert j["estado"] == "hecho" and "espera confirmación en su panel" in pasos
    assert "herdr no respondió" not in pasos


def test_bloqueado_en_el_log_del_job_es_accionable(server):
    _script_error(server, "agent_blocked", "agent is blocked: a pending approval is waiting in its pane",
                    ["agent", "start"], cid="cli:agent:start")
    code, r = server.mod.api_office(json.dumps(
        {"name": "prueba agy", "equipo": [{"perfil": "obrero", "kind": "agy", "model": "gemini-pro", "n": 1}]}))
    j = server.job(r["job"])
    paso = [p for p in j["pasos"] if "obrero" in p][-1]
    assert "ERROR" in paso and "aprobación" in paso and "agent is blocked" in paso
    assert "herdr no respondió" not in " ".join(j["pasos"])


def test_office_delete_superficie_el_error_de_herdr(server):
    """T4: borrar una oficina remota reporta el codigo real, y `ok` no es un false positivo."""
    server.add_agent(name="agy-obrero", kind="agy", status="idle", mach="mac-mini", workspace_id="w9")
    _script_error(server, "workspace_not_found", "no workspace w9", ["workspace", "close"],
                    mach="mac-mini", cid="cli:workspace:close")
    code, r = server.mod.api_office_delete(json.dumps({"id": "mac-mini:w9", "confirm": "w9"}))
    assert code == 502 and "workspace_not_found" in r["error"] and r["ok"] is False
    assert r["code"] == "workspace_not_found"


def test_office_delete_ok_con_el_sobre_emulado(server):
    server.add_agent(name="agy-obrero", kind="agy", status="idle", mach="mac-mini", workspace_id="w9")
    code, r = server.mod.api_office_delete(json.dumps({"id": "mac-mini:w9", "confirm": "w9"}))
    assert code == 200 and r["ok"] is True and "workspace_close" in r["output"]


def test_hj_usa_el_helper_y_devuelve_result(server):
    """`hj` pasa por el helper: el `result` de los comandos list va envuelto y se parsea igual."""
    r = server.mod.hj(["workspace", "list"])
    assert r["workspaces"][0]["workspace_id"] == "w1"
    _script_error(server, "usage", "stub sin respuesta", ["workspace", "list"], cid="cli:workspace:list")
    assert server.mod.hj(["workspace", "list"]) is None


def test_send_a_bloqueado_es_legible(server):
    """T4 (ancla de T5 vuelta verde): `agent_blocked` dice lo que hay que hacer, no "herdr no responde"."""
    _script_error(server, "agent_blocked", "agent is blocked: a pending approval is waiting in its pane",
                    ["agent", "prompt"])
    code, r = _send(server, text="sigue", agent="explorer")
    assert code == 502
    assert r["code"] == "agent_blocked"                       # el codigo real, para el log
    assert r["ok"] is False and r["confirmed"] is False       # T5: no se afirma una entrega no observada
    assert "aprobación" in r["error"] and "agent is blocked" in r["error"]
    assert r["error"] != "herdr no responde"
    assert server.api_hilo("explorer")["envios"] == []        # no queda como enviado


# ---------- T5: la entrega se observa (`agent prompt --wait --timeout`) ----------

def test_el_set_de_stalled_es_el_de_la_tabla(server):
    """`stalled` es la familia de "no se observó un turno": `agent_prompt_stalled` y `timeout`.

    `agent_blocked` y `agent_not_ready` no son stall: su accion es aprobar en el panel, y decir
    "no se entregó" a un agente que esta esperando una aprobacion mandaria al humano al camino equivocado.
    """
    assert server.mod.STALLED == ("agent_prompt_stalled", "timeout")
    assert server.mod.PANEL == {"agent_blocked": "blocked", "agent_not_ready": "not_ready"}
    assert server.mod.HERDR_MSG["agent_not_ready"] == "espera confirmación en su panel"


def test_argv_lleva_wait_y_timeout_en_ms(server):
    """T5: el argv observa la entrega. `--timeout` en ms: el binario informa su plazo en el mensaje
    de stall (`no working or blocked state observed within 5000 ms`), lo que fija la unidad y una base
    verificada."""
    code, r = _send(server, text="hola")
    argv = _prompts(server)[0]
    assert argv[:3] == ("agent", "prompt", "claude") and argv[3] == "adrian: hola"
    assert "--wait" in argv
    ms = _ms(argv)
    assert ms == server.mod.send_timeout(len("adrian: hola"), True) == 5300
    assert r["timeout_ms"] == ms
    assert server.mod.WAIT_BASE <= ms < server.mod.WAIT_MAX


def test_el_plazo_crece_con_el_texto_y_topea(server):
    """El plazo depende del texto (herdr escribe el mensaje en el panel) y del agente."""
    corto, largo, enorme = (server.mod.send_timeout(n, True) for n in (1, 400, 4000))
    assert corto < largo < enorme
    assert enorme == server.mod.WAIT_MAX                      # tope: el humano no espera mas
    sin_prompt = server.mod.send_timeout(1, False)            # `interactive_ready` False -> mas plazo
    assert corto < sin_prompt <= server.mod.WAIT_MAX


def test_el_plazo_de_subprocess_deja_que_herdr_devuelva_su_sobre(server, monkeypatch):
    """El timeout del subprocess es mayor que `--timeout`: gana herdr y trae su sobre (`timeout` /
    `agent_prompt_stalled`). Un `TimeoutExpired` de Python dejaria stdout y stderr vacios, que el
    servidor lee como socket muerto: el resultado observable se perderia."""
    vistos = []

    def fake(args, timeout=8, mach=None):
        vistos.append((list(args), timeout))
        return {"out": "", "err": "", "rc": 1, "env": None, "code": None, "msg": None,
                "res": None, "type": None, "dead": True}
    monkeypatch.setattr(server.mod, "herdr_cmd", fake)
    code, r = _send(server, text="x" * 4000)
    argv, t = next((a, tt) for a, tt in vistos if a[:2] == ["agent", "prompt"])
    assert code == 502 and r["ok"] is False and r["stalled"] is True
    assert t > _ms(argv) / 1000


def test_bloqueado_dice_aprobar_en_el_panel_y_no_se_registra_como_enviado(server):
    """Forma VERIFICADA en el binario 2026-10-06: stdout vacio, JSON en stderr, exit 1."""
    raw = (FIXTURES / "error_agent_blocked.json").read_text(encoding="utf-8")
    server.script_response(["agent", "prompt"], "", raw, 1)
    code, r = _send(server, text="sigue", agent="explorer")
    assert code == 502 and r["ok"] is False and r["confirmed"] is False
    assert r["code"] == "agent_blocked" and r["stalled"] is False
    assert "aprueba" in r["error"] and "panel" in r["error"]          # accion para el humano
    assert "agent probeagy is blocked and requires interactive input" in r["error"]
    assert server.api_hilo("explorer")["envios"] == []                # nada aparece como enviado
    d = _log(server)[0]
    assert d["outcome"] == "blocked" and d["confirmed"] is False and d["text"] == "sigue"


def test_stalled_no_afirma_que_el_mensaje_fue_enviado(server):
    raw = (FIXTURES / "error_agent_prompt_stalled.json").read_text(encoding="utf-8")
    server.script_response(["agent", "prompt"], "", raw, 1)
    code, r = _send(server, text="sigue", agent="explorer")
    assert code == 502 and r["ok"] is False and r["confirmed"] is False
    assert r["code"] == "agent_prompt_stalled" and r["stalled"] is True
    assert "el mensaje no se entregó" in r["error"]
    assert server.api_hilo("explorer")["envios"] == []
    d = _log(server)[0]
    assert d["outcome"] == "stalled" and d["confirmed"] is False


def test_timeout_de_wait_es_stalled_y_no_enviado(server):
    """`timeout` en `agent prompt` es el mismo caso: la entrega no se observó."""
    err = json.loads((FIXTURES / "error_timeout.json").read_text(encoding="utf-8"))["error"]
    payload = json.dumps({"id": "cli:agent:prompt", "error": err}, separators=(",", ":")) + "\n"
    server.script_response(["agent", "prompt"], "", payload, 1)
    code, r = _send(server, text="sigue", agent="explorer")
    assert code == 502 and r["ok"] is False and r["confirmed"] is False
    assert r["code"] == "timeout" and r["stalled"] is True
    assert "tiempo límite" in r["error"]
    assert server.api_hilo("explorer")["envios"] == []


def test_entrega_observada_como_working_confirma_y_se_registra(server):
    server.script_response(["agent", "prompt"], _prompt_envelope("working"), "", 0)
    code, r = _send(server, text="revisa la cola")
    assert code == 200 and r["ok"] is True and r["confirmed"] is True
    assert r["state"] == "working" and r["pane_id"] == "wK:p3" and r["stalled"] is False
    d = _log(server)[0]
    assert d["outcome"] == "working" and d["confirmed"] is True and d["pane_id"] == "wK:p3"
    assert d["timeout_ms"] == r["timeout_ms"]
    assert server.api_hilo("claude")["envios"][0].endswith("[direct] revisa la cola")


def test_entrega_que_vuelve_a_idle_es_entregada(server):
    """Entregado y el agente volvió a `idle`/`done`: aceptó el prompt, no es un stall."""
    server.script_response(["agent", "prompt"], _prompt_envelope("idle"), "", 0)
    code, r = _send(server, text="sigue", agent="explorer")
    assert code == 200 and r["ok"] is True and r["confirmed"] is True
    assert r["state"] == "idle" and r["stalled"] is False
    assert _log(server)[0]["outcome"] == "idle"


def test_bloqueado_al_aceptar_el_prompt_no_es_stalled(server):
    """El prompt se entregó y el agente quedó esperando una aprobación: hay que decirlo, no callarlo."""
    server.script_response(["agent", "prompt"], _prompt_envelope("blocked"), "", 0)
    code, r = _send(server, text="sigue", agent="explorer")
    assert code == 200 and r["ok"] is True and r["confirmed"] is True
    assert r["state"] == "blocked" and r["stalled"] is False
    assert "aprueba" in r["msg"]


def test_sobre_sin_agente_no_reventa_y_no_afirma(server):
    """Defensivo: un `agent_prompted` sin `agent` (forma no capturada) no reventa y no afirma entrega."""
    server.script_response(["agent", "prompt"], '{"type":"agent_prompted"}\n', "", 0)
    code, r = _send(server, text="x", agent="explorer")
    assert code == 502 and r["ok"] is False and r["confirmed"] is False and r["stalled"] is True
    assert Path(server.mod.ENVLOG).exists() is False


def test_socket_inalcanzable_no_afirma_la_entrega(server):
    server.script_response(["agent", "prompt"], "", "herdr: connect: No such file or directory\n", 1)
    code, r = _send(server, text="x", agent="explorer")
    assert code == 502 and r["ok"] is False and r["confirmed"] is False
    assert r["code"] == "no_response" and r["stalled"] is True
    assert "No such file" in r["error"]
    assert server.api_hilo("explorer")["envios"] == []
    assert Path(server.mod.ENVLOG).exists() is False   # sin sobre no hay resultado que registrar


def test_un_stalled_no_se_reintenta_solo(server):
    """No hay reintento: un stall deja una sola llamada y la respuesta no promete nada."""
    raw = (FIXTURES / "error_agent_prompt_stalled.json").read_text(encoding="utf-8")
    server.script_response(["agent", "prompt"], "", raw, 1)
    code, r = _send(server, text="sigue", agent="explorer")
    assert len(_prompts(server)) == 1
    assert "reenvi" not in r["error"] and "se reenvía" not in r["error"]
    assert r["stalled"] is True


def test_envios_log_registra_el_resultado_de_cada_envio(server):
    """El audit trail dice lo que pasó: blocked, stalled y entregado quedan con su outcome."""
    server.script_response(["agent", "prompt"], "", (FIXTURES / "error_agent_blocked.json").read_text(encoding="utf-8"), 1)
    _send(server, text="uno", agent="explorer")
    server.script_response(["agent", "prompt"], "", (FIXTURES / "error_agent_prompt_stalled.json").read_text(encoding="utf-8"), 1)
    _send(server, text="dos", agent="explorer")
    server.script_response(["agent", "prompt"], (FIXTURES / "agent_prompt.json").read_text(encoding="utf-8"), "", 0)
    _send(server, text="tres", agent="explorer")
    assert [d["outcome"] for d in _log(server)] == ["blocked", "stalled", "done"]   # `done` es la captura viva de --wait
    assert [d["confirmed"] for d in _log(server)] == [False, False, True]
    assert [d["text"] for d in _log(server)] == ["uno", "dos", "tres"]
    assert [d["code"] for d in _log(server)] == ["agent_blocked", "agent_prompt_stalled", None]
    env = server.api_hilo("explorer")["envios"]
    assert len(env) == 1 and env[0].endswith("[direct] tres")


def test_el_sobre_de_start_se_parsea_defensivo_con_wait(server):
    """T5: `agent start` sigue con su `--timeout`; si el sobre trajera un campo de `--wait`, `_start`
    lee con `.get()` y un campo extra no rompe el `listo`. Ningun campo inventado."""
    env = json.loads((FIXTURES / "agent_start.json").read_text(encoding="utf-8"))
    env["matched_status"] = "idle"
    server.script_response(["agent", "start"], json.dumps(env, separators=(",", ":")) + "\n", "", 0)
    assert server.mod._start("tester", "pi", "", "w1:pH", "/tmp", False) == "listo"
    assert server.mod._start("otro", "pi", "", "w1:pH", "/tmp", False) == "listo"
    server.script_response(["agent", "start"], "", "", 0)
    assert server.mod._start("otro2", "pi", "", "w1:pH", "/tmp", False).startswith("ERROR")


def test_start_pide_su_propio_timeout_y_se_mantiene(server):
    """`agent start` sigue pidiendo `--timeout` en ms y su sobre verificado."""
    server.mod._start("tester", "pi", "", "w1:pH", "/tmp", False)
    argv = [c for c in server.calls() if c[:2] == ("agent", "start")][0]
    assert argv[argv.index("--timeout") + 1] == "60000"
    assert "--pane" in argv and "--kind" in argv


def test_claves_del_frontend_de_send_siguen_vivas(server):
    """`index.html` lee `ok`, `confirmed`, `output` y `error`: ninguna se renombra; las nuevas se añaden."""
    code, r = _send(server, text="hola")
    assert {"ok", "confirmed", "output"} <= set(r)
    assert isinstance(r["output"], str)
    raw = (FIXTURES / "error_agent_blocked.json").read_text(encoding="utf-8")
    server.script_response(["agent", "prompt"], "", raw, 1)
    code2, r2 = _send(server, text="sigue", agent="explorer")
    assert {"ok", "confirmed", "error"} <= set(r2) and isinstance(r2["error"], str)


def test_envio_remoto_lleva_machine_y_wait(server):
    """TRIANGULAR: un envio a un agente de maquina remota sigue la ruta multi-máquina y observa la entrega."""
    server.add_agent(name="agy-obrero", kind="agy", status="idle", mach="mac-mini", workspace_id="w9")
    code, r = _send(server, text="sigue", agent="agy-obrero")
    argv = [c for c in server.calls("mac-mini") if c[:2] == ("agent", "prompt")]
    assert len(argv) == 1 and "--wait" in argv[0] and "--timeout" in argv[0]
    assert code == 200 and r["ok"] is True and r["confirmed"] is True
    d = _log(server)[0]
    assert d["outcome"] == "working" and d["agent"] == "agy-obrero" and d["mode"] == "direct"


def test_viaclaude_apunta_a_claude_y_deja_la_huella_del_autor(server):
    """TRIANGULAR: `viaclaude` escribe a claude, pero el log dice quien lo mando y el hilo de claude lo ve."""
    code, r = _send(server, text="OK a B1", agent="explorer", mode="viaclaude")
    argv = _prompts(server)[0]
    assert argv[2] == "claude" and argv[3] == "adrian (oficina): OK a B1"
    assert "--wait" in argv
    d = _log(server)[0]
    assert d["agent"] == "explorer" and d["mode"] == "viaclaude" and d["target"] == "claude"
    assert d["outcome"] == "working" and d["confirmed"] is True
    assert server.api_hilo("claude")["envios"][0].endswith("[viaclaude] OK a B1")
    assert server.api_hilo("explorer")["envios"][0].endswith("[viaclaude] OK a B1")


def test_api_offices_linea_base(server):
    r = server.api_offices()
    assert r["strata"] == "w1"
    assert r["maquinas"] == ["bazzite", "mac-mini", "macbook-air"]
    assert len(r["offices"]) == 1
    w = r["offices"][0]
    assert w["id"] == "w1" and w["nombre"] == "Strata3060" and w["strata"] and w["foco"]
    assert w["needs"] is True and w["tareas"] == 4
    assert w["agentes"] == ["claude", "opencode2", "tester", "explorer", "suplente"]
    calls = [tuple(c[:2]) for c in server.calls()]
    # T6: la fuente es un solo `api snapshot` por maquina, no `workspace list` + `agent list`
    assert ("api", "snapshot") in calls
    assert ("agent", "list") not in calls and ("workspace", "list") not in calls


def test_snapshot_emulado_tiene_la_forma_de_la_captura(server):
    """ANCLA T6: `api snapshot` emulado se puede parse con el mismo codigo que la captura real."""
    snap = server.mod.hj(["api", "snapshot"])["snapshot"]
    real = json.loads((FIXTURES / "api_snapshot.json").read_text(encoding="utf-8"))["result"]["snapshot"]
    assert set(snap) == set(real)
    assert [a.get("name") for a in snap["agents"]] == [a.get("name") for a in real["agents"]]
    assert snap["focused_workspace_id"] == "w1" and snap["version"] == "0.9.3"
    server.set_state("explorer", "blocked")
    snap = server.mod.hj(["api", "snapshot"])["snapshot"]
    assert {a["agent_status"] for a in snap["panes"] if a["pane_id"] == "w1:pM"} == {"blocked"}


def test_api_offices_con_oficina_remota(server):
    server.add_agent(name="agy-obrero", kind="agy", status="blocked", mach="mac-mini", workspace_id="w9")
    r = server.api_offices()
    rem = [o for o in r["offices"] if o["maquina"] == "mac-mini"]
    assert len(rem) == 1
    assert rem[0]["id"] == "mac-mini:w9" and rem[0]["agentes"] == ["agy-obrero"]
    assert rem[0]["strata"] is False and rem[0]["needs"] is False
    assert ("api", "snapshot") in [tuple(c[:2]) for c in server.calls("mac-mini")]   # T6: una sola llamada
    assert ("workspace", "list") not in [tuple(c[:2]) for c in server.calls("mac-mini")]


def test_api_office_validaciones(server):
    """Linea base de T8: las validaciones de `api_office` se resuelven contra `kinds()` emulado."""
    for equipo, error in (([{"perfil": "dev", "kind": "no-kind", "n": 1}], "tipo de agente no permitido"),
                          ([{"perfil": "dev", "kind": "agy", "model": "no-model", "n": 1}],
                           "modelo no disponible para agy"),
                          ([{"perfil": "dev", "kind": "agy", "n": 9}], "de 1 a 4 por perfil")):
        code, r = server.mod.api_office(json.dumps({"name": "x", "equipo": equipo}))
        assert code == 400 and r["error"] == error
    code, r = server.mod.api_office(json.dumps({"name": "x", "equipo": [], "maquina": "no-existe"}))
    assert code == 400 and r["error"] == "máquina desconocida"


def test_api_office_acepta_equipo_agy(server):
    """La unica verificacion viva permitida es con agentes `agy`: el stub la deja preparar."""
    code, r = server.mod.api_office(json.dumps(
        {"name": "prueba agy", "equipo": [{"perfil": "obrero", "kind": "agy", "model": "gemini-pro", "n": 1}]}))
    assert code == 200 and r["ok"] and r["job"]
    j = server.job(r["job"])
    assert j["estado"] == "hecho", j
    prefijos = [c[:2] for c in server.calls()]
    assert ("workspace", "create") in prefijos
    assert ("pane", "split") in prefijos
    assert ("agent", "start") in prefijos


def test_office_delete_protge_la_oficina_strata(server):
    code, r = server.mod.api_office_delete(json.dumps({"id": "w1", "confirm": "Strata3060"}))
    assert code == 403 and r["error"] == "la oficina de Strata no se puede borrar"
    code, r = server.mod.api_office_delete(json.dumps({"id": "w9", "confirm": "w9"}))
    assert code == 404 and r["error"] == "oficina no encontrada"


def test_api_hilo_linea_base(server):
    server.send_log(json.dumps({"ts": "2026-10-06T08:00:00Z", "agent": "claude",
                                "mode": "direct", "text": "revisa la cola"}))
    r = server.api_hilo("claude")
    assert r["envios"] == ["2026-10-06T08:00:00Z [direct] revisa la cola"]
    assert r["menciones"] == ["2026-10-04 4: Tope 3072 por defecto (c41c564)"]


def test_state_ws_remota_conserva_el_bloqueado(server):
    """T2/T8: el estado `blocked` de una maquina remota sobrevive igual que en la local."""
    server.add_agent(name="agy-obrero", kind="agy", status="blocked", mach="mac-mini", workspace_id="w9")
    ag = _agentes(server.state_ws("mac-mini:w9"))
    assert ag["agy-obrero"]["status"] == "blocked" and ag["agy-obrero"]["activity"] == "esperando confirmación"
    assert ag["agy-obrero"]["agent"] == "agy" and ag["agy-obrero"]["pane_id"] == "w9:p19"
    assert server.valid_agent("agy-obrero") is True


def test_tester_usa_el_estado_de_herdr(server):
    """T2: `tester` en `agent list` manda su estado real; el lock del motor queda solo como actividad."""
    server.lock("progreso = 42%")
    server.set_state("tester", "blocked")
    ag = _agentes(server.build_state())
    assert ag["tester"]["status"] == "blocked" and ag["tester"]["activity"] == "progreso=42%"


def test_tester_sin_herdr_usa_el_lock(server):
    """T2: si `tester` no esta en `agent list`, el lock del motor sigue mandando (comportamiento de siempre)."""
    server.lock("progreso = 42%")
    server.stub.agents[None] = [a for a in server.stub.agents[None] if a.get("name") != "tester"]
    ag = _agentes(server.build_state())
    assert ag["tester"]["status"] == "working" and ag["tester"]["activity"] == "progreso=42%"
    assert "tester" not in server.mod.agmap()


def test_herdr_no_responde_no_reventa(server):
    """T2 con la fuente vacia (el snapshot no responde): los cinco de siempre siguen en reposo y los
    campos de Herdr son `None`. El estado ausente no es un estado desconocido: no hay agente."""
    _script_error(server, "usage", "stub sin respuesta", ["api", "snapshot"], cid="cli:api:snapshot")
    st = server.build_state()
    assert st["herdr"] is False
    ag = _agentes(st)
    assert list(ag) == ["claude", "opencode2", "tester", "explorer", "suplente"]
    assert all(a["status"] == "idle" for a in ag.values())
    assert ag["opencode2"]["activity"] == "opencode2 (yo) espera OK de Adrián"   # viene del CHANGELOG
    assert ag["claude"]["activity"] == "supervisando" and ag["tester"]["activity"] == "libre"
    assert ag["explorer"]["activity"] == "investigando" and ag["suplente"]["activity"] == "en reposo"
    assert all(all(a[k] is None for k in CAMPOS_T2 | {"agent"}) for a in ag.values())


def test_obrero_bloqueado_sobrevive_y_es_valido(server):
    """T2: un obrero de contrato (no FIXED) pasa igual, y `LAST_AGENTS` lo sigue viendo para enviar."""
    server.add_agent(name="obrero-C40", kind="opencode", status="blocked", workspace_id="w1")
    ag = _agentes(server.build_state())
    assert ag["obrero-C40"]["status"] == "blocked" and ag["obrero-C40"]["role"] == "Contrato obrero-C40"
    assert ag["obrero-C40"]["completion_seq"] is None and ag["obrero-C40"]["agent"] == "opencode"
    assert "obrero-C40" in server.mod.LAST_AGENTS and server.valid_agent("obrero-C40") is True


def test_state_ws_con_varios_estados(server):
    """T2: en una misma oficina pueden convivir los cinco estados, y cada actividad es la de su estado."""
    server.set_state("explorer", "blocked"); server.set_state("tester", "done")
    server.set_state("suplente", "working"); server.stub.find("claude")["agent_status"] = "weird"
    ag = _agentes(server.state_ws("w1"))
    assert {n: ag[n]["status"] for n in ("explorer", "tester", "suplente", "claude", "opencode2")} == {
        "explorer": "blocked", "tester": "done", "suplente": "working", "claude": "unknown",
        "opencode2": "idle"}
    assert all(a["activity"] == server.mod.ACTS[a["status"]] for a in ag.values())


def test_claves_de_siempre_y_payload_serializable(server):
    """T2 no renombra nada: `index.html` lee name/status/activity/role/color/since/ts y el payload sigue siendo JSON."""
    st = server.get_state()
    assert set(st) == {"agents", "metrics", "ticker", "queue", "lock", "bench", "suplencia", "herdr",
                       "tareas", "updated", "interval"}
    for a in st["agents"]:
        assert {"name", "status", "activity", "color", "role", "since", "ts"} <= set(a)
    d = json.loads(json.dumps(st, ensure_ascii=False))
    assert d == st
    ws = server.state_ws("w1")
    assert set(ws) == {"agents", "metrics", "ticker", "queue", "lock", "bench", "suplencia", "herdr",
                       "tareas", "ws", "maquina", "updated"}
    assert json.loads(json.dumps(ws, ensure_ascii=False)) == ws


def test_agente_sin_name_remota_se_omite(server):
    """T2: el caso del agente sin `name` existe tambien en maquinas remotas y se omite igual."""
    base = {"focused": False, "workspace_id": "w9", "tab_id": "w9:t1", "terminal_id": "t1",
            "terminal_title": "x", "terminal_title_stripped": "x", "cwd": "/x", "foreground_cwd": "/x",
            "revision": 1, "agent": "agy"}
    server.set_remote_agents("mac-mini", [dict(base, name="agy-ok", agent_status="idle", pane_id="w9:p1"),
                                          dict(base, agent_status="blocked", pane_id="w9:p2")])
    assert [a["name"] for a in server.state_ws("mac-mini:w9")["agents"]] == ["agy-ok"]
    rem = [o for o in server.api_offices()["offices"] if o["maquina"] == "mac-mini"][0]
    assert rem["agentes"] == ["agy-ok"]


def test_api_offices_remota_vacia(server):
    server.set_remote_agents("mac-mini", [])
    r = server.api_offices()
    assert [o["maquina"] for o in r["offices"]] == ["bazzite"]
    assert len(_snaps(server, "mac-mini")) == 1          # T6: la remota se sondea una sola vez
    assert ("workspace", "list") not in [tuple(c[:2]) for c in server.calls("mac-mini")]


# ---------- T6: un solo `api snapshot` por ciclo de estado ----------

def test_snapshot_trae_las_mismas_agentes_que_agent_list(server):
    """La captura verbatim de `api snapshot` (2026-10-06, 6.6 KB, rc=0) trae `agents` IDENTICAS a las de
    `agent list`: la oficina cambia la fuente sin cambiar el payload."""
    ags = json.loads((FIXTURES / "agent_list.json").read_text(encoding="utf-8"))["result"]["agents"]
    snap = json.loads((FIXTURES / "api_snapshot.json").read_text(encoding="utf-8"))["result"]["snapshot"]
    assert snap["agents"] == ags
    assert snap["workspaces"] == json.loads(
        (FIXTURES / "workspace_list.json").read_text(encoding="utf-8"))["result"]["workspaces"]
    r = server.mod.hj(["api", "snapshot"])
    assert r["snapshot"]["agents"] == server.stub.agents[None]
    assert [a.get("name") for a in r["snapshot"]["agents"]] == [a.get("name") for a in ags]


def test_el_payload_de_state_es_el_mismo_con_snapshot(server):
    """T6: el payload de `build_state` sale del snapshot y conserva las anclas de T2/T3: mismas claves,
    los mismos 5 estados, los mismos campos de Herdr por agente."""
    ags = json.loads((FIXTURES / "agent_list.json").read_text(encoding="utf-8"))["result"]["agents"]
    by = {a["name"]: a for a in ags if a.get("name")}
    st = server.build_state()
    assert [a["name"] for a in st["agents"]] == ["claude", "opencode2", "tester", "explorer", "suplente"]
    assert st["herdr"] is True
    for a in st["agents"]:
        src = by[a["name"]]
        assert a["status"] == server.mod.estado_real(src["agent_status"])
        assert all(a[k] == src.get(k) for k in CAMPOS_T2 | {"agent"})
        assert {"name", "status", "activity", "color", "role", "since", "ts"} <= set(a)
    assert server.calls() == [("api", "snapshot")]   # una sola llamada, la nueva fuente


def test_snapshot_bloqueado_llega_al_payload(server):
    """T23 fijó `api_snapshot_blocked.json` (agy `explorer` blocked): la fuente snapshot sostiene el
    contrato de T2 sin tocar `mkagent`, y el ciclo sigue siendo una sola llamada."""
    server.load("api_snapshot_blocked.json")
    ag = _agentes(server.build_state())
    assert ag["explorer"]["status"] == "blocked" and ag["explorer"]["activity"] == "investigando"
    assert ag["explorer"]["pane_id"] == "w1:pM" and ag["explorer"]["agent"] == "agy"
    assert ag["explorer"]["interactive_ready"] is True and ag["explorer"]["completion_seq"] == 1100
    assert server.calls() == [("api", "snapshot")]


def test_el_snapshot_del_ciclo_se_reutiliza_en_offices(server):
    """`api_offices` lee el MISMO snapshot que construyó el estado: no vuelve a lanzar un subproceso local."""
    st = server.get_state()
    r = server.api_offices()
    assert st["interval"] == 5 and r["strata"] == "w1" and len(r["offices"]) == 1
    assert len(_snaps(server, None)) == 1                              # un solo subproceso local
    prefijos = [c[1][:2] for c in _herdr(server)]
    assert ("agent", "list") not in prefijos and ("workspace", "list") not in prefijos


def test_api_offices_local_y_una_remota_dos_snapshots(server):
    """T6: local + 1 target remoto = 2 snapshots (uno por target), no `workspace list` + `agent list` por
    target. El regimen por ciclo es lo que T9 necesita para pasar a eventos."""
    _solo_mac_mini(server)
    server.add_agent(name="agy-obrero", kind="agy", status="blocked", mach="mac-mini", workspace_id="w9")
    r = server.api_offices()
    snaps = _snaps(server)
    assert len(snaps) == 2
    assert [m for m, a in server.stub.calls if tuple(a[:2]) == ("api", "snapshot")] == [None, "mac-mini"]
    prefijos = [c[1][:2] for c in _herdr(server)]
    assert ("workspace", "list") not in prefijos and ("agent", "list") not in prefijos
    assert r["maquinas"] == ["bazzite", "mac-mini"]
    assert [(o["id"], o["agentes"]) for o in r["offices"]] == [
        ("w1", ["claude", "opencode2", "tester", "explorer", "suplente"]), ("mac-mini:w9", ["agy-obrero"])]
    assert r["offices"][1]["strata"] is False and r["offices"][1]["foco"] is False


def test_oficina_remota_sin_agentes_y_un_workspace(server):
    """Forma capturada en vivo para mac-mini (2026-10-06): 1 workspace `w8` label `~`, 1 pane, 0 agentes.
    La oficina se renderia vacia, igual que hoy con `workspace list`."""
    _solo_mac_mini(server)
    server.set_remote_agents("mac-mini", [])
    server.stub.workspaces["mac-mini"] = [{"active_tab_id": "w8:t1", "agent_status": "idle", "focused": False,
                                           "label": "~", "number": 8, "pane_count": 1, "tab_count": 1,
                                           "workspace_id": "w8"}]
    r = server.api_offices()
    assert len(_snaps(server, None)) == 1 and len(_snaps(server, "mac-mini")) == 1
    rem = [o for o in r["offices"] if o["maquina"] == "mac-mini"]
    assert len(rem) == 1
    assert rem[0]["id"] == "mac-mini:w8" and rem[0]["nombre"] == "~"
    assert rem[0]["agentes"] == [] and rem[0]["tareas"] == 0 and rem[0]["needs"] is False
    assert rem[0]["strata"] is False and rem[0]["foco"] is False


def test_snapshot_vacio_degrada_como_herdr_no_responde(server):
    """Snapshot con 0 agentes y 0 workspaces: `herdr` False, los cinco en reposo, `api_offices` sin oficinas.
    No reventa: los campos de Herdr son `None` y no hay crash."""
    server.stub.agents[None] = []; server.stub.workspaces[None] = []
    st = server.build_state()
    assert st["herdr"] is False
    assert [a["name"] for a in st["agents"]] == ["claude", "opencode2", "tester", "explorer", "suplente"]
    assert all(a["status"] == "idle" for a in st["agents"])
    assert all(all(a[k] is None for k in CAMPOS_T2 | {"agent"}) for a in st["agents"])
    r = server.api_offices()
    assert r["offices"] == [] and r["strata"] == "w1"
    assert len(_snaps(server, None)) == 1     # la degradacion no repite el subproceso


def test_snapshot_con_campos_ausentes_se_lee_defensivo(server):
    """Un snapshot cuyo agente no trae `name`, no trae `interactive_ready`, y otro con estado fuera de
    dominio: el comportamiento es el de T2/T5, sin crash y sin inventar campos."""
    a = server.add_agent(name="agy-sin-flags", kind="agy", status="idle", workspace_id="w1")
    del a["interactive_ready"]
    b = server.add_agent(name="agy-futuro", kind="agy", status="idle", workspace_id="w1")
    b["agent_status"] = "waiting"
    c = server.add_agent(name="agy-sin-name", kind="agy", status="idle", workspace_id="w1")
    del c["name"]
    ag = _agentes(server.build_state())
    assert ag["agy-futuro"]["status"] == "unknown"   # actividad: la del CHANGELOG (T2), no la del estado
    assert ag["agy-sin-flags"]["interactive_ready"] is None and ag["agy-sin-flags"]["status"] == "idle"
    assert all(x.get("name") for x in ag.values()) and "agy-sin-name" not in ag
    assert "agy-sin-name" not in server.mod.LAST_AGENTS
    # T5: `interactive_ready` ausente es `None` (no observamos), no `False`: no penaliza el plazo
    code, r = _send(server, text="hola", agent="agy-sin-flags")
    argv = _prompts(server)[-1]
    assert argv[2] == "agy-sin-flags" and "--wait" in argv
    assert _ms(argv) == server.mod.send_timeout(len("adrian: hola"), True) == 5300
    assert code == 200 and r["confirmed"] is True


def test_snapshot_con_ready_false_pide_mas_plazo(server):
    """El campo opcional llega del snapshot y sigue mandando el plazo de `agent prompt --wait` (T5)."""
    a = server.add_agent(name="agy-panel", kind="agy", status="blocked", workspace_id="w1")
    a["interactive_ready"] = False
    code, r = _send(server, text="hola", agent="agy-panel")
    assert _ms(_prompts(server)[-1]) == server.mod.send_timeout(len("adrian: hola"), False) == 9300
    assert code == 200 and r["confirmed"] is True


def test_raw_agents_y_agmap_salen_del_snapshot(server):
    """`raw_agents`, `agmap` y `state_ws` leen la misma fuente: el snapshot por maquina."""
    server.add_agent(name="agy-obrero", kind="agy", status="blocked", mach="mac-mini", workspace_id="w9")
    assert [a.get("name") for a in server.mod.raw_agents()] == [a.get("name") for a in server.stub.agents[None]]
    assert server.mod.agmap()["agy-obrero"] == "mac-mini"
    assert server.mod.agmap()["explorer"] is None
    prefijos = [c[1][:2] for c in _herdr(server)]
    assert prefijos.count(("api", "snapshot")) == 3          # local, mac-mini, macbook-air: una por target
    assert ("agent", "list") not in prefijos
    assert _agentes(server.state_ws("mac-mini:w9"))["agy-obrero"]["status"] == "blocked"


def test_office_create_invalida_el_snapshot(server):
    """T6: la oficina creada por la propia oficina tiene que aparecer en la siguiente lectura de
    `/api/offices`, no quedar cacheada hasta el proximo ciclo (el cache nuevo no puede robar una lectura)."""
    server.get_state()                                   # ciclo 1: snapshot local cacheado
    assert len(_snaps(server, None)) == 1
    code, r = server.mod.api_office(json.dumps(
        {"name": "prueba agy", "equipo": [{"perfil": "obrero", "kind": "agy", "model": "gemini-pro", "n": 1}]}))
    j = server.job(r["job"])
    offs = [o["id"] for o in server.api_offices()["offices"]]
    assert j["estado"] == "hecho" and j["ws"] in offs
    assert len(_snaps(server, None)) == 2                # se volvio a sondear: el cache se invalido al crear


def test_office_delete_invalida_el_snapshot(server):
    """T6: una oficina borrada desaparece de `/api/offices` en la siguiente lectura, no 5 s despues.
    El snapshot remoto queda cacheado por la primera lectura, asi que el borrado tiene que invalidarlo."""
    server.add_agent(name="agy-obrero", kind="agy", status="idle", mach="mac-mini", workspace_id="w9")
    assert [o["id"] for o in server.api_offices()["offices"]] == ["w1", "mac-mini:w9"]
    code, r = server.mod.api_office_delete(json.dumps({"id": "mac-mini:w9", "confirm": "w9"}))
    assert code == 200 and r["ok"] is True
    offs = [o["id"] for o in server.api_offices()["offices"]]
    assert "mac-mini:w9" not in offs
    assert len(_snaps(server, "mac-mini")) == 2          # la remota se sondeo de nuevo tras borrar
    assert len(_snaps(server, None)) == 1                # y el ciclo local sigue siendo una sola llamada


def test_kinds_local_no_lanza_binarios(server):
    k = server.kinds()
    assert set(k) == {"claude", "opencode", "pi", "ada-cli", "agy"}
    assert k["pi"]["modelos"] == ["openai/gpt-4.1", "anthropic/claude-sonnet"]
    assert k["agy"]["modelos"] == ["gemini-pro", "claude-sonnet"]
    assert server.unknown() == []


def test_kinds_remota_sin_agentes(server):
    assert server.kinds("mac-mini") == {}


def test_get_state_cache_e_intervalo(server):
    a = server.get_state()
    assert a["interval"] == 5 and server.get_state() is a
    server.bench(True)
    server.mod.CACHE.update(data=None, at=0.0)
    assert server.get_state()["interval"] == 30
    server.bench(False)


def test_seguridad_de_sesion_aislada(server):
    assert server.pw_ok(server.password) is True
    assert server.pw_ok("mala") is False
    sid = server.session()
    assert server.mod.SESS[sid] > 0 and server.mod.SESSF.startswith(str(server.tmp))


def test_stub_no_lanza_binarios_real(server):
    server.build_state(); server.api_offices(); server.api_log("claude"); server.kinds()
    assert server.unknown() == []
    assert server.executables() == ["ada-cli", "agy", "herdr", "opencode", "pi"]


def test_stub_script_error_llega_como_sobre_verificado(server):
    """T4: el defecto latente del stub esta arreglado. `script_error` (la API del stub) produce
    stdout vacio, JSON en stderr y exit 1, y el servidor lo parsea como sobre verificado.
    Antes de la correccion, `_herdr` indexaba el dict como `scripted[0]` -> KeyError, que el
    servidor traga como salida vacia: la evidencia de `herdr no responde` salia de un KeyError,
    no de la forma capturada del binario 0.9.3."""
    server.script_error("agent_blocked", "pendiente de aprobacion", ["agent", "prompt"],
                        cid="cli:agent:prompt")
    r = server.mod.herdr_cmd(["agent", "prompt", "explorer", "hola"])
    assert r["out"] == "" and r["rc"] == 1
    assert r["code"] == "agent_blocked" and "pendiente de aprobacion" in r["msg"]
    assert r["dead"] is False   # socket muerto es stdout y stderr vacios; aqui stderr trae el sobre


def test_office_delete_no_quita_la_meta_si_herdr_fallo(server):
    """T4: borrar no puede dejar la oficina sin `cwd` y `perfiles` cuando herdr no cerro nada."""
    server.add_agent(name="agy-obrero", kind="agy", status="idle", mach="mac-mini", workspace_id="w9")
    oid = "mac-mini:w9"
    server.mod.save_meta({oid: {"nombre": "Ofi9", "cwd": "/Users/x/of9", "maquina": "mac-mini",
                                "agentes": [{"name": "agy-obrero", "rol": "Dev", "kind": "agy", "model": ""}]}})
    _script_error(server, "workspace_not_found", "no workspace w9", ["workspace", "close"],
                  mach="mac-mini", cid="cli:workspace:close")
    code, r = server.mod.api_office_delete(json.dumps({"id": oid, "confirm": "w9"}))
    assert code == 502
    assert oid in server.mod.meta(), "la meta se quito aunque herdr no cerro el workspace"
    assert server.mod.meta()[oid].get("cwd") == "/Users/x/of9"
