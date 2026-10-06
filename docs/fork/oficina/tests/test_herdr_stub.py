"""Suite de la oficina con Herdr emulado a nivel argv: sin GPU, sin Herdr vivo, sin red.

El proposito es doble:
  1. Probar que el stub reproduce las formas que `server.py` parsea HOY. Un run verde hoy es la
     linea base que T2 tiene que mantener verde.
  2. Dejar anclas visibles para el RED->GREEN de las tareas T2..T6: `test_mkagent_colapsa`,
     `test_build_state_pierde_el_bloqueado`, `test_t2_objetivo_el_bloqueado_sobrevive` (xfail strict),
     `test_herdr_out_solo_stdout`, `test_send_no_observa_la_entrega` y
     `test_build_state_no_pide_snapshot`.

Las formas verificadas contra el binario herdr 0.9.3 estan en `fixtures/` (ver README.md).
"""
from __future__ import annotations
import json
import pytest
from stub_herdr import FIXTURES, STATUSES

CAMPOS_AGENTE = {"agent", "agent_status", "completion_seq", "cwd", "focused", "foreground_cwd",
                 "name", "pane_id", "revision", "state_change_seq", "tab_id", "terminal_id",
                 "terminal_title", "terminal_title_stripped", "workspace_id"}
CAMPOS_T2 = {"pane_id", "focused", "interactive_ready", "completion_seq", "state_change_seq"}


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


@pytest.mark.xfail(strict=True, reason="T2: mkagent debe dejar pasar el estado real")
def test_t2_objetivo_el_bloqueado_sobrevive(server):
    """Especie viva de T2: hoy falla (el xfail strict lo registra); T2 tiene que dejarlo verde."""
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
                                                 "suplente", "?"]
    assert st["metrics"]["base"]["B1"] == 50.7
    assert st["tareas"] and st["ticker"][-1] == "COLA 3/9"


def test_build_state_no_pide_snapshot(server):
    server.build_state()
    assert server.calls() == [("agent", "list")]
    assert ("api", "snapshot") not in server.calls()


def test_mkagent_colapsa(server):
    """ANCLA T2: hoy solo `working` sobrevive. T2 tiene que cambiar estas 3 filas a su estado real."""
    for status, esperado in [("working", "working"), ("idle", "idle"), ("blocked", "idle"),
                             ("done", "idle"), ("unknown", "idle")]:
        assert server.mkagent("tester", status)["status"] == esperado


def test_build_state_pierde_el_bloqueado(server):
    """ANCLA T2: el fixture sintetico `blocked` (agy explorer) se pinta inactivo."""
    server.set_state("explorer", "blocked")
    ag = _agentes(server.build_state())
    assert ag["explorer"]["status"] == "idle"
    assert ag["explorer"]["activity"] == "investigando"


def test_state_ws_contradice_estado_y_actividad(server):
    """ANCLA T2: `state_ws` sabe que esta bloqueado (actividad) y lo declara inactivo (status)."""
    server.set_state("explorer", "blocked")
    ag = _agentes(server.state_ws("w1"))
    assert ag["explorer"]["status"] == "idle" and ag["explorer"]["activity"] == "esperando confirmación"
    assert all(a["status"] in ("idle", "working") for a in ag.values())


def test_captura_no_tiene_bloqueados_y_el_fixture_sintetico_si(server):
    idle = json.loads((FIXTURES / "agent_list.json").read_text(encoding="utf-8"))["result"]["agents"]
    assert all(a["agent_status"] == "idle" for a in idle)
    bloq = json.loads((FIXTURES / "agent_list_blocked.json").read_text(encoding="utf-8"))["result"]["agents"]
    assert [(a.get("name"), a["agent_status"]) for a in bloq if a.get("agent_status") == "blocked"] == [("explorer", "blocked")]
    server.load("agent_list_blocked.json")
    assert _agentes(server.build_state())["explorer"]["status"] == "idle"   # sigue colapsado


