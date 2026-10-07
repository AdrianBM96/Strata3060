"""T9: `events.subscribe` por socket Unix: push en vez de polling, con degradacion a polling.

El protocolo esta capturado VERBATIM del binario herdr 0.9.3 (odd/tasks/oficina-herdr-v5.md, "Sondeos
de eventos", 2026-10-06, workspaces wJ..wR, todos cerrados): JSON newline-delimited en
`$HERDR_SOCKET_PATH` o `~/.config/herdr/herdr.sock`; los pedidos llevan `id` (sin `id` el servidor
responde `invalid_request: missing field id`); `events.subscribe` contesta
`{"id":..,"result":{"type":"subscription_started"}}` y los eventos llegan por la MISMA conexion con
forma `{"data":{...},"event":"..."}`. Las suscripciones globales necesitan solo `type`;
`pane.agent_status_changed` y `pane.scroll_changed` requieren `pane_id`; `pane.output_matched`
requiere `pane_id + source + match` con `OutputMatch = {type: substring|regex, value: string}`. El
binario rechazo el SET ENTERO cuando una entrada estaba mal formada (observado dos veces), y la
segunda escritura sobre una misma conexion dio BrokenPipe: de ahi la conexion larga del hilo y el
reintento acotado.

El evento de estado NO trae el nombre del agente (trae `pane_id` y `workspace_id`), y el evento de
deteccion emite `pane_agent_detected` con guiones mientras la suscripcion es `pane.agent_detected`
con punto. La correspondencia pane->nombre viene del snapshot de T6 y se refresca en la deteccion.

Lo que se prueba aqui (socket emulado en el temp del test, `HerdrSocket` en `stub_herdr.py`):
  - el set de suscripciones es el verificado, y las formas invalidas (`text` en vez de `value`, un
    `pane_id` ausente) nunca se mandan: matarian el set entero;
  - en reposo, un ciclo de estado no lanza ningun subproceso herdr;
  - `pane.agent_status_changed` pone el estado real (incluido `blocked`) del agente conocido, y eso
    llega al stream SSE;
  - la correspondencia pane->nombre se refresca en `pane.agent_detected`;
  - la re-suscripcion es idempotente y crece con los paneles nuevos;
  - socket refusado, ack que no es `subscription_started` y conexion cortada a mitad: degradacion a
    polling, el servidor sigue sirviendo, y el reintento esta acotado;
  - la salud de la ruta dice el modo (`live` | `polling` | `unavailable`), como clave aditiva;
  - por el socket nunca sale un metodo mutante;
  - T10: un agente que ENTRA en `blocked` lanza `herdr notification show` con la forma verificada en el
    binario (titulo posicional, `--body`, `--position`, `--sound`), con transicion por panel, dedupe con
    ventana, cota de spawn, sin bloquear la lectura del socket, y un aviso que falla se registra y no
    mata el canal ni el push del estado.

El hilo de eventos se arranca solo en `__main__` del servidor; cada test lo arranca contra su socket
emulado, y la suite que no lo arranca (la linea base de T1..T8) no lanza subproceso de socket ni aviso.
El worker de avisos vive en su propio hilo y se apaga con `ev_stop()`, asi que el spawn nunca espera el
hilo de lectura (requisito 5 de T10).
"""
from __future__ import annotations
import json, os, socket, sys, threading, time
import pytest
from http.server import ThreadingHTTPServer

from stub_herdr import GLOBAL_SUBS, _sub_ok

# Lo que la oficina jamas debe mandar por el socket. El CLI de herdr tiene 102 metodos; la oficina usa
# `events.subscribe` (y `ping` si hiciera falta) y nada mas: un `agent prompt` por socket duplicaria un
# mensaje y un `workspace close` borraria una oficina desde un hilo de lectura.
MUTANTES = ("agent prompt", "agent start", "pane split", "pane run", "pane send-keys",
            "workspace create", "workspace close", "server.stop", "server.restart")


def _agentes(state):
    return {a["name"]: a for a in state["agents"]}


def _snaps(server, mach=None):
    return [c for c in server.calls(mach) if c[:2] == ("api", "snapshot")]


def _handlers(server, srv):
    """Handlers del `ThreadingHTTPServer` `srv` cuya pila esta dentro del `Handler` de `server.py`.

    La definicion de "hilo del servidor" es la de T28 (`conftest.py:_server_threads`), acotada al servidor
    de este test: el conteo del proceso entero no es un baseline estable, porque los handlers de otros tests
    entran y salen durante la sesion. Lo que el test afirma es que el handler de SU servidor desaparece
    cuando el cliente se va, y la guardia de sesion (`hilos del servidor al final=[]`) es la que cuenta el
    proceso entero.
    """
    path = os.path.realpath(getattr(server.mod, "__file__", ""))
    out = []
    for t in threading.enumerate():
        f = sys._current_frames().get(t.ident)
        while f is not None:
            h = f.f_locals.get("self")
            if getattr(h, "server", None) is srv and os.path.realpath(f.f_code.co_filename) == path:
                out.append(t); break
            f = f.f_back
    return out


def _notifs(server):
    """Los `notification show` lanzados por el worker, en el orden registrado por el stub."""
    return [c for c in server.calls() if c[:2] == ("notification", "show")]


def _own(sock):
    """Conexiones de LA OFICINA al socket: las que mandan `oficina:subscribe`.

    DEFECTO del harness, encontrado al correr la suite entera: `monkeypatch` restaura el `subprocess` del
    modulo al terminar cada test, y un hilo de eventos de un test anterior que sobrevive al `ev_stop()` +
    `join(2.0)` vuelve a sondear con el binario REAL (`herdr api snapshot`), heredando `HERDR_SOCKET_PATH`
    de la variable de proceso: el CLI real se conecta al socket del test que corre ahora y manda
    `api-client:status`, que cuenta como conexion. Medido: 24 lanzamientos reales en la suite (la base de
    T9 tambien los tiene). Contar `sock.conns` a secas contaria esa fuga, asi que la cadencia de backoff
    se mide sobre los pedidos de la oficina, que es lo que el test afirma.
    """
    return sum(1 for r in sock.requests if r.get("id") == "oficina:subscribe")


def _aviso(server, k=1, timeout=4.0):
    """Espera a ver `k` avisos lanzados; devuelve lo OBSERVADO (la lista de argv), no el tiempo esperado."""
    return _espera(lambda: _notifs(server) if len(_notifs(server)) >= k else None, timeout)


def _argv(a):
    """El argv de un aviso partido en titulo posicional y banderas, con el orden de estas fijado.

    El stub registra el argv como tuple (sin `herdr`), asi que se compara con tuple.
    """
    assert a[:2] == ("notification", "show"), "el argv de notificacion es `notification show ...`: %s" % (a,)
    flags = {a[i]: a[i + 1] for i in range(3, len(a) - 1, 2)}
    assert list(flags) == ["--body", "--position", "--sound"], "el orden de banderas es --body --position --sound: %s" % (a,)
    assert len(a) == 9, "el argv completo es `notification show <TITLE> --body <TEXT> --position <corner> --sound request`: %s" % (a,)
    return a[2], flags


def _envejecer(server, s):
    """Pone las marcas de `CACHE` y `SNAP` `s` s atras: es el paso del tiempo, sin dormir.

    En la maquina real las dos marcas envejecen juntas (`SNAP["ttl"]` es el intervalo del ciclo, T6),
    asi que envejecer solo `CACHE` daria una reconstrucción que lee la cache y no sondea: la asercion
    de regimen tiene que envejecer las dos.
    """
    server.mod.CACHE["at"] -= s
    for k, (at, data) in list(server.mod.SNAP["data"].items()):
        server.mod.SNAP["data"][k] = (at - s, data)


