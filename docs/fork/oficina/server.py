#!/usr/bin/env python3
"""Login scrypt + sesion cookie; GET lectura (+SSE) y POST solo con sesion."""
import base64, hashlib, hmac, json, os, re, secrets, shlex, socket, select, subprocess, sys, threading, time
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
# T8 (defecto 5): el audit log vive solo en bazzite, y el ambito se declara en la respuesta de `/api/hilo`.
AUDIT_SCOPE = "el historial de envíos es local (bazzite): los envíos a %s no se registran aquí"
SESSF = H("~/.cache/strata-oficina/sesiones.json"); SELOCK = threading.RLock(); FAILS = {}; FLOCK = threading.Lock()
# T7 (locks): `SELOCK` protege `SESS` y su fichero. `FLOCK` protege `FAILS`: podar, comprobar el
# limite y registrar un fallo son una sola lectura-modificacion-escritura; sin lock dos hilos cuentan
# números distintos y un fallo registrado se pierde.
try: SESS = {k: v for k, v in json.loads(open(SESSF).read()).items() if v > time.time()}
except Exception: SESS = {}
def _atomic(path, texto):
    """Escritura atomica (T7): temp en el mismo directorio, mode 0600, y `os.replace` publica.

    Abrir el fichero con `"w"` trunca antes de escribir: un crash a mitad deja `sesiones.json` o
    `oficinas.json` vacio o truncado. Con `os.replace` el lector ve el fichero viejo completo o el
    nuevo completo, nunca un fichero a medias.
    """
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: f.write(texto)
    os.replace(tmp, path)
def save_sess():
    try:
        os.makedirs(os.path.dirname(SESSF), exist_ok=True)
        with SELOCK: _atomic(SESSF, json.dumps(SESS))   # T7: SELOCK y publicacion atomica
    except OSError: pass
FIXED = ["claude", "opencode2", "tester", "explorer", "suplente"]
ROLES = {"claude": "Arquitecto · supervisa y lleva la cola", "opencode2": "Dev · ejecuta contratos", "tester": "Carga con motor", "explorer": "Investiga hardware", "suplente": "Reserva"}
COLORS = {"claude": "#8b5cf6", "opencode2": "#22c55e", "tester": "#f59e0b", "explorer": "#3b82f6", "suplente": "#9ca3af"}
CACHE = {"at": 0.0, "data": None}; SEEN = {}; LAST_AGENTS = set(FIXED)
LAST_SEND = 0.0; SEND_LOCK = threading.Lock()
# T7: `STLOCK` es el lock del rebuild: protege `CACHE` y todo lo que se escribe dentro de una
# construccion (`SEEN`, `SNAP`, `LAST_AGENTS`). Es RLock porque `mkagent` lo toma desde `build_state`
# (ya dentro del lock) y desde `state_ws` (solo).
# `MLOCK` protege `MCACHE`, `HOMES` y `AGCACHE` (las maquinas y su mapa de agentes); es RLock porque
# `rhome` -> `ssh_run` -> `target` -> `machines` vuelve a entrar. `KLOCK` protege `KCACHE`.
# Orden de adquisicion: `MLOCK` -> `STLOCK` (solo en `agmap`) y `KLOCK` -> `MLOCK` (solo en `kinds`
# remoto). `STLOCK` nunca toma `MLOCK` ni `KLOCK`, asi que no hay ciclo y no hay deadlock.
STLOCK = threading.RLock(); MLOCK = threading.RLock(); KLOCK = threading.Lock()
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
    n = time.time()
    with FLOCK:   # T7: podar y contar bajo un solo lock
        FAILS[ip] = [t for t in FAILS.get(ip, []) if n - t < 900]
        return len(FAILS[ip]) >= 5
def last_line(path): txt = read(path).strip().splitlines(); return txt[-1].strip() if txt else ""
def estado_segments():
    segs = []
    for line in read(CHANGELOG).splitlines():
        if line.startswith("ESTADO:"): segs = [s.strip() for s in line[len("ESTADO:"):].split("|")]
    return segs
def table_rows(n=8): return [l for l in read(CHANGELOG).splitlines() if re.match(r"\|\s*20", l)][-n:]
def short_row(row): c = [x.strip() for x in row.strip().strip("|").split("|")]; return "%s %s: %s (%s)" % tuple(c[:2] + c[2:3] + c[4:5]) if len(c) >= 5 else row.strip()
def herdr_out(args, timeout=8, mach=None):
    try: return subprocess.run(["herdr"] + (["--machine", mach] if mach else []) + args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception: return ""
# Verificado en herdr 0.9.3 (tests/README.md): los errores van a `stderr` con exit 1 como
# `{"id":...,"error":{"code":...,"message":...}}`; el exit 0 trae el sobre en `stdout`
# (`{"id":...,"result":...}` en los comandos list/api, `{"type":"agent_started"|"agent_prompted",...}` en
# start/prompt). `herdr_out` queda para lo que solo necesita stdout; `herdr_cmd` es el camino para todo
# lo que inspecciona un resultado. Las formas INFERIDAS (workspace create / pane split / workspace close)
# se leen con `.get()`, sin inventar campos.
def _env(s):
    """JSON de herdr como dict, o None: texto de `agent read`, JSON roto, lista desnuda."""
    try: d = json.loads(s)
    except Exception: return None
    return d if isinstance(d, dict) else None
def herdr_cmd(args, timeout=8, mach=None):
    """Llamada a herdr con stdout, stderr y exit status, y su sobre parseado.

    `code`/`msg` son el codigo y el mensaje real de herdr (None si no los trajo); `res` es el `result`
    de los comandos list/api; `type` es el sobre de start/prompt; `dead` es socket muerto (vacio por
    los dos canales). No reventa con JSON malformado ni con salida vacia.
    """
    try:
        p = subprocess.run(["herdr"] + (["--machine", mach] if mach else []) + args, capture_output=True, text=True, timeout=timeout)
        out, err, rc = p.stdout, p.stderr, p.returncode
    except Exception:
        out, err, rc = "", "", 1
    env = _env(out) or _env(err)
    # La forma verificada de todo CLI de herdr es `{"id":.., "result":{..}}`: el `type` y el `agent` del
    # prompt viven DENTRO de `result` (captura viva 2026-10-06: agent prompt --wait exito). Leer de la
    # raiz daba None y un envio entregado se reportaba como 502. Se lee `result` primero y la raiz
    # como fallback, sin inventar campos.
    res = env.get("result") if isinstance(env, dict) else None
    body = res if isinstance(res, dict) else env
    e = env.get("error") if env else None
    code = e.get("code") if isinstance(e, dict) else None
    msg = e.get("message") if isinstance(e, dict) else None
    return {"out": out, "err": err, "rc": rc, "env": env, "code": code, "msg": msg,
            "res": res, "type": body.get("type") if body else None,
            "agent": body.get("agent") if isinstance(body, dict) else None,
            "dead": not out and not err}
# Codigo de error de herdr -> mensaje que un humano puede accionar. Un codigo de un herdr futuro cae en
# el default, que muestra el codigo: no se traga.
HERDR_MSG = {"agent_blocked": "el agente está esperando una aprobación en su panel: aprueba y vuelve a enviar",
             "agent_not_ready": "espera confirmación en su panel",
             "agent_prompt_stalled": "el mensaje no se entregó: el agente no pasó a working ni a blocked",
             "agent_name_not_found": "ese agente no existe en herdr",
             "agent_not_found": "ese agente no existe en herdr",   # codigo capturado en vivo (agent get)
             "timeout": "herdr no observó el estado antes de su tiempo límite",
             "usage": "comando no válido para herdr"}
SIN_RESPUESTA = "herdr no responde: el socket no devolvió nada"
def herdr_msg(code, msg):
    t = HERDR_MSG.get(code)
    if not t: t = "herdr devolvió el código %s" % code if code else "respuesta de herdr sin sobre JSON"
    return "%s (%s)" % (t, msg[:120]) if msg else t
def herdr_error(r):
    """Mensaje humano para el resultado de herdr; None si la respuesta es sana.

    Distingue el error de un agente (`code` en stderr) del socket muerto (vacio por los dos canales),
    que es lo que antes se fundia en un solo "herdr no responde".
    """
    if r["code"]: return herdr_msg(r["code"], r["msg"])
    if r["dead"]: return SIN_RESPUESTA
    if not r["out"]: return herdr_msg(None, r["err"].strip()[:120])
    return None
SIN_ENTREGA = "herdr respondió un sobre que no confirma la entrega"
# ---------- T5: la entrega se observa (`agent prompt --wait --timeout`) ----------
# `--timeout` va en ms. La unidad y una base estan verificadas en la captura viva del binario 0.9.3:
# su stall informa el plazo en el mensaje ("no working or blocked state observed within 5000 ms").
# 25 ms por caracter: herdr escribe el texto en el panel y el mensaje de la oficina es de hasta 4000
# chars, asi que el plazo tiene que crecer con el texto. 4000 ms extra cuando el agente no esta en un
# prompt interactivo (`interactive_ready`, campo opcional por agente en la captura real). Tope 20000
# ms: mas alla, el humano se queda mirando y la peticion del navegador se cuelga. El timeout del
# subprocess es `ms/1000 + 10` s: herdr tiene que ganar la carrera y devolver su sobre (`timeout` o
# `agent_prompt_stalled`); un `TimeoutExpired` de Python dejaria stdout y stderr vacios, que se lee
# como socket muerto, y se perderia el resultado observado. 5000 ms y la unidad son verificadas; 25
# ms/caracter, 4000 de margen y el tope 20000 son eleccion de la oficina, no una medida del binario.
WAIT_BASE = 5000; WAIT_CHAR = 25; WAIT_NOT_READY = 4000; WAIT_MAX = 20000; WAIT_SLACK = 10
def send_timeout(chars, ready):
    ms = WAIT_BASE + WAIT_CHAR * chars + (0 if ready else WAIT_NOT_READY)
    return max(WAIT_BASE, min(ms, WAIT_MAX))
# `stalled` = la entrega no se observo (ni `working`, ni `blocked`, ni `idle` tras el prompt): la UI no
# puede decir "enviado". `timeout` en `agent prompt` es el mismo caso que `agent_prompt_stalled`.
# Los codigos de panel (`agent_blocked`, `agent_not_ready`) no son stall: el agente esta vivo y espera
# una aprobacion; decir "no se entregó" mandaria al humano a reenviar en vez de aprobar.
# No hay reintento automatico: un stall lo decide el humano, porque reenviar a un panel que puede
# estar a medias es la unica forma de duplicar un mensaje.
STALLED = ("agent_prompt_stalled", "timeout")
PANEL = {"agent_blocked": "blocked", "agent_not_ready": "not_ready"}
# El sobre de `agent prompt --wait` trae el agente despues de aceptar el prompt. `matched_status` es
# INFERIDO (el stub lo emite, la captura viva no lo trae): no se usa para decidir, solo el estado del
# agente, que si esta verificado. Lo que no esta en el dominio `idle|working|blocked|done|unknown`, y
# un sobre sin `agent`, no confirman nada.
def send_outcome(r):
    """(outcome, entregado, stalled, estado observado, pane_id) a partir del sobre observado."""
    # `agent` viene dentro de `result` en la forma verificada; se lee del helper, con fallback a la raiz.
    ag = r.get("agent") if isinstance(r.get("agent"), dict) else (r["res"] or {}).get("agent") if isinstance(r["res"], dict) else None
    ag = ag if isinstance(ag, dict) else {}
    st = estado_real(ag["agent_status"]) if ag.get("agent_status") in ESTADOS else None
    pane = ag.get("pane_id")
    if r["code"] in PANEL: return PANEL[r["code"]], False, False, st, pane
    if r["code"] in STALLED: return "stalled", False, True, st, pane
    if r["type"] == "agent_prompted" and st: return st, True, False, st, pane
    return "no_response", False, True, st, pane
ENTREGA = {"working": "entregado: el agente pasó a working",
           "idle": "entregado: el agente aceptó el mensaje y volvió a idle",
           "done": "entregado: el agente aceptó el mensaje y quedó done",
           "blocked": "entregado: el agente aceptó el mensaje y quedó esperando una aprobación en su panel: aprueba"}
# El dominio de `agent_status`, verificado en el binario herdr 0.9.3 (tests/README.md). Lo que no esta
# en el dominio (clave ausente, None, un string de un Herdr futuro) es `unknown`, nunca `idle`.
ESTADOS = ("idle", "working", "blocked", "done", "unknown")
CAMPOS_H = ("pane_id", "focused", "interactive_ready", "completion_seq", "state_change_seq", "agent")
# `state_ws` no tiene fuentes de actividad propias: el texto se deriva del estado real, para que
# status y actividad no se contradigan nunca.
ACTS = {"working": "trabajando", "blocked": "esperando confirmación", "done": "terminado",
        "idle": "en reposo", "unknown": "estado desconocido"}
def estado_real(st): return st if st in ESTADOS else "unknown"
def mkagent(name, status, activity, color, role, h=None):   # h: dict del agente en `agent list` (None si no esta)
    st = estado_real(status)
    with STLOCK:   # T7: `SEEN` se escribe dentro del lock del rebuild: una transicion por cambio de estado,
                   # no dos. `state_ws` tambien pasa por aqui, sin `build_state`, y toma el mismo lock.
        if SEEN.get(name, [None])[0] != st: SEEN[name] = [st, time.strftime("%d %H:%M UTC", time.gmtime()), time.time()]
        since, ts = SEEN[name][1], SEEN[name][2]
    a = {"name": name, "status": st, "activity": activity, "color": color, "role": role, "since": since, "ts": ts}
    for k in CAMPOS_H: a[k] = h.get(k) if h else None   # opcionales por agente: None si Herdr no los trae
    return a
def tasks_list():
    try: return json.loads(read(TAREAS) or "{}").get("tareas", [])
    except Exception: return []
def tasks_cols():
    """Las columnas declaradas en `TAREAS.json`. T17: la UI no debe fijarlasy decir "el payload no las trae".
    `columnas` existe en el fichero; `tasks_list` lo descartaba, asi que el tablero y la pizarra 3D usaban 5
    columnas hardcodeadas. Se manda como viene; `None` si el fichero no lo trae, y la UI lo dice.
    """
    try: return json.loads(read(TAREAS) or "{}").get("columnas") or None
    except Exception: return None
def build_state():
    try:
        # el agente sin `name` no es un agente de la oficina: se omite, no se convierte en "?"
        # T6: la fuente es `api snapshot` (ver `snapshot`), no `agent list`: un subproceso por maquina y
        # por ciclo. Las `agents` del snapshot traen las mismas claves que `agent list` (captura verbatim).
        statuses = {a["name"]: a for a in raw_agents(None, 8) if a.get("name")}
    except Exception:
        statuses = {}
    segs = estado_segments()
    yo = next((s for s in segs if "(yo)" in s), segs[0] if segs else "")
    obrs = [s for s in segs if "wt-" in s or "(obrero)" in s]
    lock_txt = read(MOTOR)
    m = re.search(r"progreso\s*=\s*([^\s]+)", lock_txt)
    tact = ("progreso=" + m.group(1)) if m else ("ocupado" if lock_txt.strip() else "libre")
    supl = os.path.exists(SUPLENCIA)
    g = lambda n: statuses[n].get("agent_status") if n in statuses else "idle"
    C = COLORS; R = ROLES; cs = g("claude")
    agents = [mkagent("claude", cs, "pensando" if cs == "working" else "supervisando", C["claude"], R["claude"], statuses.get("claude")),
              mkagent("opencode2", g("opencode2"), yo or "trabajando", C["opencode2"], R["opencode2"], statuses.get("opencode2")),
              mkagent("tester", g("tester") if "tester" in statuses else ("working" if lock_txt.strip() else "idle"), tact, C["tester"], R["tester"], statuses.get("tester")),
              mkagent("explorer", g("explorer"), last_line(ENVIADOS) or "investigando", C["explorer"], R["explorer"], statuses.get("explorer")),
              mkagent("suplente", "working" if supl else "idle", "SUPLENTE AL MANDO" if supl else "en reposo", C["suplente"], R["suplente"], statuses.get("suplente"))]
    wsS = statuses.get("opencode2", {}).get("workspace_id", "w1")   # solo los obreros de la oficina de Strata
    for name, a in statuses.items():
        if name not in FIXED and a.get("workspace_id") == wsS:
            agents.append(mkagent(name, a.get("agent_status"),
                                  next((s for s in segs if name in s), obrs[0] if obrs else "trabajando"),
                                  "#fb7185", "Contrato " + name, a))
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
            "herdr": bool(statuses), "tareas": tasks_list(), "columnas": tasks_cols(),
            "updated": time.strftime("%H:%M:%S UTC", time.gmtime())}
