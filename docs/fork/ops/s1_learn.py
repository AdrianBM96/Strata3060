"""s1_learn — el bucle de autoaprendizaje de System One (PLAN_MAESTRO.md §4.4-4.5).  Solo librería estándar.

Piezas:
  * Store: SQLite con cada decisión (plantilla, estado, puntuaciones por permutación, salida) y sus etiquetas, de tres
    fuentes: "human" (feedback explícito: ORO), "system2" (decisiones escaladas que resolvió System Two: PLATA) y
    "audit" (una muestra aleatoria de TODAS las decisiones, escalen o no, resuelta por System Two: PLATA).  Solo la
    muestra de auditoría es uniforme: las escaladas son casi todas las de confianza baja, así que medir el error con
    ellas lo sesga.  Por eso la garantía conformal y la comparación campeón/aspirante usan solo decisiones auditadas.
  * Calibración por plantilla ("vector scaling"): p ∝ exp((z_k + b_k) / T), con z_k la logprob de la opción ya sin el
    sesgo de letra.  Se ajusta con las etiquetas de esa plantilla (log-loss + L2) y solo entra si GANA en datos
    retenidos por tiempo (campeón/aspirante); cada versión se guarda y se puede volver atrás.
  * Umbral conformal (Learn-then-Test, cola binomial exacta): la confianza mínima a partir de la cual el error de las
    decisiones automáticas es ≤ alpha con confianza 1 - delta, medido en datos que la calibración no vio.  Por debajo,
    la decisión escala.  La garantía es respecto a la fuente de las etiquetas: con ORO, respecto a la verdad; con
    PLATA, respecto a lo que decidiría System Two.  Y supone que lo que viene se parece a lo reciente.
  * SystemTwo: un hilo que resuelve en segundo plano las decisiones encoladas pidiendo a Strata una respuesta con
    razonamiento.  Empieza solo con Strata libre un rato (GET /status: ni busy ni queued durante idle_s), y si
    mientras razona llega otra petición (queued > 0) corta la conexión: Strata cancela la suya en 0,5 s y la otra
    entra.  La decisión vuelve a la cola.  La cola vive en la base: lo pendiente se retoma al reiniciar.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import queue
import random
import re
import socket
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import uuid

GOLD = "human"
SILVER = ("system2", "audit")
SOURCE_WEIGHT = {"human": 1.0, "system2": 0.5, "audit": 0.5}


def template_id(instructions: str, keys: list[str], criteria: dict) -> str:
    """La plantilla de una pregunta: instrucciones + opciones (clave y texto), sin el estado."""
    raw = json.dumps([instructions, [[k, criteria[k]] for k in keys]], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def option_features(keys: list[str], passes: list[dict]) -> list[float]:
    """z_k: la logprob de cada opción ya sin el sesgo de letra (si se midió), media sobre las permutaciones.  No depende
    del orden en que se preguntó, así que sirve de entrada a la calibración aprendida."""
    z = []
    for k in keys:
        vals = [p["raw"][k] - (p["cf"][k] if p.get("cf") else 0.0) for p in passes if k in p["raw"]]
        z.append(sum(vals) / len(vals) if vals else -30.0)
    return z


def softmax(xs: list[float]) -> list[float]:
    m = max(xs)
    e = [math.exp(x - m) for x in xs]
    s = sum(e)
    return [v / s for v in e]


# ------------------------------------------------------------------ registro
class Store:
    def __init__(self, path: str, keep_state_days: float = 30.0):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.lock = threading.Lock()
        self.keep_state_days = keep_state_days
        with self.lock:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY, ts REAL, template TEXT, qtype TEXT, instructions TEXT, keys TEXT, criteria TEXT,
                    state TEXT, state_hash TEXT, features TEXT, passes TEXT, probs TEXT, choice TEXT, conf REAL,
                    escalated INTEGER, auto INTEGER, model_version INTEGER);
                CREATE INDEX IF NOT EXISTS decisions_template ON decisions(template, ts);
                CREATE TABLE IF NOT EXISTS labels (
                    decision_id TEXT, source TEXT, label TEXT, ts REAL, detail TEXT,
                    PRIMARY KEY (decision_id, source));
                CREATE TABLE IF NOT EXISTS models (
                    template TEXT, version INTEGER, kind TEXT, params TEXT, metrics TEXT, ts REAL, active INTEGER,
                    PRIMARY KEY (template, version));
            """)
            # columnas nuevas (bases de antes): audit = la decisión cayó en la muestra uniforme de auditoría; reasons =
            # por qué escaló.  En las filas viejas quedan NULL: no cuentan como auditadas (no se sabe cómo se eligieron)
            have = {r[1] for r in self.db.execute("PRAGMA table_info(decisions)")}
            for col, typ in (("audit", "INTEGER"), ("reasons", "TEXT")):
                if col not in have:
                    self.db.execute(f"ALTER TABLE decisions ADD COLUMN {col} {typ}")

    def _q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def record(self, qtype, tid, instructions, keys, criteria, state, features, passes, probs, choice, conf,
               escalated, auto, model_version, audit: bool = False, reasons: list | None = None) -> str:
        did = uuid.uuid4().hex
        st = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
        self._q("INSERT INTO decisions (id, ts, template, qtype, instructions, keys, criteria, state, state_hash, "
                "features, passes, probs, choice, conf, escalated, auto, model_version, audit, reasons) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (did, time.time(), tid, qtype, instructions, json.dumps(keys), json.dumps(criteria, ensure_ascii=False),
                 st, hashlib.sha1(st.encode()).hexdigest(), json.dumps(features), json.dumps(passes),
                 json.dumps(probs), choice, conf, int(bool(escalated)), int(bool(auto)), model_version,
                 int(bool(audit)), json.dumps(list(reasons or []))))
        return did

    def decision(self, did: str) -> dict | None:
        rows = self._q("SELECT id, ts, template, qtype, instructions, keys, criteria, state, features, choice, conf, "
                       "escalated, auto, audit, reasons FROM decisions WHERE id = ?", (did,))
        if not rows:
            return None
        r = rows[0]
        return {"id": r[0], "ts": r[1], "template": r[2], "qtype": r[3], "instructions": r[4], "keys": json.loads(r[5]),
                "criteria": json.loads(r[6]), "state": r[7], "features": json.loads(r[8]), "choice": r[9],
                "conf": r[10], "escalated": bool(r[11]), "auto": bool(r[12]), "audit": bool(r[13]),
                "reasons": json.loads(r[14]) if r[14] else []}

    def has_label(self, did: str) -> bool:
        return bool(self._q("SELECT 1 FROM labels WHERE decision_id = ? LIMIT 1", (did,)))

    def audited(self, tid: str) -> int:
        """Cuántas decisiones de esta plantilla cayeron en la muestra de auditoría (para el arranque en frío)."""
        return self._q("SELECT COUNT(*) FROM decisions WHERE template = ? AND audit = 1", (tid,))[0][0]

    def pending(self) -> list[tuple[str, str]]:
        """Lo que System Two tiene por hacer (auditadas o escaladas, con estado, sin ninguna etiqueta ni fallo), de más
        antigua a más nueva: la cola sobrevive a un reinicio."""
        rows = self._q("SELECT d.id, d.audit FROM decisions d WHERE (d.audit = 1 OR d.escalated = 1) "
                       "AND d.state IS NOT NULL AND NOT EXISTS (SELECT 1 FROM labels l WHERE l.decision_id = d.id) "
                       "ORDER BY d.ts")
        return [(did, "audit" if audit else "system2") for did, audit in rows]

    def add_label(self, did: str, source: str, label: str, detail: dict | None = None) -> None:
        self._q("INSERT OR REPLACE INTO labels VALUES (?,?,?,?,?)",
                (did, source, str(label), time.time(), json.dumps(detail or {}, ensure_ascii=False)))

    def labeled(self, tid: str | None = None, sources=(GOLD,) + SILVER) -> list[dict]:
        """Las decisiones con etiqueta, por orden de tiempo; con varias fuentes gana ORO."""
        where = "WHERE d.template = ?" if tid else ""
        rows = self._q(f"SELECT d.id, d.ts, d.template, d.keys, d.features, l.source, l.label, d.audit, d.reasons "
                       f"FROM decisions d JOIN labels l ON l.decision_id = d.id {where} ORDER BY d.ts",
                       (tid,) if tid else ())
        best: dict[str, dict] = {}
        for did, ts, t, keys, feats, src, lab, audit, reasons in rows:
            if src not in sources:
                continue
            cur = best.get(did)
            if cur is None or (src == GOLD and cur["source"] != GOLD):
                # eligible: podría haberse decidido solo (no escaló por otra cosa que el umbral conformal)
                rs = json.loads(reasons) if reasons else []
                best[did] = {"id": did, "ts": ts, "template": t, "keys": json.loads(keys),
                             "features": json.loads(feats), "source": src, "label": lab, "audit": bool(audit),
                             "eligible": all(x == "conformal" for x in rs)}
        return sorted((r for r in best.values() if r["label"] in r["keys"]), key=lambda r: r["ts"])

    def active_model(self, tid: str) -> dict | None:
        rows = self._q("SELECT version, kind, params, metrics FROM models WHERE template = ? AND active = 1", (tid,))
        if not rows:
            return None
        v, kind, params, metrics = rows[0]
        return {"version": v, "kind": kind, "params": json.loads(params), "metrics": json.loads(metrics)}

    def save_model(self, tid: str, kind: str, params: dict, metrics: dict) -> int:
        with self.lock:
            v = (self.db.execute("SELECT COALESCE(MAX(version), 0) FROM models WHERE template = ?", (tid,))
                 .fetchone()[0]) + 1
            self.db.execute("UPDATE models SET active = 0 WHERE template = ?", (tid,))
            self.db.execute("INSERT INTO models VALUES (?,?,?,?,?,?,1)",
                            (tid, v, kind, json.dumps(params), json.dumps(metrics), time.time()))
        return v

    def rollback(self, tid: str) -> int | None:
        """Vuelve a la versión anterior (o a ninguna: sin calibración aprendida).  -> la versión activa."""
        with self.lock:
            rows = self.db.execute("SELECT version FROM models WHERE template = ? ORDER BY version DESC",
                                   (tid,)).fetchall()
            act = self.db.execute("SELECT version FROM models WHERE template = ? AND active = 1", (tid,)).fetchone()
            self.db.execute("UPDATE models SET active = 0 WHERE template = ?", (tid,))
            if not act:
                return None
            older = [r[0] for r in rows if r[0] < act[0]]
            if older:
                self.db.execute("UPDATE models SET active = 1 WHERE template = ? AND version = ?", (tid, older[0]))
                return older[0]
            return None

    def purge_states(self) -> int:
        """Borra el texto de los estados más viejos que keep_state_days (se quedan las puntuaciones y las etiquetas)."""
        cut = time.time() - self.keep_state_days * 86400
        with self.lock:
            return self.db.execute("UPDATE decisions SET state = NULL WHERE ts < ? AND state IS NOT NULL",
                                   (cut,)).rowcount

    def stats(self) -> dict:
        out = {}
        for tid, n, esc, auto in self._q("SELECT template, COUNT(*), SUM(escalated), SUM(auto) FROM decisions "
                                         "GROUP BY template"):
            labs = dict(self._q("SELECT l.source, COUNT(*) FROM labels l JOIN decisions d ON d.id = l.decision_id "
                                "WHERE d.template = ? GROUP BY l.source", (tid,)))
            agree = self._q("SELECT SUM(l.label = d.choice), COUNT(*) FROM labels l JOIN decisions d "
                            "ON d.id = l.decision_id WHERE d.template = ? AND l.source = 'audit'", (tid,))[0]
            m = self.active_model(tid)
            out[tid] = {"decisions": n, "escalated": esc or 0, "auto": auto or 0, "audited": self.audited(tid),
                        "labels": labs,
                        "audit_agreement": (agree[0] / agree[1]) if agree[1] else None,
                        "model_version": m["version"] if m else None, "model_metrics": m["metrics"] if m else None}
        return out