def _espera(cond, timeout=6.0):
    """Espera a que el hilo de eventos produzca un valor; devuelve lo OBSERVADO, no el tiempo esperado.

    El assert es el valor, nunca un `time.sleep(n)` como asercion: el limite es cota superior.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v: return v
        time.sleep(0.01)
    return None


class _HTTP:
    """Cliente HTTP real contra el `Handler` de `server.py`, con la cookie de sesion.

    La salud del canal es un campo de la RUTA, asi que se lee por HTTP: un helper que replicara el
    codigo del handler (copiar `payload["events"] = ev_health()`) se vuelve verde cuando el handler
    lo quita, y eso no prueba nada. Aqui la asercion es la respuesta que sale del servidor.
    """

    def __init__(self, server, sid="s" * 32):
        server.session(sid)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.mod.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.cookie = "sid=" + sid
        self.base = ("127.0.0.1", self.srv.server_address[1])

    def get(self, path):
        c = socket.create_connection(self.base, 20)
        c.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1\r\nCookie: %s\r\nConnection: close\r\n\r\n"
                   % (path, self.cookie)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = c.recv(65536)
            if not chunk: break
            buf += chunk
        head, body = buf.split(b"\r\n\r\n", 1)
        status = int(head.split(b"\n")[0].split()[1])
        while True:   # `Connection: close`: el servidor cierra despues de la respuesta
            try:
                chunk = c.recv(65536)
            except OSError:
                break
            if not chunk: break
            body += chunk
        c.close()
        return status, json.loads(body.decode("utf-8", "replace"))

    def close(self):
        self.srv.shutdown(); self.srv.server_close()


_STREAMS = []


class _SSE:
    """Consumidor real del handler `/api/events` del servidor aislado.

    Se usa el `Handler` de `server.py` y un `ThreadingHTTPServer` en puerto ephemero: la asercion es el
    stream que sale de la ruta, no una replica de su condicion de latido. Se lee con `recv` y corte por
    `\n\n`: un `makefile` que una vez dio timeout queda envenenado (`cannot read from timed out object`
    en socket.py:707), y el bucle de latido necesita varias lecturas con limite.

    Cada stream se registra en `_STREAMS` y se cierra en el teardown (`_cerrar_streams`): un test que
    revienta antes de `sse.close()` deja el cliente vivo, y el handler no sale hasta `SSE_MAX`.
    """

    def __init__(self, server, sid="s" * 32, path="/api/events"):
        server.session(sid)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.mod.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.c = socket.create_connection(("127.0.0.1", self.srv.server_address[1]), 20)
        _STREAMS.append(self)
        self.c.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1\r\nCookie: sid=%s\r\nConnection: close\r\n\r\n"
                        % (path, sid)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.c.recv(4096)
            if not chunk: break
            buf += chunk
        head, self.buf = buf.split(b"\r\n\r\n", 1)
        assert int(head.split(b"\n")[0].split()[1]) == 200, "la ruta SSE respondio %s" % head[:60]

    def frames(self, dur):
        """Frames `data:` leidos en `dur` segundos. El stream escribe `data: ...\n\n`, asi que el corte
        por `\n\n` no ve un frame a medias."""
        out = []
        end = time.monotonic() + dur
        while True:
            while b"\n\n" in self.buf:
                frame, self.buf = self.buf.split(b"\n\n", 1)
                for line in frame.split(b"\n"):
                    if line.startswith(b"data: "): out.append(json.loads(line[6:]))
            left = end - time.monotonic()
            if left <= 0: break
            self.c.settimeout(left)
            try:
                chunk = self.c.recv(4096)
            except (socket.timeout, TimeoutError):
                break
            if not chunk: break
            self.buf += chunk
        return out

    def first(self, dur):
        """Primer frame `data:` y lo que tardo en llegar, medido desde la llamada.

        Se mide la latencia de llegada, no el final de la ventana: `frames()` corre la ventana entera y
        daria siempre `dur`, que no dice nada de la promesa de T9 (el evento llega antes del latido).
        """
        t0 = time.monotonic()
        end = t0 + dur
        while True:
            while b"\n\n" in self.buf:
                fr, self.buf = self.buf.split(b"\n\n", 1)
                for line in fr.split(b"\n"):
                    if line.startswith(b"data: "):
                        return json.loads(line[6:]), time.monotonic() - t0
            left = end - time.monotonic()
            if left <= 0: return None, time.monotonic() - t0
            self.c.settimeout(left)
            try:
                chunk = self.c.recv(4096)
            except (socket.timeout, TimeoutError):
                return None, time.monotonic() - t0
            if not chunk: return None, time.monotonic() - t0
            self.buf += chunk

    def close(self):
        try: self.c.close()
        except OSError: pass
        self.srv.shutdown(); self.srv.server_close()
        if self in _STREAMS: _STREAMS.remove(self)


@pytest.fixture(autouse=True)
def _cerrar_streams():
    """Cierra los streams que el test no cerro, tambien cuando el test falla en mitad.

    Un handler de SSE con el cliente vivo no sale del bucle: T28 lo drenaba hasta `HEARTBEAT_DRAIN`
    (35 s) y la guardia de sesion lo contaba como hilo del servidor. Con la condicion de salida de T27,
    cerrar el cliente basta: el handler ve el EOF en su quantum y `server_close()` vuelve.
    """
    yield
    for s in list(_STREAMS):
        s.close()


# ---------- el set de suscripciones, contra lo verificado en el binario ----------

def test_el_set_de_suscripciones_es_el_verificado_en_vivo(server, herdr_socket):
    """Las globales van con solo `type` y `pane.agent_status_changed` lleva su `pane_id`."""
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    assert sock.acks[0] == {"type": "subscription_started"}   # el ack verbatim, no un invento
    subs = sock.subscriptions(0)
    assert [t for t, p in subs if p is None] == list(GLOBAL_SUBS), "el set global es el capturado"
    por_pane = [p for t, p in subs if t == "pane.agent_status_changed"]
    assert por_pane and all(p for p in por_pane), "cada panel conocido lleva su `pane_id`"
    assert set(por_pane) == server.panes()   # la siembra es el snapshot de T6, no una conjetura
    assert all(_sub_ok({"type": t, "pane_id": p}) is None for t, p in subs)


def test_una_forma_invalida_mataria_el_set_y_la_oficina_no_la_manda(server, herdr_socket):
    """TRIANGULAR: el rechazo del set entero esta emulado, y la oficina no cae en el bache.

    El binario rechazo el set ENTERO dos veces con una entrada mal formada. El stub valida con las
    reglas capturadas, asi que `text` en vez de `value` (el bache documentado) o un `pane_id` ausente
    se ven como rechazo explícito, no como un canal mudo.
    """
    ok = {"type": "pane.output_matched", "pane_id": "w1:pM", "source": "recent-unwrapped",
          "match": {"type": "substring", "value": "DONE"}}
    assert _sub_ok(ok) is None                       # la forma verificada pasa
    assert _sub_ok({"type": "pane.output_matched", "pane_id": "w1:pM", "source": "recent-unwrapped",
                     "match": {"type": "substring", "text": "DONE"}}), "`value`, no `text`"
    assert _sub_ok({"type": "pane.agent_status_changed"}), "sin `pane_id` no es una suscripcion valida"
    sock = herdr_socket("herdr2.sock")
    server.start_events(sock)
    for s in sock.requests[0]["params"]["subscriptions"]:
        assert _sub_ok(s) is None, "la oficina mando una forma que mata el set: %s" % s
    assert "text" not in json.dumps(sock.requests[0])


def test_el_socket_nunca_manda_un_metodo_mutante(server, herdr_socket):
    """Solo `events.subscribe` (y `ping`): ningun prompt, start, split, create, close o stop."""
    sock = herdr_socket("herdr.sock")
    server.start_events(sock)
    server.get_state(); server.state_ws("w1"); server.api_offices(); server.api_log("explorer")
    assert sock.requests, "no hay pedido de suscripcion registrado"
    assert set(sock.methods()) <= {"events.subscribe", "ping"}
    for m in sock.methods():
        assert m not in MUTANTES
    for r in sock.requests:
        assert r.get("id"), "todo pedido de herdr necesita `id` (sin `id` responde invalid_request)"
        assert r.get("method") == "events.subscribe"
        assert r["params"]["subscriptions"], "el set vacio no suscribe nada"


# ---------- el camino rapido: eventos, no reloj ----------

def test_en_repojo_un_ciclo_de_estado_no_lanza_subprocesos(server, herdr_socket):
    """El criterio 2 de la feature: en reposo la oficina no lanza subprocesos de herdr.

    Se hacen 10 quanta del bucle SSE (`ev_cycle`, el sueno del handler) sin ningun evento. En el modo
    de hoy serian 10 construcciones de estado con su `api snapshot`; con el canal vivo son 0.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    server.mod.CACHE.update(data=None, at=0.0)
    server.get_state()                       # el set de paneles queda poblado por el snapshot
    n = len(server.stub.calls)
    for _ in range(10):
        st = server.get_state()
        server.mod.ev_cycle(0.05)
    assert len(server.stub.calls) == n, "el reposo lanzo %d subproceso(s) herdr" % (len(server.stub.calls) - n)
    assert server.get_state()["interval"] == 5, "el quantum del latido SSE no cambia"


