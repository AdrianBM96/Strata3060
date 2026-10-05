#!/usr/bin/env python3
"""Login scrypt + sesion cookie; GET lectura (+SSE) y POST solo con sesion."""
import base64, hashlib, hmac, json, os, re, secrets, shlex, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8095
HERE = os.path.dirname(os.path.abspath(__file__))
FORK = "/tmp/opencode/Strata3060/docs/fork"
CHANGELOG = FORK + "/CHANGELOG.md"; COLA = FORK + "/COLA.md"
METRICAS = FORK + "/METRICAS.json"; TAREAS = FORK + "/TAREAS.json"
MOTOR = "/tmp/strata-motor.lock"; BENCH = "/tmp/strata-bench.lock"
H = os.path.expanduser
ENVIADOS = H("~/explorer/ENVIADOS.md"); SUPLENCIA = H("~/.cache/strata-watchdog/suplencia")
LOG_UP = H("~/.cache/strata-upstream/log"); LOG_WD = H("~/.cache/strata-watchdog/log")
PWF = os.environ.get("STRATA_OFICINA_PW", H("~/.config/strata-oficina/password.scrypt"))
ENVLOG = H("~/.cache/strata-oficina/envios.log"); B64 = base64.b64decode
SESSF = H("~/.cache/strata-oficina/sesiones.json"); SELOCK = threading.Lock(); FAILS = {}
try: SESS = {k: v for k, v in json.loads(open(SESSF).read()).items() if v > time.time()}
except Exception: SESS = {}
def save_sess():
    try:
        os.makedirs(os.path.dirname(SESSF), exist_ok=True)
        with open(os.open(SESSF, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: json.dump(SESS, f)
    except OSError: pass
FIXED = ["claude", "opencode2", "tester", "explorer", "suplente"]
ROLES = {"claude": "Arquitecto · supervisa y lleva la cola", "opencode2": "Dev · ejecuta contratos", "tester": "Carga con motor", "explorer": "Investiga hardware", "suplente": "Reserva"}
COLORS = {"claude": "#8b5cf6", "opencode2": "#22c55e", "tester": "#f59e0b", "explorer": "#3b82f6", "suplente": "#9ca3af"}
CACHE = {"at": 0.0, "data": None}; SEEN = {}; LAST_AGENTS = set(FIXED)
LAST_SEND = 0.0; SEND_LOCK = threading.Lock()
def tailscale_ip():
    try: return re.search(r"(\d+\.\d+\.\d+\.\d+)", subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout).group(1)
    except Exception: return "100.79.41.59"
def read(path):
    try: return open(path, encoding="utf-8", errors="replace").read()
    except OSError: return ""
def sess_ok(h):
    m = re.search(r"sid=([A-Za-z0-9_-]{20,})", h.get("Cookie", "") or "")
    s = m.group(1) if m else ""
    with SELOCK:
        ok = SESS.get(s, 0) > time.time()
        if ok: SESS[s] = time.time() + 2592000
        else: SESS.pop(s, None)
    return ok
def pw_ok(pw):
    try:
        _, N, r, p, sb, hb = read(PWF).strip().split("$")
        h = hashlib.scrypt(bytes(pw or "", "utf8"), salt=B64(sb), n=int(N), r=int(r), p=int(p), maxmem=33554432, dklen=len(B64(hb)))
    except Exception:
        return False
    return hmac.compare_digest(h, B64(hb))
def limited(ip):
    n = time.time(); FAILS[ip] = [t for t in FAILS.get(ip, []) if n - t < 900]
    return len(FAILS[ip]) >= 5
def last_line(path): txt = read(path).strip().splitlines(); return txt[-1].strip() if txt else ""
def estado_segments():
    segs = []
    for line in read(CHANGELOG).splitlines():
        if line.startswith("ESTADO:"): segs = [s.strip() for s in line[len("ESTADO:"):].split("|")]
    return segs
def table_rows(n=8): return [l for l in read(CHANGELOG).splitlines() if re.match(r"\|\s*20", l)][-n:]
def short_row(row): c = [x.strip() for x in row.strip().strip("|").split("|")]; return "%s %s: %s (%s)" % tuple(c[:2] + c[2:3] + c[4:5]) if len(c) >= 5 else row.strip()
def herdr_out(args, timeout=8):
    try: return subprocess.run(["herdr"] + args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception: return ""
def mkagent(name, status, activity, color, role):
    st = "working" if status == "working" else "idle"
    if SEEN.get(name, [None])[0] != st: SEEN[name] = [st, time.strftime("%d %H:%M UTC", time.gmtime()), time.time()]
    return {"name": name, "status": st, "activity": activity, "color": color, "role": role, "since": SEEN[name][1], "ts": SEEN[name][2]}
def tasks_list():
    try: return json.loads(read(TAREAS) or "{}").get("tareas", [])
    except Exception: return []
def build_state():
    try:
        statuses = {a.get("name", "?"): a for a in json.loads(herdr_out(["agent", "list"]))["result"]["agents"]}
    except Exception:
        statuses = {}
    segs = estado_segments()
    yo = next((s for s in segs if "(yo)" in s), segs[0] if segs else "")
    obrs = [s for s in segs if "wt-" in s or "(obrero)" in s]
    lock_txt = read(MOTOR)
    m = re.search(r"progreso\s*=\s*([^\s]+)", lock_txt)
    tact = ("progreso=" + m.group(1)) if m else ("ocupado" if lock_txt.strip() else "libre")
    supl = os.path.exists(SUPLENCIA)
    g = lambda n: statuses.get(n, {}).get("agent_status", "idle")
    C = COLORS; R = ROLES; cs = g("claude")
    agents = [mkagent("claude", cs, "pensando" if cs == "working" else "supervisando", C["claude"], R["claude"]),
              mkagent("opencode2", g("opencode2"), yo or "trabajando", C["opencode2"], R["opencode2"]),
              mkagent("tester", "working" if lock_txt.strip() else "idle", tact, C["tester"], R["tester"]),
              mkagent("explorer", g("explorer"), last_line(ENVIADOS) or "investigando", C["explorer"], R["explorer"]),
              mkagent("suplente", "working" if supl else "idle", "SUPLENTE AL MANDO" if supl else "en reposo", C["suplente"], R["suplente"])]
    wsS = statuses.get("opencode2", {}).get("workspace_id", "w1")   # solo los obreros de la oficina de Strata
    for name, a in statuses.items():
        if name not in FIXED and a.get("workspace_id") == wsS:
            agents.append(mkagent(name, a.get("agent_status", "idle"),
                                  next((s for s in segs if name in s), obrs[0] if obrs else "trabajando"),
                                  "#fb7185", "Contrato " + name))
    global LAST_AGENTS
    LAST_AGENTS = set(a["name"] for a in agents)
    try: metrics = json.loads(read(METRICAS) or "{}")
    except Exception: metrics = {}
    txt = read(COLA); done = len(re.findall(r"^-\s*\[x\]", txt, re.M)); total = done + len(re.findall(r"^-\s*\[ \]", txt, re.M))
    ticker = [short_row(r) for r in table_rows()] + ["COLA %d/%d" % (done, total)]
    if last_line(LOG_UP): ticker.append("upstream: " + last_line(LOG_UP))
    if last_line(LOG_WD): ticker.append("vigia: " + last_line(LOG_WD))
    return {"agents": agents, "metrics": metrics, "ticker": ticker, "queue": {"done": done, "total": total},
            "lock": bool(lock_txt.strip()), "bench": os.path.exists(BENCH), "suplencia": supl,
            "herdr": bool(statuses), "tareas": tasks_list(), "updated": time.strftime("%H:%M:%S UTC", time.gmtime())}
def get_state():
    interval = 30 if os.path.exists(BENCH) else 5
    if CACHE["data"] is None or time.monotonic() - CACHE["at"] >= interval:
        CACHE["data"] = build_state(); CACHE["at"] = time.monotonic(); CACHE["data"]["interval"] = interval
    return CACHE["data"]
def valid_agent(agent): get_state(); return bool(re.match(r"^[\w][\w.\-]{0,31}$", agent or "")) and (agent in LAST_AGENTS or agent in {a.get("name") for a in raw_agents()})
def api_log(agent):
    if not valid_agent(agent): return {"error": "agente desconocido"}
    out = herdr_out(["agent", "read", agent, "--source", "recent-unwrapped", "--lines", "45", "--format", "text"], 10)
    if not out:
        return {"agent": agent, "error": "herdr no responde"}
    return {"agent": agent, "lines": out.strip().splitlines()[-30:]}
def api_hilo(agent):
    if not valid_agent(agent): return {"error": "agente desconocido"}
    env = []
    for line in read(ENVLOG).strip().splitlines()[-40:]:
        try: d = json.loads(line)
        except Exception: continue
        if d.get("agent") == agent or (d.get("mode") == "viaclaude" and agent == "claude"):
            env.append("%s [%s] %s" % (d.get("ts", ""), d.get("mode", ""), (d.get("text") or "")[:120]))
    men = [short_row(r) for r in table_rows(60) if agent.lower() in r.lower()][-8:]
    return {"agent": agent, "envios": env[-10:], "menciones": men}
OFIMETA = H("~/.cache/strata-oficina/oficinas.json")
PERFILES = {"arquitecto": ("claude", "Arquitecto"), "desarrollador": ("opencode", "Desarrollador"),
            "investigador": ("agy", "Investigador"), "tester": ("pi", "Tester")}
JOBS = {}
def meta():
    try: return json.loads(read(OFIMETA) or "{}")
    except Exception: return {}
def save_meta(m):
    os.makedirs(os.path.dirname(OFIMETA), exist_ok=True)
    with open(OFIMETA, "w") as f: json.dump(m, f, ensure_ascii=False)
def hj(args, timeout=15):
    try: return json.loads(herdr_out(args, timeout))["result"]
    except Exception: return None
def raw_agents():
    r = hj(["agent", "list"]); return r["agents"] if r else []
def strata_ws(ws):
    for w in ws:
        if w.get("label") == "Strata3060": return w.get("workspace_id")
    names = {a.get("name"): a.get("workspace_id") for a in raw_agents()}
    return names.get("opencode2", "w1")
def api_offices():
    st = get_state(); r = hj(["workspace", "list"]); ws = r["workspaces"] if r else []
    sid = strata_ws(ws); raw = raw_agents(); m = meta(); offs = []
    for w in ws:
        wid = w.get("workspace_id"); es = wid == sid
        ags = [a.get("name") for a in raw if a.get("workspace_id") == wid and a.get("name")]
        tl = tasks_list() if es else []
        esp = [t for t in tl if t.get("columna") == "ESPERA OK" and t.get("aprobador") == "adrian"]
        offs.append({"id": wid, "nombre": w.get("label") or wid, "agentes": [a["name"] for a in st["agents"]] if es else ags,
                     "tareas": len([t for t in tl if t.get("columna") in ("EN CURSO", "ESPERA OK", "EN COLA")]),
                     "needs": bool(esp), "strata": es, "foco": es, "cwd": m.get(wid, {}).get("cwd", ""),
                     "perfiles": m.get(wid, {}).get("agentes", [])})
    return {"offices": offs, "strata": sid}
def state_ws(wid):
    m = meta().get(wid, {}); roles = {a["name"]: a["rol"] for a in m.get("agentes", [])}
    ags = [mkagent(a["name"], a.get("agent_status", "idle"), "trabajando" if a.get("agent_status") == "working" else
                   ("esperando confirmación" if a.get("agent_status") == "blocked" else "en reposo"),
                   "#fb7185", roles.get(a["name"], a.get("agent", "agente")))
           for a in raw_agents() if a.get("workspace_id") == wid and a.get("name")]
    return {"agents": ags, "metrics": {}, "ticker": [], "queue": {"done": 0, "total": 0}, "lock": False, "bench": False,
            "suplencia": False, "herdr": True, "tareas": [], "ws": wid, "updated": time.strftime("%H:%M:%S UTC", time.gmtime())}
def slug(t): return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:12] or "ofi"
KINDS = {"claude": "Claude Code", "opencode": "opencode", "pi": "pi", "ada-cli": "ada-cli", "agy": "Antigravity (agy)"}
KCACHE = {"at": 0, "data": None}
ADA_DIR = H("~/.local/share/strata-oficina/ada")
ADA_PATH = ADA_DIR + ":" + H("~/.nvm/versions/node/v22.23.2/bin") + ":" + H("~/.local/bin") + ":/usr/local/bin:/usr/bin:/bin"
try:
    os.makedirs(ADA_DIR, exist_ok=True)
    if not os.path.islink(os.path.join(ADA_DIR, "pi")): os.symlink(H("~/ada-cli/packages/coding-agent/dist/cli.js"), os.path.join(ADA_DIR, "pi"))
except OSError: pass
def _cmd(args, t=25):
    env = dict(os.environ, PATH=H("~/.local/bin") + ":/snap/bin:" + os.path.dirname(sys.executable) + ":" + os.environ.get("PATH", ""))
    for d in (H("~/.nvm/versions/node"),):
        try: env["PATH"] = ":".join(os.path.join(d, v, "bin") for v in os.listdir(d)) + ":" + env["PATH"]
        except OSError: pass
    try: return subprocess.run(args, capture_output=True, text=True, timeout=t, env=env, cwd=H("~")).stdout
    except Exception: return ""
def _table(out):
    res = []
    for ln in out.splitlines():
        c = ln.split()
        if len(c) >= 2 and c[0] not in ("provider",) and not ln.startswith("["): res.append(c[0] + "/" + c[1])
    return res
def kinds():
    if KCACHE["data"] and time.time() - KCACHE["at"] < 600: return KCACHE["data"]
    d = {"claude": ["opus", "sonnet", "haiku", "fable"],
         "opencode": [l.strip() for l in _cmd(["opencode", "models"]).splitlines() if "/" in l],
         "pi": _table(_cmd(["pi", "--list-models"])), "ada-cli": _table(_cmd(["ada-cli", "--list-models"])),
         "agy": [l.split()[0] for l in _cmd(["agy", "models"]).splitlines() if l and not l.startswith("Fetching")]}
    KCACHE.update(at=time.time(), data={k: {"nombre": KINDS[k], "modelos": v} for k, v in d.items()}); return KCACHE["data"]
def _start(nombre, kind, model, pid, cwd, auto):
    if kind == "ada-cli":   # en su panel, `pi` es un enlace a ada-cli (ADA_PATH), así herdr lo reconoce como pi
        extra = ["--model", model] if model else []
        out = herdr_out(["agent", "start", nombre, "--kind", "pi", "--pane", pid, "--timeout", "60000"] + (["--"] + extra if extra else []), 75)
    else:
        extra = (["--model", model] if model and kind != "opencode" else [])
        if auto and kind in ("claude", "agy"): extra.append("--dangerously-skip-permissions")
        if auto and kind == "opencode": extra.append("--auto")
        out = herdr_out(["agent", "start", nombre, "--kind", kind, "--pane", pid, "--timeout", "60000"] + (["--"] + extra if extra else []), 75)
    if '"interactive_ready":true' in out or '"agent_started"' in out: return "listo"
    if "agent_not_ready" in out: return "espera confirmación en su panel"
    try: return "ERROR " + json.loads(out)["error"]["message"][:160]
    except Exception: return "ERROR " + (out[-160:] or "herdr no respondió")
def oc_config(nombre, model):
    """opencode no acepta --model en su interfaz: cada agente recibe su propio fichero de configuración."""
    d = H("~/.cache/strata-oficina/opencode"); os.makedirs(d, exist_ok=True)
    f = os.path.join(d, nombre + ".json")
    with open(f, "w") as fh: json.dump({"$schema": "https://opencode.ai/config.json", "model": model}, fh)
    return f
def _crear(jid, name, cwd, filas, auto):
    J = JOBS[jid]; log = J["pasos"].append
    try:
        env = "PATH=" + H("~/.local/bin") + ":/snap/bin:/usr/local/bin:/usr/bin:/bin"
        r = hj(["workspace", "create", "--cwd", cwd, "--label", name, "--no-focus", "--env", env], 20)
        if not r: raise RuntimeError("herdr no pudo crear el workspace")
        wid = r["workspace"]["workspace_id"]; pane = r["root_pane"]["pane_id"]; J["ws"] = wid
        log("Workspace %s creado en %s" % (wid, cwd))
        m = meta(); m[wid] = {"nombre": name, "cwd": cwd, "agentes": []}; save_meta(m)
        lista = [(f, n + 1, ("%s-%s%d" % (slug(name)[:10], slug(f["perfil"])[:8], n + 1))[:32]) for f in filas for n in range(f["n"])]
        log("Panel %s: consola de la oficina" % pane)
        allp = [pane]; panes = []
        for idx, (f, n, nombre) in enumerate(lista):   # cada agente en su propio panel; el raíz queda como consola
            base = allp[idx // 2]
            env = ["--env", "OPENCODE_CONFIG=" + oc_config(nombre, f["model"])] if f["kind"] == "opencode" and f["model"] else []
            if f["kind"] == "ada-cli": env = ["--env", "PATH=" + ADA_PATH]
            sp = hj(["pane", "split", base, "--direction", "right" if idx % 2 == 0 else "down", "--cwd", cwd, "--no-focus"] + env, 15)
            if not sp: raise RuntimeError("no se pudo crear el panel de " + nombre)
            allp.append(sp["pane"]["pane_id"]); panes.append(sp["pane"]["pane_id"])
        time.sleep(1.5)
        for (f, n, nombre), pid in zip(lista, panes):
            res = _start(nombre, f["kind"], f["model"], pid, cwd, auto)
            log("%s · %s · %s%s: %s" % (nombre, f["perfil"], f["kind"], (" · " + f["model"]) if f["model"] else "", res))
            m = meta(); m[wid]["agentes"].append({"name": nombre, "rol": f["perfil"], "kind": f["kind"], "model": f["model"]}); save_meta(m)
        J["estado"] = "hecho"
    except Exception as e:
        J["estado"] = "error"; J["error"] = str(e)[:300]
def api_office(body):
    try: d = json.loads(body[:4096])
    except Exception: return 400, {"ok": False, "error": "JSON invalido"}
    name = str(d.get("name") or "").strip()[:40]
    if not re.match(r"^[\w][\w .\-]{0,39}$", name): return 400, {"ok": False, "error": "nombre invalido"}
    home = os.path.realpath(H("~"))
    cwd = os.path.realpath(H(str(d.get("cwd") or ("~/" + slug(name)))))
    if not (cwd == home or cwd.startswith(home + os.sep)): return 400, {"ok": False, "error": "la carpeta debe estar dentro de tu home"}
    if any(part.startswith(".") for part in cwd[len(home):].split(os.sep) if part): return 400, {"ok": False, "error": "no se permiten carpetas ocultas (agy no las ve)"}
    K = kinds(); filas = []
    for f in (d.get("equipo") or [])[:8]:
        perfil = str(f.get("perfil") or "").strip()[:24]; kind = str(f.get("kind") or ""); model = str(f.get("model") or "")
        try: n = int(f.get("n", 1))
        except Exception: n = 0
        if not re.match(r"^[\w][\w .\-]{0,23}$", perfil): return 400, {"ok": False, "error": "perfil invalido: " + perfil}
        if kind not in K: return 400, {"ok": False, "error": "tipo de agente no permitido"}
        if model and model not in K[kind]["modelos"]: return 400, {"ok": False, "error": "modelo no disponible para " + kind}
        if not 1 <= n <= 4: return 400, {"ok": False, "error": "de 1 a 4 por perfil"}
        filas.append({"perfil": perfil, "kind": kind, "model": model, "n": n})
    total = sum(f["n"] for f in filas)
    if total > 8: return 400, {"ok": False, "error": "maximo 8 agentes por oficina"}
    os.makedirs(cwd, exist_ok=True)
    jid = secrets.token_hex(6); JOBS[jid] = {"estado": "en curso", "pasos": [], "ws": None, "total": total}
    threading.Thread(target=_crear, args=(jid, name, cwd, filas, bool(d.get("auto"))), daemon=True).start()
    return 200, {"ok": True, "job": jid}
def api_send(body):
    global LAST_SEND
    try: d = json.loads(body[:8192])
    except Exception: return 400, {"ok": False, "error": "JSON invalido"}
    agent, mode, text = d.get("agent"), d.get("mode"), str(d.get("text") or "")
    if not valid_agent(agent): return 400, {"ok": False, "error": "agente desconocido"}
    if mode not in ("direct", "viaclaude"): return 400, {"ok": False, "error": "modo invalido"}
    if not 1 <= len(text) <= 4000: return 400, {"ok": False, "error": "texto 1..4000 chars"}
    with SEND_LOCK:
        if time.monotonic() - LAST_SEND < 3.0:
            return 429, {"ok": False, "error": "limite 1 envio/3s"}
        LAST_SEND = time.monotonic()
    tgt, pre = (agent, "adrian: ") if mode == "direct" else ("claude", "adrian (oficina): ")
    out = herdr_out(["agent", "prompt", tgt, pre + text], 30)
    if not out: return 502, {"ok": False, "error": "herdr no responde"}
    try:
        os.makedirs(os.path.dirname(ENVLOG), exist_ok=True)
        with open(ENVLOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "agent": agent, "mode": mode, "text": text}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return 200, {"ok": True, "confirmed": "agent_prompted" in out, "output": out[-500:]}
def api_office_delete(body):
    try: d = json.loads(body[:1024])
    except Exception: return 400, {"ok": False, "error": "JSON invalido"}
    wid = str(d.get("id") or "")
    r = hj(["workspace", "list"]); ws = r["workspaces"] if r else []
    w = next((x for x in ws if x.get("workspace_id") == wid), None)
    if not w: return 404, {"ok": False, "error": "oficina no encontrada"}
    if wid == strata_ws(ws): return 403, {"ok": False, "error": "la oficina de Strata no se puede borrar"}
    if str(d.get("confirm") or "") != (w.get("label") or wid): return 400, {"ok": False, "error": "escribe el nombre exacto para confirmar"}
    out = herdr_out(["workspace", "close", wid], 20)
    m = meta(); m.pop(wid, None); save_meta(m)
    return 200, {"ok": True, "output": out[-200:]}
with open(os.path.join(HERE, "index.html"), "rb") as f:
    INDEX = f.read()
with open(os.path.join(HERE, "login.html"), "rb") as f:
    LOGIN = f.read()
with open(os.path.join(HERE, "vendor", "three.module.min.js"), "rb") as f:
    THREE = f.read()
class Handler(BaseHTTPRequestHandler):
    server_version = "Oficina/4"
    protocol_version = "HTTP/1.1"
    def _send(self, code, body, ctype, ck=""):
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")):
            self.send_header(k, v)
        if ck:
            self.send_header("Set-Cookie", ck)
        self.end_headers(); self.wfile.write(body)
    def _json(self, code, obj): self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query).get("agent", [""])[0]
        if u.path == "/favicon.ico":
            self.send_response(204); self.send_header("Content-Length", "0"); self.end_headers()
        elif u.path == "/login": self._send(200, LOGIN, "text/html; charset=utf-8")
        elif u.path == "/" and not sess_ok(self.headers):
            self.send_response(302); self.send_header("Location", "/login"); self.end_headers()
        elif u.path.startswith("/api/") and not sess_ok(self.headers):
            self._json(401, {"error": "login"})
        elif u.path == "/": self._send(200, INDEX, "text/html; charset=utf-8")
        elif u.path == "/vendor/three.module.min.js":
            self._send(200, THREE, "text/javascript; charset=utf-8")
        elif u.path == "/api/state":
            ws = parse_qs(u.query).get("ws", [""])[0]
            self._json(200, state_ws(ws) if ws and re.match(r"^w[0-9A-Za-z]+$", ws) else get_state())
        elif u.path == "/api/kinds":
            self._json(200, kinds())
        elif u.path == "/api/office/job":
            j = JOBS.get(parse_qs(u.query).get("id", [""])[0]); self._json(200 if j else 404, j or {"error": "no existe"})
        elif u.path == "/api/log": self._json(200, api_log(q))
        elif u.path == "/api/hilo": self._json(200, api_hilo(q))
        elif u.path == "/api/offices":
            self._json(200, api_offices())
        elif u.path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                prev = None; quiet = 0
                while True:
                    st = get_state(); body = json.dumps({k: v for k, v in st.items() if k != "updated"}, ensure_ascii=False)
                    if body != prev or quiet >= 6:   # solo si cambia; latido cada ~30 s para mantener viva la conexión
                        self.wfile.write(("data: " + json.dumps(st, ensure_ascii=False) + "\n\n").encode()); self.wfile.flush()
                        prev = body; quiet = 0
                    else:
                        quiet += 1
                    time.sleep(st.get("interval", 5))
            except (BrokenPipeError, ConnectionResetError):
                pass
        else: self.send_error(404)
    def do_POST(self):
        p = urlparse(self.path).path
        try: n = min(int(self.headers.get("Content-Length", "0")), 8192)
        except ValueError: n = 0
        body = self.rfile.read(n)
        if p == "/api/login":
            try: d = json.loads(body[:1024])
            except Exception: d = {}
            ip = self.client_address[0]
            if limited(ip):
                self._json(429, {"ok": False, "error": "demasiados intentos"})
            elif pw_ok(d.get("password")):
                FAILS.pop(ip, None)
                sid = secrets.token_urlsafe(32)
                with SELOCK: SESS[sid] = time.time() + 2592000; save_sess()
                self._send(200, b'{"ok": true}', "application/json",
                            "sid=" + sid + "; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict")
            else:
                FAILS.setdefault(ip, []).append(time.time())
                self._json(403, {"ok": False, "error": "password"})
        elif p.startswith("/api/") and not sess_ok(self.headers): self._json(401, {"ok": False, "error": "login"})
        elif p == "/api/send": self._json(*api_send(body))
        elif p == "/api/office": self._json(*api_office(body))
        elif p == "/api/office/delete": self._json(*api_office_delete(body))
        else: self.send_error(404)
    def log_message(self, *a): pass
def serve(ip): ThreadingHTTPServer((ip, PORT), Handler).serve_forever()
if __name__ == "__main__":
    ips = ["127.0.0.1", tailscale_ip()]
    for ip in dict.fromkeys(ips): threading.Thread(target=serve, args=(ip,), daemon=True).start()
    print("oficina en %s (puerto %d)" % (" y ".join(ips), PORT), flush=True); threading.Event().wait()