# ------------------------------------------------------------------ calibración aprendida
def predict_vs(z: list[float], b: list[float], log_t: float) -> list[float]:
    t = math.exp(log_t)
    return softmax([(zi + bi) / t for zi, bi in zip(z, b)])


def fit_vector_scaling(X: list[list[float]], y: list[int], w: list[float], prior: float = 5.0,
                       iters: int = 400) -> tuple[list[float], float]:
    """Ajusta b (un sesgo por opción) y log T minimizando la log-loss media ponderada + (prior / W) * (|b|² + log_t²):
    un prior hacia "sin cambio" que pesa como `prior` decisiones, así que con pocas etiquetas casi no se mueve y con
    muchas manda el dato.  Descenso de gradiente con paso adaptativo: pocos parámetros, milisegundos."""
    K = len(X[0])
    b, lt = [0.0] * K, 0.0
    W = sum(w) or 1.0
    l2 = prior / W

    def loss_grad(b, lt):
        t = math.exp(lt)
        L = l2 * (sum(x * x for x in b) + lt * lt)
        gb = [2 * l2 * x for x in b]
        glt = 2 * l2 * lt
        for z, yi, wi in zip(X, y, w):
            s = [(zi + bi) / t for zi, bi in zip(z, b)]
            p = softmax(s)
            L -= wi / W * math.log(max(p[yi], 1e-12))
            for k in range(K):
                d = (p[k] - (1.0 if k == yi else 0.0)) * wi / W
                gb[k] += d / t
                glt += d * (-s[k])
        return L, gb, glt

    step = 0.5
    L, gb, glt = loss_grad(b, lt)
    for _ in range(iters):
        nb = [x - step * g for x, g in zip(b, gb)]
        nlt = max(-3.0, min(3.0, lt - step * glt))
        nL, ngb, nglt = loss_grad(nb, nlt)
        if nL < L:
            b, lt, L, gb, glt = nb, nlt, nL, ngb, nglt
            step *= 1.2
        else:
            step *= 0.5
            if step < 1e-6:
                break
    return b, lt


