#!/usr/bin/env python3
"""ada-decide — System One (Jev / SystemOne) sobre el modelo que Strata tenga cargado.

Contrato compatible con Jev/SystemOne:

    POST /v1/systemone
    {"state": <str|obj>, "questions": {
        "<id>": {"type":"choice", "instructions":"...", "criteria": {"<op>":"<desc>", ...}},
        "<id>": {"type":"score",  "instructions":"...", "criteria": ["...", "..."]},
        "<id>": {"type":"noul",   "instructions":"...", "criteria": {...opcional...}}
     },
     "options": {"permutations": 2, "calibrate": true, "temperature": 1.0}   <- opcional, por peticion}

Respuesta: {"model": ..., "answers": {"<id>": {type, choice|score|value, confidence, probabilities,
            margin, option_mass, escalate[, escalate_reasons, legend]}}}

Cómo funciona: por cada pregunta se renderiza un prompt que acaba en "Answer: (" y se
pide UNA vez a Strata con `max_tokens=1` y `strata_lpids` = los ids de las etiquetas. El motor
devuelve `strata_logprobs` (logprobs absolutos de esos ids en la ultima posicion del prompt) y aqui
se normaliza SOBRE LAS OPCIONES de la pregunta.

Calibración (las tres son opcionales y se combinan):
  * permutations=P: la misma pregunta con las opciones rotadas P veces; se promedian las
    probabilidades por opcion.  Quita el sesgo de letra y de posicion.  Con el estado en el mensaje
    de sistema (--prompt-cache-root) cada permutacion extra solo lee la pregunta (~0,4 s medido).
  * calibrate=true: calibracion contextual (Zhao et al. 2021, "Calibrate Before Use").  Una peticion
    con el estado "N/A" da el sesgo p_cf de cada opcion para esa plantilla; p ∝ p / p_cf.  Se guarda
    en memoria por plantilla (instrucciones + opciones + orden), asi que cuesta una peticion por
    plantilla, no por decision.
  * temperature=T: p ∝ exp(logprob / T).  Ajustad T con fit-calibration.py sobre decisiones
    etiquetadas (el log de --log).  T > 1 baja la sobreconfianza.

Señales para escalar a "System Two" (una generacion normal con razonamiento):
  * margin: p(1.ª) - p(2.ª).  Bajo = dudoso.
  * option_mass: probabilidad ABSOLUTA que el modelo da a las etiquetas de las opciones (antes de
    normalizar).  Baja = el modelo queria escribir otra cosa: la respuesta no es fiable.
  * escalate: true si margin < --escalate-margin u option_mass < --min-mass.

Limites honestos:
  * No es un decisor entrenado: son las preferencias del modelo base.  La calibracion de arriba
    reduce el sesgo; no sustituye a medir el acierto con decisiones etiquetadas.

Uso:  python3 ada-decide.py [--port 8087] [--strata http://127.0.0.1:8081] [--tokenizer DIR]
                            [--permutations 1] [--calibrate] [--temperature 1.0]
                            [--escalate-margin 0.2] [--min-mass 0.2] [--log decisions.jsonl]
"""
from __future__ import annotations
import argparse, hashlib, json, math, os, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CONTENT_FREE = "N/A"

DEFAULT_TOKENIZER = "/home/bazzite/Strata-data/packs/swift-iq2_xs/tokenizer"
STRATA_TOOLS = os.environ.get("STRATA_TOOLS", "/home/bazzite/Strata/tools")


# ---------------------------------------------------------------- tokenizer
def build_tokenizer(tpath: Path):
    """El mismo tokenizer que usa el servidor de Strata (tools/strata_tokenizer.py)."""
    sys.path.insert(0, STRATA_TOOLS)
    import strata_tokenizer as ST
    vocab = json.loads((tpath / "vocab.json").read_text(encoding="utf-8"))
    tokens = [None] * len(vocab)
    for t, i in vocab.items():
        tokens[i] = t
    merges = (tpath / "merges.txt").read_text(encoding="utf-8").split("\n")
    types = json.loads((tpath / "token_type.json").read_text())
    return ST.Tokenizer(tokens, merges, types)


