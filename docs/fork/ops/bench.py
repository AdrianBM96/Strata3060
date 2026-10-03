#!/usr/bin/env python3
"""bench.py — el banco fijo del plan maestro (PLAN_MAESTRO.md §1.1): las mismas pruebas, siempre igual, y una
comparación A/B con estadística.  Solo librería estándar; habla con el servidor de Strata (y con ada-decide para
System One) por HTTP, así que mide lo que ve un cliente.

    # una tanda: las pruebas elegidas, N pasadas, etiquetadas y añadidas a un .jsonl
    python3 bench.py run --tag A --runs 2 --tests B1,B2,P1,S1,S2 --out banco.jsonl
    # (cambiar la configuración, reiniciar Strata)
    python3 bench.py run --tag B --runs 2 --tests B1,B2,P1,S1,S2 --out banco.jsonl
    # ... alternando A, B, A, B hasta 10+10 pasadas por prueba
    python3 bench.py compare banco.jsonl --a A --b B
    python3 bench.py show banco.jsonl

Pruebas (temperatura 0; las de "prompt nuevo" llevan un nonce al principio para que ninguna caché las acorte):

| Id | Qué mide | Métrica |
| --- | --- | --- |
| B1 | respuesta de 1.024 tokens con razonamiento, contexto corto | decode tokens/s |
| B2 / B3 | turno de agente: 512 tokens tras 32K / 128K de contexto (el contexto se lee antes, sin medir) | decode tokens/s |
| P1 / P2 | prompt nuevo de 24K / 128K tokens | prefill tokens/s |
| S1 | System One: estado nuevo de ~500 tokens + 4 preguntas | segundos de la tanda |
| S2 | System One: 4 preguntas DISTINTAS sobre el estado de S1 ya leído | segundos de la tanda |
| S3 | System One masivo: --bulk ítems cortos, la misma pregunta | ítems por segundo |

Las cifras de decode y prefill son las del reloj del motor (`timings` de la respuesta, como en llama.cpp); las de
System One son de pared, desde el cliente.  Las pruebas que no caben en el contexto del servidor se saltan.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TESTS = ("B1", "B2", "B3", "P1", "P2", "S1", "S2", "S3")
METRIC = {"B1": "decode_tps", "B2": "decode_tps", "B3": "decode_tps", "P1": "prompt_tps", "P2": "prompt_tps",
          "S1": "wall_s", "S2": "wall_s", "S3": "items_per_s"}
HIGHER_IS_BETTER = {"decode_tps": True, "prompt_tps": True, "wall_s": False, "items_per_s": True}
CHARS_PER_TOKEN = 3.4          # código y markdown con el tokenizador de Qwen; el tamaño real sale en prompt_tokens

REASONING_PROMPT = ("A warehouse robot moves on a 12x12 grid from (0,0) to (11,11), only right or up, and must avoid "
                    "the cells (3,4), (5,5), (7,2) and (9,9). Count the number of valid paths step by step, explaining "
                    "the method, checking the arithmetic, and then write a short Python function that computes it.")
AGENT_QUESTION = ("You are a coding agent. Read the code above and write a concise plan to add structured logging to "
                  "the three most central functions, then the patch for the first one.")


# ------------------------------------------------------------------ textos deterministas
def corpus(tokens: int) -> str:
    """~`tokens` tokens de código y documentación de este repo, siempre los mismos ficheros en el mismo orden."""
    want = int(tokens * CHARS_PER_TOKEN)
    parts, size = [], 0
    roots = [REPO / d for d in ("src", "include", "serve", "tools", "docs")]
    files = sorted(p for r in roots if r.is_dir() for p in r.rglob("*")
                   if p.suffix in (".cpp", ".cu", ".hpp", ".py", ".md") and p.is_file() and "fork" not in p.parts)
    for p in files:
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunk = f"\n// ===== {p.relative_to(REPO)} =====\n{t}"
        parts.append(chunk)
        size += len(chunk)
        if size >= want:
            break
    if size < want:                                     # sin el repo al lado: texto sintético, también determinista
        rnd = random.Random(7)
        words = "state expert cache window token layer prompt decode verify commit draft router".split()
        while size < want:
            line = " ".join(rnd.choice(words) for _ in range(14)) + ".\n"
            parts.append(line)
            size += len(line)
    return "".join(parts)[:want]


def state_text(i: int, tokens: int = 500) -> str:
    """Un estado de soporte técnico, distinto para cada i, de ~`tokens` tokens."""
    rnd = random.Random(1000 + i)
    services = ["checkout API", "login service", "billing worker", "search index", "email queue", "CDN edge"]
    lines = [f"Ticket #{4000 + i}. Customer plan: {rnd.choice(['free', 'pro', 'enterprise'])}."]
    while len(" ".join(lines)) < tokens * 4:
        s = rnd.choice(services)
        lines.append(f"{rnd.randint(0, 23):02d}:{rnd.randint(0, 59):02d} the {s} returned "
                     f"{rnd.choice(['HTTP 500', 'HTTP 502', 'a timeout', 'HTTP 200 slowly', 'a duplicate charge'])} "
                     f"for {rnd.randint(1, 900)} requests; the customer says: "
                     f"{rnd.choice(['it is urgent', 'please refund me', 'nothing works since the update', 'is my data safe?'])}.")
    return "\n".join(lines)


QUESTIONS_A = {
    "department": {"type": "choice", "instructions": "Which team should handle this ticket?",
                   "criteria": {"billing": "Billing and refunds", "technical": "Bugs and outages",
                                "sales": "Plans and upgrades"}},
    "urgency": {"type": "score", "instructions": "How urgent is it?", "criteria": ["low", "medium", "high"]},
    "outage": {"type": "noul", "instructions": "Is there an ongoing outage?"},
    "refund": {"type": "noul", "instructions": "Does the customer ask for money back?"},
}
QUESTIONS_B = {                                         # S2: preguntas distintas de las de S1 sobre el mismo estado
    "security": {"type": "noul", "instructions": "Is the customer worried about data security?"},
    "channel": {"type": "choice", "instructions": "How should we answer?",
                "criteria": {"email": "An email", "call": "A phone call", "status": "A status page update"}},
    "sentiment": {"type": "score", "instructions": "How upset is the customer?",
                  "criteria": ["calm", "annoyed", "angry"]},
    "escalate": {"type": "noul", "instructions": "Should an engineer be paged now?"},
}


# ------------------------------------------------------------------ HTTP
def post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get(url: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def chat(server: str, system: str, user: str, max_tokens: int, effort: str, timeout: float) -> dict:
    body = {"model": "strata", "max_tokens": max_tokens, "temperature": 0, "reasoning_effort": effort,
            "messages": ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]}
    t0 = time.monotonic()
    d = post(server.rstrip("/") + "/v1/chat/completions", body, timeout)
    t = d.get("timings") or {}
    u = d.get("usage") or {}
    return {"wall_s": time.monotonic() - t0, "prompt_tokens": u.get("prompt_tokens"),
            "completion_tokens": u.get("completion_tokens"), "cache_n": t.get("cache_n"), "prompt_n": t.get("prompt_n"),
            "prompt_tps": t.get("prompt_per_second"), "decode_tps": t.get("predicted_per_second"),
            "predicted_n": t.get("predicted_n"), "draft_n": t.get("draft_n"), "draft_n_accepted": t.get("draft_n_accepted")}


# ------------------------------------------------------------------ pruebas
def run_test(test: str, a, max_context: int | None, run_idx: int) -> dict:
    """Una pasada de una prueba -> el registro (con "skipped" o "error" si no se pudo)."""
    srv, to = a.server, a.timeout
    fits = lambda n: max_context is None or n + 1100 <= max_context
    nonce = f"[bench {uuid.uuid4().hex[:12]}]"
    if test == "B1":
        return chat(srv, "", REASONING_PROMPT, 1024, "high", to)
    if test in ("B2", "B3"):
        n = 32768 if test == "B2" else 131072
        if not fits(n):
            return {"skipped": f"max_context {max_context} < {n}"}
        ctx = corpus(n - 600)
        system = f"{nonce} You are a careful coding agent."
        chat(srv, system, ctx + "\n\n" + AGENT_QUESTION, 1, "none", to)           # lee el contexto (no se mide)
        r = chat(srv, system, ctx + "\n\n" + AGENT_QUESTION, 512, "none", to)     # el mismo prompt: solo decode
        return r
    if test in ("P1", "P2"):
        n = 24576 if test == "P1" else 131072
        if not fits(n):
            return {"skipped": f"max_context {max_context} < {n}"}
        return chat(srv, f"{nonce} You are a careful coding agent.", corpus(n - 200) + "\n\nSummarize in one line.",
                    1, "none", to)
    if test in ("S1", "S2", "S3"):
        if not a.decide:
            return {"skipped": "sin --decide"}
        url = a.decide.rstrip("/") + "/v1/systemone"
        if test == "S3":
            t0 = time.monotonic()
            for i in range(a.bulk):
                post(url, {"state": f"{nonce} item {i}\n" + state_text(run_idx * 100000 + i, 60),
                           "questions": {"department": QUESTIONS_A["department"]}}, to)
            wall = time.monotonic() - t0
            return {"wall_s": wall, "items": a.bulk, "items_per_s": a.bulk / wall if wall > 0 else None}
        state = f"{nonce}\n" + state_text(run_idx)
        t0 = time.monotonic()
        d1 = post(url, {"state": state, "questions": QUESTIONS_A}, to)
        s1 = time.monotonic() - t0
        if test == "S1":
            return {"wall_s": s1, "questions": len(QUESTIONS_A), "answers": d1.get("answers")}
        t0 = time.monotonic()
        d2 = post(url, {"state": state, "questions": QUESTIONS_B}, to)
        return {"wall_s": time.monotonic() - t0, "questions": len(QUESTIONS_B), "first_s": s1,
                "answers": d2.get("answers")}
    raise ValueError(test)


def cmd_run(a) -> int:
    tests = [t.strip().upper() for t in a.tests.split(",") if t.strip()]
    bad = [t for t in tests if t not in TESTS]
    if bad:
        sys.exit(f"pruebas desconocidas: {bad} (hay {', '.join(TESTS)})")
    try:
        health = get(a.server.rstrip("/") + "/health")
    except (urllib.error.URLError, OSError, ValueError) as e:
        sys.exit(f"el servidor {a.server} no responde: {e}")
    max_context = health.get("max_context")
    out = Path(a.out)
    for r in range(a.runs):
        for test in tests:
            rec = {"tag": a.tag, "test": test, "run": r, "ts": time.time(), "model": health.get("model"),
                   "max_context": max_context, "note": a.note}
            try:
                rec.update(run_test(test, a, max_context, r))
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                rec["error"] = str(e)[:300]
            if not a.keep_answers:
                rec.pop("answers", None)
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            m = METRIC[test]
            val = rec.get(m)
            print(f"[{a.tag}] {test} pasada {r + 1}/{a.runs}: "
                  + (f"{m} = {val:.2f}" if isinstance(val, (int, float)) else rec.get("skipped") or rec.get("error", "?")),
                  flush=True)
    return 0


# ------------------------------------------------------------------ estadística
def mann_whitney_p(x: list[float], y: list[float]) -> float:
    """p bilateral del test U de Mann-Whitney, aproximación normal con corrección de empates y de continuidad."""
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return float("nan")
    allv = sorted([(v, 0) for v in x] + [(v, 1) for v in y])
    ranks = [0.0] * len(allv)
    i = 0
    ties = 0.0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = r
        t = j - i + 1
        ties += t ** 3 - t
        i = j + 1
    r1 = sum(r for r, (_, g) in zip(ranks, allv) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    n = n1 + n2
    var = n1 * n2 / 12 * ((n + 1) - ties / (n * (n - 1)))
    if var <= 0:
        return 1.0
    z = (abs(u1 - mu) - 0.5) / math.sqrt(var)
    return max(0.0, min(1.0, math.erfc(max(z, 0.0) / math.sqrt(2))))


def bootstrap_ci(x: list[float], y: list[float], iters: int = 4000, seed: int = 11) -> tuple[float, float]:
    """IC del 95 % de mediana(y) - mediana(x) por bootstrap (semilla fija: el mismo informe cada vez)."""
    rnd = random.Random(seed)
    d = sorted(statistics.median(rnd.choices(y, k=len(y))) - statistics.median(rnd.choices(x, k=len(x)))
               for _ in range(iters))
    return d[int(0.025 * iters)], d[int(0.975 * iters) - 1]


def load(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def compare(rows: list[dict], a: str, b: str) -> list[dict]:
    out = []
    for test in TESTS:
        m = METRIC[test]
        xa = [r[m] for r in rows if r["test"] == test and r["tag"] == a and isinstance(r.get(m), (int, float))]
        xb = [r[m] for r in rows if r["test"] == test and r["tag"] == b and isinstance(r.get(m), (int, float))]
        if not xa or not xb:
            continue
        ma, mb = statistics.median(xa), statistics.median(xb)
        lo, hi = bootstrap_ci(xa, xb) if len(xa) > 1 and len(xb) > 1 else (float("nan"), float("nan"))
        p = mann_whitney_p(xa, xb)
        better = HIGHER_IS_BETTER[m]
        if lo > 0 or hi < 0:
            verdict = "B mejor" if (lo > 0) == better else "B peor"
        else:
            verdict = "sin diferencia medible"
        out.append({"test": test, "metric": m, "n_a": len(xa), "n_b": len(xb), "median_a": ma, "median_b": mb,
                    "delta_pct": 100 * (mb - ma) / ma if ma else float("nan"), "ci_lo": lo, "ci_hi": hi, "p": p,
                    "verdict": verdict})
    return out


def cmd_compare(a) -> int:
    res = compare(load(a.file), a.a, a.b)
    if not res:
        sys.exit(f"no hay pruebas con las dos etiquetas ({a.a}, {a.b})")
    print(f"{'prueba':6} {'métrica':12} {'n':>7} {a.a:>10} {a.b:>10} {'Δ %':>7} {'IC 95 % de la Δ':>22} {'p':>6}  veredicto")
    for r in res:
        print(f"{r['test']:6} {r['metric']:12} {r['n_a']:>3}+{r['n_b']:<3} {r['median_a']:>10.2f} {r['median_b']:>10.2f} "
              f"{r['delta_pct']:>+7.1f} [{r['ci_lo']:>+9.2f}, {r['ci_hi']:>+9.2f}] {r['p']:>6.3f}  {r['verdict']}")
        if min(r["n_a"], r["n_b"]) < 6:
            print(f"{'':6} (menos de 6 pasadas por brazo: un efecto de ~2 % no se distingue del ruido)")
    return 0


def cmd_show(a) -> int:
    rows = load(a.file)
    for tag in sorted({r["tag"] for r in rows}):
        for test in TESTS:
            m = METRIC[test]
            v = [r[m] for r in rows if r["tag"] == tag and r["test"] == test and isinstance(r.get(m), (int, float))]
            if v:
                print(f"[{tag}] {test} {m}: mediana {statistics.median(v):.2f} (mín {min(v):.2f}, máx {max(v):.2f}, n {len(v)})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--server", default="http://127.0.0.1:8081")
    r.add_argument("--decide", default=None, help="ada-decide (p. ej. http://127.0.0.1:8087) para S1-S3")
    r.add_argument("--tag", required=True, help="la configuración que se mide (A, B, 'pf_fused'...)")
    r.add_argument("--tests", default="B1,B2,P1,S1,S2")
    r.add_argument("--runs", type=int, default=1)
    r.add_argument("--bulk", type=int, default=50, help="ítems de S3")
    r.add_argument("--out", default="banco.jsonl")
    r.add_argument("--note", default="", help="texto libre que se guarda en cada registro")
    r.add_argument("--timeout", type=float, default=1800.0)
    r.add_argument("--keep-answers", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("file")
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    s = sub.add_parser("show")
    s.add_argument("file")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "compare": cmd_compare, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
