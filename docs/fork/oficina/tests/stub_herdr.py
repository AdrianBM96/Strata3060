"""Emulador de `herdr` 0.9.3 a nivel argv: la oficina se prueba sin Herdr vivo, sin GPU y sin red.

Las formas JSON son las capturadas del binario real (ver `fixtures/` y README.md):
`herdr_out` (`server.py:63`) ve solo `stdout`; los errores van a `stderr` con exit 1 como
`{"id":...,"error":{"code":...,"message":...}}`; el JSON real va compacto, sin espacios
(eso es lo que hace que el literal `"interactive_ready":true` de `server.py:255` coincida).

El stub registra cada invocation en `calls` y cualquier binario no emulado en `unknown`
(que los tests exigen vacio), de modo que nunca se lanza un proceso real.
"""
from __future__ import annotations
import copy, json, subprocess
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
STATUSES = ("idle", "working", "blocked", "done", "unknown")
LOCAL = ("tailscale", "ssh", "opencode", "pi", "ada-cli", "agy", "nvidia-smi")
SCROLL = {"max_offset_from_bottom": 0, "offset_from_bottom": 0, "viewport_rows": 24}


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _dump(obj):
    return json.dumps(obj, separators=(",", ":")) + "\n"


def _cp(args, stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess(args=args, returncode=rc, stdout=stdout, stderr=stderr)


class HerdrStub:
    def __init__(self, fixtures=FIXTURES, home="/home/oficina-test"):
        self.fixtures = Path(fixtures)
        self.home = home
        self.calls = []      # (machine, args) en orden
        self.exes = []       # argv[0] de cada invocation registrada
        self.unknown = []    # binarios no emulados: deben quedar vacios
        self.agents = {None: copy.deepcopy(_load("agent_list.json")["result"]["agents"])}
        self.workspaces = {None: copy.deepcopy(_load("workspace_list.json")["result"]["workspaces"])}
        self.tabs = {None: copy.deepcopy(_load("api_snapshot.json")["result"]["snapshot"]["tabs"])}
        self.layouts = {None: copy.deepcopy(_load("api_snapshot.json")["result"]["snapshot"]["layouts"])}
        self.machines = copy.deepcopy(_load("machine_list.json"))
        self.scroll = {p["pane_id"]: p["scroll"] for p in _load("pane_list.json")["result"]["panes"]}
        self.logs = {}       # nombre -> lineas que devuelve `agent read`
        self._errors = []    # [(match, machine, payload)]
        self._seq = 1100     # por encima del mayor state_change_seq capturado (1086)
        self._pane = 18      # w1:pR es el ultimo pane_id de la captura
        self._ws = 2         # w1 existe; el workspace creado despues es w2

    # ---------- interfaz de subprocess ----------
    def run(self, argv, **kw):
        argv = list(argv)
        exe = argv[0]
        self.exes.append(exe)
        if exe == "herdr":
            # `herdr_out` construye ["herdr"] + ["--machine", mach] + args (server.py:63)
            mach, args = ((argv[2], argv[3:]) if len(argv) > 3 and argv[1] == "--machine"
                          else (None, argv[1:]))
            self.calls.append((mach, args))
            return self._herdr(mach, args)
        if exe in LOCAL:
            self.calls.append((None, argv))
            return self._local(argv)
        self.unknown.append(argv)
        return _cp(argv, "", "stub: binario no emulado: %s\n" % " ".join(argv), 1)

    def commands(self, mach=None):
        """Comandos herdr ejecutados (sin la bandera --machine) para aserciones de regimen."""
        return [tuple(a) for m, a in self.calls if a and m == mach]

    def executables(self):
        return sorted(set(self.exes))

    # ---------- estado controlable por test ----------
    def set_agent_state(self, name, status, mach=None):
        if status not in STATUSES:
            raise ValueError("agent_status real: %s" % " | ".join(STATUSES))
        a = self.find(name, mach)
        if not a:
            raise KeyError("no hay agente %s en %s" % (name, mach or "local"))
        self._seq += 1
        a["agent_status"] = status
        a["state_change_seq"] = self._seq
        a["revision"] = a.get("revision", 0) + 1
        if status in ("idle", "done"):
            a["completion_seq"] = self._seq
        if status == "blocked":
            a.setdefault("interactive_ready", True)
        return a

    def add_agent(self, name, kind, status="idle", mach=None, workspace_id="w1", pane_id=None):
        self._pane += 1
        a = {"agent": kind, "agent_status": status, "cwd": self.home, "focused": False,
             "foreground_cwd": self.home, "interactive_ready": True, "name": name,
             "pane_id": pane_id or "%s:p%d" % (workspace_id, self._pane), "revision": 1,
             "state_change_seq": self._seq, "tab_id": "w1:tC",
             "terminal_id": "term_stub%04x" % self._pane, "terminal_title": name,
             "terminal_title_stripped": name, "workspace_id": workspace_id}
        self.agents.setdefault(mach, []).append(a)
        return a

    def set_remote_agents(self, label, agents):
        self.agents[label] = copy.deepcopy(agents)

    def set_agent_log(self, name, lines):
        self.logs[name] = list(lines)

    def focus(self, pane_id, mach=None):
        for a in self.agents.get(mach, []):
            a["focused"] = a["pane_id"] == pane_id

    def load(self, name):
        """Carga una captura completa (p. ej. agent_list_blocked.json) como estado local."""
        d = _load(name)
        if isinstance(d, list):
            self.machines = d
        else:
            cid = d.get("id", "")
            res = d.get("result", {})
            if cid.endswith("agent:list"):
                self.agents[None] = copy.deepcopy(res["agents"])
            elif cid.endswith("api:snapshot"):
                snap = res["snapshot"]
                for k, store in (("agents", self.agents), ("workspaces", self.workspaces),
                                 ("tabs", self.tabs), ("layouts", self.layouts)):
                    store[None] = copy.deepcopy(snap[k])
            elif cid.endswith("workspace:list"):
                self.workspaces[None] = copy.deepcopy(res["workspaces"])
            elif cid.endswith("pane:list"):
                self.scroll = {p["pane_id"]: p["scroll"] for p in res["panes"]}
            elif cid.endswith("agent:get"):
                self.agents[None] = [copy.deepcopy(res["agent"])]
        return d

    def script_error(self, code, message, match, mach=None, cid="cli:agent:prompt"):
        """Programa una respuesta de error: stdout vacio, JSON en stderr, exit 1 (verificado en el binario)."""
        self._errors.append((tuple(match), mach, {"id": cid, "error": {"code": code, "message": message}}))

    def script_response(self, match, stdout="", stderr="", rc=0, mach=None):
        self._errors.append((tuple(match), mach, ("raw", stdout, stderr, rc)))

    # ---------- vistas derivadas (Herdr mantiene agentes y paneles sincronizados) ----------
    def find(self, name, mach=None):
        return next((a for a in self.agents.get(mach, []) if a.get("name") == name), None)

    def panes_for(self, mach=None):
        out = []
        for a in self.agents.get(mach, []):
            p = {k: v for k, v in a.items() if k not in ("name", "interactive_ready", "completion_seq", "state_change_seq")}
            p["scroll"] = self.scroll.get(a["pane_id"], SCROLL)
            out.append({k: p[k] for k in sorted(p)})
        return out

    def tabs_for(self, mach=None):
        ags = self.agents.get(mach, [])
        out = []
        for t in copy.deepcopy(self.tabs.get(mach, [])):
            mine = [a for a in ags if a.get("tab_id") == t["tab_id"]]
            t["pane_count"] = len(mine)
            t["focused"] = any(a.get("focused") for a in mine)
            t["agent_status"] = _worst([a.get("agent_status", "idle") for a in mine])
            out.append(t)
        return out

    def workspaces_for(self, mach=None):
        ags = self.agents.get(mach, [])
        if mach is not None and mach not in self.workspaces:   # regla del stub: workspaces desde los agentes
            out = []
            for wid in dict.fromkeys(a["workspace_id"] for a in ags):
                mine = [a for a in ags if a["workspace_id"] == wid]
                out.append({"workspace_id": wid, "label": wid, "number": int(wid[1:]),
                            "active_tab_id": mine[0]["tab_id"], "agent_status": _worst(
                                [a.get("agent_status", "idle") for a in mine]),
                            "focused": any(a.get("focused") for a in mine),
                            "pane_count": len(mine), "tab_count": len(set(a["tab_id"] for a in mine))})
            return out
        out = []
        for w in copy.deepcopy(self.workspaces.get(mach, [])):
            mine = [a for a in ags if a.get("workspace_id") == w["workspace_id"]]
            w["pane_count"] = len(mine)
            w["focused"] = any(a.get("focused") for a in mine)
            w["agent_status"] = _worst([a.get("agent_status", "idle") for a in mine])
            out.append(w)
        return out

    def snapshot(self, mach=None):
        ags = self.agents.get(mach, [])
        f = next((a for a in ags if a.get("focused")), None)
        return {"agents": copy.deepcopy(ags), "focused_pane_id": f["pane_id"] if f else None,
                "focused_tab_id": f["tab_id"] if f else None,
                "focused_workspace_id": f["workspace_id"] if f else None,
                "layouts": copy.deepcopy(self.layouts.get(mach, [])), "panes": self.panes_for(mach),
                "protocol": 22, "tabs": self.tabs_for(mach), "version": "0.9.3",
                "workspaces": self.workspaces_for(mach)}

    # ---------- despacho de comandos herdr ----------
    def _herdr(self, mach, args):
        scripted = self._take(mach, args)
        if scripted:
            if isinstance(scripted, dict):   # script_error: JSON en stderr, exit 1 (verificado en el binario)
                return _cp(["herdr"] + args, "", _dump(scripted), 1)
            _, out, err, rc = scripted
            return _cp(["herdr"] + args, out, err, rc)
        key = tuple(args[:2])
        if key == ("agent", "list"):
            return self._ok("cli:agent:list", {"agents": copy.deepcopy(self.agents.get(mach, [])), "type": "agent_list"})
        if key == ("api", "snapshot"):
            return self._ok("cli:api:snapshot", {"snapshot": self.snapshot(mach), "type": "session_snapshot"})
        if key == ("workspace", "list"):
            return self._ok("cli:workspace:list", {"type": "workspace_list", "workspaces": self.workspaces_for(mach)})
        if key == ("pane", "list"):
            return self._ok("cli:pane:list", {"panes": self.panes_for(mach), "type": "pane_list"})
        if key == ("agent", "get"):
            a = self.find(args[2], mach)
            if not a:
                return self._err("cli:agent:get", "agent_name_not_found", "no agent named " + args[2])
            return self._ok("cli:agent:get", {"agent": copy.deepcopy(a), "type": "agent_info"})
        if key == ("machine", "list"):
            return _cp(["herdr"] + args, _dump(self.machines) if "--json" in args
                       else "".join("%s %s\n" % (m["label"], m["target"]) for m in self.machines))
        if key == ("agent", "read"):
            a = self.find(args[2], mach)
            if not a:
                return self._err("cli:agent:read", "agent_name_not_found", "no agent named " + args[2])
            lines = self.logs.get(a["name"]) or ["%s · %s · %s" % (a["name"], a["agent_status"], a["terminal_title_stripped"])]
            return _cp(["herdr"] + args, "\n".join(lines) + "\n")
        if key == ("agent", "start"):
            a = self.find(args[2], mach)
            if not a:
                a = self.add_agent(args[2], args[4] if "--kind" in args else "pi", "idle", mach)
            a["interactive_ready"] = True
            a["agent_status"] = "idle"
            pane = next((args[i + 1] for i, v in enumerate(args) if v == "--pane"), None)
            a["pane_id"] = pane or a["pane_id"]
            argv = [a["agent"]] + [v for v in args[args.index("--") + 1:]] if "--" in args else [a["agent"]]
            return _cp(["herdr"] + args, _dump({"type": "agent_started", "agent": copy.deepcopy(a), "argv": argv}))
        if key == ("agent", "prompt"):
            a = self.find(args[2], mach)
            if not a:
                return self._err("cli:agent:prompt", "agent_name_not_found", "no agent named " + args[2])
            a["agent_status"] = "working"
            self._seq += 1
            a["state_change_seq"] = self._seq
            res = {"type": "agent_prompted", "agent": copy.deepcopy(a)}
            if "--wait" in args:   # forma de --wait no verificada en vivo; T5 la ajustara
                res["matched_status"] = a["agent_status"]
            return _cp(["herdr"] + args, _dump(res))
        if key == ("workspace", "create"):   # forma INFERIDA: server.py:275 lee r["workspace"]["workspace_id"] y r["root_pane"]["pane_id"]
            wid = "w%d" % self._ws
            self._ws += 1
            label = next((args[i + 1] for i, v in enumerate(args) if v == "--label"), wid)
            w = {"workspace_id": wid, "label": label, "number": self._ws, "active_tab_id": wid + ":t1",
                 "agent_status": "unknown", "focused": False, "pane_count": 1, "tab_count": 1}
            self.workspaces.setdefault(mach, []).append(w)
            pane = {"pane_id": wid + ":p1", "tab_id": wid + ":t1", "workspace_id": wid}
            return self._ok("cli:workspace:create", {"type": "workspace_create", "workspace": w, "root_pane": pane})
        if key == ("pane", "split"):   # forma INFERIDA: server.py:287 lee sp["pane"]["pane_id"]
            base = args[2] if len(args) > 2 and not args[2].startswith("--") else "w1:p1"
            self._pane += 1
            wid = base.split(":")[0]
            pane = {"pane_id": "%s:p%d" % (wid, self._pane), "tab_id": "%s:t1" % wid, "workspace_id": wid,
                    "agent_status": "unknown", "focused": False}
            return self._ok("cli:pane:split", {"type": "pane_split", "pane": pane})
        if key == ("workspace", "close"):   # el id cli:workspace:close esta en el binario; el result es INFERIDO (server.py:368 solo usa out[-200:])
            wid = args[2]
            self.workspaces[mach] = [w for w in self.workspaces.get(mach, []) if w["workspace_id"] != wid]
            self.agents[mach] = [a for a in self.agents.get(mach, []) if a.get("workspace_id") != wid]
            return self._ok("cli:workspace:close", {"type": "workspace_close", "workspace_id": wid, "closed": True})
        return self._err("cli:stub", "usage", "stub: comando no emulado: " + " ".join(args))

    def _ok(self, cid, result):
        return _cp(["herdr"], _dump({"id": cid, "result": result}))

    def _err(self, cid, code, message):
        return _cp(["herdr"], "", _dump({"id": cid, "error": {"code": code, "message": message}}), 1)

    def _take(self, mach, args):
        for i, (match, m, payload) in enumerate(self._errors):
            if tuple(args[:len(match)]) == match and (m is None or m == mach):
                return self._errors.pop(i)[2]
        return None

    # ---------- binarios locales que server.py lanza ----------
    def _local(self, argv):
        exe = argv[0]
        if exe == "tailscale":
            return _cp(argv, "100.79.41.59\n")
        if exe == "ssh":
            cmd = argv[-1] if len(argv) > 3 else ""
            if "echo $HOME" in cmd:
                return _cp(argv, self.home + "\n")
            if "command -v" in cmd:
                return _cp(argv, "")          # en la remota no hay agentes instalados
            if "opencode models" in cmd:
                return _cp(argv, "openai/gpt-4.1\nanthropic/claude-sonnet\n")
            if "agy models" in cmd:
                return _cp(argv, "gemini-pro\nclaude-sonnet\n")
            if "pi --list-models" in cmd:
                return _cp(argv, "provider model\nopenai gpt-4.1\nanthropic claude-sonnet\n")
            return _cp(argv, "")
        if exe == "opencode":
            return _cp(argv, "openai/gpt-4.1\nanthropic/claude-sonnet\n")
        if exe in ("pi", "ada-cli"):
            return _cp(argv, "provider model\nopenai gpt-4.1\nanthropic claude-sonnet\n")
        if exe == "agy":
            return _cp(argv, "gemini-pro\nclaude-sonnet\n")
        if exe == "nvidia-smi":
            return _cp(argv, "0 %, 240 MiB / 12000 MiB\n")
        return _cp(argv, "")


def _worst(statuses):
    for s in ("blocked", "working", "unknown", "done", "idle"):
        if s in statuses:
            return s
    return "idle"