def get_state():
    interval = 30 if os.path.exists(BENCH) else 5
    live = ev_health() == "live"   # T9: con eventos el camino rapido es el evento, no el reloj
    ciclo = RECONCILE if live else interval
    with STLOCK:   # T7: un solo hilo construye por intervalo; los demas leen lo publicado. Antes era
                   # check-then-act: N hilos SSE y de peticion entraban todos a `build_state`.
        if CACHE["data"] is None or (live and ev_dirty()) or time.monotonic() - CACHE["at"] >= ciclo:
            SNAP["ttl"] = ciclo   # T6: el snapshot envejece igual que el estado que construye
            nuevo = build_state(); nuevo["interval"] = interval   # T7: `interval` antes de publicar
            if live:
                with ELOCK: EV["dirty"] = False   # T9: la construccion consume la suciedad del evento
            CACHE["data"] = nuevo; CACHE["at"] = time.monotonic()   # publicar con una sola asignacion
        return CACHE["data"]   # el dict publicado no se muta nunca: los hilos SSE lo iteran sin riesgo
def valid_agent(agent):   # `LAST_AGENTS` se reasocia entero en `build_state`, nunca se muta en sitio
    get_state(); return bool(re.match(r"^[\w][\w.\-]{0,31}$", agent or "")) and (agent in LAST_AGENTS or agent in agmap())
def api_log(agent):
    if not valid_agent(agent): return {"error": "agente desconocido"}
    r = herdr_cmd(["agent", "read", agent, "--source", "recent-unwrapped", "--lines", "45", "--format", "text"], 15, agmap().get(agent))
    e = herdr_error(r)
    if e:
        return {"agent": agent, "error": e}
    return {"agent": agent, "lines": r["out"].strip().splitlines()[-30:]}
def api_hilo(agent):
    if not valid_agent(agent): return {"error": "agente desconocido"}
    mach = agmap().get(agent)
    env = []
    for line in read(ENVLOG).strip().splitlines()[-40:]:
        try: d = json.loads(line)
        except Exception: continue
        if d.get("confirmed") is False: continue   # T5: el hilo muestra lo entregado, no lo intentado
        if d.get("agent") == agent or (d.get("mode") == "viaclaude" and agent == "claude"):
            env.append("%s [%s] %s" % (d.get("ts", ""), d.get("mode", ""), (d.get("text") or "")[:120]))
    men = [short_row(r) for r in table_rows(60) if agent.lower() in r.lower()][-8:]
    # T8 (defecto 5): `envios.log` lo escribe `api_send` en bazzite, asi que el historial de un agente
    # remoto no existe en este servidor. No se finge: el payload declara el ambito con un campo que la UI
    # puede mostrar, y `envios` queda vacio porque no hay historial, no porque no se haya enviado nada.
    return {"agent": agent, "envios": env[-10:], "menciones": men, "audit": "local",
            "maquina": mach or "bazzite", "scope": AUDIT_SCOPE % mach if mach else None}
OFIMETA = H("~/.cache/strata-oficina/oficinas.json")
MFLOCK = threading.Lock()   # T7: `oficinas.json` (antes read-modify-write sin lock y truncate-and-write)
PERFILES = {"arquitecto": ("claude", "Arquitecto"), "desarrollador": ("opencode", "Desarrollador"),
            "investigador": ("agy", "Investigador"), "tester": ("pi", "Tester")}
JOBS = {}
JLOCK = threading.Lock()   # T7: `JOBS`: el hilo de `_crear` escribe y el handler lee una copia estable
def job_step(jid, s):
    with JLOCK:
        if jid in JOBS: JOBS[jid]["pasos"].append(s)
def job_set(jid, k, v):
    with JLOCK:
        if jid in JOBS: JOBS[jid][k] = v
def job_view(jid):
    """Copia estable del job para serializar (T7): `json.dumps` sobre la lista viva que crece es la misma
    clase de crash que iterar el estado en el SSE. Las claves que lee index.html (`pasos`, `estado`,
    `error`, `ws`) se conservan tal cual.
    """
    with JLOCK:
        j = JOBS.get(jid)
        return {k: (list(v) if isinstance(v, list) else v) for k, v in j.items()} if j else None
def meta():
    with MFLOCK: return _meta()
def meta_edit(fn):
    """Read-modify-write de `oficinas.json` bajo MFLOCK y con una sola escritura atomica (T7).

    `meta()` y `save_meta()` por separado no sirven: dos hilos que crean y borran a la vez se pierden
    oficinas. `fn` muta el dict y el resultado se publica entero.
    """
    with MFLOCK:
        m = _meta(); fn(m); _publica_meta(m)
def save_meta(m):
    with MFLOCK: _publica_meta(m)   # publicar un dict completo, atomico y bajo el lock
def _meta():
    try: return json.loads(read(OFIMETA) or "{}")
    except Exception: return {}