def test_el_ciclo_lento_es_reconciliacion_no_el_camino_rapido(server, herdr_socket, monkeypatch):
    """30 s de reconciliacion para `cwd`/`terminal_title`/`interactive_ready`/`completion_seq`, que los
    eventos no llevan. Se simula el paso del tiempo moviendo las marcas: en polling 6 quanta de 5 s son
    6 construcciones; en `live` son 1, y el limite de 30 s si sondea el snapshot."""
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    builds = []
    real = server.mod.build_state
    monkeypatch.setattr(server.mod, "build_state", lambda: (builds.append(1), real())[1])
    for _ in range(6):
        _envejecer(server, 5.0)
        server.get_state()
    assert len(builds) == 1, "el ciclo de eventos reconstruyo %d veces en 30 s" % len(builds)
    # Los campos que los eventos NO llevan son los de T2 que solo el snapshot trae. `completion_seq` es
    # el que viaja en el payload, asi que se observa ese; `terminal_title` y `cwd` quedan en la fuente
    # (Herdr los trae, el payload de T2 no los propaga), y `interactive_ready` manda el plazo de `--wait`.
    server.stub.find("explorer")["completion_seq"] = 4242
    _envejecer(server, server.mod.RECONCILE)
    st = server.get_state()
    assert len(builds) == 2, "el limite de reconciliacion no reconstruyo"
    assert _agentes(st)["explorer"]["completion_seq"] == 4242, "el ciclo lento no refresca el snapshot"
    assert server.mod.SNAP["ttl"] == server.mod.RECONCILE
    assert server.get_state()["interval"] == 5, "el quantum del latido SSE sigue siendo el de hoy"


def test_la_reconciliacion_es_eleccion_de_la_oficina_y_el_quantum_no_se_renombra(server):
    """Guarda de la linea base: `RECONCILE` es un numero de la oficina, y el latido sigue siendo 6 quanta."""
    assert server.mod.RECONCILE == 30 and server.mod.EV_WAKE <= 1.0
    assert server.mod.EV_BACK0 <= server.mod.EV_BACKMAX
    http = _HTTP(server)
    code, st = http.get("/api/state")
    assert st["interval"] == 5 and st["events"] == "unavailable"   # sin socket: la base sondea
    http.close()


# ---------- el estado que llega del evento ----------

def test_el_evento_de_estado_pone_el_valor_real_incluido_bloqueado(server, herdr_socket):
    """`pane.agent_status_changed` trae `pane_id` y `workspace_id`, NO el nombre: el nombre lo pone el
    snapshot y el estado lo pone el evento. `blocked` no se colapsa a `idle` (el defecto de T2)."""
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    st = server.get_state()
    assert _agentes(st)["explorer"]["status"] == "idle"
    sock.emit(sock.event_status("w1:pM", "blocked"))
    assert _espera(lambda: server.ev_status("w1:pM")) == "blocked"
    ag = _agentes(server.get_state())["explorer"]
    assert ag["status"] == "blocked" and ag["pane_id"] == "w1:pM"
    # `state_ws` deriva la actividad del estado (`ACTS`, T2): el evento llega al payload de la ruta
    wsa = _agentes(server.state_ws("w1"))["explorer"]
    assert wsa["status"] == "blocked" and wsa["activity"] == "esperando confirmación"
    server.set_state("explorer", "working")   # el snapshot dice `working`: el evento observado gana
    server.mod.CACHE.update(data=None, at=0.0)
    assert _agentes(server.get_state())["explorer"]["status"] == "blocked"


def test_la_correspondencia_pane_nombre_se_refresca_en_la_deteccion(server, herdr_socket):
    """El evento de deteccion si trae `agent` y `pane_id`: la correspondencia se actualiza en el acto y
    el snapshot envejece para que el ciclo la confirme."""
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    server.add_agent(name="agy-obrero", kind="agy", status="idle", workspace_id="w2", pane_id="w2:p1")
    sock.emit(sock.event_detected("w2:p1", "agy"))
    assert _espera(lambda: server.ev_names().get("w2:p1")) == "agy"
    assert _espera(lambda: "w2:p1" in server.panes())
    n = len(server.stub.calls)
    server.get_state()   # el ciclo consume la suciedad del evento: el snapshot envejecido se vuelve a leer
    assert len(server.stub.calls) > n, "el snapshot no envejecio: la oficina nueva queda oculta"
    ws = server.state_ws("w2")
    assert [a["name"] for a in ws["agents"]] == ["agy-obrero"], "la correspondencia pane->nombre no se refresco"
    assert ws["agents"][0]["pane_id"] == "w2:p1"
    sock.emit(sock.event_status("w2:p1", "blocked"))
    assert _espera(lambda: server.ev_status("w2:p1")) == "blocked"
    assert _agentes(server.state_ws("w2"))["agy-obrero"]["status"] == "blocked"


def test_el_evento_de_deteccion_llega_con_guiones_y_la_suscripcion_con_punto(server, herdr_socket):
    """TRIANGULAR: `pane_agent_detected` (guiones, capturado) y `pane.agent_detected` (punto, suscripcion)
    son el mismo evento. Un evento de un herdr futuro se ignora, no se traga ni ensucia el estado."""
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    n = len(server.stub.calls)
    assert server.mod.ev_event(json.dumps({"data": {"type": "pane_created", "pane": {"pane_id": "w1:pZ"}},
                                           "event": "pane_created"})) is True
    assert server.mod.ev_event(json.dumps({"data": {"agent_status": "working", "pane_id": "w1:pM"},
                                           "event": "pane_agent_status_changed"})) is True
    assert server.ev_status("w1:pM") == "working"
    assert server.mod.ev_event(json.dumps({"data": {"type": "unknown_future"}, "event": "future.event"})) is False
    assert server.mod.ev_event("no es json") is False
    assert server.mod.ev_event(json.dumps({"data": {"agent_status": "waiting", "pane_id": "w1:pM"},
                                           "event": "pane.agent_status_changed"})) is True
    assert server.ev_status("w1:pM") == "working", "un estado fuera de dominio no pisa el observado"


def test_un_cambio_de_estado_ensucia_y_el_sse_empuja_antes_del_latido(server, herdr_socket):
    """El stream SSE esta alimentado por eventos: el frame llega en < 3 s, no en los 5 s del polling."""
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    sse = _SSE(server)
    f0, dt0 = sse.first(2.0)
    assert f0 and f0["events"] == "live" and f0["interval"] == 5
    n = len(_snaps(server))
    t0 = time.monotonic()
    sock.emit(sock.event_status("w1:pM", "blocked"))
    frame, dt = sse.first(3.0)   # se mide la llegada del frame, no el final de la ventana
    assert frame is not None, "el stream no empujo el evento"
    ag = _agentes(frame)["explorer"]
    assert ag["status"] == "blocked" and ag["pane_id"] == "w1:pM"   # el roster fijo tiene su texto de
    # actividad propio (T2 alinea `state_ws`): el stream muestra el estado observado por el evento
    assert dt < 1.5, "el evento tardo %s s en llegar al navegador (el polling tardaria 5 s)" % round(dt, 2)
    # T10: el MISMO evento tiene un segundo efecto, el spawn de `notification show` (el aviso es un
    # subproceso aparte, no una construccion de estado), asi que la cuenta de "una sola construccion"
    # se mide sobre `api snapshot`, que es lo que construye el estado.
    assert len(_snaps(server)) - n == 1, "un evento cuesta una sola construccion de estado"
    sse.close()


