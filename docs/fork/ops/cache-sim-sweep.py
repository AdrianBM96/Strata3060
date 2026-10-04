#!/usr/bin/env python3
"""cache-sim-sweep.py: sweep fetch-admit variants without re-running belady/lru every time.

The stock cache-sim.py --grid only sweeps the `adapt` policy, never `fetch-admit`
(see main(): the grid loop calls run_adapt only).  This driver imports the simulator,
reads the trace once, and runs only the policies we care about.

Usage:
  python3 cache-sim-sweep.py TRACE [--profile P] [--slots N] [--pcie-frac F] \
      [--mode adapt|fa|both] [--every LIST] [--decay LIST] [--swaps LIST]
"""
from __future__ import annotations
import argparse
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_sim():
    spec = importlib.util.spec_from_file_location("cache_sim", HERE / "cache-sim.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--slots", type=int, required=True)
    ap.add_argument("--pcie-frac", type=float, default=0.30)
    ap.add_argument("--mode", choices=["adapt", "fa", "both"], default="both")
    ap.add_argument("--every", default="4")
    ap.add_argument("--decay", default="0.7")
    ap.add_argument("--swaps", default="96")
    ap.add_argument("--max-windows", type=int, default=None)
    a = ap.parse_args(argv)

    sim = load_sim()
    sim.PCIE_NUM = max(0, min(256, int(a.pcie_frac * 256.0 + 0.5)))

    windows = sim.read_windows(a.trace, a.max_windows)
    if not windows:
        raise SystemExit("no windows")
    if a.profile:
        start = {l * sim.N_EXPERT + e for l, e in sim.read_profile(a.profile)[:a.slots]}
        sname = "profile"
    else:
        start = sim.oracle_static(windows, a.slots)
        sname = "oracle"
    print(f"# {a.trace}: {len(windows)} windows, {a.slots} slots, start={sname}, pcie_frac={a.pcie_frac}")
    print(f"# {'policy':34s} {'eng.hit':>8s} {'swaps/w':>8s} {'free/w':>8s} {'copied/w':>10s}")

    def row(res):
        eng = res.hit_e / (res.hit_e + res.cpu_e) if (res.hit_e + res.cpu_e) else 0.0
        sw = res.swaps / res.windows if res.windows else 0.0
        fr = res.free / res.windows if res.windows else 0.0
        print(f"  {res.name:34s} {100*eng:7.2f}% {sw:8.2f} {fr:8.2f} {sw*sim.BLOB_MB:8.1f} MB", flush=True)

    evs = [int(x) for x in a.every.split(",")]
    dcs = [float(x) for x in a.decay.split(",")]
    sws = [int(x) for x in a.swaps.split(",")]

    # fetch-admit alone is flag-independent (no adapt on top)
    if a.mode in ("fa", "both"):
        row(sim.run_fetch_admit(windows, start, adapt=False))
    for every in evs:
        for decay in dcs:
            for swaps in sws:
                if a.mode in ("adapt", "both"):
                    row(sim.run_adapt(windows, start, every, decay, swaps,
                                      name=f"adapt e={every} d={decay} s={swaps}"))
                if a.mode in ("fa", "both"):
                    row(sim.run_fetch_admit(windows, start, adapt=True, every=every,
                                            decay=decay, swaps=swaps,
                                            name=f"fa+adapt e={every} d={decay} s={swaps}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