def _publica_meta(m):
    os.makedirs(os.path.dirname(OFIMETA), exist_ok=True)
    _atomic(OFIMETA, json.dumps(m, ensure_ascii=False))
def hj(args, timeout=15, mach=None):
    r = herdr_cmd(args, timeout, mach)
    return r["res"] if isinstance(r["res"], dict) else None
# T6: `herdr api snapshot`, verificado en el binario 0.9.3 (2026-10-06): rc=0, 6.6 KB, una sola llamada
# que trae `agents`, `workspaces`, `tabs`, `panes`, `layouts` y el foco. `herdr --machine mac-mini api
# snapshot` funciona igual (protocol 22, version 0.9.3) y trae la misma forma. Los `agents` del snapshot
# traen las mismas claves que `agent list` (`name`, `agent_status`, `pane_id`, `workspace_id`, `focused`,
# `interactive_ready` opcional, ...), asi que la fuente cambia y el payload no.
# `SNAP` guarda el snapshot por maquina con la TTL del intervalo del ciclo: el snapshot que construyo el
# estado es el mismo que leen `api_offices`, `agmap` y `strata_ws`, asi cada target se sondea una sola vez
# por ciclo. Se cachea tambien la respuesta vacia (herdr no responde) para no repetir un subproceso que ya
# se sabe muerto; el estado envejece igual que hoy. T7: `SNAP` vive bajo `STLOCK`, el mismo lock del
# rebuild, asi el snapshot que construyo el estado es el que se publica con el estado.
SNAP = {"data": {}, "ttl": 5}
def fld(snap, k):
    """Campo del snapshot como lista; [] si herdr no lo trajo. Nunca un crash por un campo ausente."""
    v = snap.get(k) if isinstance(snap, dict) else None
    return v if isinstance(v, list) else []
def snapshot(mach=None, timeout=15):
    with STLOCK:   # T7: leer y escribir `SNAP` es una sola operacion bajo el lock del rebuild
        c = SNAP["data"].get(mach)
        if c and time.monotonic() - c[0] < SNAP["ttl"]: return c[1]
        r = hj(["api", "snapshot"], timeout, mach)
        s = r.get("snapshot") if isinstance(r, dict) else None
        s = s if isinstance(s, dict) else {}
        SNAP["data"][mach] = (time.monotonic(), s)   # se publica la pareja entera, de una
        return s
def raw_agents(mach=None, timeout=15):
    """Agentes del snapshot de la maquina: la misma lista que daba `agent list`, en la misma forma.

    T9: en la maquina local se superpone el estado vivo del socket (`ev_overlay`). El snapshot de una
    maquina remota no se superpone: el socket de herdr es el de bazzite, y los eventos no llegan de
    la maquina remota.
    """
    ags = fld(snapshot(mach, timeout), "agents")
    return ev_overlay(ags) if mach is None else ags

# ---------- T9: `events.subscribe` por socket Unix: push en vez de polling ----------
# Protocolo VERBATIM del binario herdr 0.9.3, capturado en vivo 2026-10-06 (ver
# odd/tasks/oficina-herdr-v5.md, "Sondeos de eventos"): JSON newline-delimited en
# `$HERDR_SOCKET_PATH` (la unidad systemd la fija) o `~/.config/herdr/herdr.sock`. Los pedidos llevan
# `id` (sin `id` el servidor responde `invalid_request: missing field id`).
# `events.subscribe` -> ack `{"id":..,"result":{"type":"subscription_started"}}` y los eventos llegan
# por la MISMA conexion con forma `{"data":{...},"event":"..."}`. `ping` ->
# `{"id":..,"result":{"type":"pong","version":"0.9.3","protocol":22,...}}`.
# Las suscripciones GLOBALES necesitan solo `type`. `pane.agent_status_changed` y
# `pane.scroll_changed` requieren `pane_id`; `pane.output_matched` requiere `pane_id + source +
# match`, y `OutputMatch` es `{type: substring|regex, value: string}` (la clave es `value`, no
# `text`). Una entrada de forma invalida rechaza el SET ENTERO (observado dos veces), asi que la
# oficina manda solo las formas verificadas: nunca `scroll_changed` ni `output_matched`, porque un
# item mal formado mata toda la suscripcion y el canal se queda mudo.
# El evento de estado NO trae el nombre del agente (trae `pane_id` y `workspace_id`), asi que la
# correspondencia pane->nombre viene del snapshot de T6 y se refresca en `pane.agent_detected`.
# En el probe vivo, la segunda escritura sobre una misma conexion dio BrokenPipe: la suscripcion
# necesita una conexion larga y cada pedido suelto reconecta. El hilo NUNCA manda un metodo mutante:
# solo `events.subscribe` (y `ping` si hiciera falta); nunca `agent prompt`, `agent start`,
# `pane split`, `workspace create`/`close`, `server.stop`.
GLOBAL_SUBS = ("pane.agent_detected", "pane.created", "pane.closed", "pane.exited", "pane.updated",
               "pane.focused", "pane.moved", "workspace.created", "workspace.closed",
               "workspace.updated", "workspace.renamed", "workspace.metadata_updated",
               "workspace.focused", "workspace.moved", "workspace.reordered")
EV_ACK = "subscription_started"      # el unico ack que pone el canal en modo `live`
EV_TIMEOUT = 10.0                    # s de connect y de lectura del ack: el hilo nunca se cuelga
EV_BACK0 = 1.0                       # s: primer reintento
EV_BACKMAX = 30.0                    # s: tope del backoff (cota de la oficina, no una medida)
EV_WAKE = 0.2                        # s: granularidad con la que el SSE despierta ante un evento
# `RECONCILE` es el polling LENTO que mantiene frescos los campos que los eventos NO llevan: `cwd`,
# `terminal_title`, `interactive_ready`, `completion_seq` (ver `CAMPOS_H`). 30 s es eleccion de la
# oficina, no una medida: el ciclo de 5 s cuesta 12 subprocesos `api snapshot` por minuto compitiendo
# con la medicion de la RTX 3060, y el de 30 s cuesta 2. Ademas es el plazo del latido del SSE
# (`quiet >= 6` con quantum de 5 s) y el intervalo que la oficina ya usa con BENCH, asi el reposo son
# 2 subprocesos por minuto y el latido sigue siendo ~30 s. El camino rapido son los eventos: un cambio
# de estado se empuja en menos de 0.2 s, no en 5 s.
RECONCILE = 30
# `EV_TICK` es el tiempo maximo que el hilo espera una linea antes de mirar la bandera de parada. Con
# `readline()` sin `select` el hilo estaria 10 s colgado en un socket mudo y la parada no se notaria.
# `select` (stdlib) acota la espera sin imponer una reconexion periodica: el canal en reposo no se
# corta, solo se mira la bandera cada `EV_TICK` s.
EV_TICK = 1.0
# `agent_names` es la correspondencia pane_id -> NOMBRE de la oficina que pone el snapshot (T10: el titulo
# del aviso). `names` es la que ponen los eventos de deteccion y trae el `agent` (el tipo, p. ej. `agy`),
# que no es el nombre de la oficina: las dos se mantienen separadas para no inventar nombres.
# `dirty_ws` (T27) es la suciedad POR OFICINA: cada stream SSE esta confinado a la `ws` que pidio, asi que
# un cambio en `w2` tiene que despertar a quien mira `w2`, no solo a la oficina de Strata. La bandera global
# `dirty` sigue siendo la que consume `get_state` (T9), y es la unica fuente de la oficina de Strata.
EV = {"mode": "unavailable", "dirty": True, "subs": [], "panes": set(), "status": {}, "names": {},
      "agent_names": {}, "dirty_ws": {}, "thread": None, "stop": threading.Event()}
# `ELOCK` protege `EV` (`mode`, `dirty`, `dirty_ws`, `subs`, `panes`, `status`, `names`). Orden de adquisicion
# (T7, server.py:44-50): `MLOCK` -> `STLOCK` -> `ELOCK`, que es lo que hacen `agmap` -> `raw_agents` ->
# `ev_overlay` y `get_state` -> `build_state` -> `raw_agents`. `ELOCK` nunca toma `STLOCK` ni `MLOCK`
# (el hilo de eventos los toma por separado), asi que no hay ciclo y no hay deadlock.
ELOCK = threading.Lock()

def ev_path():
    """Ruta del socket: la que fija la unidad systemd (`HERDR_SOCKET_PATH`) o la del binario."""
    return os.environ.get("HERDR_SOCKET_PATH") or H("~/.config/herdr/herdr.sock")

def ev_subs(panes):
    """El set de suscripciones, en UN solo sitio: las globales (solo `type`) mas
    `pane.agent_status_changed` por cada `pane_id` conocido, en orden estable.

    El orden estable es lo que hace idempotente la re-suscripcion: dos listas iguales no vuelven a
    mandarse. Los `pane_id` salen del snapshot (`ev_overlay`) y de los eventos.
    """
    out = [{"type": t} for t in GLOBAL_SUBS]
    out += [{"type": "pane.agent_status_changed", "pane_id": p} for p in sorted(panes) if p]
    return out

def ev_health():
    """Salud del canal de eventos, para la UI: `live` | `polling` | `unavailable`."""
    with ELOCK: return EV["mode"]

def ev_dirty(oid=None):
    """True si hay que empujar YA. Solo en modo `live`: el polling no necesita bandera.

    `oid` es la oficina del stream (None = la de Strata, la que construye `get_state`). T27: un stream
    mirando `w2` se despierta con el evento de `w2`, no con el de otra oficina; y al reves, el evento de
    `w2` no mantiene despierto indefinidamente al stream de Strata, que solo consume la bandera global.
    La separacion por oficina es lo que evita el spin: la bandera que mira el stream es la que se consume
    en su propio bucle (`ev_take`).
    """
    with ELOCK:
        if EV["mode"] != "live": return False
        return EV["dirty"] if oid is None else bool(EV["dirty_ws"].get(oid))

def ev_take(oid):
    """Consumir la suciedad de la oficina `oid` despues de empujar (T27).

    `get_state` consume la bandera global dentro de `STLOCK` (T9), y la oficina de Strata se construye por
    ahi. `state_ws` no pasa por `get_state`, asi que un stream de otra oficina tiene que consumir SU
    bandera: sin consumo `ev_cycle` devuelve al instante en cada vuelta y el bucle gira sin dormir.
    Se consume solo la de la oficina pedida: la global queda intacta para el stream de Strata.
    """
    with ELOCK: EV["dirty_ws"][oid] = False

def ev_resub_needed():
    """El set crecio (aparecio un panel nuevo): hay que re-suscribir. Idempotente por comparacion."""
    with ELOCK: return ev_subs(EV["panes"]) != EV["subs"]