def label_ids(tok, label: str) -> list[int]:
    """Los ids con los que el modelo puede EMPEZAR a escribir esa etiqueta.

    El "(" de "Answer: (" esta en el mensaje de usuario y la respuesta empieza en un turno nuevo,
    asi que el primer token puede ser "A", " A", "(A", "A)"...  Se toma el PRIMER token de cada
    variante (el que el motor puntua en la ultima posicion) y, si una variante es un solo token,
    ese token.  Las probabilidades de las variantes se SUMAN (logsumexp) al puntuar la opcion.
    """
    out = set()
    for text in (label, " " + label, "(" + label, " (" + label, label + ")", "**" + label):
        try:
            ids = tok.encode(text, parse_special=False)
        except TypeError:
            ids = tok.encode(text)
        if not ids:
            continue
        if len(ids) == 1 or text in (label, " " + label):
            out.add(ids[0] if len(ids) == 1 else ids[-1])
        else:
            # "(A" -> ["(", "A"]: el primer token es "(" y no distingue opciones; solo vale si la
            # variante entera es un token propio (p. ej. "(A" o "A)" fusionados)
            pass
    return sorted(out)


def logsumexp(xs: list[float]) -> float:
    m = max(xs)
    return m + math.log(sum(math.exp(x - m) for x in xs))


def softmax(xs: list[float]) -> list[float]:
    m = max(xs)
    e = [math.exp(x - m) for x in xs]
    s = sum(e) or 1.0
    return [v / s for v in e]


# ---------------------------------------------------------------- render
def render(state, instructions, options: list[str]) -> tuple[str, str]:
    """(mensaje de sistema, mensaje de usuario).

    El ESTADO va en el mensaje de sistema a proposito: es lo que permite que varias
    preguntas sobre el mismo estado compartan prefijo y el motor solo lea la cola de
    cada una (con --prompt-cache-root).  La pregunta y las opciones van en el de
    usuario, que es lo que cambia entre preguntas (y entre permutaciones).
    """
    st = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    system = ("You are a decision model. Choose exactly ONE option from the list. "
              "Answer with its letter only, nothing else.\n\nState:\n" + st)
    lines = [f"Question: {instructions}", "Options:"]
    for i, desc in enumerate(options):
        lines.append(f"({LABELS[i]}) {desc}")
    lines.append("Answer: (")
    return system, "\n".join(lines)


def ask_strata(url: str, system: str, user: str, timeout: float, lpids=None) -> tuple[dict, dict]:
    body = {"model": "strata",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": 1, "temperature": 0, "reasoning_effort": "none"}
    if lpids:
        # lpids=a,b,c: el motor imprime la logprob de EXACTAMENTE esos ids, no el top-k
        body["strata_lpids"] = [int(i) for i in lpids]
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=timeout))
    return d.get("strata_logprobs") or {}, (d.get("usage") or {})


ASK = ask_strata          # los tests lo sustituyen por un Strata simulado


# ---------------------------------------------------------------- una pasada
def option_scores(url, tok, state, instructions, keys, criteria, timeout):
    """Una peticion con las opciones en el orden de `keys`.
    -> ({clave: logprob absoluta de la opcion (logsumexp de sus variantes)}, ids que faltaron, usage)."""
    system, user = render(state, instructions, [criteria[k] for k in keys])
    per_option = {k: label_ids(tok, LABELS[i]) for i, k in enumerate(keys)}
    flat = sorted({i for ids in per_option.values() for i in ids})
    lp, usage = ASK(url, system, user, timeout, flat)
    if not lp:
        raise RuntimeError("el motor no devolvio strata_logprobs (¿arrancado sin --logprobs?)")
    lp = {int(k): float(v) for k, v in lp.items()}
    missing = sorted({i for i in flat if i not in lp})
    scores = {}
    for k in keys:
        cands = [lp[i] for i in per_option[k] if i in lp]
        scores[k] = logsumexp(cands) if cands else -30.0
    return scores, missing, usage


def rotations(keys: list[str], n: int) -> list[list[str]]:
    """n ordenes distintos: rotaciones repartidas, para que cada opcion pase por varias letras."""
    n = max(1, min(n, len(keys)))
    step = len(keys) / n
    return [keys[int(round(r * step)):] + keys[:int(round(r * step))] for r in range(n)]


