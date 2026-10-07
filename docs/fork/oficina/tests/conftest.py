"""Fixture `server`: server.py importado aislado y con `subprocess.run` sustituido por el stub de Herdr.

El import de `server.py` tiene efectos propios, y hay que aislarlos todos:
  - `server.py:369-374` leen `index.html`, `login.html` y `vendor/three.module.min.js` a memoria.
  - `server.py:210-215` escriben el shim de ada en `~/.local/share/strata-oficina/ada/pi`.
  - `server.py:18` lee `~/.cache/strata-oficina/sesiones.json`.
  - `server.py:8` fija `FORK` con ruta absoluta; `CHANGELOG`, `COLA`, `METRICAS`, `TAREAS` se derivan
    en el import, así que `FORK` solo no basta: las cuatro rutas se parchean.
  - `server.py:6` `PORT = int(sys.argv[1])` → con `pytest -q` eso reventaria el import: `sys.argv`
    se reduce al nombre del script.
  - `server.py:15` `PWF` respeta `STRATA_OFICINA_PW`: se le da un scrypt de la fixture.

Aislamiento: `HOME` temporal, `STRATA_OFICINA_PW` temporal, `FORK` al arbol de fixtures, y `MOTOR` /
`BENCH` a ficheros temporales (en la maquina real son locks compartidos: harian los tests no
deterministas). Ningun proceso real se lanza: `subprocess` del modulo es el stub.

Hermeticidad (T28): el defecto medido era que el teardown restauraba el `subprocess` real y un hilo
de eventos que sobrevivia al `ev_stop()` + `join(2.0)` volvia a sondear contra el Herdr vivo, porque
`HERDR_SOCKET_PATH` es una variable de proceso. Medido: 25 lanzamientos reales (`api snapshot`) en la
suite entera, y el guard `stub.unknown == []` no lo detecta porque `herdr` es un nombre legitimo que
el stub conoce. El teardown de `server` hace ahora el orden de la fuga: neutralizar el socket -> apagar
los DOS hilos -> comprobar que ninguno esta vivo -> comprobar los ejecutables -> sellar el shim. La
restauracion del modulo `subprocess` real es al final de la sesion, cuando ya no hay hilo del servidor
vivo, y el shim sellado registra cualquier pedido y reventa: un launch tardio no llega al binario.
"""
from __future__ import annotations
import base64, hashlib, importlib.util, json, os, shutil, subprocess, sys, threading, time
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stub_herdr import LOCAL, HerdrSocket, HerdrStub, FIXTURES   # noqa: E402

SERVER = HERE.parent / "server.py"
PASSWORD = "oficina-stub-pw"

# T28: los unicos ejecutables que el servidor puede pedir. `herdr` y los binarios locales que el stub
# emula (`LOCAL`). Cualquier otro nombre es un binario real lanzado desde el test.
EMULATED = frozenset(LOCAL) | {"herdr"}
# Los dos hilos que `ev_start` arranca (`server.py:626-627`): el canal de eventos y el worker de avisos.
SERVER_THREADS = ("oficina-events", "oficina-notif")
# `JOIN_TIMEOUT` es cota superior, no una espera elegida al azar: el hilo de eventos sale en `EV_TICK`
# (1 s) y el worker en `NOTIF_IDLE` (0.5 s), pero el worker puede estar dentro de un `herdr_cmd` con
# `NOTIF_TIMEOUT` (5 s). 1 + 5 + margen = 12 s. Pasado el limite, el hilo se declara fugado.
JOIN_TIMEOUT = 12.0
# Ventana tras el sellado: un pedido de binario real o un hilo que reviva se ve aqui.
OBSERVE = 0.05
# `HEARTBEAT_DRAIN`: cota de salida de un handler de SSE que quedo vivo, para la guardia de sesion.
HEARTBEAT_DRAIN = 35.0