def ev_overlay(ags):
    """Estado vivo superpuesto por `pane_id` sobre los agentes del snapshot.

    El evento de estado no trae el nombre: el nombre lo pone el snapshot, por eso el overlay indexa
    por `pane_id`. Copia cada agente: el snapshot cacheado no se muta en sitio (T7: el dict publicado
    no se muta). Los `pane_id` del snapshot entran en `EV["panes"]`, que es lo que hace crecer la
    re-suscripcion cuando aparece un panel.
    """
    with ELOCK:
        st = dict(EV["status"])
        for a in ags:
            if a.get("pane_id"):
                EV["panes"].add(a["pane_id"])
                if a.get("name"): EV["agent_names"][a["pane_id"]] = a["name"]   # T10: el titulo del aviso
        if not st: return ags
    out = []
    for a in ags:
        p = a.get("pane_id")
        if p in st:
            a = dict(a); a["agent_status"] = estado_real(st[p])
        out.append(a)
    return out

def ev_event(line):
    """Aplica una linea de evento y marca el estado sucio. True si hay que refrescar el snapshot.

    `pane_agent_detected` llega con `event` de guiones y la suscripcion es de punto: se manejan las
    dos grafias. El evento de deteccion si trae `agent` y `pane_id`, asi que la correspondencia
    pane->nombre se actualiza en el acto; el snapshot envejece para que el ciclo la confirme.
    """
    try: d = json.loads(line)
    except Exception: return False
    if not isinstance(d, dict): return False
    kind = str(d.get("event") or "").replace(".", "_")
    data = d.get("data") if isinstance(d.get("data"), dict) else {}
    pane = data.get("pane_id") or (data["pane"].get("pane_id") if isinstance(data.get("pane"), dict) else None)
    # T27: el evento pertenece a una oficina, y cada stream SSE esta confinado a la que pidio, asi que hay
    # que marcar sucia LA suya. El `workspace_id` esta verbatim en la captura del evento de estado; para los
    # eventos de panel que no lo traen, el `pane_id` (`w2:p3`) dice la oficina. Una oficina remota nunca se
    # marca: el socket es el de bazzite y sus eventos no llegan (ver `raw_agents`), asi que su stream se
    # refresca en el latido, no en el evento.
    wsid = data.get("workspace_id") or (data["workspace"].get("workspace_id")
                                        if isinstance(data.get("workspace"), dict) else None)
    if not wsid and pane and ":" in pane: wsid = pane.split(":")[0]
    if kind == "pane_agent_detected":
        if pane and data.get("agent"):
            with ELOCK: EV["names"][pane] = data["agent"]
        wide = True
    elif kind == "pane_agent_status_changed":
        st = data.get("agent_status")
        if pane and st in ESTADOS:
            with ELOCK:
                prev = EV["status"].get(pane)   # T10: el ultimo estado OBSERVADO por panel
                EV["status"][pane] = st
            if st == "blocked" and prev != "blocked":   # solo la TRANSICION de entrada, no cada evento
                notif_plan(pane, data.get("workspace_id"))
        wide = bool(pane)
    elif kind in ("pane_created", "pane_closed", "pane_exited", "pane_updated", "pane_focused", "pane_moved"):
        wide = True
    elif kind.startswith("workspace_"):
        wide = True
    else:
        return False   # un evento de un herdr futuro no se traga ni ensucia: se ignora
    with ELOCK:
        if pane: EV["panes"].add(pane)
        EV["dirty"] = True
        if wsid: EV["dirty_ws"][wsid] = True   # T27: la oficina del evento, para su stream
    if wide:
        with STLOCK: SNAP["data"].pop(None, None)   # el snapshot local envejece: pane->nombre al ciclo
    return wide

def _ev_send(s, obj):
    s.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode())

def _ev_ack(line):
    """El ack verbatim: `{"id":..,"result":{"type":"subscription_started"}}`."""
    try: d = json.loads(line)
    except Exception: return False
    res = d.get("result") if isinstance(d, dict) else None
    return bool(isinstance(res, dict) and res.get("type") == EV_ACK)

def ev_subscribe(s, buf):
    """Manda la suscripcion con su `id` requerido y espera el ack en la cola del cliente. Devuelve True si empezo."""
    with ELOCK:
        subs = ev_subs(EV["panes"]); EV["subs"] = subs
    _ev_send(s, {"id": "oficina:subscribe", "method": "events.subscribe",
                 "params": {"subscriptions": subs}})
    return _ev_ack(_ev_read(buf, s, EV_TIMEOUT))

def ev_seed():
    """Los `pane_id` que la oficina conoce: los del snapshot local, la misma fuente de T6.

    Se siembra antes de la primera suscripcion: sin esto la suscripcion inicial lleva solo las globales
    y el estado de los paneles existentes no se enteraria hasta el primer `pane.agent_detected`.
    `raw_agents` registra los `pane_id` en `EV["panes"]` por `ev_overlay`.
    """
    return raw_agents(None)

def ev_client():
    """El hilo de eventos: una conexion larga, suscribir, aplicar, re-suscribir al crecer, reconectar.

    Degradacion obligatoria: socket ausente, refusado, timeout, ack que no es `subscription_started` o
    conexion cortada -> el modo pasa a `polling`/`unavailable` y `get_state` vuelve al ciclo de 5 s /30 s
    de hoy. El hilo no sale del bucle, no lanza subproceso, no bloquea los hilos HTTP, y el backoff esta
    acotado (`EV_BACK0` -> `EV_BACKMAX`, con reset al funcionar).

    Si el ack no es `subscription_started` la conexion se cierra y se reintenta: una conexion que no
    suscribio no tiene eventos que leer, y quedarse leyendo 10 s en ella es la espera muerta que hoy
    sondea cada ciclo. El reintento acotado es lo que permite que la oficina se recupera sola cuando
    herdr vuelve, sin reiniciar el servicio.
    """
    back = EV_BACK0
    while True:
        s = None
        if EV["stop"].is_set(): return
        try:
            ev_seed()   # los paneles existentes entran en el set: la suscripcion inicial es completa
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(EV_TIMEOUT)   # acotado: el hilo nunca se cuelga en un socket mudo
            s.connect(ev_path())
            buf = bytearray()   # la cola del cliente: T10 exige que un chunk de 8 lineas se procese entero
            live = ev_subscribe(s, buf)
            with ELOCK: EV["mode"] = "live" if live else "polling"
            if live:
                back = EV_BACK0        # el canal funciona: el reintento vuelve a su cota minima
                while True:
                    if EV["stop"].is_set(): break
                    line = _ev_read(buf, s, EV_TICK)   # `select`: mirar la bandera sin colgarse
                    if line is None: continue        # el canal en reposo: no es un fallo
                    if not line: break               # el servidor cerro la conexion
                    if _ev_ack(line): continue       # ack de una re-suscripcion
                    if ev_event(line) and ev_resub_needed() and not ev_subscribe(s, buf): break
        except Exception:
            pass                                          # nunca un crash del servidor por el socket
        with ELOCK:
            if EV["mode"] == "live": EV["mode"] = "polling"   # cortada: se sondea hasta reconectar
        if s:
            try: s.close()
            except Exception: pass
        EV["stop"].wait(back)   # el sueno del backoff se corta al parar: el hilo sale sin esperar el tope
        back = min(back * 2, EV_BACKMAX)

def _ev_read(buf, s, timeout):
    """Una linea de la cola del cliente, con `select` acotado solo cuando la cola esta vacia.

    DEFECTO encontrado por T10 (requisito 6, el bloqueado en masa): `makefile` lee 8192 B del socket y
    deja las lineas que ya llegaron en su propio buffer, y `select` sobre el socket entonces dice "no hay
    nada": con 3 `pane.agent_status_changed` en un solo chunk el cliente veia la primera linea y se
    quedaba 1 s sin ver la segunda (observado en el test). La cola del cliente es la que tiene las lineas
    pendientes, asi que se lee con `recv` y se parte por `\n`, la misma lectura que hace el stub emulado
    (que tampoco usa `makefile`: un `readline` bloqueado sostiene el lock del socket y `close()` tarda 10 s).

    Devuelve None si no hay linea en `timeout` (el canal en reposo: no es un fallo, no hay que reconectar),
    y `""` si el peer cerro (EOF: `recv` da cadena vacia).
    """
    if not buf:
        rd, _, _ = select.select([s], [], [], timeout)
        if not rd: return None
        try: chunk = s.recv(4096)
        except (OSError, ValueError): return ""   # `drop()` cerro la conexion: el corte del probe vivo
        if not chunk: return ""   # EOF: el servidor cerro la conexion
        buf.extend(chunk)
    i = buf.find(b"\n")
    if i < 0: return None   # media linea: se espera el resto, no se reconecta
    line = bytes(buf[:i]); del buf[:i + 1]
    return line.decode("utf-8", "replace")

def ev_stop():
    """Parada ordenada del hilo de eventos (el cierre del servicio, y el teardown de los tests).

    Es un `Event`, no un bool: el hilo esta dormido en el backoff (hasta `EV_BACKMAX` s) y un bool solo
    se veria al despertar. Con `Event.set()` el sueno se corta al instante, y el hilo sale en `EV_TICK` s.
    Los hilos daemon de un test anterior seguirian reconectando contra el socket del test que corre
    ahora, porque `HERDR_SOCKET_PATH` es una variable de proceso.
    """
    with ELOCK: EV["stop"].set()

def ev_start():
    """Un solo hilo de fondo para la suscripcion y el worker de avisos. Se arranca en `__main__`, no en el
    import: los tests lo arrancan cuando quieren un socket emulado, y la suite que no lo arranca no lanza
    subprocesos (T9) ni avisos (T10).

    Los dos hilos comparten `EV["stop"]`, asi `ev_stop()` apaga el canal y el worker en el mismo paso
    (el teardown de los tests y el cierre del servicio).

    `dirty_ws` se limpia aqui, no en el `reset` de la fixture: el canal que arranca ahora es el que pone
    la suciedad, y una marca de un test anterior despertaria a un stream nuevo sin evento suyo.
    """
    with ELOCK:
        if EV["thread"] and EV["thread"].is_alive(): return EV["thread"]
        EV["stop"].clear(); EV["dirty_ws"].clear()   # T27: la suciedad por oficina es del canal nuevo
        t = threading.Thread(target=ev_client, daemon=True, name="oficina-events")
        n = threading.Thread(target=notif_client, daemon=True, name="oficina-notif")
        EV["thread"] = t; NOTIF["thread"] = n
        t.start(); n.start(); return t

def ev_cycle(interval, oid=None):
    """El sueno del bucle SSE: un quantum de `interval` segundos, cortado cuando un evento ensucia.

    `oid` (T27) es la oficina del stream: en `live` el sueno se corta solo si hay evento PARA ESA oficina.
    En `polling`/`unavailable` es el `time.sleep(interval)` de hoy, asi que el latido (`quiet >= 6`) sigue
    siendo ~30 s y el respaldo de la UI (el sondeo de 5 s) es el mismo de hoy. En `live` se despierta a los
    `EV_WAKE` s: un cambio de estado se empuja en menos de 0.2 s, y un reposo completo duerme el quantum
    entero, asi que la condicion de latido no cambia.
    """
    if ev_health() != "live":
        time.sleep(interval); return
    end = time.monotonic() + interval
    while time.monotonic() < end:
        if ev_dirty(oid): return   # hay evento PARA esta oficina: el bucle empuja ahora
        time.sleep(min(EV_WAKE, end - time.monotonic()))