def test_agent_list_puede_no_tener_name(server):
    """La captura real trae un agente SIN `name`: server.py:74 lo convierte en "?".

    `state_ws` (server.py:197) si exige `a["name"]`; `build_state` lo tolera y `valid_agent`
    nunca aceptara "?". El stub reproduce el caso para que T2 lo trate, no para esconderlo.
    """
    st = server.build_state()
    assert "?" in [a["name"] for a in st["agents"]]
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
    """ANCLA T4: el error de herdr esta en stderr y `herdr_out` solo devuelve stdout -> ""."""
    server.script_error("agent_name_not_found", "no agent named explorer", ["agent", "read"],
                        cid="cli:agent:read")
    assert server.api_log("explorer") == {"agent": "explorer", "error": "herdr no responde"}


def test_herdr_out_solo_stdout(server):
    server.script_error("agent_blocked", "pendiente de aprobacion", ["agent", "prompt"],
                        cid="cli:agent:prompt")
    assert server.mod.herdr_out(["agent", "prompt", "explorer", "hola"], 30) == ""


def test_checks_de_substring_de_start_estan_muertos(server):
    """ANCLA T4: con el error en stderr, `server.py:255-256` no ve nunca `agent_not_ready`."""
    server.script_error("agent_not_ready", "panel sin prompt interactivo", ["agent", "start"],
                        cid="cli:agent:start")
    assert server.mod._start("tester", "pi", "", "w1:pH", "/tmp", False) == "ERROR herdr no respondió"
    err = json.loads((FIXTURES / "error_agent_not_ready.json").read_text(encoding="utf-8"))
    assert err["error"]["code"] == "agent_not_ready"      # el codigo real vive solo en stderr
    assert '"agent_started"' not in json.dumps(err, separators=(",", ":"))


def test_start_listo_cuando_el_stub_devuelve_agent_started(server):
    assert server.mod._start("tester", "pi", "", "w1:pH", "/tmp", False) == "listo"
    assert '"interactive_ready":true' in json.dumps(
        json.loads((FIXTURES / "agent_start.json").read_text(encoding="utf-8")),
        separators=(",", ":"))


def test_send_no_observa_la_entrega(server):
    """ANCLA T5: sin `--wait` la entrega se confirma por texto en stdout, no por estado observado."""
    code, r = server.mod.api_send(json.dumps({"agent": "claude", "mode": "direct", "text": "hola"}))
    assert code == 200 and r["confirmed"] is True
    prompt = server.calls()[-1]
    assert prompt == ("agent", "prompt", "claude", "adrian: hola")
    assert "--wait" not in prompt   # server.py:346 lanza el prompt sin esperar


def test_send_a_bloqueado_da_el_mensaje_actual(server):
    """ANCLA T5: `agent_blocked` llega como 502 "herdr no responde", no como mensaje legible."""
    server.script_error("agent_blocked", "pendiente de aprobacion", ["agent", "prompt"])
    code, r = server.mod.api_send(json.dumps({"agent": "explorer", "mode": "direct", "text": "sigue"}))
    assert code == 502 and r == {"ok": False, "error": "herdr no responde"}


def test_api_offices_linea_base(server):
    r = server.api_offices()
    assert r["strata"] == "w1"
    assert r["maquinas"] == ["bazzite", "mac-mini", "macbook-air"]
    assert len(r["offices"]) == 1
    w = r["offices"][0]
    assert w["id"] == "w1" and w["nombre"] == "Strata3060" and w["strata"] and w["foco"]
    assert w["needs"] is True and w["tareas"] == 4
    assert w["agentes"] == ["claude", "opencode2", "tester", "explorer", "suplente", "?"]
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


def test_state_ws_remota_colapsa_igual(server):
    """ANCLA T2/T8: el estado `blocked` de una maquina remota se pierde igual que en la local."""
    server.add_agent(name="agy-obrero", kind="agy", status="blocked", mach="mac-mini", workspace_id="w9")
    ag = _agentes(server.state_ws("mac-mini:w9"))
    assert ag["agy-obrero"]["status"] == "idle" and ag["agy-obrero"]["activity"] == "esperando confirmación"
    assert server.valid_agent("agy-obrero") is True


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