def metrics(probs: list[list[float]], y: list[int]) -> dict:
    n = len(y)
    nll = -sum(math.log(max(p[t], 1e-12)) for p, t in zip(probs, y)) / n
    brier = sum(sum((pk - (1.0 if k == t else 0.0)) ** 2 for k, pk in enumerate(p)) for p, t in zip(probs, y)) / n
    acc = sum(max(range(len(p)), key=p.__getitem__) == t for p, t in zip(probs, y)) / n
    bins = [[0, 0.0, 0.0] for _ in range(10)]
    for p, t in zip(probs, y):
        k = max(range(len(p)), key=p.__getitem__)
        i = min(int(p[k] * 10), 9)
        bins[i][0] += 1
        bins[i][1] += p[k]
        bins[i][2] += k == t
    ece = sum(abs(c[1] - c[2]) for c in bins if c[0]) / n
    return {"n": n, "nll": nll, "brier": brier, "accuracy": acc, "ece": ece}


# ------------------------------------------------------------------ garantía conformal
def binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k), X ~ Binomial(n, p), exacta (log-gamma)."""
    if k >= n:
        return 1.0
    if k < 0:
        return 0.0
    lp, lq = math.log(p), math.log1p(-p)
    s = 0.0
    for i in range(k + 1):
        s += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(1.0, s)


THRESHOLD_GRID = (0.99, 0.98, 0.97, 0.96, 0.95, 0.93, 0.9, 0.875, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5,
                  0.45, 0.4, 0.35)


def select_threshold(conf: list[float], correct: list[bool], alpha: float, delta: float,
                     grid: tuple = THRESHOLD_GRID) -> dict | None:
    """Learn-then-Test con Bonferroni sobre una rejilla FIJA de umbrales de confianza: para cada umbral, H0 "el error
    entre las decisiones con confianza >= umbral es > alpha" se rechaza si P(Bin(n, alpha) <= errores) <= delta / |rejilla|.
    Todos los rechazos valen a la vez (Bonferroni), así que se elige el umbral rechazado más bajo (más cobertura):
    P(su error real > alpha) <= delta.  (Una secuencia fija sobre los valores observados no sirve: el primer umbral
    tiene 1-2 decisiones, nunca se rechaza, y la búsqueda se para ahí.)  None si ninguno se rechaza: todo escala."""
    level = delta / len(grid)
    best = None
    for lam in sorted(grid, reverse=True):
        acc = [ok for c, ok in zip(conf, correct) if c >= lam]
        n = len(acc)
        if n == 0:
            continue
        errors = n - sum(acc)
        if binom_cdf(errors, n, alpha) <= level:
            best = {"threshold": lam, "accepted": n, "errors": errors, "coverage": n / len(conf)}
    return best


# ------------------------------------------------------------------ la fachada que usa ada-decide
class Learner:
    def __init__(self, store: Store, alpha: float = 0.05, delta: float = 0.1, min_labels: int = 30,
                 holdout: float = 0.5, retrain_every: int = 20, gold_min: int = 50, min_gain: float = 0.01):
        self.store, self.alpha, self.delta = store, alpha, delta
        self.min_labels, self.holdout, self.retrain_every = min_labels, holdout, retrain_every
        self.gold_min, self.min_gain = gold_min, min_gain
        self.cache: dict[str, dict | None] = {}
        self.new_labels: dict[str, int] = {}
        self.lock = threading.Lock()

    def model(self, tid: str) -> dict | None:
        with self.lock:
            if tid not in self.cache:
                self.cache[tid] = self.store.active_model(tid)
            return self.cache[tid]

    def adjust(self, tid: str, keys: list[str], z: list[float], probs: dict) -> tuple[dict, dict | None]:
        """Las probabilidades con la calibración aprendida de la plantilla (si hay una activa), y su garantía."""
        m = self.model(tid)
        if not m or m["kind"] != "vector_scaling" or len(m["params"]["b"]) != len(keys):
            return probs, None
        p = predict_vs(z, m["params"]["b"], m["params"]["log_t"])
        return dict(zip(keys, p)), m

    def gate(self, m: dict | None, conf: float) -> tuple[bool | None, dict | None]:
        """(auto, garantía): auto es None si la plantilla aún no tiene umbral (sin datos: lo deciden las reglas de hoy)."""
        thr = (m or {}).get("params", {}).get("conformal")
        if not thr:
            return None, None
        g = {"alpha": self.alpha, "delta": self.delta, "threshold": thr["threshold"], "coverage": thr["coverage"],
             "n": thr["n"], "source": thr["source"]}
        return conf >= thr["threshold"], g

    def on_label(self, tid: str) -> bool:
        """Cuenta una etiqueta nueva; cada retrain_every lanza un reentrenamiento de esa plantilla en segundo plano."""
        with self.lock:
            self.new_labels[tid] = self.new_labels.get(tid, 0) + 1
            due = self.new_labels[tid] >= self.retrain_every
            if due:
                self.new_labels[tid] = 0
        if due:
            threading.Thread(target=self.retrain, args=(tid,), daemon=True).start()
        return due

    def retrain(self, tid: str) -> dict:
        """Campeón/aspirante para una plantilla.  -> qué pasó (para el log y los tests)."""
        rows = self.store.labeled(tid)
        if len(rows) < self.min_labels:
            return {"template": tid, "status": "few_labels", "labels": len(rows)}
        keys = rows[-1]["keys"]
        rows = [r for r in rows if r["keys"] == keys]
        # Retenidos: la parte más reciente (holdout) de las AUDITADAS, que son una muestra uniforme de las decisiones.
        # Las escaladas sin auditar tienen casi todas confianza baja: con ellas el error medido no es el de lo que se
        # decide solo.  Sirven para ajustar la calibración (que modela p(etiqueta | z), no la mezcla de casos).
        uni = [r for r in rows if r["audit"]]
        test = uni[len(uni) - int(len(uni) * self.holdout):] if uni else []
        held = {r["id"] for r in test}
        train = [r for r in rows if r["id"] not in held]
        if len(test) < 10 or len(train) < 2 or len({r["label"] for r in train}) < 2:
            return {"template": tid, "status": "few_labels", "labels": len(rows), "audited": len(uni)}
        X = [r["features"] for r in train]
        y = [keys.index(r["label"]) for r in train]
        w = [SOURCE_WEIGHT.get(r["source"], 0.5) for r in train]
        b, lt = fit_vector_scaling(X, y, w)
        yt = [keys.index(r["label"]) for r in test]
        cand = metrics([predict_vs(r["features"], b, lt) for r in test], yt)
        cur_m = self.store.active_model(tid)
        if cur_m and cur_m["kind"] == "vector_scaling":
            cur = metrics([predict_vs(r["features"], cur_m["params"]["b"], cur_m["params"]["log_t"]) for r in test], yt)
        else:
            cur = metrics([softmax(r["features"]) for r in test], yt)   # lo de hoy: sin calibración aprendida
        better = cand["nll"] < cur["nll"] - self.min_gain and cand["accuracy"] >= cur["accuracy"] - 0.01
        # el umbral conformal, sobre los datos retenidos con el modelo que vaya a quedar (ORO si hay bastante)
        use_b, use_lt = (b, lt) if better else ((cur_m["params"]["b"], cur_m["params"]["log_t"])
                                                  if cur_m and cur_m["kind"] == "vector_scaling" else (None, None))
        # el umbral vale para lo que se decide solo: las que no escalaron por otra razón que el propio umbral
        elig = [r for r in test if r["eligible"]]
        gold = [r for r in elig if r["source"] == GOLD]
        calib, source = (gold, "human") if len(gold) >= self.gold_min else (elig, "system2")
        confs, oks = [], []
        for r in calib:
            p = predict_vs(r["features"], use_b, use_lt) if use_b is not None else softmax(r["features"])
            k = max(range(len(p)), key=p.__getitem__)
            confs.append(p[k])
            oks.append(keys[k] == r["label"])
        thr = select_threshold(confs, oks, self.alpha, self.delta)
        conformal = dict(thr, n=len(calib), source=source) if thr else None
        if better:
            v = self.store.save_model(tid, "vector_scaling", {"b": b, "log_t": lt, "keys": keys,
                                                              "conformal": conformal},
                                      {"candidate": cand, "previous": cur, "labels": len(rows)})
            status = "promoted"
        elif cur_m and cur_m["kind"] == "vector_scaling":
            params = dict(cur_m["params"], conformal=conformal)
            v = self.store.save_model(tid, "vector_scaling", params, dict(cur_m["metrics"], recheck=cur))
            status = "kept (conformal refreshed)"
        else:
            # sin calibración aprendida que gane: identidad (b = 0, T = 1), con su umbral conformal
            v = self.store.save_model(tid, "vector_scaling", {"b": [0.0] * len(keys), "log_t": 0.0, "keys": keys,
                                                              "conformal": conformal},
                                      {"candidate": cand, "previous": cur, "labels": len(rows), "identity": True})
            status = "identity"
        with self.lock:
            self.cache.pop(tid, None)
        return {"template": tid, "status": status, "version": v, "candidate": cand, "previous": cur,
                "conformal": conformal}


# ------------------------------------------------------------------ System Two en segundo plano
ANSWER_RE = re.compile(r"ANSWER\s*:\s*([^\s`*]+)", re.IGNORECASE)


def system2_prompt(d: dict) -> tuple[str, str]:
    system = ("You are a careful decision maker. Think the case through, then end your reply with one final line "
              "'ANSWER: <key>' using exactly one of the option keys.")
    st = d["state"] if d["state"] is not None else "(state no longer kept)"
    opts = "\n".join(f"- {k}: {d['criteria'][k]}" for k in d["keys"])
    return system, f"State:\n{st}\n\nQuestion: {d['instructions']}\nOptions:\n{opts}"


def parse_answer(text: str, keys: list[str]) -> str | None:
    found = ANSWER_RE.findall(text or "")
    if not found:
        return None
    ans = found[-1].strip().strip(".,;:()[]\"'").lower()
    for k in keys:
        if k.lower() == ans:
            return k
    return None


class Yielded(Exception):
    """System Two soltó Strata porque otra petición esperaba; la decisión vuelve a la cola."""


class SystemTwo:
    """Resuelve decisiones encoladas con razonamiento, de una en una, sin hacer esperar a nadie:

    * empieza solo con Strata libre (ni ocupado ni con peticiones en cola) durante idle_s seguidos;
    * mientras razona, mira GET /status cada yield_poll s, y si hay una petición en cola cierra la conexión.  Strata
      ve el cierre y cancela la suya en 0,5 s (server.py, _watch_client), así que la otra petición entra enseguida.
      La decisión vuelve a la cola;
    * con resume, al arrancar retoma lo que quedó pendiente en la base (Store.pending)."""

    def __init__(self, store: Store, learner: Learner | None, strata: str, effort: str = "high",
                 max_tokens: int = 2048, timeout: float = 600.0, idle_poll: float = 2.0, idle_s: float = 30.0,
                 yield_poll: float = 1.0, resume: bool = True, ask=None, busy=None, waiting=None):
        self.store, self.learner, self.strata = store, learner, strata.rstrip("/")
        self.effort, self.max_tokens, self.timeout, self.idle_poll = effort, max_tokens, timeout, idle_poll
        self.idle_s, self.yield_poll = idle_s, yield_poll
        self.q: queue.Queue = queue.Queue()
        self.busy = busy or self._busy
        self.waiting = waiting or self._waiting
        self.ask = ask or self._ask
        self.done = 0
        self.failed = 0
        self.yielded = 0
        if resume:
            for item in store.pending():
                self.q.put(item)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def enqueue(self, did: str, source: str) -> None:
        self.q.put((did, source))

    def _status(self) -> dict | None:
        try:
            with urllib.request.urlopen(self.strata + "/status", timeout=5) as r:
                return json.load(r)
        except (OSError, ValueError):
            return None

    def _busy(self) -> bool:
        s = self._status()
        return s is None or bool(s.get("busy")) or int(s.get("queued") or 0) > 0

    def _waiting(self) -> bool:
        s = self._status()
        return s is not None and int(s.get("queued") or 0) > 0

    def _wait_idle(self) -> None:
        since = None
        while True:
            if self.busy():
                since = None
            else:
                since = since if since is not None else time.monotonic()
                if time.monotonic() - since >= self.idle_s:
                    return
            time.sleep(self.idle_poll)

    def _ask(self, system: str, user: str) -> str:
        body = {"model": "strata", "max_tokens": self.max_tokens, "temperature": 0, "reasoning_effort": self.effort,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        u = urllib.parse.urlsplit(self.strata)
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=self.timeout)
        stop, cut = threading.Event(), threading.Event()

        def watch():
            while not stop.wait(self.yield_poll):
                if self.waiting():
                    cut.set()
                    try:
                        conn.sock.shutdown(socket.SHUT_RDWR)   # Strata lo ve como cliente que se fue: cancela
                    except (OSError, AttributeError):
                        pass
                    return
        try:
            conn.connect()
            threading.Thread(target=watch, daemon=True).start()
            conn.request("POST", u.path + "/v1/chat/completions", json.dumps(body),
                         {"Content-Type": "application/json"})
            r = conn.getresponse()
            raw = r.read()
            if r.status != 200:
                raise OSError(f"Strata HTTP {r.status}: {raw[:200]!r}")
            m = json.loads(raw)["choices"][0]["message"]
        except (OSError, http.client.HTTPException, ValueError):
            if cut.is_set():
                raise Yielded() from None
            raise
        finally:
            stop.set()
            conn.close()
        return (m.get("content") or "") + "\n" + (m.get("reasoning_content") or "")

    def _loop(self):
        while True:
            did, source = self.q.get()
            try:
                self._wait_idle()
                d = self.store.decision(did)
                if d is None or d["state"] is None or self.store.has_label(did):   # sin estado, o ya hecha
                    continue
                text = self.ask(*system2_prompt(d))
                label = parse_answer(text, d["keys"])
                if label is None:
                    self.failed += 1
                    self.store.add_label(did, "failed", "", {"tail": (text or "")[-300:]})   # no se reintenta
                    continue
                self.store.add_label(did, source, label, {"agrees": label == d["choice"]})
                self.done += 1
                if self.learner:
                    self.learner.on_label(d["template"])
            except Yielded:
                self.yielded += 1
                self.q.put((did, source))                # otra vez a la cola; se retoma con Strata libre
            except Exception:   # un fallo de una no para la cola (se reintenta al reiniciar)
                self.failed += 1
            finally:
                self.q.task_done()