# ---------- T10: `herdr notification show` cuando un agente pasa a `blocked` ----------
# Forma VERIFICADA en el binario herdr 0.9.3 (2026-10-06, `herdr notification show --help`):
# `notification show <TITLE> [--body <TEXT>] [--position top-left|top-right|bottom-left|bottom-right]
# [--sound none|done|request]`. El titulo es POSICIONAL y OBLIGATORIO, asi que va primero; las banderas
# salen en el orden `--body --position --sound`. Se lanza por CLI (no por socket: `notification` no esta
# en los metodos suscribibles) con `subprocess.run` y `shell=False`, via `herdr_cmd`.
#
# Es el pago humano de T9: `blocked` significa que el agente esta parado delante de un dialogo de
# aprobacion y NECESITA a un humano. Si solo se pinta en la pagina, el aviso llega cuando el humano ya
# esta mirando la pagina; el sonido `request` es el que lo llama desde el otro lado de la casa.
NOTIF_CMD = ("notification", "show")   # argv verbatim; sin `--machine`: el socket de avisos es el de bazzite
NOTIF_POS = "top-right"
NOTIF_SOUND = "request"
# `NOTIF_POS` es eleccion de la oficina, no una medida: el diorama y el panel que hay que aprobar viven
# en el centro y la izquierda de la pantalla, y la esquina inferior derecha es la que usa el sistema para
# sus propios avisos, asi que ahi habria colision. Arriba a la derecha el aviso cae fuera del area de
# trabajo, no tapa el panel que hay que aprobar, y es la esquina donde la mirada descansa.
# `NOTIF_WIN` es la ventana de dedupe POR PANEL, eleccion de la oficina, no medida: un humano tarda mas
# de 20 s en oir un aviso, caminar y aprobar, asi que dos avisos del mismo panel en menos de 20 s son
# ruido. El caso real que hay que colapsar es `blocked -> idle -> blocked` rapido: el agente que vuelve a
# pedir confirmacion justo despues de responder la anterior. Con ventana de 20 s sale UN aviso; el
# bloqueado de un trabajo nuevo 30 s despues si tiene el suyo.
# `NOTIF_BURST` en `NOTIF_BURST_WIN` acotan el SPAWN: la creacion de una oficina divide hasta 8 paneles y
# arranca hasta 8 agentes, que pueden bloquearse en su prompt de confianza a la vez. Ocho subproceso de
# golpe compiten con la medicion de la RTX 3060 y un humano no puede leer 8 avisos a la vez. 4 en 10 s es
# cota de la oficina, no medida: la cota se aplica en el SPAWN (el worker cuenta lo que lanza, no lo que se
# le pide), asi que el limite es una cota de procesos, no de intenciones. Lo que no cabe se registra como
# omitido, no como un fallo: el panel sigue bloqueado y se ve rojo en la oficina.
NOTIF_WIN = 20.0; NOTIF_BURST = 4; NOTIF_BURST_WIN = 10.0
NOTIF_QUEUE = 16      # cola acotada: un pico de eventos no hace crecer la memoria; lo que no cabe se omite
NOTIF_TIMEOUT = 5.0   # s de subproceso: un CLI local responde en <1 s; 5 s es la cota para que un herdr
                      # colgado no tenga al worker sin consumir cola. Nunca se sostiene un lock en el spawn.
NOTIF_IDLE = 0.5      # s de sueno del worker con la cola vacia: `ev_stop()` corta el sueno al instante
NOTIF = {"pend": [], "last": {}, "spawns": [], "sent": 0, "skips": 0, "fails": 0,
         "wake": threading.Event(), "thread": None}
# `NLOCK` protege `NOTIF` (`pend`, `last`, `spawns`, contadores). Es un lock HOJA: nunca toma `ELOCK`,
# `STLOCK` ni `MLOCK`, asi que se anade al orden de T7 (`MLOCK` -> `STLOCK` -> `ELOCK` -> `NLOCK`) sin
# ciclo. El subproceso se lanza con `NLOCK` SUELTO: T10 exige que el spawn no sostenga `STLOCK`.
NLOCK = threading.Lock()

def notif_body(pane, ws, ts):
    """`--body`: el panel, la oficina y la hora. La hora es la marca UTC que ya usa `SEEN` en `mkagent`."""
    return "panel %s · oficina %s · %s" % (pane, ws or "sin workspace", ts)

def notif_view():
    """Salud de los avisos, para los tests y para una futura ruta: `sent`, `skips`, `fails`, ultimo panel.

    NO entra en el payload de `/api/state`: las dos pruebas de claves exactas de T2
    (`test_build_state_linea_base`, `test_claves_de_siempre_y_payload_serializable`) fijan el set de claves
    del estado y viven en `test_herdr_stub.py`, fuera de la superficie de T10. La clave aditiva (`notif`) se
    anade cuando el padre autoriza esas dos anclas; el helper ya esta aqui y no renombra nada.
    """
    with NLOCK:
        l = max(NOTIF["last"].values(), key=lambda x: x["at"]) if NOTIF["last"] else None
        return {"sent": NOTIF["sent"], "skips": NOTIF["skips"], "fails": NOTIF["fails"],
                "last_pane": l["pane"] if l else None, "last_ts": l["ts"] if l else None,
                "win": NOTIF_WIN, "burst": NOTIF_BURST}

def notif_plan(pane, ws):
    """Decide el aviso en la transicion a `blocked` y lo pone en la cola. NO lanza el subproceso aqui.

    Tres cotas, en el orden de la lectura:
      1. la transicion: `EV["status"]` guarda el ultimo estado OBSERVADO por panel, y solo una ENTRADA en
         `blocked` (previo distinto de `blocked`) es aviso nuevo: un panel que sigue bloqueado no spam;
      2. `NOTIF_WIN` POR PANEL (`NOTIF["last"]` indexado por `pane_id`), que colapsa la rafaga
        `blocked -> idle -> blocked` de ese panel sin tapar el aviso de otro panel; la ventana global
        convertiria el bloqueado en masa en UN solo aviso, que es el peor caso para el humano;
      3. `NOTIF_BURST` en `NOTIF_BURST_WIN` y la cola acotada, que es el bloqueado en masa. La cota de
         spawn se aplica en el worker (`notif_client`), sobre lo que se va a lanzar; aqui se acota la cola.
    Se llama desde el hilo de eventos: lee la correspondencia pane->nombre bajo `ELOCK` y escribe la cola
    bajo `NLOCK`. El titulo usa el nombre de la oficina; si no lo conocemos dice el `pane_id` a secas.
    Devuelve True si el aviso quedo en cola.
    """
    with ELOCK:
        name = EV["agent_names"].get(pane) or EV["names"].get(pane)
    ts = time.strftime("%d %H:%M UTC", time.gmtime())
    now = time.monotonic()
    with NLOCK:
        l = NOTIF["last"].get(pane)
        if l and now - l["at"] < NOTIF_WIN:
            NOTIF["skips"] += 1; return False   # ventana por panel: una rafaga es un solo aviso
        if len(NOTIF["pend"]) >= NOTIF_QUEUE:
            NOTIF["skips"] += 1; return False   # cola acotada: un pico no hace crecer la memoria
        title = (name + " te necesita") if name else ("Panel " + pane + " te necesita")
        NOTIF["pend"].append({"pane": pane, "title": title, "body": notif_body(pane, ws, ts), "ts": ts})
        NOTIF["wake"].set()
    return True

def notif_client():
    """El worker que lanza `herdr notification show`: el hilo de eventos nunca espera un subproceso.

    El spawn se hace con `NLOCK` suelto y sin `STLOCK`, `MLOCK` ni `ELOCK`; el unico argv nuevo es
    `notification show` (ningun metodo mutante). Un exit 1, salida vacia por los dos canales, socket
    muerto o binario ausente se registra en `fails` y el bucle sigue: un aviso que no salio no puede
    matar el canal de eventos ni el estado que se empuja al navegador. El subproceso corre en su propio
    hilo, asi que la lectura del socket no se detiene ni 5 s.
    """
    while True:
        if EV["stop"].is_set(): return
        with NLOCK:
            item = NOTIF["pend"].pop(0) if NOTIF["pend"] else None
            if item is not None:
                # La cota de spawn se aplica AQUI, sobre lo que se va a lanzar, no sobre lo que se pidio:
                # si no, una rafaga de 8 eventos pasa el filtro de `notif_plan` y fork 8 procesos.
                now = time.monotonic()
                live = [t for t in NOTIF["spawns"] if now - t < NOTIF_BURST_WIN]
                NOTIF["spawns"] = live
                if len(live) >= NOTIF_BURST:
                    NOTIF["skips"] += 1; item = None
        if item is None:
            NOTIF["wake"].clear()
            NOTIF["wake"].wait(NOTIF_IDLE)   # sueno acotado: la parada se ve al despertar
            continue
        r = herdr_cmd(list(NOTIF_CMD) + [item["title"], "--body", item["body"], "--position", NOTIF_POS,
                                          "--sound", NOTIF_SOUND], NOTIF_TIMEOUT)
        # El exito de `notification show` no esta capturado en vivo: se exige exit 0 y ALGO de salida. Un
        # rc=0 con los dos canales vacios es el socket muerto de T4, y ese aviso no se cuenta como enviado.
        ok = r["rc"] == 0 and bool(r["out"] or r["err"])
        with NLOCK:
            NOTIF["spawns"].append(time.monotonic())
            NOTIF["sent"] += 1
            NOTIF["last"][item["pane"]] = {"pane": item["pane"], "ts": item["ts"], "at": time.monotonic()}
            if not ok: NOTIF["fails"] += 1
MCACHE = {"at": 0, "data": []}; HOMES = {}; AGCACHE = {"at": 0, "data": {}, "ttl": 20}
# T8 (defecto 2): `HOMES` guarda la pareja `(home, marca de la ultima sondadura)`. Un `""` es un fallo,
# no un resultado: el exito no caduca (el home de una maquina no cambia) y el fallo solo durante
# `RHOME_NEG` segundos, para que una maquina muerta no se sondee en cada peticion y una que volvio a
# responder se recupere sin reiniciar el servicio.
RHOME_NEG = 60
# T8 (defecto 6): `AGCACHE` declara su TTL (`ttl`) para que crear y borrar la invaliden igual que `SNAP`
# (T6). Invalidar una cache es dejar su marca fuera de su TTL, sin tocar lock ni datos.
def envejecer(c): c["at"] = 0.0
def machines():
    """Máquinas remotas guardadas en herdr (las habilitadas). bazzite es la local."""
    with MLOCK:   # T7: `MCACHE` se publica entero (`data` se reasocia, nunca se muta en sitio)
        if time.time() - MCACHE["at"] < 300: return MCACHE["data"]
        try: ms = [m for m in json.loads(herdr_out(["machine", "list", "--json"], 10)) if m.get("enabled")]
        except Exception: ms = []
        MCACHE.update(at=time.time(), data=[{"label": m["label"], "target": m["target"]} for m in ms]); return MCACHE["data"]
