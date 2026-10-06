"""Suite de la oficina con Herdr emulado a nivel argv: sin GPU, sin Herdr vivo, sin red.

El proposito es doble:
  1. Probar que el stub reproduce las formas que `server.py` parsea. Un run verde es la linea base
     que cada tarea tiene que mantener verde.
  2. Dejar anclas visibles para el RED->GREEN de las tareas T2..T6: despues de T2 quedan
     `test_herdr_out_solo_stdout` (linea base de T4: `herdr_out` sigue viendo solo stdout),
     `test_send_no_observa_la_entrega` y `test_build_state_no_pide_snapshot` (lineas base).
     T4 volvio verdes sus tres anclas (`test_start_parsea_el_error_de_stderr`,
     `test_api_log_sin_respuesta_es_el_error_actual`, `test_send_a_bloqueado_es_legible`) y anadio
     la seccion `T4: el error de herdr vive en stderr`.
     Quedan anclas de T5 (`test_send_no_observa_la_entrega`) y T6
     (`test_snapshot_emulado_tiene_la_forma_de_la_captura`, `test_build_state_no_pide_snapshot`,
     `test_api_offices_linea_base`).

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


def test_build_state_no_pide_snapshot(server):
    server.build_state()
    assert server.calls() == [("agent", "list")]
    assert ("api", "snapshot") not in server.calls()


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


def test_send_no_observa_la_entrega(server):
    """ANCLA T5: sin `--wait` la entrega se confirma por texto en stdout, no por estado observado."""
    code, r = server.mod.api_send(json.dumps({"agent": "claude", "mode": "direct", "text": "hola"}))
    assert code == 200 and r["confirmed"] is True
    prompt = server.calls()[-1]
    assert prompt == ("agent", "prompt", "claude", "adrian: hola")
    assert "--wait" not in prompt   # server.py:346 lanza el prompt sin esperar


# ---------- T4: stdout, stderr y exit status ----------

def test_herdr_cmd_captura_los_tres_canales(server):
    """El helper ve lo que `herdr_out` tiraba: el JSON de `stderr` y el exit 1 verificados."""
    raw = (FIXTURES / "error_agent_blocked.json").read_text(encoding="utf-8")
    server.script_response(["agent", "prompt"], "", raw, 1)
    r = server.mod.herdr_cmd(["agent", "prompt", "explorer", "hola"], 30)
    assert r["out"] == "" and r["rc"] == 1
    assert r["code"] == "agent_blocked" and r["msg"].startswith("agent is blocked")
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
        code, r = server.mod.api_send(json.dumps({"agent": "explorer", "mode": "direct", "text": "sigue"}))
        assert code == 502 and r["code"] == err["code"]
        assert server.mod.HERDR_MSG[err["code"]] in r["error"] and err["message"] in r["error"]
        assert r["error"] != "herdr no responde"
        assert server.api_hilo("explorer")["envios"] == []   # ninguno queda registrado como enviado


def test_codigo_desconocido_muestra_el_codigo(server):
    """Un codigo de un herdr futuro se ve, no se traga."""
    m = server.mod.herdr_msg("pane_locked", "pane is locked")
    assert "pane_locked" in m and "pane is locked" in m
    assert server.mod.herdr_msg(None, "") == "respuesta de herdr sin sobre JSON"


def test_socket_muerto_se_distingue_de_un_error_de_agente(server):
    server.script_response(["agent", "prompt"], "", "", 1)
    code, r = server.mod.api_send(json.dumps({"agent": "explorer", "mode": "direct", "text": "sigue"}))
    assert code == 502 and r["code"] == "no_response"
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
    server.script_response(["agent", "list"], "herdr: socket closed\n", "", 1)
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
    server.script_response(["agent", "prompt"], (FIXTURES / "agent_prompt.json").read_text(encoding="utf-8"), "", 0)
    code, r = server.mod.api_send(json.dumps({"agent": "claude", "mode": "direct", "text": "hola"}))
    assert code == 200 and r["confirmed"] is True
    assert json.loads(r["output"])["type"] == "agent_prompted"


def test_exit0_sin_sobre_no_confirma_el_envio(server):
    server.script_response(["agent", "prompt"], "", "", 0)
    code, r = server.mod.api_send(json.dumps({"agent": "explorer", "mode": "direct", "text": "x"}))
    assert code == 502 and r["code"] == "no_response"
    assert Path(server.mod.ENVLOG).exists() is False   # nada se registra como enviado


def test_envio_exitoso_deja_la_huella_en_envios_log(server):
    code, r = server.mod.api_send(json.dumps({"agent": "claude", "mode": "direct", "text": "revisa la cola"}))
    assert code == 200 and r["confirmed"] is True
    d = json.loads(Path(server.mod.ENVLOG).read_text(encoding="utf-8").strip())
    assert d["agent"] == "claude" and d["mode"] == "direct" and d["text"] == "revisa la cola"


def test_limite_de_3s_y_huella_se_mantienen(server):
    code, r = server.mod.api_send(json.dumps({"agent": "claude", "mode": "direct", "text": "primero"}))
    assert code == 200 and r["confirmed"] is True
    code2, r2 = server.mod.api_send(json.dumps({"agent": "claude", "mode": "direct", "text": "segundo"}))
    assert code2 == 429 and r2["error"] == "limite 1 envio/3s"
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
    code, r = server.mod.api_send(json.dumps({"agent": "explorer", "mode": "direct", "text": "sigue"}))
    assert code == 502
    assert r["code"] == "agent_blocked"                       # el codigo real, para el log
    assert "aprobación" in r["error"] and "agent is blocked" in r["error"]
    assert r["error"] != "herdr no responde"
    assert server.api_hilo("explorer")["envios"] == []        # no queda como enviado


def test_api_offices_linea_base(server):
    r = server.api_offices()
    assert r["strata"] == "w1"
    assert r["maquinas"] == ["bazzite", "mac-mini", "macbook-air"]
    assert len(r["offices"]) == 1
    w = r["offices"][0]
    assert w["id"] == "w1" and w["nombre"] == "Strata3060" and w["strata"] and w["foco"]
    assert w["needs"] is True and w["tareas"] == 4
    assert w["agentes"] == ["claude", "opencode2", "tester", "explorer", "suplente"]
    calls = server.calls()
    assert ("agent", "list") in calls and ("workspace", "list") in calls   # dos llamadas separadas


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
    assert ("workspace", "list") in [tuple(c[:2]) for c in server.calls("mac-mini")]


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
    """T2 con `agent list` vacio (herdr_out == ""): los cinco de siempre siguen en reposo y los
    campos de Herdr son `None`. El estado ausente no es un estado desconocido: no hay agente."""
    _script_error(server, "usage", "stub sin respuesta", ["agent", "list"], cid="cli:agent:list")
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
    assert ("workspace", "list") in [tuple(c[:2]) for c in server.calls("mac-mini")]


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
