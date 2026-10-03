#!/usr/bin/env python3
"""ada-decide — System One (Jev / SystemOne) sobre el modelo que Strata tenga cargado.

Contrato compatible con Jev/SystemOne:

    POST /v1/systemone
    {"state": <str|obj>, "questions": {
        "<id>": {"type":"choice", "instructions":"...", "criteria": {"<op>":"<desc>", ...}},
        "<id>": {"type":"score",  "instructions":"...", "criteria": ["...", "..."]},
        "<id>": {"type":"noul",   "instructions":"...", "criteria": {...opcional...}}
    }}

Respuesta: {"model": ..., "answers": {"<id>": {type, choice|score, confidence, probabilities[, legend, value]}},
            "usage": {...}}

Cómo funciona: por cada pregunta se renderiza un prompt que acaba en "Answer: (" y se
pide UNA vez a Strata con `max_tokens=1`. El motor devuelve `strata_logprobs` (la
distribucion de la ultima posicion del prompt, logprobs absolutos) y aqui se normaliza
SOBRE LAS OPCIONES de la pregunta. No se genera texto: una pasada, probabilidades.

Limites honestos:
  * No es un decisor entrenado: son las preferencias del modelo base. La calibracion es
    suya, no la de un head con perdida de Brier -> tiende a ser sobreconfiado.
  * Se leen los N tokens mas probables (--logprobs en el motor): sirve para ~11 opciones
    por pregunta con etiquetas (A)..(K). Para mas, cambiar el esquema de etiquetado.

Uso:  python3 ada-decide.py [--port 8087] [--strata http://127.0.0.1:8081] [--tokenizer DIR]
"""
from __future__ import annotations
import argparse, json, math, os, re, sys, threading, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

DEFAULT_TOKENIZER = "/home/bazzite/Strata-data/packs/swift-iq2_xs/tokenizer"


# ---------------------------------------------------------------- tokenizer
def build_tokenizer(tpath: Path):
    """El mismo tokenizer que usa el servidor de Strata (tools/strata_tokenizer.py)."""
    sys.path.insert(0, "/home/bazzite/Strata/tools")
    import strata_tokenizer as ST
    vocab = json.loads((tpath / "vocab.json").read_text(encoding="utf-8"))
    tokens = [None] * len(vocab)
    for t, i in vocab.items():
        tokens[i] = t
    merges = (tpath / "merges.txt").read_text(encoding="utf-8").split("\n")
    types = json.loads((tpath / "token_type.json").read_text())
    return ST.Tokenizer(tokens, merges, types)


def label_ids(tok, label: str) -> list[int]:
    """Los ids con los que el modelo puede escribir esa etiqueta.

    Segun el tokenizador la letra puede ser un token propio o venir pegada al parentesis
    o al espacio; se prueban las variantes y se suman sus probabilidades al normalizar.
    """
    out = []
    for text in (label, " " + label, "(" + label, " (" + label):
        try:
            ids = tok.encode(text, parse_special=False)
        except TypeError:
            ids = tok.encode(text)
        if ids:
            out.append(ids[-1])
    return sorted(set(out))


# ---------------------------------------------------------------- render
def render(state, instructions, options: list[str]) -> tuple[str, str]:
    """(mensaje de sistema, mensaje de usuario).

    El ESTADO va en el mensaje de sistema a proposito: es lo que permite que varias
    preguntas sobre el mismo estado compartan prefijo y el motor solo lea la cola de
    cada una (con --prompt-cache-root).  La pregunta y las opciones van en el de
    usuario, que es lo que cambia entre preguntas.
    """
    st = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    system = ("You are a decision model. Choose exactly ONE option from the list. "
              "Answer with its letter only, nothing else.\n\nState:\n" + st)
    lines = [f"Question: {instructions}", "Options:"]
    for i, desc in enumerate(options):
        lines.append(f"({LABELS[i]}) {desc}")
    lines.append("Answer: (")
    return system, "\n".join(lines)


def ask_strata(url: str, system: str, user: str, timeout: float,
               lpids=None) -> tuple[dict, str]:
    body = {"model": "strata",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": 1, "temperature": 0, "reasoning_effort": "none"}
    if lpids:
        # lpids=a,b,c: el motor imprime la logprob de EXACTAMENTE esos ids, no el top-k.
        # Sin esto, una letra que no cae en el top-32 cuenta como 0 y deforma la
        # distribucion (fallo real, corregido).
        body["strata_lpids"] = [int(i) for i in lpids]
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=timeout))
    return d.get("strata_logprobs") or {}, (d.get("usage") or {})