def target(mach): return next((m["target"] for m in machines() if m["label"] == mach), None)
def ssh_run(mach, cmd, t=30, inp=None):
    tg = target(mach)
    if not tg: return ""
    full = 'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; ' + cmd
    try: return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", tg, full], capture_output=True, text=True, timeout=t, input=inp).stdout
    except Exception: return ""
def rhome(mach):
    """Home de una maquina remota, sin cache de un fallo para siempre (T8, defecto 2).

    Antes `if mach not in HOMES` guardaba tambien el `""` de una sondadura fallida: una sola SSH que no
    respondia convertia cada creacion de oficina remota en 502 de forma permanente, sin recuperacion sin
    reinicio del servicio. El exito se cachea sin caducidad; el fallo solo `RHOME_NEG` segundos, asi una
    maquina muerta no se sondea en cada peticion y la que volvio a responder se recupera sola.
    """
    with MLOCK:   # T7: `HOMES` por maquina; RLock porque `ssh_run` -> `target` -> `machines` reentra
        h = HOMES.get(mach)
        if h and h[0]: return h[0]                              # el home conocido no caduca
        if h and time.monotonic() - h[1] < RHOME_NEG: return ""   # muerta: una sola sondadura por ventana
        home = ssh_run(mach, "echo $HOME", 15).strip()
        HOMES[mach] = (home, time.monotonic())   # T7: se publica la pareja entera, de una
        return home
def split_id(oid): return tuple(oid.split(":", 1)) if ":" in oid else (None, oid)
def agmap():
    """nombre de agente -> máquina (None = bazzite)."""
    with MLOCK:   # T7: `AGCACHE` bajo `MLOCK`; `raw_agents` toma `STLOCK` dentro (MLOCK -> STLOCK)
        if time.time() - AGCACHE["at"] < AGCACHE["ttl"]: return AGCACHE["data"]   # T8: la TTL es explicita
        d = {a.get("name"): None for a in raw_agents() if a.get("name")}
        for m in machines():
            for a in raw_agents(m["label"]):
                if a.get("name") and a["name"] not in d: d[a["name"]] = m["label"]
        AGCACHE.update(at=time.time(), data=d); return d
def strata_ws(ws, raw=None):
    for w in ws:
        if w.get("label") == "Strata3060": return w.get("workspace_id")
    names = {a.get("name"): a.get("workspace_id") for a in (raw if raw is not None else raw_agents())}
    return names.get("opencode2", "w1")
def api_offices():
    st = get_state()
    loc = snapshot()   # T6: la maquina local: una sola llamada, la misma que construyo el estado
    sid = strata_ws(fld(loc, "workspaces"), fld(loc, "agents"))
    m = meta(); offs = []
    for mach in [None] + [x["label"] for x in machines()]:
        snap = loc if mach is None else snapshot(mach, 20)
        ws = fld(snap, "workspaces"); raw = fld(snap, "agents")
        for w in ws:
            wid = w.get("workspace_id"); oid = (mach + ":" + wid) if mach else wid; es = (mach is None and wid == sid)
            ags = [a.get("name") for a in raw if a.get("workspace_id") == wid and a.get("name")]
            tl = tasks_list() if es else []
            esp = [t for t in tl if t.get("columna") == "ESPERA OK" and t.get("aprobador") == "adrian"]
            offs.append({"id": oid, "nombre": w.get("label") or wid, "maquina": mach or "bazzite",
                         "agentes": [a["name"] for a in st["agents"]] if es else ags,
                         "tareas": len([t for t in tl if t.get("columna") in ("EN CURSO", "ESPERA OK", "EN COLA")]),
                         "needs": bool(esp), "strata": es, "foco": es, "cwd": m.get(oid, {}).get("cwd", ""),
                         "perfiles": m.get(oid, {}).get("agentes", [])})
    return {"offices": offs, "strata": sid, "maquinas": ["bazzite"] + [x["label"] for x in machines()]}
def state_ws(oid):
    mach, wid = split_id(oid)
    m = meta().get(oid, {}); roles = {a["name"]: a["rol"] for a in m.get("agentes", [])}
    ags = []
    for a in raw_agents(mach):
        if a.get("workspace_id") == wid and a.get("name"):
            st = estado_real(a.get("agent_status"))
            ags.append(mkagent(a["name"], st, ACTS[st], "#fb7185", roles.get(a["name"], a.get("agent", "agente")), a))
    return {"agents": ags, "metrics": {}, "ticker": [], "queue": {"done": 0, "total": 0}, "lock": False, "bench": False,
            "suplencia": False, "herdr": True, "tareas": [], "ws": oid, "maquina": mach or "bazzite", "updated": time.strftime("%H:%M:%S UTC", time.gmtime())}
# T8 (defecto 3): el dominio de label que `/api/state?ws=` acepta es el que `machine list --json` produce
# de verdad (letras, digitos, `-` y `_`, con mayusculas: la captura real trae `mac-mini` y `macbook-air`,
# y el target `31017423Z@100.99.86.60` lleva mayusculas). Antes la regex `[a-z0-9][a-z0-9-]{0,31}`
# rechazaba `_` y mayusculas, y la peticion caia en silencio a `get_state()`: una oficina remota
# renderiaba la oficina de Strata. Un label desconocido es un error explicito, nunca el estado local.
WSRE = r"^([A-Za-z0-9][A-Za-z0-9_-]{0,31}:)?w[0-9A-Za-z]+$"
def state_for(ws):
    """(code, payload) para `/api/state?ws=...`: la maquina del label, sin fallback silencioso a lo local.

    `ws` vacio es la oficina de Strata, como siempre. Con label, la maquina tiene que estar en
    `machine list`: si no esta, se responde un error y el payload dice que se pidio, de modo que la UI
    no puede pintar la oficina local como si fuera la remota.
    """
    if not ws: return 200, get_state()
    mach, wid = split_id(ws)
    if not re.match(WSRE, ws): return 400, {"error": "oficina inválida: usa wN o máquina:wN", "ws": ws}
    if mach == "bazzite": return 200, state_ws(wid)   # bazzite es la maquina local: `bazzite:wN` es la misma oficina
    if mach and not target(mach): return 404, {"error": "máquina desconocida: " + mach, "ws": ws, "maquina": mach}
    return 200, state_ws(ws)

# ---------- T27: `/api/events` lleva LA oficina pedida, y el bucle sale cuando el cliente se va ----------
# El stream de hoy construye `build_state`, que filtra a la oficina de Strata (`wsS` = el workspace de
# `opencode2`), asi que un cambio en otra oficina no genera frame (medido en vivo: 16 frames, los mismos
# 5 agentes de `w1`, y ningun frame ante un cambio real en `wJ`). El dominio de `ws` que `/api/events`
# acepta es el MISMO que `/api/state?ws` (T8, `WSRE`), y la regla es la misma: sin `ws` es la oficina de
# Strata exactamente como hoy, y un label desconocido es un error explicito, nunca el estado local.
# Cada conexion esta confinada a la oficina que pidio: no se envia a todos los clientes el dato de todas.
# `ev_scope` devuelve (code, oid) con `oid` None para la oficina de Strata, que es la unica que construye
# `get_state` (metrics, cola, ticker, tareas); las demas se construyen con `state_ws`, que lee el snapshot
# de su maquina y el overlay de eventos por `pane_id`.
def ev_scope(ws):
    if not ws: return 200, None
    mach, wid = split_id(ws)
    if not re.match(WSRE, ws):
        return 400, {"error": "oficina inválida: usa wN o máquina:wN", "ws": ws}   # el mismo error que `state_for`
    if not mach: return 200, (None if wid == strata_ws(fld(snapshot(), "workspaces")) else wid)
    if mach == "bazzite":   # `bazzite:wN` es la maquina local, como en `state_for`
        return 200, (None if wid == strata_ws(fld(snapshot(), "workspaces")) else wid)
    if not target(mach):
        return 404, {"error": "máquina desconocida: " + mach, "ws": ws, "maquina": mach}
    return 200, ws