class Calibrator:
    """Sesgo de cada plantilla con el estado vacio (calibracion contextual), en memoria."""

    def __init__(self):
        self.cache: dict[str, dict[str, float]] = {}
        self.lock = threading.Lock()

    @staticmethod
    def key(instructions, order, criteria) -> str:
        raw = json.dumps([instructions, [[k, criteria[k]] for k in order]], ensure_ascii=False)
        return hashlib.sha1(raw.encode()).hexdigest()

    def bias(self, url, tok, instructions, order, criteria, timeout) -> dict[str, float]:
        """log p_cf(opcion), normalizado sobre las opciones, para este orden."""
        k = self.key(instructions, order, criteria)
        with self.lock:
            if k in self.cache:
                return self.cache[k]
        scores, _, _ = option_scores(url, tok, CONTENT_FREE, instructions, order, criteria, timeout)
        lse = logsumexp(list(scores.values()))
        b = {o: s - lse for o, s in scores.items()}
        with self.lock:
            self.cache[k] = b
        return b


CALIBRATOR = Calibrator()


def decide_choice(url, tok, state, instructions, criteria: dict, timeout: float, cfg: dict):
    """criteria: {clave_opcion: descripcion}.  cfg: permutations, calibrate, temperature,
    escalate_margin, min_mass.  Devuelve la distribucion sobre las opciones y las señales."""
    keys = list(criteria.keys())
    if not 2 <= len(keys) <= len(LABELS):
        return None, {"error": f"choice admite 2..{len(LABELS)} opciones, tiene {len(keys)}"}
    T = float(cfg.get("temperature") or 1.0)
    acc = {k: 0.0 for k in keys}
    masses, missing_all, passes, usage = [], set(), [], {}
    orders = rotations(keys, int(cfg.get("permutations") or 1))
    for order in orders:
        scores, missing, usage = option_scores(url, tok, state, instructions, order, criteria, timeout)
        missing_all.update(missing)
        masses.append(sum(math.exp(s) for s in scores.values()))
        cal = dict(scores)
        cf = None
        if cfg.get("calibrate"):
            cf = CALIBRATOR.bias(url, tok, instructions, order, criteria, timeout)
            cal = {k: scores[k] - cf[k] for k in order}
        probs = softmax([cal[k] / T for k in keys])
        for k, p in zip(keys, probs):
            acc[k] += p / len(orders)
        passes.append({"order": order, "raw": scores, **({"cf": cf} if cf else {})})
    best, second = sorted(keys, key=acc.get, reverse=True)[:2]
    margin = acc[best] - acc[second]
    mass = sum(masses) / len(masses)
    reasons = []
    if margin < float(cfg.get("escalate_margin", 0.2)):
        reasons.append("margin")
    if mass < float(cfg.get("min_mass", 0.2)):
        reasons.append("option_mass")
    out = {"type": "choice", "choice": best, "confidence": acc[best], "probabilities": acc,
           "margin": margin, "option_mass": mass, "escalate": bool(reasons)}
    if reasons:
        out["escalate_reasons"] = reasons
    if missing_all:
        out["missing_label_ids"] = sorted(missing_all)   # no deberia pasar con lpids; si pasa, se dice
    out["_passes"] = passes                               # para el log; se quita antes de responder
    return out, usage


def decide_score(url, tok, state, instructions, criteria: list, timeout: float, cfg: dict):
    ans, usage = decide_choice(url, tok, state, instructions,
                               {str(i): criteria[i] for i in range(len(criteria))}, timeout, cfg)
    if ans is None:
        return None, usage
    p = ans["probabilities"]
    ans.update({"type": "score", "score": sum(float(k) * v for k, v in p.items()), "legend": criteria})
    ans.pop("choice")
    return ans, usage


def decide_noul(url, tok, state, instructions, timeout: float, cfg: dict):
    ans, usage = decide_choice(url, tok, state, instructions, {"true": "Yes", "false": "No"}, timeout, cfg)
    if ans is None:
        return None, usage
    p = ans["probabilities"]
    ans.update({"type": "noul", "value": p["true"] >= 0.5})
    ans.pop("choice")
    return ans, usage


