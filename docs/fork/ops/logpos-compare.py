#!/usr/bin/env python3
"""logpos-compare — compara dos configuraciones de Strata sobre el MISMO texto, token a token.

Sirve para preguntas como "¿YaRN x2 hace peor al modelo?" con miles de posiciones en vez de 6 tareas.
Es el método de upstream (docs/UNSLOTH_Q4.md, "Quality: against llama.cpp on the same file").

Cómo sacar los datos (por cada configuración, A y B):

    STRATA_LOGPOS=/tmp/A.tsv STRATA_LOGPOS_TOPK=20 <arrancar el servidor con esa config> \
        con --short-read 100000 --adapt-every 100000   (el texto entero por las ventanas, caché quieta)
    y mandad el texto como prompt con max_tokens=1 (código, documentos y una conversación vuestra;
    unos 2.000-8.000 tokens en total).

El motor escribe una linea por token leído: posición, token, logprob del token, top-1, su logprob,
acierto, y los 20 más probables (`id:logprob`).

    python3 logpos-compare.py A.tsv B.tsv [--floor A2.tsv]

`--floor`: una SEGUNDA pasada de A.  Strata no es determinista entre pasadas (otros expertos en VRAM, otro
redondeo GPU/CPU: upstream mide ~94 % de acuerdo de A consigo mismo), así que la diferencia A-B solo
cuenta en lo que supere a la de A-A2.

Salida: posiciones, acuerdo del top-1, solape top-5/top-10, KL(A||B) sobre el top-K de A + un cubo para
el resto, perplejidad de cada una y la diferencia de NLL por token con su error estándar (pareada).
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys


def load(path: str) -> dict[int, dict]:
    """{posición: {"tgt", "lp", "top", "top_lp", "topk": {id: logprob}}} (la última aparición gana)."""
    rows = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) < 6:
                continue
            try:
                pos, tgt, lp, top, top_lp = int(c[0]), int(c[1]), float(c[2]), int(c[3]), float(c[4])
            except ValueError:
                continue
            topk = {}
            for kv in c[8:]:
                i, _, v = kv.partition(":")
                try:
                    topk[int(i)] = float(v)
                except ValueError:
                    pass
            rows[pos] = {"tgt": tgt, "lp": lp, "top": top, "top_lp": top_lp, "topk": topk}
    return rows


def kl_topk(pa: dict[int, float], pb: dict[int, float], floor: float = -30.0) -> float | None:
    """KL(A||B) sobre el top-K de A más un cubo con la masa restante.  Un id de A que B no lista toma, como
    mucho, la masa que B deja fuera de su top-K repartida por igual (cota prudente)."""
    if not pa or not pb:
        return None
    a_rest = max(1e-12, 1.0 - sum(math.exp(v) for v in pa.values()))
    b_rest_total = max(1e-12, 1.0 - sum(math.exp(v) for v in pb.values()))
    missing = [i for i in pa if i not in pb]
    b_missing_each = b_rest_total / (len(missing) + 1)
    kl = 0.0
    for i, la in pa.items():
        p = math.exp(la)
        q = math.exp(pb[i]) if i in pb else b_missing_each
        kl += p * (la - math.log(max(q, math.exp(floor))))
    kl += a_rest * (math.log(a_rest) - math.log(b_missing_each))
    return max(0.0, kl)


def overlap(pa: dict[int, float], pb: dict[int, float], k: int) -> float | None:
    if len(pa) < k or len(pb) < k:
        return None
    ta = sorted(pa, key=pa.get, reverse=True)[:k]
    tb = sorted(pb, key=pb.get, reverse=True)[:k]
    return len(set(ta) & set(tb)) / k


def compare(a: dict[int, dict], b: dict[int, dict]) -> dict:
    common = sorted(p for p in a if p in b and a[p]["tgt"] == b[p]["tgt"])
    if not common:
        raise SystemExit("no hay posiciones comunes con el mismo token: ¿es el mismo texto?")
    agree = sum(a[p]["top"] == b[p]["top"] for p in common) / len(common)
    d_nll = [(-b[p]["lp"]) - (-a[p]["lp"]) for p in common]          # >0: B peor en ese token
    o5 = [x for x in (overlap(a[p]["topk"], b[p]["topk"], 5) for p in common) if x is not None]
    o10 = [x for x in (overlap(a[p]["topk"], b[p]["topk"], 10) for p in common) if x is not None]
    kls = [x for x in (kl_topk(a[p]["topk"], b[p]["topk"]) for p in common) if x is not None]
    nll_a = statistics.fmean(-a[p]["lp"] for p in common)
    nll_b = statistics.fmean(-b[p]["lp"] for p in common)
    se = statistics.stdev(d_nll) / math.sqrt(len(d_nll)) if len(d_nll) > 1 else float("nan")
    return {"positions": len(common), "argmax_agreement": agree,
            "top5_overlap": statistics.fmean(o5) if o5 else None,
            "top10_overlap": statistics.fmean(o10) if o10 else None,
            "kl": statistics.fmean(kls) if kls else None,
            "ppl_a": math.exp(nll_a), "ppl_b": math.exp(nll_b),
            "delta_nll": statistics.fmean(d_nll), "delta_nll_se": se}


def fmt(r: dict) -> str:
    f = lambda v, d=3: "-" if v is None else f"{v:.{d}f}"
    return (f"posiciones {r['positions']} | top-1 igual {100 * r['argmax_agreement']:.1f} % | "
            f"top-5/10 {f(r['top5_overlap'])}/{f(r['top10_overlap'])} | KL {f(r['kl'], 4)} | "
            f"PPL {r['ppl_a']:.3f} -> {r['ppl_b']:.3f} | ΔNLL {r['delta_nll']:+.4f} ± {r['delta_nll_se']:.4f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--floor", help="segunda pasada de A: el ruido de Strata consigo mismo")
    x = ap.parse_args()
    a, b = load(x.a), load(x.b)
    r = compare(a, b)
    print("A vs B:  " + fmt(r))
    if x.floor:
        f = compare(a, load(x.floor))
        print("A vs A2: " + fmt(f) + "   (ruido)")
        excess = r["delta_nll"] - f["delta_nll"]
        se = math.hypot(r["delta_nll_se"], f["delta_nll_se"])
        print(f"\nΔNLL de B sobre el ruido: {excess:+.4f} ± {se:.4f} nats/token "
              + ("-> B es PEOR" if excess > 2 * se else "-> B es MEJOR" if excess < -2 * se
                 else "-> sin diferencia medible"))
    else:
        se = r["delta_nll_se"]
        print("\n" + ("B es PEOR" if r["delta_nll"] > 2 * se else "B es MEJOR" if r["delta_nll"] < -2 * se
                      else "sin diferencia medible") + " (a 2 errores estándar; sin --floor no se descuenta el ruido)")


if __name__ == "__main__":
    sys.exit(main())