def test_el_handler_SSE_en_repojo_no_lanza_subproceso_de_estado(server, herdr_socket):
    """El criterio 2 de la feature por la ruta real: el bucle `/api/events` en `live` no construye
    estado si nada cambia. Se cuentan los subproceso del stub antes y despues de 10 s de stream.

    En el modo de hoy 10 s de SSE son 2 construcciones con su `api snapshot`; en `live` son 0, y el
    latido de ~30 s sigue sin empujar frames (la condicion `quiet >= 6` no se acorta).
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    sse = _SSE(server)
    f0, dt = sse.first(2.0)
    assert f0 and f0["events"] == "live"
    n = len(server.stub.calls)
    mas = sse.frames(10.0)
    assert mas == [], "en `live` el bucle empujo sin evento: el latido se acorto"
    assert len(server.stub.calls) == n, "10 s de SSE en reposo lanzaron %d subproceso(s)" % (len(server.stub.calls) - n)
    sse.close()


def test_en_polling_el_latido_de_30s_se_mantiene(server, herdr_socket):
    """Degradacion: sin canal vivo el bucle SSE empuja solo cuando cambia y el latido sigue siendo ~30 s.

    En 10 s de stream se ve 1 frame (el inicial): el condition `body != prev or quiet >= 6` con quantum
    de 5 s no se acorta.
    """
    sock = herdr_socket("refused.sock", refuse=True)
    assert server.start_events(sock) == "unavailable"
    sse = _SSE(server)
    f0, dt0 = sse.first(1.0)
    assert f0 and f0["events"] == "unavailable" and f0["interval"] == 5
    n = len(_snaps(server))
    mas = sse.frames(10.0)
    assert mas == [], "el polling empujo sin cambio de estado: el latido se acorto"
    assert len(_snaps(server)) - n >= 2, "la linea base de polling (ciclo de 5 s) se acorto"
    sse.close()


# ---------- re-suscripcion: un solo set, idempotente ----------

def test_la_re_suscripcion_crece_con_el_panel_nuevo_y_es_idempotente(server, herdr_socket):
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    n0 = len(sock.requests)
    server.add_agent(name="agy-nuevo", kind="agy", status="idle", workspace_id="w1", pane_id="w1:pZ")
    sock.emit(sock.event_detected("w1:pZ", "agy"))
    assert _espera(lambda: len(sock.requests) > n0), "el set crecio y no se re-suscribio"
    subs = sock.subscriptions(1)
    assert ("pane.agent_status_changed", "w1:pZ") in subs
    assert [t for t, p in subs if p is None] == list(GLOBAL_SUBS)
    assert all(_sub_ok({"type": t, "pane_id": p}) is None for t, p in subs)
    for _ in range(100):   # sin cambios nuevos: no hay un tercer pedido
        if len(sock.requests) > n0 + 1: break
        time.sleep(0.01)
    assert len(sock.requests) == n0 + 1, "se re-suscribio sin que el set creciera: no es idempotente"


# ---------- degradacion y recuperacion ----------

def test_socket_refusado_degrada_a_polling_y_la_oficina_sigue_sirviendo(server, herdr_socket):
    sock = herdr_socket("refused.sock", refuse=True)
    assert server.start_events(sock) == "unavailable"
    http = _HTTP(server)
    code, st = http.get("/api/state")
    assert code == 200 and st["events"] == "unavailable"
    assert st["interval"] == 5 and st["agents"], "la degradacion no vacio el estado"
    assert len(_snaps(server)) >= 1, "en degradacion no se sondea: la oficina se queda congelada"
    assert http.get("/api/offices")[1]["events"] == "unavailable"
    http.close()


def test_ack_que_no_es_subscription_started_degrada_a_polling(server, herdr_socket):
    bad = {"type": "invalid_request", "message": "missing field pane_id"}
    sock = herdr_socket("badack.sock", ack=bad)
    assert server.start_events(sock) == "polling"
    http = _HTTP(server)
    code, st = http.get("/api/state")
    assert st["events"] == "polling" and st["agents"]
    assert sock.acks[0] == bad, "el ack observado es el del socket, no un invento del test"
    http.close()


def test_conexion_cortada_a_media_transmision_degrada_y_reconecta(server, herdr_socket):
    """El binario corto la conexion en el probe vivo (BrokenPipe). El estado observado sobrevive al corte
    y el canal se recupera solo, sin reiniciar el servicio."""
    sock = herdr_socket("cut.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    sock.emit(sock.event_status("w1:pM", "blocked"))
    assert _espera(lambda: server.ev_status("w1:pM")) == "blocked"
    c0 = sock.conns
    sock.drop()
    assert _espera(lambda: sock.conns > c0), "la conexion cortada no se reconecto"
    assert _espera(lambda: server.health() == "live"), "el canal no volvio a suscribir"
    http = _HTTP(server)
    code, st = http.get("/api/state")
    assert st["events"] == "live" and st["agents"]
    assert server.ev_status("w1:pM") == "blocked", "el estado observado se pierde al reconectar"
    http.close()


def test_el_reintento_esta_acotado(server, herdr_socket):
    """Con la suscripcion muerta (el ack no es `subscription_started`), el reintento crece 1,2,4,8 s y
    topea en `EV_BACKMAX`: en 8 s hay pocas reconexiones, no una por segundo, y la oficina sigue sirviendo.

    La conexion se cierra y se reintenta: quedarse leyendo 10 s en una conexion que no suscribio es la
    espera muerta. El backoff acotado es lo que recupera el canal cuando herdr vuelve, sin reinicio.
    """
    sock = herdr_socket("badack.sock", ack={"type": "invalid_request", "message": "missing field pane_id"})
    assert server.start_events(sock) == "polling"
    c0 = _own(sock)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 8.0:
        time.sleep(0.05)
    k = _own(sock) - c0
    assert 1 <= k <= 4, "el backoff no es acotado: %d reconexiones de la oficina en 8 s con la suscripcion muerta" % k
    http = _HTTP(server)
    assert http.get("/api/state")[1]["events"] == "polling"
    assert server.get_state()["agents"], "con la suscripcion muerta la oficina sigue sirviendo"
    http.close()


def test_un_canal_que_funciona_y_se_corta_reintenta_con_la_cota_minima(server, herdr_socket):
    """El canal suscribe y se corta (el BrokenPipe del probe vivo): el backoff se resetea al funcionar, así
    que la cadencia queda acotada por `EV_BACK0` (1 s), no crece."""
    sock = herdr_socket("kick.sock", kick=True)
    assert server.start_events(sock) in ("live", "polling")   # el corte llega justo despues del ack
    c0 = _own(sock)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 8.0:
        time.sleep(0.05)
    k = _own(sock) - c0
    # La cadencia es connect+subscribe+EOF+`EV_BACK0` de sueno, asi que la cota es ~1 conexion/s
    # (el backoff se resetea porque el canal si suscribe). Se admite margen de 4 por scheduling y
    # por el tiempo que el helper de salud tarda en ver el modo. La asercion es que NO hay spin:
    # 111 conexiones en 8 s (observado con un hilo vivo de un test anterior) esta rojo. Con `_own` las
    # conexiones del CLI real que fuga el harness no cuentan; las de la oficina si.
    assert 1 <= k <= 4 + int(8.0 / server.mod.EV_BACK0), "el reintento no esta acotado: %d en 8 s" % k
    assert server.get_state()["agents"], "con el socket cortado la oficina sigue sirviendo"
    http = _HTTP(server)
    assert http.get("/api/state")[1]["events"] in ("polling", "live")
    http.close()


def test_la_salud_de_la_ruta_reporta_cada_modo(server, herdr_socket):
    """`events` es clave aditiva leida POR LA RUTA: `live` con el canal, `polling` con socket que no
    suscribe, `unavailable` sin socket, y nada se renombra."""
    http = _HTTP(server)
    base = server.get_state()
    code, st = http.get("/api/state")
    assert code == 200 and st["events"] == "unavailable"
    assert set(st) == set(base) | {"events"}, "la salud es aditiva: ninguna clave se quita ni se renombra"
    assert all(st[k] == base[k] for k in base if k != "updated")
    code, offs = http.get("/api/offices")
    assert code == 200 and offs["events"] == "unavailable"
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    code, st = http.get("/api/state")
    assert st["events"] == "live"
    code, st = http.get("/api/state?ws=w1")
    assert st["events"] == "live" and st["ws"] == "w1"
    code, offs = http.get("/api/offices")
    assert offs["events"] == "live" and offs["offices"]
    http.close()


def test_el_label_desconocido_sigue_dando_error_y_con_salud(server):
    """T8 no se rompe: un label desconocido es 404 explicito, nunca el estado local, y la salud acompana."""
    http = _HTTP(server)
    code, r = http.get("/api/state?ws=no-existe:w9")
    assert code == 404 and r["events"] == "unavailable" and r.get("ws") == "no-existe:w9"
    assert "agents" not in r, "el 404 no puede devolver el estado local"
    http.close()


def test_sin_hilo_de_eventos_la_linea_base_de_polling_es_la_de_hoy(server):
    """La suite de T1..T8 no arranca el hilo: `unavailable`, ciclo de 5 s, y el subproceso por ciclo.
    Esta es la linea base que T9 no puede romper."""
    assert server.health() == "unavailable"
    st = server.get_state()
    assert st["interval"] == 5 and len(_snaps(server)) == 1
    _envejecer(server, 5.0)
    server.get_state()
    assert len(_snaps(server)) == 2, "el polling de hoy sondea en cada ciclo"
    server.bench(True)
    server.mod.CACHE.update(data=None, at=0.0)
    assert server.get_state()["interval"] == 30


# ---------- T10: el aviso cuando un agente pasa a `blocked` ----------
# Forma VERIFICADA en el binario herdr 0.9.3 (2026-10-06, `herdr notification show --help`):
# `notification show <TITLE> [--body <TEXT>] [--position top-left|top-right|bottom-left|bottom-right]
# [--sound none|done|request]`. El titulo es POSICIONAL y OBLIGATORIO; `notification` no es un metodo
# suscribible, asi que el aviso sale por CLI (`shell=False`) y NUNCA por el socket.
CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
SONIDOS = ("none", "done", "request")


def test_la_transicion_a_bloqueado_avisa_y_el_bloqueado_repetido_no(server, herdr_socket):
    """Requisito 1: se avisa al ENTRAR en `blocked`, no en cada evento que dice `blocked`.

    `EV["status"]` guarda el ultimo estado OBSERVADO por panel, y `notif_plan` se llama solo cuando el
    previo es distinto de `blocked` y el nuevo es `blocked`. Tres `blocked` del mismo panel = UN
    subproceso, y `skips` queda en 0: lo que corta la repeticion es el seguimiento de la transicion por
    `pane_id`, no la ventana de tiempo.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()   # el snapshot siembra la correspondencia pane->nombre
    sock.emit(sock.event_status("w1:pM", "blocked"))
    a = _aviso(server)
    assert a and len(a) == 1, "la transicion a blocked no lanzo un aviso"
    assert "explorer" in _argv(a[0])[0], "el titulo debe nombrar el agente (w1:pM es explorer en el snapshot)"
    n = len(_notifs(server))
    for _ in range(3):
        sock.emit(sock.event_status("w1:pM", "blocked"))
    assert _espera(lambda: server.ev_status("w1:pM") == "blocked")
    time.sleep(1.2)   # el worker suena `NOTIF_IDLE` s: 1.2 s es suficiente para ver un spawn si lo hubiera
    assert len(_notifs(server)) == n, "un panel que sigue bloqueado volvio a avisar: spam"
    assert server.mod.NOTIF["sent"] == 1 and server.mod.NOTIF["skips"] == 0, \
        "la transicion no se sigue por panel: %s" % (server.mod.NOTIF,)
    assert server.health() == "live", "el estado repetido no debe matar el canal"