# ---------------------------------------------------------------- servidor
class Handler(BaseHTTPRequestHandler):
    tokenizer = None
    strata = "http://127.0.0.1:8081"
    timeout = 120.0
    model = "ada-next-systemone"
    defaults: dict = {}
    log_path: str | None = None
    lock = threading.Lock()

    def log_message(self, *a):        # silencio: el journal no necesita cada peticion
        pass

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", ""):
            return self._send(200, {"status": "ok", "service": "ada-decide", "model": self.model,
                                    "defaults": self.defaults})
        self._send(404, {"error": "not found"})

    def _log(self, state, qid, q, ans, cfg):
        """Una linea JSONL por pregunta: lo necesario para etiquetarla y ajustar la calibracion
        despues (fit-calibration.py).  Añadid "label": "<opcion>" a mano o desde vuestra app."""
        if not self.log_path:
            return
        rec = {"ts": time.time(), "qid": qid, "type": q.get("type"), "instructions": q.get("instructions"),
               "criteria": q.get("criteria"), "state": state, "config": cfg,
               "passes": ans.get("_passes"), "probabilities": ans.get("probabilities"), "label": None}
        with self.lock, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def do_POST(self):
        if self.path.rstrip("/") not in ("/v1/systemone", "/systemone"):
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"error": f"bad json: {e}"})
        state = req.get("state")
        questions = req.get("questions") or {}
        if not state or not isinstance(questions, dict) or not questions:
            return self._send(400, {"error": "state and questions are required"})
        cfg = {**self.defaults, **{k: v for k, v in (req.get("options") or {}).items()
                                   if k in ("permutations", "calibrate", "temperature", "escalate_margin",
                                            "min_mass")}}
        answers = {}
        for qid, q in questions.items():
            q = q or {}
            qtype = q.get("type")
            instr = q.get("instructions") or str(qid)
            crit = q.get("criteria")
            try:
                with self.lock:                      # el motor atiende una peticion a la vez
                    if qtype == "choice":
                        ans, u = decide_choice(self.strata, self.tokenizer, state, instr, crit or {},
                                               self.timeout, cfg)
                    elif qtype == "score":
                        ans, u = decide_score(self.strata, self.tokenizer, state, instr, crit or [],
                                              self.timeout, cfg)
                    elif qtype == "noul":
                        ans, u = decide_noul(self.strata, self.tokenizer, state, instr, self.timeout, cfg)
                    else:
                        ans, u = None, {"error": f"unknown question type {qtype!r}"}
            except Exception as e:
                ans, u = None, {"error": str(e)[:200]}
            if ans is None:
                answers[qid] = {"type": qtype, **(u if isinstance(u, dict) else {"error": "failed"})}
                continue
            try:
                self._log(state, qid, q, ans, cfg)
            except OSError:
                pass
            ans.pop("_passes", None)
            answers[qid] = ans
        return self._send(200, {"model": self.model, "answers": answers})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8087)
    ap.add_argument("--strata", default="http://127.0.0.1:8081")
    ap.add_argument("--tokenizer", default=os.environ.get("STRATA_TOKENIZER", DEFAULT_TOKENIZER))
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--permutations", type=int, default=1, help="ordenes de opciones por pregunta (1 = sin permutar)")
    ap.add_argument("--calibrate", action="store_true", help="calibracion contextual (estado 'N/A') por plantilla")
    ap.add_argument("--temperature", type=float, default=1.0, help="T ajustada con fit-calibration.py")
    ap.add_argument("--escalate-margin", type=float, default=0.2)
    ap.add_argument("--min-mass", type=float, default=0.2,
                    help="masa absoluta minima en las etiquetas; ajustadla mirando el log")
    ap.add_argument("--log", default=None, help="JSONL con cada decision (para etiquetar y ajustar T)")
    a = ap.parse_args()
    Handler.tokenizer = build_tokenizer(Path(a.tokenizer))
    Handler.strata = a.strata
    Handler.timeout = a.timeout
    Handler.log_path = a.log
    Handler.defaults = {"permutations": a.permutations, "calibrate": a.calibrate, "temperature": a.temperature,
                        "escalate_margin": a.escalate_margin, "min_mass": a.min_mass}
    print(f"[ada-decide] :{a.port} -> {a.strata} (tokenizer {a.tokenizer}) {Handler.defaults}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
