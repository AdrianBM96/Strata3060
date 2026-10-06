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
"""
from __future__ import annotations
import base64, hashlib, importlib.util, json, shutil, sys, time
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stub_herdr import HerdrStub, FIXTURES   # noqa: E402

SERVER = HERE.parent / "server.py"
PASSWORD = "oficina-stub-pw"


def _scrypt(path: Path, pw: str) -> None:
    """Misma forma que lee `pw_ok` (server.py:44): `algo$N$r$p$salt64$hash64`.

    `pw_ok` decodea el salt (`B64(sb)`), asi que el salt que se escribe es la base64 de los bytes crudos.
    """
    N, r, p = 16384, 8, 1
    salt = b"oficina-stub-salt"
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=N, r=r, p=p, maxmem=33554432, dklen=32)
    path.write_text("strata-oficina$%d$%d$%d$%s$%s" % (N, r, p, base64.b64encode(salt).decode(),
                                                       base64.b64encode(dk).decode()), "utf-8")


class _Shim:
    """Sustituye el modulo `subprocess` del servidor: solo `run`, y siempre al stub."""

    def __init__(self, stub):
        self.stub = stub

    def run(self, argv, **kw):
        return self.stub.run(argv, **kw)


class Oficina:
    """Control del servidor aislado: helpers de estado, caches y regimen de subprocess."""

    def __init__(self, mod, stub, tmp):
        self.mod = mod
        self.stub = stub
        self.tmp = tmp
        self.password = PASSWORD

    def reset(self):
        m = self.mod
        m.CACHE.update(data=None, at=0.0)
        m.SEEN.clear(); m.FAILS.clear()
        m.MCACHE.update(at=0, data=[]); m.AGCACHE.update(at=0, data={}); m.KCACHE.clear()
        m.JOBS.clear(); m.SESS.clear()
        m.LAST_AGENTS = set(m.FIXED); m.LAST_SEND = 0.0
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

    def unknown(self):
        return self.stub.unknown


@pytest.fixture
def stub():
    return HerdrStub()


@pytest.fixture
def server(tmp_path, stub, monkeypatch):
    home = tmp_path / "home"
    (home / ".config" / "strata-oficina").mkdir(parents=True)
    pwf = tmp_path / "password.scrypt"
    _scrypt(pwf, PASSWORD)
    fork = tmp_path / "fork"
    shutil.copytree(FIXTURES / "fork", fork)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("STRATA_OFICINA_PW", str(pwf))
    monkeypatch.delenv("HERDR_SOCKET_PATH", raising=False)
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
    monkeypatch.setattr(mod, "subprocess", _Shim(stub))
    of = Oficina(mod, stub, tmp_path).reset()
    yield of
    # ningun test debe lanzar un binario real: el stub es la unica fuente de subprocess
    assert stub.unknown == [], "el servidor pidio un binario no emulado: %s" % stub.unknown
    sys.modules.pop("oficina_server", None)