def test_bloqueado_idle_bloqueado_avisa_dos_y_la_ventana_colapsa_la_ragafa(server, herdr_socket):
    """Requisito 2: `blocked -> idle -> blocked` son dos transiciones, y la ventana colapsa la rafaga.

    El paso del tiempo se simula moviendo la marca `NOTIF["last"][pane]["at"]`, no durmiendo `NOTIF_WIN`
    s: el assert es el mecanismo; dormir daria un test de 20 s. Con la ventana activa, la segunda
    transicion rapida se colapsa y se registra como omitida.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    sock.emit(sock.event_status("w1:pM", "blocked"))
    assert len(_aviso(server)) == 1
    assert _espera(lambda: server.mod.NOTIF["sent"] == 1)
    server.mod.NOTIF["last"]["w1:pM"]["at"] = -1.0   # la ventana paso: es el paso del tiempo, sin dormir
    sock.emit(sock.event_status("w1:pM", "idle"))
    sock.emit(sock.event_status("w1:pM", "blocked"))
    assert len(_aviso(server, 2)) == 2, "la segunda transicion a blocked no avisa"
    n = len(_notifs(server))
    sock.emit(sock.event_status("w1:pM", "idle"))
    sock.emit(sock.event_status("w1:pM", "blocked"))   # rafaga: dentro de la ventana
    time.sleep(1.2)
    assert len(_notifs(server)) == n, "la rafaga blocked->idle->blocked produjo un segundo aviso"
    assert server.mod.NOTIF["skips"] >= 1, "la ventana no registro el aviso colapsado"
    assert 0 < server.mod.NOTIF_WIN <= 60, "la ventana es eleccion de la oficina, acotada"
    assert server.health() == "live"


def test_el_argv_del_aviso_es_la_forma_verificada_en_el_binario(server, herdr_socket):
    """`notification show <TITLE> --body <TEXT> --position <corner> --sound request`, en ese orden.

    El titulo es posicional y obligatorio (capturado del binario), y los unicos valores legales de
    `--position` son los cuatro corners y los de `--sound` son `none|done|request`. El `--body` lleva el
    panel, la oficina y la hora UTC (la misma marca que `SEEN` en `mkagent`).
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    sock.emit(sock.event_status("w1:pM", "blocked", "w1"))
    a = _aviso(server)
    assert a, "no se lanzo el aviso"
    title, flags = _argv(a[0])
    assert title == "explorer te necesita", "el titulo debe nombrar al agente: %s" % title
    assert flags["--body"].startswith("panel w1:pM · oficina w1 · "), flags["--body"]
    assert flags["--body"].endswith(" UTC"), flags["--body"]
    assert flags["--position"] == "top-right"
    assert flags["--position"] in CORNERS
    assert flags["--sound"] == "request" and flags["--sound"] in SONIDOS
    assert server.mod.NOTIF_POS in CORNERS and server.mod.NOTIF_SOUND in SONIDOS


