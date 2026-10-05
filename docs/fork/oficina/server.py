#!/usr/bin/env python3
"""Oficina v4: GET+SSE lectura; POST send/office escribe herdr (argv+token). 127.0.0.1+Tailscale."""
import hmac, json, os, re, subprocess, sys, threading, time
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
TOKENF = H("~/.config/strata-oficina/token"); ENVLOG = H("~/.cache/strata-oficina/envios.log")
FIXED = ["claude", "opencode2", "tester", "explorer", "suplente"]
ROLES = {"claude": "Arquitecto · supervisa y lleva la cola", "opencode2": "Dev · ejecuta contratos", "tester": "Carga con motor",
         "explorer": "Investiga hardware", "suplente": "Reserva"}
COLORS = {"claude": "#8b5cf6", "opencode2": "#22c55e", "tester": "#f59e0b", "explorer": "#3b82f6", "suplente": "#9ca3af"}
CACHE = {"at": 0.0, "data": None}; SEEN = {}; LAST_AGENTS = set(FIXED)
LAST_SEND = 0.0; SEND_LOCK = threading.Lock()
def tailscale_ip():
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"\d+\.\d+\.\d+\.\d+", out)
        return m.group(0) if m else "100.79.41.59"
    except Exception:
        return "100.79.41.59"
def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""
def token_ok(tok):
    try:
        good = open(TOKENF, encoding="utf-8").read().strip()
    except OSError:
        return False
    return bool(good) and hmac.compare_digest(str(tok or ""), good)
def last_line(path):
    txt = read(path).strip().splitlines()
    return txt[-1].strip() if txt else ""
def estado_segments():
    for line in read(CHANGELOG).splitlines():
        if line.startswith("ESTADO:"):
            return [s.strip() for s in line[len("ESTADO:"):].split("|")]
    return []
def table_rows(n=8):
    return [l for l in read(CHANGELOG).splitlines() if re.match(r"\|\s*20", l)][-n:]
def short_row(row):
    c = [x.strip() for x in row.strip().strip("|").split("|")]
    return "%s %s: %s (%s)" % tuple(c[:2] + c[2:3] + c[4:5]) if len(c) >= 5 else row.strip()