def _scrypt(path: Path, pw: str) -> None:
    """Misma forma que lee `pw_ok` (server.py:44): `algo$N$r$p$salt64$hash64`.

    `pw_ok` decodea el salt (`B64(sb)`), asi que el salt que se escribe es la base64 de los bytes crudos.
    """
    N, r, p = 16384, 8, 1
    salt = b"oficina-stub-salt"
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=N, r=r, p=p, maxmem=33554432, dklen=32)
    path.write_text("strata-oficina$%d$%d$%d$%s$%s" % (N, r, p, base64.b64encode(salt).decode(),
                                                       base64.b64encode(dk).decode()), "utf-8")


def _server_threads():
    """Hilos del servidor: los que tienen una pila ejecutando `server.py`.

    Dos formas de llegar, y la suite toca las dos:
      - el objetivo del hilo es una funcion de `server.py`: `ev_client`, `notif_client`, `_crear`
        (server.py:626, 627, 1024) y `serve` (server.py:1175);
      - el objetivo es de `socketserver` y la pila esta DENTRO del `Handler` de `server.py`: los hilos
        que `ThreadingHTTPServer` crea para `_HTTP` / `_SSE` (test_events.py:145, 188). El bucle del SSE
        (`server.py:1213-1228`) llama `get_state` -> `build_state` -> `api snapshot`. T27 le puso una
        condicion de salida DENTRO del bucle: `_sse_alive` (`server.py:1256`) mira la conexion con `select`
        de timeout 0 y el `fileno()` del socket de escucha al inicio de cada quantum, y el fallo de la
        escritura se comprueba en el `try` del `wfile.write`, asi que el handler sale en <= 1 quantum
        (~5 s) tras irse el cliente. En la base el bucle solo salia cuando una escritura contra el cliente
        cerrado daba `BrokenPipeError`, y eso podia tardar un latido entero (~30 s) despues de
        `sse.close()`: esa era la fuente de los 25 lanzamientos.

    `sys._current_frames()` da la pila viva de cada hilo y `f_back` recorre el stack. El hilo del test no
    cuenta aunque este dentro de una funcion de `server.py`: es el que hace la llamada, no un hilo
    arrancado por el servidor.
    """
    frames = sys._current_frames()
    me = threading.current_thread()
    out = []
    for t in threading.enumerate():
        if t is me or not t.is_alive():
            continue
        code = getattr(getattr(t, "_target", None), "__code__", None)
        if code is not None and os.path.realpath(code.co_filename) == str(SERVER):
            out.append(t); continue
        f = frames.get(t.ident)
        while f is not None:
            if os.path.realpath(f.f_code.co_filename) == str(SERVER):
                out.append(t); break
            f = f.f_back
    return out


def _owned_threads():
    """Subconjunto de `_server_threads`: los cuyo objetivo es una funcion de `server.py`.

    Son los unicos que el servidor puede parar (`ev_stop`) o esperar (`_crear` termina sola). Los
    handlers de `ThreadingHTTPServer` no estan aqui: su objetivo es `socketserver`, y esperarles no
    sirve. Con la condicion de salida de T27 (`_sse_alive`) el handler sale solo, en <= 1 quantum, al
    irse el cliente; la guardia de sesion (`hermetic`) es la que comprueba que no queda ninguno.
    """
    me = threading.current_thread()
    out = []
    for t in threading.enumerate():
        if t is me or not t.is_alive():
            continue
        code = getattr(getattr(t, "_target", None), "__code__", None)
        if code is not None and os.path.realpath(code.co_filename) == str(SERVER):
            out.append(t)
    return out


def _describe(threads):
    """Que hace cada hilo fugado, para el mensaje: nombre + la linea de `server.py` en su pila."""
    frames = sys._current_frames()
    out = []
    for t in threads:
        f = frames.get(t.ident)
        while f is not None and os.path.realpath(f.f_code.co_filename) != str(SERVER):
            f = f.f_back
        where = "%s:%d en %s" % (SERVER.name, f.f_lineno, f.f_code.co_name) if f else "sin pila de server.py"
        out.append("%s (%s)" % (t.name, where))
    return sorted(out)