def test_un_panel_sin_nombre_avisa_con_el_pane_id_y_no_inventa(server, herdr_socket):
    """Requisito 3: el evento de estado NO trae el nombre. Si la correspondencia no lo tiene, el titulo
    dice el `pane_id` a secas: nunca se fabrica un nombre.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    assert server.ev_names().get("w9:pZ") is None
    assert server.mod.EV["agent_names"].get("w9:pZ") is None
    sock.emit(sock.event_status("w9:pZ", "blocked", "w9"))
    a = _aviso(server)
    assert a, "el panel desconocido no avisa"
    title, flags = _argv(a[0])
    assert "w9:pZ" in title, "el titulo debe decir que panel es: %s" % title
    assert title == "Panel w9:pZ te necesita", "se fabrico un nombre: %s" % title
    assert flags["--body"].startswith("panel w9:pZ · oficina w9 · "), flags["--body"]


def test_un_aviso_que_falla_no_detiene_el_hilo_y_el_estado_sigue_empujando(server, herdr_socket):
    """Requisito 5: un aviso que falla se registra y se salta, no es fatal.

    El stub no emula `notification show`: responde `{"error":{"code":"usage",...}}` con exit 1 y stdout
    vacio, que es exactamente el caso de un herdr sin el comando o con el socket muerto. Se anade el caso
    de exit 0 con salida vacia (el socket muerto de T4). En los dos el hilo de eventos sigue vivo, el push
    del estado sigue saliendo por el SSE, y el fallo queda contado.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    sse = _SSE(server)
    f0, _ = sse.first(2.0)
    assert f0 and f0["events"] == "live"
    sock.emit(sock.event_status("w1:pM", "blocked"))
    assert _aviso(server), "el aviso se lanzo"
    assert _espera(lambda: server.mod.NOTIF["sent"] == 1 and server.mod.NOTIF["fails"] == 1), \
        "el fallo del aviso no se registro"
    assert server.health() == "live", "un aviso que fallo mato el canal de eventos"
    f1, dt = sse.first(3.0)
    assert f1 and _agentes(f1)["explorer"]["status"] == "blocked", "el push del estado se detuvo"
    server.stub.script_response(("notification", "show"), "", "", 0)   # exit 0 y salida vacia
    server.mod.NOTIF["last"]["w1:pM"]["at"] = -1.0
    sock.emit(sock.event_status("w1:pM", "idle"))
    sock.emit(sock.event_status("w1:pM", "blocked"))
    n = len(_notifs(server))
    assert _espera(lambda: len(_notifs(server)) > n), "el worker paro de consumir la cola tras el fallo"
    assert server.mod.NOTIF["fails"] == 2, "un rc=0 con salida vacia no se conto como aviso fallido"
    assert server.get_state()["agents"], "la oficina dejo de servir tras el aviso fallido"
    sse.close()


def test_el_bloqueado_en_masa_queda_bajo_la_cota_de_spawn(server, herdr_socket):
    """Requisito 6: 8 paneles bloqueados a la vez no pueden fork 8 subproceso.

    La cota se aplica en el SPAWN (`NOTIF_BURST` en `NOTIF_BURST_WIN`), no en la intencion: el worker
    cuenta lo que lanza, asi que el limite es una cota de procesos. Lo que no cabe se registra como
    omitido, no como fallo, y el estado de los 8 paneles sigue llegando.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    panes = ["w2:p%d" % (i + 1) for i in range(8)]
    # Se programan 4 exitos: la cota es 4 spawns en la ventana, y los 4 que salen tienen que contar como
    # enviados. El stub no emula `notification show` (rc=1, `usage`), asi que sin estos 4 sobres los 4
    # spawns serian fallos y la asercion de `fails` no diria nada sobre la cota.
    for _ in range(server.mod.NOTIF_BURST):
        server.stub.script_response(("notification", "show"), '{"type":"ok"}', "", 0)
    for i, p in enumerate(panes):
        server.add_agent(name="agy-masa%d" % i, kind="agy", status="idle", workspace_id="w2", pane_id=p)
        sock.emit(sock.event_status(p, "blocked", "w2"))
    assert _espera(lambda: server.mod.NOTIF["sent"] >= server.mod.NOTIF_BURST), "la cota no dejo lanzar nada"
    time.sleep(2.0)   # tiempo de sobra para que el worker consuma toda la cola
    k = len(_notifs(server))
    assert k <= server.mod.NOTIF_BURST, "el bloqueado en masa forco %d subproceso (cota %d)" % (k, server.mod.NOTIF_BURST)
    assert server.mod.NOTIF["skips"] >= 8 - server.mod.NOTIF_BURST, "los omitidos no se registraron"
    assert server.mod.NOTIF["fails"] == 0, "el omitido por cota no es un fallo"
    assert all(server.ev_status(p) == "blocked" for p in panes), "la tormenta de avisos perdio estados"
    assert server.health() == "live", "la tormenta de avisos mato el canal"


def test_el_aviso_no_bloquea_la_lectura_del_socket(server, herdr_socket):
    """Requisito 5: el spawn vive en su propio hilo. Con un `notification show` de 1.2 s, los tres
    eventos siguientes se aplican MIENTRAS el primer subproceso esta en vuelo: la lectura del socket no
    espera al subproceso.

    El stub registra la invocacion al empezar, asi que la prueba de que el spawn esta en vuelo es la cola
    pendiente (`pend`) y `sent == 0`: los tres estados llegan con dos avisos aun en cola y ninguno
    terminado. Si el spawn fuera en el hilo de lectura, los tres estados tardarian 3.6 s.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    server.stub.script_response(("notification", "show"), '{"type":"ok"}', "", 0, None, 1.2)
    t0 = time.monotonic()
    for p in ("w1:pM", "w1:pD", "w1:pE"):
        sock.emit(sock.event_status(p, "blocked"))
    hit = _espera(lambda: all(server.ev_status(p) == "blocked" for p in ("w1:pM", "w1:pD", "w1:pE"))
                  and len(server.mod.NOTIF["pend"]) >= 2, 1.0)
    assert hit, "los tres eventos no se aplicaron con un spawn en vuelo"
    dt = time.monotonic() - t0
    assert dt < 1.0, "la lectura del socket espero %s s al subproceso de aviso" % round(dt, 2)
    assert server.mod.NOTIF["sent"] == 0, "el worker termino el spawn antes: no hay hilo separado"
    assert _espera(lambda: server.mod.NOTIF["sent"] >= 1, 3.0)
    assert time.monotonic() - t0 >= 1.2, "el subproceso no tardo lo programado: la cola no se consumio"
    assert _espera(lambda: len(_notifs(server)) == 3, 6.0), "la cola no se consumio entera"
    assert server.health() == "live"