def herdr_out(args, timeout=8):
    try:
        return subprocess.run(["herdr"] + args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""
def mkagent(name, status, activity, color, role):
    st = "working" if status == "working" else "idle"
    if SEEN.get(name, [None])[0] != st:
        SEEN[name] = [st, time.strftime("%d %H:%M UTC", time.gmtime()), time.time()]
    return {"name": name, "status": st, "activity": activity, "color": color, "role": role, "since": SEEN[name][1],
            "ts": SEEN[name][2]}
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
    for name, a in statuses.items():
        if name not in FIXED:
            agents.append(mkagent(name, a.get("agent_status", "idle"),
                                  next((s for s in segs if name in s), obrs[0] if obrs else "trabajando"),
                                  "#fb7185", "Contrato " + name))
    global LAST_AGENTS
    LAST_AGENTS = set(a["name"] for a in agents)
    try: metrics = json.loads(read(METRICAS) or "{}")
    except Exception: metrics = {}
    txt = read(COLA)
    done = len(re.findall(r"^-\s*\[x\]", txt, re.M))
    total = done + len(re.findall(r"^-\s*\[ \]", txt, re.M))
    ticker = [short_row(r) for r in table_rows()] + ["COLA %d/%d" % (done, total)]
    if last_line(LOG_UP):
        ticker.append("upstream: " + last_line(LOG_UP))
    if last_line(LOG_WD):
        ticker.append("vigia: " + last_line(LOG_WD))
    return {"agents": agents, "metrics": metrics, "ticker": ticker, "queue": {"done": done, "total": total},
            "lock": bool(lock_txt.strip()), "bench": os.path.exists(BENCH), "suplencia": supl,
            "herdr": bool(statuses), "tareas": tasks_list(), "updated": time.strftime("%H:%M:%S UTC", time.gmtime())}
def get_state():
    interval = 30 if os.path.exists(BENCH) else 5
    if CACHE["data"] is None or time.monotonic() - CACHE["at"] >= interval:
        CACHE["data"] = build_state(); CACHE["at"] = time.monotonic(); CACHE["data"]["interval"] = interval
    return CACHE["data"]
def valid_agent(agent):
    get_state(); return bool(re.match(r"^[\w][\w.\-]{0,31}$", agent or "")) and agent in LAST_AGENTS
def api_log(agent):
    if not valid_agent(agent):
        return {"error": "agente desconocido"}
    out = herdr_out(["agent", "read", agent, "--source", "recent-unwrapped", "--lines", "45", "--format", "text"], 10)
    if not out:
        return {"agent": agent, "error": "herdr no responde"}
    return {"agent": agent, "lines": out.strip().splitlines()[-30:]}
def api_hilo(agent):
    if not valid_agent(agent):
        return {"error": "agente desconocido"}
    env = []
    for line in read(ENVLOG).strip().splitlines()[-40:]:
        try: d = json.loads(line)
        except Exception: continue
        if d.get("agent") == agent or (d.get("mode") == "viaclaude" and agent == "claude"):
            env.append("%s [%s] %s" % (d.get("ts", ""), d.get("mode", ""), (d.get("text") or "")[:120]))
    men = [short_row(r) for r in table_rows(60) if agent.lower() in r.lower()][-8:]
    return {"agent": agent, "envios": env[-10:], "menciones": men}
def intasks(ags, foco, alln):
    return [t for t in tasks_list() if t.get("responsable") in ags or (foco and t.get("responsable") not in alln)]
def api_offices():
    st = get_state()
    try: ws = json.loads(herdr_out(["workspace", "list"]))["result"]["workspaces"]
    except Exception: ws = []
    try: raw = json.loads(herdr_out(["agent", "list"]))["result"]["agents"]
    except Exception: raw = []
    if not ws and st.get("agents"):
        ws = [{"workspace_id": "w1", "label": "Strata3060", "focused": True}]
    alln = set(a.get("name") for a in raw)
    offs = []
    for w in ws:
        wid = w.get("workspace_id")
        ags = [a.get("name") for a in raw if a.get("workspace_id", "w1") == wid] or [a["name"] for a in st["agents"]]
        tl = intasks(ags, bool(w.get("focused")), alln)
        esp = [t for t in tl if t.get("columna") == "ESPERA OK" and t.get("aprobador") == "adrian"]
        offs.append({"id": wid, "nombre": w.get("label") or wid, "agentes": ags, "tareas": len(tl), "tl": tl,
                     "needs": bool(esp), "foco": bool(w.get("focused"))})
    return {"offices": offs}
def api_send(body):
    global LAST_SEND
    try: d = json.loads(body[:8192])
    except Exception: return 400, {"ok": False, "error": "JSON invalido"}
    if not token_ok(d.get("token")):
        return 403, {"ok": False, "error": "token invalido o ausente"}
    agent, mode, text = d.get("agent"), d.get("mode"), str(d.get("text") or "")
    if not valid_agent(agent):
        return 400, {"ok": False, "error": "agente desconocido"}
    if mode not in ("direct", "viaclaude"):
        return 400, {"ok": False, "error": "modo invalido"}
    if not 1 <= len(text) <= 4000:
        return 400, {"ok": False, "error": "texto 1..4000 chars"}
    with SEND_LOCK:
        if time.monotonic() - LAST_SEND < 3.0:
            return 429, {"ok": False, "error": "limite 1 envio/3s"}
        LAST_SEND = time.monotonic()
    tgt, pre = (agent, "adrian: ") if mode == "direct" else ("claude", "adrian (oficina): ")
    out = herdr_out(["agent", "prompt", tgt, pre + text], 30)
    if out is None:
        return 502, {"ok": False, "error": "herdr no responde"}
    try:
        os.makedirs(os.path.dirname(ENVLOG), exist_ok=True)
        with open(ENVLOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "agent": agent, "mode": mode, "text": text}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return 200, {"ok": True, "confirmed": "agent_prompted" in out, "output": out[-500:]}
def api_office(body):
    try: d = json.loads(body[:1024])
    except Exception: return 400, {"ok": False, "error": "JSON invalido"}
    if not token_ok(d.get("token")):
        return 403, {"ok": False, "error": "token invalido o ausente"}
    name = str(d.get("name") or "").strip()[:40]
    if not re.match(r"^[\w][\w .\-]{0,39}$", name):
        return 400, {"ok": False, "error": "nombre invalido"}
    out = herdr_out(["workspace", "create", "--label", name], 15)
    if out is None:
        return 502, {"ok": False, "error": "herdr no responde"}
    return 200, {"ok": True, "output": out[-300:]}
with open(os.path.join(HERE, "index.html"), "rb") as f:
    INDEX = f.read()
class Handler(BaseHTTPRequestHandler):
    server_version = "Oficina/4"
    protocol_version = "HTTP/1.1"
    def _send(self, code, body, ctype):
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")):
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)
    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query).get("agent", [""])[0]
        if u.path == "/":
            self._send(200, INDEX, "text/html; charset=utf-8")
        elif u.path == "/api/state":
            self._json(200, get_state())
        elif u.path == "/api/log": self._json(200, api_log(q))
        elif u.path == "/api/tasks": self._json(200, {"tareas": tasks_list()})
        elif u.path == "/api/hilo": self._json(200, api_hilo(q))
        elif u.path == "/api/offices": self._json(200, api_offices())
        elif u.path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    st = get_state()
                    msg = ("data: " + json.dumps(st, ensure_ascii=False) + "\n\n").encode()
                    self.wfile.write(msg); self.wfile.flush()
                    time.sleep(st.get("interval", 5))
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_error(404)
    def do_POST(self):
        p = urlparse(self.path).path
        try: n = min(int(self.headers.get("Content-Length", "0")), 8192)
        except ValueError: n = 0
        body = self.rfile.read(n)
        if p == "/api/send":
            self._json(*api_send(body))
        elif p == "/api/office":
            self._json(*api_office(body))
        else:
            self.send_error(404)
    def log_message(self, *a):
        pass
def serve(ip):
    ThreadingHTTPServer((ip, PORT), Handler).serve_forever()
if __name__ == "__main__":
    ips = ["127.0.0.1", tailscale_ip()]
    for ip in dict.fromkeys(ips): threading.Thread(target=serve, args=(ip,), daemon=True).start()
    print("oficina en %s (puerto %d)" % (" y ".join(ips), PORT), flush=True)
    threading.Event().wait()