def decide_choice(url: str, tok, state, instructions, criteria: dict, timeout: float):
    """criteria: {clave_opcion: descripcion}. Devuelve la distribucion sobre las opciones."""
    keys = list(criteria.keys())
    if not 2 <= len(keys) <= len(LABELS):
        return None, {"error": f"choice admite 2..{len(LABELS)} opciones, tiene {len(keys)}"}
    system, user = render(state, instructions, [criteria[k] for k in keys])
    # los ids EXACTOS de las etiquetas: se los pedimos al motor en vez del top-k, para
    # que ninguna opcion pueda faltar (el fallo del top-32).
    per_option = {k: label_ids(tok, LABELS[i]) for i, k in enumerate(keys)}
    flat = sorted({i for ids in per_option.values() for i in ids})
    lp, usage = ask_strata(url, system, user, timeout, lpids=flat)
    if not lp:
        return None, {"error": "el motor no devolvio strata_logprobs (¿arrancado sin --logprobs?)"}
    lp = {int(k): v for k, v in lp.items()}
    missing = sorted({i for ids in per_option.values() for i in ids if i not in lp})
    # probabilidad de cada opcion = mejor logprob de sus tokens candidatos
    raw = []
    for k in keys:
        cands = [lp[i] for i in per_option[k] if i in lp]
        raw.append(max(cands) if cands else -30.0)
    mx = max(raw)
    exps = [math.exp(r - mx) for r in raw]
    total = sum(exps) or 1.0
    probs = {k: e / total for k, e in zip(keys, exps)}
    best = max(probs, key=probs.get)
    out = {"type": "choice", "choice": best, "confidence": probs[best], "probabilities": probs}
    if missing:
        # no deberia pasar con lpids; si pasa, se dice en vez de esconderlo
        out["missing_label_ids"] = missing
    return out, usage


def decide_score(url, tok, state, instructions, criteria: list, timeout: float):
    keys = [str(i) for i in range(len(criteria))]
    ans, usage = decide_choice(url, tok, state, instructions,
                               {str(i): criteria[i] for i in range(len(criteria))}, timeout)
    if ans is None:
        return None, usage
    p = ans["probabilities"]
    expected = sum(float(k) * v for k, v in p.items())
    best = max(p, key=p.get)
    return {"type": "score", "score": expected, "confidence": p[best],
            "legend": criteria, "probabilities": p}, usage


def decide_noul(url, tok, state, instructions, timeout: float):
    ans, usage = decide_choice(url, tok, state, instructions,
                               {"true": "Yes", "false": "No"}, timeout)
    if ans is None:
        return None, usage
    p = ans["probabilities"]
    return {"type": "noul", "value": p["true"] >= 0.5, "confidence": max(p["true"], p["false"]),
            "probabilities": {"true": p["true"], "false": p["false"]}}, usage


# ---------------------------------------------------------------- servidor
class Handler(BaseHTTPRequestHandler):
    tokenizer = None
    strata = "http://127.0.0.1:8081"
    timeout = 120.0
    model = "ada-next-systemone"
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
            return self._send(200, {"status": "ok", "service": "ada-decide", "model": self.model})
        self._send(404, {"error": "not found"})

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
        answers, usage = {}, {}
        for qid, q in questions.items():
            qtype = (q or {}).get("type")
            instr = (q or {}).get("instructions") or str(qid)
            crit = (q or {}).get("criteria")
            try:
                with self.lock:                      # el motor atiende una peticion a la vez
                    if qtype == "choice":
                        ans, u = decide_choice(self.strata, self.tokenizer, state, instr, crit or {}, self.timeout)
                    elif qtype == "score":
                        ans, u = decide_score(self.strata, self.tokenizer, state, instr, crit or [], self.timeout)
                    elif qtype == "noul":
                        ans, u = decide_noul(self.strata, self.tokenizer, state, instr, self.timeout)
                    else:
                        ans, u = None, {"error": f"unknown question type {qtype!r}"}
            except Exception as e:
                ans, u = None, {"error": str(e)[:200]}
            if ans is None:
                answers[qid] = {"type": qtype, **(u if isinstance(u, dict) else {"error": "failed"})}
            else:
                answers[qid] = ans
        return self._send(200, {"model": self.model, "answers": answers})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8087)
    ap.add_argument("--strata", default="http://127.0.0.1:8081")
    ap.add_argument("--tokenizer", default=os.environ.get("STRATA_TOKENIZER", DEFAULT_TOKENIZER))
    ap.add_argument("--timeout", type=float, default=120.0)
    a = ap.parse_args()
    Handler.tokenizer = build_tokenizer(Path(a.tokenizer))
    Handler.strata = a.strata
    Handler.timeout = a.timeout
    print(f"[ada-decide] :{a.port} -> {a.strata} (tokenizer {a.tokenizer})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