def test_el_worker_solo_lanza_notification_show_y_el_socket_no_manda_mutantes(server, herdr_socket):
    """El spawn nuevo es SOLO `notification show`; por el socket sigue sin salir un metodo mutante.

    El aviso se lanza por CLI (verificado en `herdr notification show --help`), no por el socket:
    `notification` no es un metodo suscribible. Se aserciona sobre el log de invocaciones del stub y el
    log de pedidos del socket, no sobre una replica del codigo.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    for p in ("w1:pM", "w1:pD", "w2:pZ"):
        sock.emit(sock.event_status(p, "blocked", p.split(":")[0]))
    assert len(_aviso(server, 3)) == 3
    heads = {tuple(c[:2]) for c in server.calls()}
    assert ("notification", "show") in heads
    for c in server.calls():
        assert tuple(c[:2]) not in [("agent", "prompt"), ("agent", "start"), ("pane", "split"),
                                    ("pane", "run"), ("pane", "send-keys"), ("workspace", "create"),
                                    ("workspace", "close")], "spawn mutante: %s" % (c,)
    assert {tuple(c[:2]) for c in server.calls()} == {("api", "snapshot"), ("notification", "show")}, \
        "el unico spawn nuevo es `notification show`: %s" % sorted(heads)
    assert set(sock.methods()) <= {"events.subscribe", "ping"}, sock.methods()
    for m in sock.methods():
        assert m not in MUTANTES
    assert server.executables() == ["herdr"], "el aviso tiene que salir por el binario herdr: %s" % server.executables()


def test_el_aviso_es_aditivo_y_la_linea_base_de_aviso_no_lanza_subproceso(server, herdr_socket):
    """Sin hilo de eventos (la linea base de T1..T8) no hay aviso: `unavailable` no sondea el binario.

    El worker solo se arranca con `ev_start()`, y `notif_view()` es un helper de estado: no renombra
    ninguna clave del payload. La suite de la linea base sigue sin lanzar subproceso de notificacion.
    """
    assert server.health() == "unavailable"
    server.get_state()
    sock = herdr_socket("herdr.sock")
    sock.enqueue(sock.event_status("w1:pM", "blocked"))
    time.sleep(0.5)
    assert _notifs(server) == [], "la linea base de polling lanzo un aviso: no hay canal de eventos"
    v = server.mod.notif_view()
    assert set(v) == {"sent", "skips", "fails", "last_pane", "last_ts", "win", "burst"}
    assert v["last_pane"] is None
    st = server.get_state()
    assert "notif" not in st, "la clave aditiva `notif` no esta autorizada en el payload (T10)"
    assert set(st) == {"agents", "metrics", "ticker", "queue", "lock", "bench", "suplencia", "herdr",
                       "tareas", "updated", "interval", "columnas"}, "ninguna clave del estado se renombra (T17 anade `columnas` aditivo)"
    http = _HTTP(server)
    code, r = http.get("/api/state")
    assert code == 200 and r["events"] == "unavailable" and "notif" not in r
    http.close()


def test_forma_de_exito_de_notification_show_es_la_capturada_en_vivo(server):
    """T10, hueco cerrado en vivo (2026-10-07): `herdr notification show ...` responde

        rc=0, stdout: {"id":"cli:notification:show","result":{"reason":"shown","shown":true,
                       "type":"notification_show"}}

    El worker exige `rc == 0` Y salida no vacia (`notif_client`): un rc=0 con los dos canales
    vacios es el socket muerto de T4, y ese aviso no se cuenta como enviado. Este ancla fija la
    forma verbatim y prueba que `herdr_cmd` la parsea: `type` vive dentro de `result` (T25), y
    `shown` es la confirmacion real de que la notificacion salio en pantalla.
    """
    from pathlib import Path
    raw = (Path(__file__).parent / "fixtures" / "notification_show.json").read_text(encoding="utf-8")
    server.script_response(["notification", "show"], raw, "", 0)
    argv = list(server.mod.NOTIF_CMD) + ["titulo", "--body", "x", "--position",
                                          server.mod.NOTIF_POS, "--sound", server.mod.NOTIF_SOUND]
    r = server.mod.herdr_cmd(argv, server.mod.NOTIF_TIMEOUT)
    assert r["rc"] == 0 and r["out"] and not r["err"]
    assert r["type"] == "notification_show" and r["res"]["shown"] is True
    assert r["res"]["reason"] == "shown"
    assert r["code"] is None and r["dead"] is False
    assert server.mod.herdr_error(r) is None   # sana: no es un error


# ---------- T27: el stream lleva LA oficina pedida, y el handler sale cuando el cliente se va ----------
# Defecto medido en vivo: `build_state` filtra a la oficina de Strata (`wsS` = el workspace de
# `opencode2`), asi que `/api/events` solo llevaba `w1`: 16 frames con los mismos 5 agentes, y un cambio
# real de estado en otra oficina no generaba ningun frame. La UI lo tapaba sondeando `/api/state?ws=`
# cada 5 s, el polling que T9 quito.
# Defecto secundario (registrado por T28): el bucle salia solo cuando una escritura contra el cliente
# cerrado daba `BrokenPipe`, asi que el handler seguia llamando a `get_state` hasta ~30 s despues de que
# el navegador se fue. La guardia de sesion de T28 lo media: `pedidos tras el sellado=25` y
# `HEARTBEAT_DRAIN` = 35 s de drenado. La condicion de salida del servidor es la que hace innecesario ese
# drenado, no una lucha contra el teardown: el handler sale al ver el EOF, y `server_close()` (que espera
# a los handlers, `block_on_close` = True) vuelve al instante.
# Superficie: `server.py` (`ev_scope`, `ev_dirty(oid)`, `ev_take`, `ev_cycle(quantum, oid)`,
# `ev_event` marca `dirty_ws`, `Handler._sse_alive`) y `index.html` (el `EventSource` con el `ws` de la
# oficina elegida y el sondeo como respaldo solo cuando `events` no es `live`).


def test_sin_ws_el_stream_es_la_oficina_de_strata_exacta(server, herdr_socket):
    """Requisito 1: sin `ws` el stream es la oficina de Strata, exactamente como hoy.

    El roster es el de `w1` y el payload es el de `get_state` (metrics, cola, ticker, `interval`), sin
    clave nueva: `events` es la unica aditiva de T9. `w1` y `bazzite:w1` son la misma oficina (T8), asi
    que las dos salen por `get_state`, no por `state_ws`: la oficina de Strata es la unica que tiene
    metrics, cola y ticker, y un `ws` redundante no puede convertirla en una oficina vacia.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    sse = _SSE(server)
    f, dt = sse.first(2.0)
    assert f and f["events"] == "live" and f["interval"] == 5
    assert "ws" not in f, "sin `ws` el payload es el de `get_state`, sin clave nueva"
    assert {a["name"] for a in f["agents"]} == set(server.mod.FIXED), \
        "el roster sin `ws` es el de w1: %s" % sorted(a["name"] for a in f["agents"])
    assert set(f) == {"agents", "metrics", "ticker", "queue", "lock", "bench", "suplencia", "herdr",
                      "tareas", "updated", "interval", "events", "columnas"}, "solo `events` y `columnas` son aditivas"
    sse.close()
    sse = _SSE(server, path="/api/events?ws=w1")
    g, dt = sse.first(2.0)
    assert g and "ws" not in g and {a["name"] for a in g["agents"]} == set(server.mod.FIXED), \
        "`ws=w1` es la oficina de Strata: `get_state`, no `state_ws`"
    sse.close()
    sse = _SSE(server, path="/api/events?ws=bazzite:w1")
    h, dt = sse.first(2.0)
    assert h and "ws" not in h and {a["name"] for a in h["agents"]} == set(server.mod.FIXED), \
        "`bazzite:w1` es la misma oficina (T8)"
    sse.close()


def test_el_stream_lleva_la_oficina_pedida_y_el_bloqueado_llega_como_frame(server, herdr_socket):
    """Requisito 2 y 4, por la ruta real: un `pane.agent_status_changed` de `w2` genera frame en `w2`.

    El estado es el real, incluido `blocked` (el contrato de T2), y la actividad deriva de ese estado
    (`ACTS`), asi que el stream no contradice a `state_ws`. La oficina pedida lee el snapshot cacheado con
    la TTL del ciclo de reconciliacion (`SNAP["ttl"] = RECONCILE` en `live`), y el overlay por `pane_id`
    pone el estado del evento. El evento de estado envejece el snapshot local (T9: pane->nombre al ciclo),
    asi que el push cuesta UNA lectura, no dos.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()   # siembra los paneles, la correspondencia pane->nombre y la TTL del snapshot
    server.add_agent(name="agy-a1", kind="agy", status="idle", workspace_id="w2", pane_id="w2:p1")
    with server.mod.STLOCK: server.mod.SNAP["data"].pop(None, None)   # `crear` envejece el snapshot (T6)
    sse = _SSE(server, path="/api/events?ws=w2")
    f, dt = sse.first(2.0)
    assert f and f["ws"] == "w2" and f["maquina"] == "bazzite", "el stream no lleva la oficina pedida"
    assert [a["name"] for a in f["agents"]] == ["agy-a1"], "el stream de w2 lleva los agentes de w2"
    keys = set(server.state_ws("w2"))   # la forma de la oficina pedida: `state_ws`, mas `events`
    n = len(_snaps(server))
    sock.emit(sock.event_status("w2:p1", "blocked", "w2"))
    g, dt2 = sse.first(3.0)
    assert g is not None, "el cambio en w2 no genero frame: el filtro `wsS` de `build_state`"
    ag = _agentes(g)["agy-a1"]
    assert ag["status"] == "blocked" and ag["pane_id"] == "w2:p1", "el estado real no llega al stream"
    assert ag["activity"] == "esperando confirmación", "estado y actividad se contradicen (T2)"
    assert set(g) == keys | {"events"}, "la oficina pedida paga `state_ws` + `events`"
    assert dt2 < 1.5, "el evento de w2 llego tarde"
    assert len(_snaps(server)) - n == 1, "el evento de w2 costo mas de una lectura de snapshot"
    sse.close()

    server.mod.CACHE.update(data=None, at=0.0)
    assert server.state_ws("w2")["agents"][0]["status"] == "blocked"


def test_un_cliente_que_mira_A_no_recibe_el_estado_de_B(server, herdr_socket):
    """TRIANGULAR: cada conexion esta confinada a la oficina que pidio."""
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    server.add_agent(name="agy-a1", kind="agy", status="idle", workspace_id="w2", pane_id="w2:p1")
    with server.mod.STLOCK: server.mod.SNAP["data"].pop(None, None)   # `crear` envejece el snapshot (T6)
    a = _SSE(server, path="/api/events?ws=w2")
    b = _SSE(server)
    fa, _ = a.first(2.0)
    fb, _ = b.first(2.0)
    assert fa and fb
    assert {x["name"] for x in fa["agents"]} == {"agy-a1"}, "el stream de w2 lleva solo w2"
    assert "agy-a1" not in {x["name"] for x in fb["agents"]}, "el stream de Strata no debe ver w2"
    sock.emit(sock.event_status("w2:p1", "blocked", "w2"))
    g, dt = a.first(3.0)
    assert g and _agentes(g)["agy-a1"]["status"] == "blocked", "el blocked de w2 no llego"
    assert b.frames(3.0) == [], "el cambio de w2 llego a Strata"
    a.close()
    b.close()


def test_un_ws_desconocido_en_events_se_refusa_y_no_sirve_el_estado_local(server, herdr_socket):
    """Requisito 1 y la regla de T8: el dominio de `ws` es `WSRE`, y un label desconocido es un error.

    El stream refusado no puede servir el estado local: el payload de error dice lo que se pidio, y no
    lleva `agents`, asi la UI no puede pintar la oficina de Strata creyendo que mira la otra.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    http = _HTTP(server)
    code, r = http.get("/api/events?ws=no-existe:w9")
    assert code == 404 and r.get("ws") == "no-existe:w9" and r.get("maquina") == "no-existe"
    assert "agents" not in r, "un ws refusado no sirve el estado local"
    code, r = http.get("/api/events?ws=W9")
    assert code == 400 and r.get("ws") == "W9" and "agents" not in r, "el label malformado se refusa"
    assert code == http.get("/api/state?ws=W9")[0], "el dominio de `ws` es el mismo que `/api/state?ws`"
    http.close()


