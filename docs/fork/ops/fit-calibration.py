#!/usr/bin/env python3
"""fit-calibration — ajusta la temperatura de System One con decisiones etiquetadas.

1. Arrancad ada-decide con `--log decisions.jsonl` y usadlo normalmente.
2. Poned en cada linea `"label": "<clave de la opcion correcta>"` (las que no tengan label se ignoran;
   para `noul`, "true"/"false"; para `score`, el indice "0", "1"...).
3. python3 fit-calibration.py decisions.jsonl

Recalcula cada decision desde los logprobs crudos del log (sin llamar a Strata) para:
  * con y sin calibracion contextual (si el log la tiene),
  * cada T de una rejilla,
y da acierto, log-loss (NLL), Brier y ECE.  Elegid la T de menor NLL y pasadla a ada-decide con
`--temperature T` (o por peticion en "options").  Con menos de ~100 etiquetas, el resultado es ruido.
"""
from __future__ import annotations

import argparse
import json
import math
import sys

GRID = [0.25, 0.35, 0.5, 0.7, 0.85, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0]


def softmax(xs):
    m = max(xs)
    e = [math.exp(x - m) for x in xs]
    s = sum(e)
    return [v / s for v in e]


def recompute(rec: dict, T: float, contextual: bool) -> dict[str, float] | None:
    """Las probabilidades de una decision con otra T, desde las pasadas del log."""
    passes = rec.get("passes") or []
    if not passes:
        return None
    keys = list(passes[0]["raw"].keys())
    acc = {k: 0.0 for k in keys}
    for p in passes:
        raw = p["raw"]
        if contextual:
            if not p.get("cf"):
                return None
            raw = {k: raw[k] - p["cf"][k] for k in keys}
        for k, v in zip(keys, softmax([raw[k] / T for k in keys])):
            acc[k] += v / len(passes)
    return acc


def metrics(rows: list[tuple[dict, str]], bins: int = 10) -> dict:
    n = len(rows)
    nll = brier = hit = 0.0
    conf_bins = [[0, 0.0, 0.0] for _ in range(bins)]      # cuantas, suma de confianza, aciertos
    for probs, label in rows:
        p_true = max(probs.get(label, 0.0), 1e-12)
        nll -= math.log(p_true)
        brier += sum((v - (1.0 if k == label else 0.0)) ** 2 for k, v in probs.items())
        best = max(probs, key=probs.get)
        ok = best == label
        hit += ok
        b = min(int(probs[best] * bins), bins - 1)
        conf_bins[b][0] += 1
        conf_bins[b][1] += probs[best]
        conf_bins[b][2] += ok
    ece = sum(abs(c[1] - c[2]) for c in conf_bins if c[0]) / n
    return {"n": n, "accuracy": hit / n, "nll": nll / n, "brier": brier / n, "ece": ece}


def load(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("label") is not None and rec.get("passes"):
                rec["label"] = str(rec["label"])
                out.append(rec)
    return out


def fit(recs: list[dict]) -> list[dict]:
    """Una fila por (contextual, T) con sus metricas, ordenada por NLL."""
    table = []
    for contextual in (False, True):
        for T in GRID:
            rows = []
            for r in recs:
                p = recompute(r, T, contextual)
                if p is None or r["label"] not in p:
                    rows = None
                    break
                rows.append((p, r["label"]))
            if rows:
                table.append({"contextual": contextual, "T": T, **metrics(rows)})
    return sorted(table, key=lambda x: x["nll"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log")
    a = ap.parse_args()
    recs = load(a.log)
    if not recs:
        sys.exit("no hay decisiones con 'label' en el log")
    table = fit(recs)
    print(f"{len(recs)} decisiones etiquetadas" + ("  (pocas: tomadlo como orientativo)" if len(recs) < 100 else ""))
    print(f"{'contextual':>10} {'T':>5} {'acierto':>8} {'NLL':>7} {'Brier':>7} {'ECE':>6}")
    for row in table:
        print(f"{str(row['contextual']):>10} {row['T']:>5} {row['accuracy']:>8.3f} {row['nll']:>7.3f} "
              f"{row['brier']:>7.3f} {row['ece']:>6.3f}")
    best = table[0]
    print(f"\nMejor: --temperature {best['T']}" + (" --calibrate" if best["contextual"] else ""))


if __name__ == "__main__":
    main()
