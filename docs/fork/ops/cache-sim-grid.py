#!/usr/bin/env python3
"""cache-sim-grid.py: parallel parameter sweep for cache-sim.py's adapt / fetch-admit.

The stock `cache-sim.py --grid` sweeps only `run_adapt`; fetch-admit is never swept.
This driver reads the trace once in the parent, then forks workers (COW shares the
trace) to evaluate many (policy, every, decay, swaps) points at once.

    python3 cache-sim-grid.py TRACE --slots N [--profile P] [--pcie-frac F] \
        --every 2,4,8 --decay 0.5,0.7,0.85 --swaps 8,16,32,48,64,96,192
"""
from __future__ import annotations
import argparse
import importlib.util
import multiprocessing as mp
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIM = None
WINDOWS = None
START = None


def _load():
    spec = importlib.util.spec_from_file_location("cache_sim", HERE / "cache-sim.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _task(args):
    kind, every, decay, swaps = args
    if kind == "fa_alone":
        res = SIM.run_fetch_admit(WINDOWS, START, adapt=False)
    elif kind == "adapt":
        res = SIM.run_adapt(WINDOWS, START, every, decay, swaps,
                            name=f"adapt e={every} d={decay} s={swaps}")
    else:
        res = SIM.run_fetch_admit(WINDOWS, START, adapt=True, every=every, decay=decay,
                                  swaps=swaps, name=f"fa+adapt e={every} d={decay} s={swaps}")
    eng = res.hit_e / (res.hit_e + res.cpu_e) if (res.hit_e + res.cpu_e) else 0.0
    sw = res.swaps / res.windows if res.windows else 0.0
    fr = res.free / res.windows if res.windows else 0.0
    he = res.hit_e / res.n_e if res.n_e else 0.0
    return (res.name, 100 * eng, sw, fr, sw * SIM.BLOB_MB, 100 * he)


def main(argv=None):
    global SIM, WINDOWS, START
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--slots", type=int, required=True)
    ap.add_argument("--pcie-frac", type=float, default=0.30)
    ap.add_argument("--every", default="4")
    ap.add_argument("--decay", default="0.7")
    ap.add_argument("--swaps", default="96")
    ap.add_argument("--no-adapt-baseline", action="store_true")
    ap.add_argument("--jobs", type=int, default=12)
    a = ap.parse_args(argv)

    SIM = _load()
    SIM.PCIE_NUM = max(0, min(256, int(a.pcie_frac * 256.0 + 0.5)))
    WINDOWS = SIM.read_windows(a.trace)
    if not WINDOWS:
        raise SystemExit("no windows")
    if a.profile:
        START = {l * SIM.N_EXPERT + e for l, e in SIM.read_profile(a.profile)[:a.slots]}
    else:
        START = SIM.oracle_static(WINDOWS, a.slots)

    evs = [int(x) for x in a.every.split(",")]
    dcs = [float(x) for x in a.decay.split(",")]
    sws = [int(x) for x in a.swaps.split(",")]

    tasks = [("fa_alone", 0, 0.0, 0)]
    for every in evs:
        for decay in dcs:
            for swaps in sws:
                if not a.no_adapt_baseline:
                    tasks.append(("adapt", every, decay, swaps))
                tasks.append(("fa", every, decay, swaps))

    print(f"# {a.trace}: {len(WINDOWS)} windows, {a.slots} slots, pcie_frac={a.pcie_frac}, "
          f"{len(tasks)} points, {a.jobs} workers", flush=True)
    print(f"# {'policy':34s} {'eng.hit':>8s} {'swaps/w':>8s} {'free/w':>8s} {'copied/w':>10s} {'hit/entry':>9s}")

    with mp.Pool(a.jobs) as pool:
        for name, eng, sw, fr, mb, he in pool.imap_unordered(_task, tasks):
            print(f"  {name:34s} {eng:7.2f}% {sw:8.2f} {fr:8.2f} {mb:8.1f} MB {he:8.2f}%", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