class _Shim:
    """Sustituye el modulo `subprocess` del servidor: solo `run`, y siempre al stub.

    T28: el shim registra CADA ejecutable que el servidor pide (en `stub.exes`, legible por
    `server.executables()`) y tiene dos estados, armado y sellado. Armado delega al stub. Sellado
    apunta el pedido en `post` y reventa: despues del teardown el servidor ya no tiene que pedir nada,
    y un pedido es un hilo fugado. `herdr_cmd` traga la excepcion (`server.py:109-111`), asi que lo que
    hace el trabajo es el registro, y la asercion del teardown lo comprueba.
    """

    def __init__(self, stub):
        self.stub = stub
        self.sealed = False
        self.post = []

    def run(self, argv, **kw):
        argv = list(argv)
        if self.sealed:
            self.post.append(tuple(argv))
            raise RuntimeError("subprocess sellado tras el teardown: el servidor pidio %s" % " ".join(argv))
        return self.stub.run(argv, **kw)

    def executables(self):
        """Conjunto de ejecutables pedidos al shim, en orden estable (el stub los registra)."""
        return self.stub.executables()


class Oficina:
    """Control del servidor aislado: helpers de estado, caches y regimen de subprocess."""

    def __init__(self, mod, stub, tmp, monkeypatch, shim=None):
        self.mod = mod
        self.stub = stub
        self.tmp = tmp
        self.monkeypatch = monkeypatch
        self.shim = shim
        self.password = PASSWORD

    def reset(self):
        m = self.mod
        m.CACHE.update(data=None, at=0.0)
        m.SEEN.clear(); m.FAILS.clear()
        m.MCACHE.update(at=0, data=[]); m.AGCACHE.update(at=0, data={}); m.KCACHE.clear()
        m.JOBS.clear(); m.SESS.clear()
        m.LAST_AGENTS = set(m.FIXED); m.LAST_SEND = 0.0
        with m.ELOCK:   # T9: `EV` es global por modulo; el reset lo deja en el modo de la base (polling)
            m.EV.update(mode="unavailable", dirty=True, subs=[], panes=set(), status={}, names={},
                        thread=None, stop=threading.Event())
        return self

    # ---------- lecturas que emiten el estado ----------
    def build_state(self):
        return self.mod.build_state()

    def get_state(self):
        return self.mod.get_state()

    def state_ws(self, oid):
        return self.mod.state_ws(oid)

    def api_offices(self):
        return self.mod.api_offices()

    def api_log(self, agent):
        return self.mod.api_log(agent)

    def api_hilo(self, agent):
        return self.mod.api_hilo(agent)

    def machines(self):
        return self.mod.machines()

    def kinds(self, mach=None):
        return self.mod.kinds(mach)

    def valid_agent(self, agent):
        self.mod.LAST_AGENTS = set(self.mod.FIXED)   # determinismo: el estado previo no cuenta
        return self.mod.valid_agent(agent)

    def mkagent(self, name, status, activity="trabajando"):
        self.mod.SEEN.clear()
        return self.mod.mkagent(name, status, activity, "#fb7185", "rol")

    def pw_ok(self, pw):
        return self.mod.pw_ok(pw)

    # ---------- entradas del entorno, todas en el temp ----------
    def lock(self, text="progreso = 42%"):
        (self.tmp / "motor").write_text(text, encoding="utf-8")

    def no_lock(self):
        if (self.tmp / "motor").exists():
            (self.tmp / "motor").unlink()

    def bench(self, on=True):
        p = self.tmp / "bench"
        if on:
            p.write_text("", encoding="utf-8")
        elif p.exists():
            p.unlink()

    def suplencia(self, on=True):
        p = Path(self.mod.SUPLENCIA)
        p.parent.mkdir(parents=True, exist_ok=True)
        if on:
            p.write_text("", encoding="utf-8")
        elif p.exists():
            p.unlink()

    def send_log(self, *lines):
        p = Path(self.mod.ENVLOG)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(l + "\n" for l in lines), encoding="utf-8")

    def session(self, sid="s" * 32):
        self.mod.SESS[sid] = time.time() + 3600
        return sid

    def meta(self, d):
        self.mod.save_meta(d)

    def job(self, jid, timeout=8.0):
        """Espera a que el hilo de `_crear` termine (lanza subprocesos al stub, no a un binario)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            j = self.mod.JOBS.get(jid)
            if j and j["estado"] != "en curso":
                return j
            time.sleep(0.02)
        return self.mod.JOBS.get(jid)

    # ---------- estado de Herdr ----------
    def set_state(self, name, status, mach=None):
        return self.stub.set_agent_state(name, status, mach)

    def set_agent_log(self, name, lines):
        return self.stub.set_agent_log(name, lines)

    def set_remote_agents(self, label, agents):
        return self.stub.set_remote_agents(label, agents)

    def add_agent(self, **kw):
        return self.stub.add_agent(**kw)

    def load(self, fixture):
        return self.stub.load(fixture)

    def script_error(self, code, message, match, mach=None, cid="cli:agent:prompt"):
        return self.stub.script_error(code, message, match, mach, cid)

    def script_response(self, match, stdout="", stderr="", rc=0, mach=None):
        return self.stub.script_response(match, stdout, stderr, rc, mach)

    def calls(self, mach=None):
        return self.stub.commands(mach)

    def executables(self):
        return self.stub.executables()

    def sealed(self):
        return self.shim.sealed

    def post_seal(self):
        """Pedidos de `subprocess` llegados despues del sellado del teardown: la firma de un hilo fugado."""
        return list(self.shim.post)

    def unknown(self):
        return self.stub.unknown

    # ---------- T28: los hilos que el servidor arranco ----------
    def server_threads(self):
        """Hilos vivos del servidor en el proceso (ver `_server_threads`).

        Se enumeran en el proceso, no por `EV["thread"]` / `NOTIF["thread"]`: una referencia guardada por
        un test puede estar sustituida por `ev_start`, y lo que hay que probar es que no queda NINGUNO.
        """
        return _server_threads()

    # ---------- T9: el socket de eventos ----------
    def start_events(self, sock, timeout=6.0):
        """Arranca el unico hilo de eventos del servidor contra el socket emulado y espera a que se fije el modo.

        El modo tarda lo que tarda el connect + el ack; se espera con limite, no con un sleep fijo: el
        assert es el modo observado, no el tiempo esperado.
        """
        self.monkeypatch.setenv("HERDR_SOCKET_PATH", str(sock.path))
        self.mod.ev_start()
        if sock.refuse:
            return self.wait_health_mode(min(timeout, 1.0))   # sin socket en la ruta: el modo no cambia
        return self.wait_health("live", timeout)

    def wait_health(self, mode, timeout=6.0):
        p = self.mod.ev_health
        end = time.monotonic() + timeout
        while time.monotonic() < end and p() != mode:
            time.sleep(0.01)
        return p()

    def wait_health_mode(self, timeout=6.0):
        """Espera a que el hilo salga del modo inicial (`unavailable`), sea `polling` o `unavailable` repetido."""
        end = time.monotonic() + timeout
        p = self.mod.ev_health
        while time.monotonic() < end and p() == "unavailable":
            time.sleep(0.01)
        return p()

    def health(self):
        return self.mod.ev_health()

    def dirty(self, on=True):
        with self.mod.ELOCK: self.mod.EV["dirty"] = on

    def panes(self):
        with self.mod.ELOCK: return set(self.mod.EV["panes"])

    def ev_status(self, pane_id):
        with self.mod.ELOCK: return self.mod.EV["status"].get(pane_id)

    def ev_names(self):
        with self.mod.ELOCK: return dict(self.mod.EV["names"])


@pytest.fixture
def stub():
    return HerdrStub()


@pytest.fixture
def herdr_socket(tmp_path):
    """Factoria de sockets Unix de herdr emulados, en el directorio temporal del test.

    `refuse=True` deja la ruta sin socket (`connect` reventa: el caso del binario parado),
    `ack=` sustituye el ack para probar la degradacion, `kick=True` cierra la conexion justo despues
    del ack (el corte a mitad de transmision observado en el probe vivo). El fixture cierra los
    sockets al terminar el test.
    """
    made = []

    def make(name, **kw):
        s = HerdrSocket(tmp_path / name, **kw)
        s.start(); made.append(s); return s

    yield make
    for s in made:
        s.stop()


_SHIMS = []   # registry de sesion: cada shim que el fixture `server` instala, para la guardia y el reporte


@pytest.fixture(scope="session")
def hermetic():
    """Guardia de sesion (T28): la suite entera no lanza un binario real y no deja un hilo del servidor.

    El `subprocess` del modulo se sustituye por el shim con una asignacion directa y NO se devuelve
    durante la sesion: al terminar cada test el shim queda sellado, asi que un hilo fugado no puede
    lanzar un binario real, y su pedido queda registrado en `post`. La restauracion del modulo real
    ocurre aqui, al final, despues de comprobar que no queda ningun hilo del servidor vivo: un hilo que
    sondea despues de la restauracion es la fuga medida en la base (25 lanzamientos de `api snapshot`).
    """
    yield _SHIMS
    # `HEARTBEAT_DRAIN`: margen para un handler de SSE que quedo vivo. Con la condicion de salida de T27
    # (`_sse_alive`, server.py:1256) el handler sale en <= 1 quantum (~5 s) tras irse el cliente, y el
    # latido (`quiet >= 6` con quantum de `interval` = 5 s, server.py:1219) acota el paso siguiente. 35 s
    # es la cota de la base, donde el bucle solo salia al dar `BrokenPipe` una escritura contra el cliente
    # cerrado: se conserva como margen generoso, y la asercion es que no queda ningun hilo. Una sola vez
    # al final de la sesion.
    end = time.monotonic() + HEARTBEAT_DRAIN
    vivos = _server_threads()
    while vivos and time.monotonic() < end:
        time.sleep(0.05)
        vivos = _server_threads()
    assert not vivos, "hilo del servidor vivo al final de la sesion: %s" % _describe(vivos)
    pedidos = {p[0] for s in _SHIMS for p in s.post}
    lanzados = {e for s in _SHIMS for e in s.executables()}
    fuera = sorted((pedidos | lanzados) - EMULATED)
    assert not fuera, "binario real lanzado o pedido en la sesion: %s" % fuera
    mod = sys.modules.get("oficina_server")
    if mod is not None and getattr(mod, "subprocess", None) is not subprocess:
        mod.subprocess = subprocess   # restauracion, solo con la guardia pasada y sin hilo vivo


@pytest.fixture
def server(tmp_path, stub, monkeypatch, hermetic):
    home = tmp_path / "home"
    (home / ".config" / "strata-oficina").mkdir(parents=True)
    pwf = tmp_path / "password.scrypt"
    _scrypt(pwf, PASSWORD)
    fork = tmp_path / "fork"
    shutil.copytree(FIXTURES / "fork", fork)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("STRATA_OFICINA_PW", str(pwf))
    # T28: `HERDR_SOCKET_PATH` es variable de proceso (`ev_path`, server.py:421-423). Se fija a una ruta
    # sin socket, no se borra: asi `ev_path()` nunca cae en `~/.config/herdr/herdr.sock` y un hilo que
    # sobreviviera al teardown solo puede conectar contra nada. `start_events` la apunta al socket emulado.
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock.absent"))
    monkeypatch.setattr(sys, "argv", [str(SERVER)])
    # server.py esta en docs/fork/oficina/, fuera de la superficie de edicion: no escribir .pyc alli
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("oficina_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["oficina_server"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "FORK", str(fork))
    for name, f in (("CHANGELOG", "CHANGELOG.md"), ("COLA", "COLA.md"),
                    ("METRICAS", "METRICAS.json"), ("TAREAS", "TAREAS.json")):
        monkeypatch.setattr(mod, name, str(fork / f))
    monkeypatch.setattr(mod, "MOTOR", str(tmp_path / "motor"))
    monkeypatch.setattr(mod, "BENCH", str(tmp_path / "bench"))
    shim = _Shim(stub)
    hermetic.append(shim)
    mod.subprocess = shim   # el modulo `subprocess` real no se le devuelve al servidor en la sesion
    of = Oficina(mod, stub, tmp_path, monkeypatch, shim).reset()
    yield of
    # ---- T28: el orden es la garantia. Neutralizar el socket -> apagar los DOS hilos -> comprobar que
    # no queda ninguno -> comprobar los ejecutables -> sellar. La restauracion del modulo `subprocess`
    # real es al final de la sesion (`hermetic`), despues de la guardia. `join` con limite no es garantia:
    # el hilo puede estar en `select` sobre un socket que no cede, o en el sueno `NOTIF_IDLE` del worker, y
    # en la base sobrevivia y sondeaba el CLI real con el `HERDR_SOCKET_PATH` heredado del proceso. ----
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(tmp_path / "herdr.sock.teardown"))
    mod.ev_stop()   # `Event.set()`: corta el sueno del backoff y el del worker al instante
    for name, t in zip(SERVER_THREADS, (mod.EV["thread"], mod.NOTIF["thread"])):
        if t is None:
            continue
        t.join(JOIN_TIMEOUT)
        assert not t.is_alive(), ("el hilo %s sobrevivio a ev_stop() + join(%s s): con el subprocess "
                                  "restaurado lanzaria el CLI real de Herdr" % (name, JOIN_TIMEOUT))
    vivos = _owned_threads()
    for t in vivos:
        t.join(JOIN_TIMEOUT)   # `_crear` termina al acabar su serie de subproceso contra el stub
    vivos = _owned_threads()
    assert not vivos, ("hilos arrancados por server.py vivos al terminar el test: %s; con el subprocess "
                       "restaurado lanzarian el CLI real de Herdr" % _describe(vivos))
    # el stub es la unica fuente de subprocess: todo lo que el servidor pidio esta emulado
    assert stub.unknown == [], "el servidor pidio un binario no emulado: %s" % stub.unknown
    fuera = set(stub.executables()) - EMULATED
    assert not fuera, "el servidor lanzo un binario real: %s" % sorted(fuera)
    # sellar: a partir de aqui un pedido de `subprocess` se registra en `post` y reventa, y nunca llega al
    # binario. El modulo `subprocess` real NO se devuelve al servidor en el teardown: la restauracion es al
    # final de la sesion (`hermetic`). Un handler de SSE que quedo vivo (`_SSE.close` solo cierra el
    # cliente) sale ahora por la condicion de salida de T27 (`_sse_alive`); si se quedara, lo que pide
    # queda registrado: se prueba que es un binario emulado, no un lanzamiento real.
    shim.sealed = True
    sys.modules.pop("oficina_server", None)
    # ventana de observacion tras el sellado: un pedido de binario real, o un hilo de `server.py` que
    # reviva, son la fuga y el test revienta con el nombre y la linea
    real = set()
    end = time.monotonic() + OBSERVE
    while time.monotonic() < end:
        real = {p[0] for p in shim.post} - EMULATED
        if real or _owned_threads():
            break
        time.sleep(0.01)
    assert not real, "binario real pedido tras el sellado: %s" % sorted(real)
    reavivados = _owned_threads()
    assert not reavivados, "un hilo de server.py revivio tras el sellado: %s" % _describe(reavivados)


def pytest_terminal_summary(terminalreporter, *a, **kw):
    """Reporte de la hermeticidad (T28): lo que los shims registraron en la sesion.

    La corrida se lee sin `grep` del log: `executables` es el conjunto de binarios que el servidor pidio
    (todos emulados), `tras sellado` son los pedidos que llegaron despues del teardown de un test, o sea
    de un handler de SSE que siguio vivo, y `hilos al final` es lo que quedaba al cerrar la sesion. La
    asercion esta en `hermetic` y en el teardown de `server`; aqui solo se escribe lo observado.
    """
    exes = sorted({e for s in _SHIMS for e in s.executables()})
    post = [list(p) for s in _SHIMS for p in s.post]
    terminalreporter.write("T28 hermeticidad: tests=%d  executables pedidos=%s  binarios reales=0  "
                           "pedidos tras el sellado=%d  hilos del servidor al final=%s\n"
                           % (len(_SHIMS), exes, len(post), _describe(_server_threads())))