# `SSE_MAX` es la cota del bucle: un handler de SSE nunca es eterno. La condicion de salida principal es
# `_sse_alive` (el cliente se ve en un quantum, ~5 s), y esta cota es el respaldo para el caso que `select`
# no ve: un cliente que sigue conectado, no lee nada y no se va (un proxy que mantiene la conexion abierta,
# o una pestaña suspendida). 30 min son 60 latidos (`quiet >= 6` con quantum de `interval` = 5 s, ~30 s), y
# la navegacion de la oficina no tiene un hueco de 30 min: el `EventSource` del navegador se reabre solo
# (`es.onerror` -> `connect()` en 4 s), asi que cortar a la cota es una renovacion de la conexion, no un
# corte visible. Son eleccion de la oficina, no una medida del binario.
# `SSE_TIMEOUT` es el tiempo maximo de una escritura: `wfile` es un fichero de socket sin buffer
# (`BaseHTTPRequestHandler.wbufsize` = 0), asi que un `send` con la ventana TCP llena se cuelga y el hilo
# queda colgado para siempre, sin cota y sin `select` que lo vea. Con timeout, la escritura falla y el
# `except` del bucle la trata como lo que es: el cliente no consume, la conexion se acaba.
SSE_MAX = 1800.0; SSE_TIMEOUT = 2.0
def slug(t): return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:12] or "ofi"
KINDS = {"claude": "Claude Code", "opencode": "opencode", "pi": "pi", "ada-cli": "ada-cli", "agy": "Antigravity (agy)"}
KCACHE = {}
ADA_DIR = H("~/.local/share/strata-oficina/ada")
ADA_PATH = ADA_DIR + ":" + H("~/.nvm/versions/node/v22.23.2/bin") + ":" + H("~/.local/bin") + ":/usr/local/bin:/usr/bin:/bin"
# T8 (defecto 1): `ADA_PATH` y el shim `pi` -> ada-cli viven en la home de bazzite, asi que un panel de
# una maquina remota no los ve. `kinds` remoto excluye `ada-cli` por diseño; el rechazo se hace explicito
# y nunca se lanza un binario local en una maquina remota.
ADA_REMOTE = "ada-cli no se puede crear en una máquina remota: su shim vive en la home de bazzite"
# En los paneles de ada-cli, `pi` es este script: herdr lo arranca como pi y HERDR_AGENT=pi (solo en este proceso)
# mantiene la identidad aunque ada-cli se renombre a sí mismo al arrancar.
try:
    os.makedirs(ADA_DIR, exist_ok=True); f = os.path.join(ADA_DIR, "pi")
    if os.path.islink(f): os.unlink(f)
    with open(f, "w") as fh: fh.write("#!/bin/sh\nexport HERDR_AGENT=pi\nexec %s %s \"$@\"\n" % (H("~/.nvm/versions/node/v22.23.2/bin/node"), H("~/ada-cli/packages/coding-agent/dist/cli.js")))
    os.chmod(f, 0o755)
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
def kinds(mach=None):
    with KLOCK:   # T7: `KCACHE` por maquina; la pareja (time, data) se publica entera
        c = KCACHE.get(mach)
        if c and time.time() - c[0] < 600: return c[1]
        if not mach:
            d = {"claude": ["opus", "sonnet", "haiku", "fable"],
                 "opencode": [l.strip() for l in _cmd(["opencode", "models"]).splitlines() if "/" in l],
                 "pi": _table(_cmd(["pi", "--list-models"])), "ada-cli": _table(_cmd(["ada-cli", "--list-models"])),
                 "agy": [l.split()[0] for l in _cmd(["agy", "models"]).splitlines() if l and not l.startswith("Fetching")]}
        else:   # remoto: solo los tipos instalados allí; ada-cli queda fuera (herdr no lo detecta fuera del home)
            have = set(ssh_run(mach, "for c in claude opencode pi agy; do command -v $c >/dev/null && echo $c; done", 20).split())
            d = {}
            if "claude" in have: d["claude"] = ["opus", "sonnet", "haiku", "fable"]
            if "opencode" in have: d["opencode"] = [l.strip() for l in ssh_run(mach, "cd ~ && opencode models", 40).splitlines() if "/" in l]
            if "pi" in have: d["pi"] = _table(ssh_run(mach, "pi --list-models 2>&1", 40))
            if "agy" in have: d["agy"] = [l.split()[0] for l in ssh_run(mach, "agy models 2>&1", 40).splitlines() if l and not l.startswith("Fetching")]
        data = {k: {"nombre": KINDS[k], "modelos": v} for k, v in d.items()}
        KCACHE[mach] = (time.time(), data); return data
def _start(nombre, kind, model, pid, cwd, auto, mach=None):
    if kind == "ada-cli":   # en su panel, `pi` es un enlace a ada-cli (ADA_PATH), así herdr lo reconoce como pi
        if mach: return "ERROR " + ADA_REMOTE   # T8, defecto 1: el shim es una ruta de la home de bazzite
        extra = ["--model", model] if model else []
        # `mach` se pasa en las dos ramas: `--machine` es parte del comando, no del `kind` (sin la bandera
        # herdr habla con el socket de bazzite y el agente se crea en la maquina local).
        r = herdr_cmd(["agent", "start", nombre, "--kind", "pi", "--pane", pid, "--timeout", "60000"] + (["--"] + extra if extra else []), 75, mach)
    else:
        extra = (["--model", model] if model and kind != "opencode" else [])
        if auto and kind in ("claude", "agy"): extra.append("--dangerously-skip-permissions")
        if auto and kind == "opencode": extra.append("--auto")
        r = herdr_cmd(["agent", "start", nombre, "--kind", kind, "--pane", pid, "--timeout", "60000"] + (["--"] + extra if extra else []), 90, mach)
    # antes: substringes sobre stdout (`"interactive_ready":true`, `"agent_started"`, `agent_not_ready`), que
    # no veian el error: ese vive en stderr. Ahora se parsea el sobre. `--wait` no se añade a `start`: la
    # salida verificada es `agent_started` y el sobre se lee con `.get()`, asi que un campo extra de un
    # herdr futuro (p. ej. `matched_status`) no rompe el `listo` y no se inventa nada.
    if r["type"] == "agent_started": return "listo"
    if r["code"] == "agent_not_ready": return HERDR_MSG["agent_not_ready"]
    e = herdr_error(r)
    if e: return "ERROR " + e
    return "ERROR " + (r["out"][-160:] or SIN_RESPUESTA)
def oc_config(nombre, model, mach=None):
    """opencode no acepta --model en su interfaz: cada agente recibe su propio fichero de configuración."""
    if mach:
        home = rhome(mach)
        # T8, defecto 4: la ruta se construye con `rhome`. Con el home sin leer, `rhome + "/.cache/..."`
        # cae en `/.cache/...`, fuera del home de la maquina remota. Se rechaza, y la confinacion al home
        # remoto queda garantizada en el unico sitio donde se escribe fuera de `cwd`.
        if not home: raise RuntimeError(mach + " no responde por SSH: no se pudo leer su home")
        f = home + "/.cache/strata-oficina/opencode/" + nombre + ".json"
        ssh_run(mach, "mkdir -p ~/.cache/strata-oficina/opencode && cat > " + shlex.quote(f), 15, json.dumps({"$schema": "https://opencode.ai/config.json", "model": model}))
        return f
    d = H("~/.cache/strata-oficina/opencode"); os.makedirs(d, exist_ok=True)
    f = os.path.join(d, nombre + ".json")
    with open(f, "w") as fh: json.dump({"$schema": "https://opencode.ai/config.json", "model": model}, fh)
    return f
def _crear(jid, name, cwd, filas, auto, mach=None):
    log = lambda s: job_step(jid, s)   # T7: `JOBS[jid]["pasos"]` se escribe bajo `JLOCK`
    try:
        if mach and not rhome(mach):   # T8, defecto 2/4: sin home no hay PATH de panel ni confinacion posible
            raise RuntimeError(mach + " no responde por SSH: no se pudo leer su home")
        env = "PATH=" + ((rhome(mach) + "/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin") if mach else (H("~/.local/bin") + ":/snap/bin:/usr/local/bin:/usr/bin:/bin"))
        r = hj(["workspace", "create", "--cwd", cwd, "--label", name, "--no-focus", "--env", env], 30, mach)
        if not r: raise RuntimeError("herdr no pudo crear el workspace en " + (mach or "bazzite"))
        wid = r["workspace"]["workspace_id"]; pane = r["root_pane"]["pane_id"]; oid = (mach + ":" + wid) if mach else wid; job_set(jid, "ws", oid)
        log("Workspace %s creado en %s:%s" % (wid, mach or "bazzite", cwd))
        meta_edit(lambda m: m.update({oid: {"nombre": name, "cwd": cwd, "maquina": mach or "bazzite", "agentes": []}}))   # T7: read-modify-write bajo `MFLOCK`
        with STLOCK: SNAP["data"].pop(mach, None)   # T6: la oficina creada tiene que aparecer en la siguiente lectura, no cacheada hasta el proximo ciclo
        lista = [(f, n + 1, ("%s-%s%d" % (slug(name)[:10], slug(f["perfil"])[:8], n + 1))[:32]) for f in filas for n in range(f["n"])]
        log("Panel %s: consola de la oficina" % pane)
        allp = [pane]; panes = []
        for idx, (f, n, nombre) in enumerate(lista):   # cada agente en su propio panel; el raíz queda como consola
            base = allp[idx // 2]
            env = ["--env", "OPENCODE_CONFIG=" + oc_config(nombre, f["model"], mach)] if f["kind"] == "opencode" and f["model"] else []
            if f["kind"] == "ada-cli": env = ["--env", "PATH=" + ADA_PATH]
            sp = hj(["pane", "split", base, "--direction", "right" if idx % 2 == 0 else "down", "--cwd", cwd, "--no-focus"] + env, 25, mach)
            if not sp: raise RuntimeError("no se pudo crear el panel de " + nombre)
            allp.append(sp["pane"]["pane_id"]); panes.append(sp["pane"]["pane_id"])
        time.sleep(1.5)
        for (f, n, nombre), pid in zip(lista, panes):
            res = _start(nombre, f["kind"], f["model"], pid, cwd, auto, mach)
            log("%s · %s · %s%s: %s" % (nombre, f["perfil"], f["kind"], (" · " + f["model"]) if f["model"] else "", res))
            meta_edit(lambda m, ag={"name": nombre, "rol": f["perfil"], "kind": f["kind"], "model": f["model"]}: m[oid]["agentes"].append(ag))   # T8: la meta se indexa por `oid` (`mac-mini:w2`), no por `wid`: con `wid` una oficina remota reventa y los agentes no quedan registrados
        job_set(jid, "estado", "hecho")
    except Exception as e:
        job_set(jid, "estado", "error"); job_set(jid, "error", str(e)[:300])
def api_office(body):
    try: d = json.loads(body[:4096])
    except Exception: return 400, {"ok": False, "error": "JSON invalido"}
    name = str(d.get("name") or "").strip()[:40]
    if not re.match(r"^[\w][\w .\-]{0,39}$", name): return 400, {"ok": False, "error": "nombre invalido"}
    mach = str(d.get("maquina") or "bazzite"); mach = None if mach == "bazzite" else mach
    if mach and not target(mach): return 400, {"ok": False, "error": "máquina desconocida"}
    raw = str(d.get("cwd") or ("~/" + slug(name))).strip()
    rel = raw[2:] if raw.startswith("~/") else raw
    if mach:
        home = rhome(mach)
        # T8, defecto 2/4: el fallo ya no queda cacheado para siempre, y el 502 dice lo que hace la
        # oficina despues, no solo que la maquina no respondio.
        if not home: return 502, {"ok": False, "error": mach + " no responde por SSH: no se pudo leer su home (se vuelve a sondear en " + str(RHOME_NEG) + " s)"}
        rel = rel[len(home) + 1:] if rel.startswith(home + "/") else rel
        if rel.startswith("/") or not rel or any(p in ("", ".", "..") or p.startswith(".") for p in rel.split("/")): return 400, {"ok": False, "error": "carpeta inválida: usa ~/nombre, sin ocultas"}
        cwd = home + "/" + rel
    else:
        home = os.path.realpath(H("~"))
        cwd = os.path.realpath(H(raw))
        if not (cwd == home or cwd.startswith(home + os.sep)): return 400, {"ok": False, "error": "la carpeta debe estar dentro de tu home"}
        if any(part.startswith(".") for part in cwd[len(home):].split(os.sep) if part): return 400, {"ok": False, "error": "no se permiten carpetas ocultas (agy no las ve)"}
    K = kinds(mach); filas = []
    for f in (d.get("equipo") or [])[:8]:
        perfil = str(f.get("perfil") or "").strip()[:24]; kind = str(f.get("kind") or ""); model = str(f.get("model") or "")
        try: n = int(f.get("n", 1))
        except Exception: n = 0
        if not re.match(r"^[\w][\w .\-]{0,23}$", perfil): return 400, {"ok": False, "error": "perfil invalido: " + perfil}
        if kind == "ada-cli" and mach: return 400, {"ok": False, "error": ADA_REMOTE}   # T8, defecto 1
        if kind not in K: return 400, {"ok": False, "error": "tipo de agente no permitido"}
        if model and model not in K[kind]["modelos"]: return 400, {"ok": False, "error": "modelo no disponible para " + kind}
        if not 1 <= n <= 4: return 400, {"ok": False, "error": "de 1 a 4 por perfil"}
        filas.append({"perfil": perfil, "kind": kind, "model": model, "n": n})
    total = sum(f["n"] for f in filas)
    if total > 8: return 400, {"ok": False, "error": "maximo 8 agentes por oficina"}
    if mach: ssh_run(mach, "mkdir -p " + shlex.quote(cwd), 15)
    else: os.makedirs(cwd, exist_ok=True)
    jid = secrets.token_hex(6)
    with JLOCK: JOBS[jid] = {"estado": "en curso", "pasos": [], "ws": None, "total": total}   # T7: publicar el job bajo el lock
    with MLOCK: envejecer(AGCACHE)   # T8, defecto 6: el agente creado tiene que ser valido al terminar
                                     # el job, no cuando venza la TTL de 20 s (consistente con `SNAP`, T6)
    threading.Thread(target=_crear, args=(jid, name, cwd, filas, bool(d.get("auto")), mach), daemon=True).start()
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
        if time.monotonic() - LAST_SEND < 3.0:   # el limite se aplica antes de llamar a herdr
            return 429, {"ok": False, "error": "limite 1 envio/3s"}
        LAST_SEND = time.monotonic()
    tgt, pre = (agent, "adrian: ") if mode == "direct" else ("claude", "adrian (oficina): ")
    prompt = pre + text
    # `interactive_ready` es opcional por agente: ausente = no lo observamos, y no penaliza el plazo.
    ready = next((a.get("interactive_ready") for a in get_state()["agents"] if a["name"] == tgt), None) is not False
    ms = send_timeout(len(prompt), ready)
    r = herdr_cmd(["agent", "prompt", tgt, prompt, "--wait", "--timeout", str(ms)],
                  ms // 1000 + WAIT_SLACK, agmap().get(tgt))
    outcome, entregado, stalled, st, pane = send_outcome(r)
    code = r["code"] or (None if entregado else "no_response")
    if r["code"] or entregado:   # hay sobre: `envios.log` registra el resultado, no la intencion
        try:
            os.makedirs(os.path.dirname(ENVLOG), exist_ok=True)
            with open(ENVLOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "agent": agent, "mode": mode, "text": text, "target": tgt,
                                    "outcome": outcome, "confirmed": entregado, "code": code,
                                    "state": st, "pane_id": pane, "timeout_ms": ms}, ensure_ascii=False) + "\n")
        except OSError:
            pass
    if not entregado:   # `ok` solo con entrega observada: la UI no puede decir "enviado" (stalled, bloqueado, socket)
        return 502, {"ok": False, "confirmed": False, "error": herdr_error(r) or SIN_ENTREGA,
                     "code": code, "stalled": stalled, "state": st, "pane_id": pane,
                     "timeout_ms": ms, "output": (r["out"] + r["err"])[-500:]}
    return 200, {"ok": True, "confirmed": True, "output": r["out"][-500:], "state": st,
                 "pane_id": pane, "code": None, "stalled": False, "timeout_ms": ms,
                 "msg": ENTREGA.get(outcome, "entregado")}