def test_el_handler_SSE_sale_al_irse_el_cliente_y_deja_de_sondear(server, herdr_socket, monkeypatch):
    """Requisito 3: el bucle sale cuando el cliente se va, sin esperar a que una escritura reviente.

    La definicion de "hilo del servidor" es la de T28 (`conftest.py:_server_threads`): los objetivos de
    `server.py` y los handlers de `ThreadingHTTPServer` cuya pila esta dentro del `Handler`. El bucle de
    hoy solo salia cuando una escritura contra el cliente cerrado daba `BrokenPipe`, y eso puede tardar un
    latido entero: medido en T28, 25 pedidos de `api snapshot` llegaban tras el teardown y la guardia de
    sesion drenaba `HEARTBEAT_DRAIN` = 35 s. Aqui el handler vuelve al baseline en menos de 6 s y no
    construye nada despues de irse el cliente.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    builds = []
    real = server.mod.build_state
    monkeypatch.setattr(server.mod, "build_state", lambda: (builds.append(1), real())[1])
    n0 = len(server.server_threads())
    sse = _SSE(server)
    f, dt = sse.first(2.0)
    assert f and f["events"] == "live"
    assert len(_handlers(server, sse.srv)) == 1, "el handler del SSE es un hilo del servidor"
    n1 = n0 + 1
    sse.close()   # el navegador cierra el `EventSource`: solo se cierra el cliente
    assert _espera(lambda: not _handlers(server, sse.srv), 6.0), \
            "handler vivo 6 s despues de irse el cliente: %s" % _handlers(server, sse.srv)
    assert _espera(lambda: len(server.server_threads()) < n1, 6.0), \
            "el conteo del proceso no volvio al baseline: %s" % server.server_threads()
    n = len(builds)
    time.sleep(2.0)   # mas de un latido: el bucle viejo construia estado aqui
    assert not _handlers(server, sse.srv), "el handler revivio"
    assert len(builds) == n, "un cliente muerto siguio construyendo: %d" % (len(builds) - n)


def test_sin_canal_live_el_respaldo_de_la_UI_sigue_con_el_quantum_de_hoy(server, herdr_socket):
    """Requisito 5: sin `live` el handler cae al quantum de 5 s y al latido de ~30 s de hoy.

    La UI sondea solo cuando `events` no es `live` (`index.html`), asi que el respaldo tiene que seguir
    sondeando el estado: en 10 s de stream hay 2 construcciones y ningun frame nuevo. Con BENCH el
    quantum del stream es 30 s, el ciclo lento que T9 ya usa.
    """
    sock = herdr_socket("refused.sock", refuse=True)
    assert server.start_events(sock) == "unavailable"
    sse = _SSE(server)
    f0, dt0 = sse.first(1.0)
    assert f0 and f0["events"] == "unavailable" and f0["interval"] == 5
    n = len(_snaps(server))
    mas = sse.frames(10.0)
    assert mas == [], "el polling empujo sin cambio: el latido se acorto"
    assert len(_snaps(server)) - n >= 2, "el quantum de 5 s se acorto: el respaldo de la UI deja de sondear"
    sse.close()
    server.bench(True)
    server.mod.CACHE.update(data=None, at=0.0)
    sse2 = _SSE(server)
    f1, dt1 = sse2.first(1.0)
    assert f1 and f1["interval"] == 30, "con BENCH el quantum del stream es 30 s"
    sse2.close()


def test_un_cliente_de_una_oficina_remota_no_recibe_eventos_locales(server, herdr_socket):
    """TRIANGULAR: el socket de eventos es el de bazzite, y los eventos no llegan de la maquina remota.

    `raw_agents` solo superpone el overlay en la maquina local (T9), asi que el stream remoto se refresca
    en el latido, no en el evento. La confinacion por `ws` se mantiene: el evento local de `w2` no
    despierta al stream de `mac-mini:w2`, que es otra oficina.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    server.set_remote_agents("mac-mini", [{"name": "agy-rem", "agent": "agy", "agent_status": "idle",
                                           "pane_id": "w2:p1", "tab_id": "w2:t1", "workspace_id": "w2",
                                           "focused": False}])
    sse = _SSE(server, path="/api/events?ws=mac-mini:w2")
    f, dt = sse.first(2.0)
    assert f and f["ws"] == "mac-mini:w2" and f["maquina"] == "mac-mini", "el stream remoto no es el de la oficina pedida"
    assert [a["name"] for a in f["agents"]] == ["agy-rem"], "el roster remoto no es el de la maquina pedida"
    sock.emit(sock.event_status("w2:p1", "blocked", "w2"))
    time.sleep(1.0)
    g, dt2 = sse.first(1.0)
    assert g is None, "un evento local no debe llegar al stream remoto"
    assert _agentes(f)["agy-rem"]["status"] == "idle", "el overlay local no se aplica a la maquina remota"
    sse.close()


def test_en_repojo_el_stream_de_otra_oficina_no_lanza_subproceso(server, herdr_socket):
    """El criterio 2 de la feature por la oficina pedida: 10 s de stream en reposo, 0 subproceso.

    La oficina pedida lee el mismo snapshot cacheado que construyo el estado (`SNAP["ttl"]` = RECONCILE en
    `live`), y su bandera de suciedad es la suya (`dirty_ws`): sin evento suyo duerme el quantum entero, y
    el latido (`quiet >= 6`) sigue siendo ~30 s, asi que no empuja frames.
    """
    sock = herdr_socket("herdr.sock")
    assert server.start_events(sock) == "live"
    server.get_state()
    server.add_agent(name="agy-a1", kind="agy", status="idle", workspace_id="w2", pane_id="w2:p1")
    with server.mod.STLOCK: server.mod.SNAP["data"].pop(None, None)   # `crear` envejece el snapshot (T6)
    sse = _SSE(server, path="/api/events?ws=w2")
    f, dt = sse.first(2.0)
    assert f and f["ws"] == "w2"
    n = len(server.stub.calls)
    mas = sse.frames(10.0)
    assert mas == [], "sin evento no hay frame"
    assert len(server.stub.calls) == n, "10 s de stream de w2 en reposo lanzaron %d subproceso(s)" % (len(server.stub.calls) - n)
    sse.close()