def api_office_delete(body):
    try: d = json.loads(body[:1024])
    except Exception: return 400, {"ok": False, "error": "JSON invalido"}
    oid = str(d.get("id") or ""); mach, wid = split_id(oid)
    if mach and not target(mach): return 404, {"ok": False, "error": "máquina desconocida"}
    r = hj(["workspace", "list"], 20, mach); ws = r["workspaces"] if r else []
    w = next((x for x in ws if x.get("workspace_id") == wid), None)
    if not w: return 404, {"ok": False, "error": "oficina no encontrada"}
    if not mach and wid == strata_ws(ws): return 403, {"ok": False, "error": "la oficina de Strata no se puede borrar"}
    if str(d.get("confirm") or "") != (w.get("label") or wid): return 400, {"ok": False, "error": "escribe el nombre exacto para confirmar"}
    r = herdr_cmd(["workspace", "close", wid], 20, mach)
    e = herdr_error(r)
    if e: return 502, {"ok": False, "error": e, "code": r["code"] or "no_response"}   # la meta solo se quita si herdr cerró de verdad
    meta_edit(lambda m: m.pop(oid, None))   # T7: quitar la oficina bajo `MFLOCK`, con publicacion atomica
    with MLOCK: envejecer(AGCACHE)   # el mapa de agentes envejece: el agente borrado no puede seguir siendo valido
    with STLOCK: SNAP["data"].pop(mach, None)   # T6: la oficina borrada no puede seguir saliendo del snapshot cacheado
    return 200, {"ok": True, "output": r["out"][-200:]}
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
            self.send_response(302); self.send_header("Location", "/login"); self.send_header("Content-Length", "0"); self.end_headers()
        elif u.path.startswith("/api/") and not sess_ok(self.headers):
            self._json(401, {"error": "login"})
        elif u.path == "/": self._send(200, INDEX, "text/html; charset=utf-8")
        elif u.path == "/vendor/three.module.min.js":
            self._send(200, THREE, "text/javascript; charset=utf-8")
        elif u.path == "/api/state":
            ws = parse_qs(u.query).get("ws", [""])[0]
            code, payload = state_for(ws)   # T8, defecto 3: nunca un fallback silencioso al estado local
            payload = dict(payload); payload["events"] = ev_health()   # T9: salud del canal, clave aditiva
            self._json(code, payload)
        elif u.path == "/api/kinds":
            mq = parse_qs(u.query).get("m", ["bazzite"])[0]
            self._json(200, kinds(None if mq == "bazzite" else mq) if mq == "bazzite" or target(mq) else {})
        elif u.path == "/api/office/job":
            j = job_view(parse_qs(u.query).get("id", [""])[0]); self._json(200 if j else 404, j or {"error": "no existe"})   # T7: copia estable, no la estructura viva
        elif u.path == "/api/log": self._json(200, api_log(q))
        elif u.path == "/api/hilo": self._json(200, api_hilo(q))
        elif u.path == "/api/offices":
            r = api_offices(); r["events"] = ev_health()   # T9: clave aditiva, nunca renombrada
            self._json(200, r)
        elif u.path == "/api/events":
            ws = parse_qs(u.query).get("ws", [""])[0]
            code, oid = ev_scope(ws)   # T27: el mismo dominio de label que `/api/state?ws` (T8)
            if code != 200:
                self._json(code, oid)   # un label desconocido es un error explicito, nunca el estado local
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-store")
            self.end_headers()
            prev = None; quiet = 0; t0 = time.monotonic()
            self.connection.settimeout(SSE_TIMEOUT)   # T27: la escritura esta acotada, no es un bloqueo eterno
            while time.monotonic() - t0 < SSE_MAX and self._sse_alive():
                st = get_state() if oid is None else state_ws(oid)   # T27: la oficina pedida, no solo `w1`
                payload = dict(st); payload["events"] = ev_health()   # T9: clave aditiva en el stream
                body = json.dumps({k: v for k, v in payload.items() if k != "updated"}, ensure_ascii=False)
                if body != prev or quiet >= 6:   # solo si cambia; latido cada ~30 s para mantener viva la conexión
                    try:
                        self.wfile.write(("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode())
                        self.wfile.flush()
                        prev = body; quiet = 0
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break   # T27: el fallo de escritura se comprueba DENTRO del bucle, no envolviendolo
                else:
                    quiet += 1
                if oid is not None: ev_take(oid)   # T27: consumir la suciedad de ESTA oficina: sin consumo, spin
                ev_cycle(st.get("interval", 5), oid)   # T9: en `live` el sueno se corta cuando un evento ensucia
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
                with FLOCK: FAILS.pop(ip, None)   # T7: un acierto limpia la IP bajo el lock de `FAILS`
                sid = secrets.token_urlsafe(32)
                with SELOCK: SESS[sid] = time.time() + 2592000; save_sess()
                self._send(200, b'{"ok": true}', "application/json",
                            "sid=" + sid + "; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict")
            else:
                with FLOCK: FAILS.setdefault(ip, []).append(time.time())   # T7: registrar el fallo y el limite comparten lock
                self._json(403, {"ok": False, "error": "password"})
        elif p.startswith("/api/") and not sess_ok(self.headers): self._json(401, {"ok": False, "error": "login"})
        elif p == "/api/send": self._json(*api_send(body))
        elif p == "/api/office": self._json(*api_office(body))
        elif p == "/api/office/delete": self._json(*api_office_delete(body))
        else: self.send_error(404)
    def _sse_alive(self):
        """El cliente y el servidor, mirados SIN esperar a que una escritura reviente (T27, requisito 3).

        El bucle de hoy solo salia cuando una escritura contra el cliente cerrado da `BrokenPipe`, y eso
        puede tardar un latido entero (`quiet >= 6` con quantum de `interval` = 5 s, ~30 s): medido en T28,
        25 pedidos de `api snapshot` llegaban despues del teardown de los tests y la guardia de sesion tenia
        que drenar `HEARTBEAT_DRAIN` = 35 s. `select` con timeout 0 sobre `self.connection`: si el peer cerro
        el socket esta legible y `recv(1)` da `b''` (EOF), que es lo que hace un `EventSource` al cerrarse y
        lo que hizo el binario en el probe vivo (conexion cortada a mitad). Comprobado al inicio de cada
        quantum, el handler sale en <= 1 quantum (~5 s) en vez de <= 1 latido (~30 s).
        `self.server.socket.fileno() == -1` es `server_close()` en curso: el servidor cerro su socket de
        escucha, asi que los hilos de handler tienen que salir. Un socket cerrado o destruido reventa: ese
        cliente tampoco esta.
        """
        if self.server.socket.fileno() == -1: return False
        try:
            rd, _, _ = select.select([self.connection], [], [], 0)
            if not rd: return True   # nada pendiente: el cliente sigue alli
            return bool(self.connection.recv(1))   # b'' = el peer cerro; datos = el cliente sigue
        except (OSError, ValueError):
            return False
    def log_message(self, *a): pass
def serve(ip): ThreadingHTTPServer((ip, PORT), Handler).serve_forever()
if __name__ == "__main__":
    ips = ["127.0.0.1", tailscale_ip()]
    ev_start()   # T9: un solo hilo de eventos, antes de los servidores HTTP
    for ip in dict.fromkeys(ips): threading.Thread(target=serve, args=(ip,), daemon=True).start()
    print("oficina en %s (puerto %d)" % (" y ".join(ips), PORT), flush=True); threading.Event().wait()
