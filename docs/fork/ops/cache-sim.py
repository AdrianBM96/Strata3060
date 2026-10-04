#!/usr/bin/env python3
"""cache-sim.py: replays a real routing trace through policies for the VRAM expert cache, and says how much hit rate
there is left to win before anyone touches the engine.

    # 1. a trace of real use (the engine writes it; ~4 KB per generated token)
    #    in strata-<model>.json, "args": [..., "--dump-routing", "/home/bazzite/Strata/bench/routing.bin"]
    # 2. the comparison
    python3 cache-sim.py routing.bin --slots 3696 --profile ~/Strata/<learned profile>.bin
    python3 cache-sim.py routing.bin --slots 3696 --grid         # also the --adapt-decay/--adapt-every/--adapt-swaps grid

The trace is what `--dump-routing` writes in --serve: one record per token per layer (int32 layer, int32 k, k int32
ids, k float weights) in execution order.  A verify window is a pass over the layers: a new window starts when the
layer index goes back down.

Policies:
  static        the starting residency, never changed (the profile, or the trace's own top --slots pairs = an
                oracle static, the best fixed set for this trace)
  adapt         the engine's rule (generate.cpp, `adapt`): usage += entries; every `every` windows, per layer,
                swap the most-used non-resident (usage >= 2) for the least-used resident while
                cand >= victim + 1.5, at most `swaps` per round by gain; usage *= decay.  A swapped-in expert
                serves from the next window; the evicted one is a miss at once
  adapt-x       the same with victims from ANY layer (the per-layer split moves with the use)
  lru           per layer, every miss admitted, least recently used out (a reference: churn costs copies)
  belady        the offline optimum with bypass, global budget: at every miss, keep the experts whose next use comes
                soonest.  It knows the future, so no real policy reaches it: it is the ceiling
  belady-layer  the same optimum with today's per-layer budgets: the ceiling of any within-layer rule

Metrics:
  hit/entry     hits / routed entries (token x k): the engine's "decode expert cache hit rate"
  hit/distinct  hits / distinct experts per layer per window: what the CPU really computes (a missed expert runs once
                per window for all its tokens)
  swaps/win     experts copied into VRAM per window (each ~1.38 MB over PCIe, which the decode's own copies also use)

The simulator's `adapt` with the deployed flags should give about the hit rate the engine logs for the same
session.  If it does not, the simulator is wrong, not the engine: check that first.
"""
from __future__ import annotations

import argparse
import heapq
import itertools
import struct
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

N_LAYER, N_EXPERT, BLOB_MB, PCIE_GBS = 48, 512, 1.3824, 11.0


# ------------------------------------------------------------------ input
def read_windows(path: str, max_windows: int | None = None) -> list[list[Counter]]:
    """-> windows, each a list of N_LAYER Counters {expert: routed entries in this window}."""
    blob = Path(path).read_bytes()
    windows: list[list[Counter]] = []
    cur: list[Counter] | None = None
    prev = -1
    off = 0
    while off + 8 <= len(blob):
        layer, k = struct.unpack_from("<ii", blob, off)
        off += 8
        if not (0 <= layer < N_LAYER and 0 < k <= 64) or off + 8 * k > len(blob):
            raise SystemExit(f"{path}: bad record at byte {off - 8} (layer {layer}, k {k})")
        ids = struct.unpack_from("<%di" % k, blob, off)
        off += 8 * k                                              # the ids and the weights
        if cur is None or layer < prev:
            if max_windows is not None and len(windows) >= max_windows:
                break
            cur = [Counter() for _ in range(N_LAYER)]
            windows.append(cur)
        prev = layer
        for e in ids:
            if 0 <= e < N_EXPERT:
                cur[layer][e] += 1
    return windows


def read_profile(path: str) -> list[tuple[int, int]]:
    blob = Path(path).read_bytes()
    if blob[:4] != b"STRP":
        raise SystemExit(f"{path}: not a Strata profile")
    _ver, nl, ne, _slots, n = struct.unpack_from("<5I", blob, 4)
    if (nl, ne) != (N_LAYER, N_EXPERT):
        raise SystemExit(f"{path}: {nl}x{ne}, not {N_LAYER}x{N_EXPERT}")
    return [struct.unpack_from("<HH", blob, 24 + 4 * i) for i in range(n)]


def oracle_static(windows, slots: int) -> set[int]:
    freq: Counter = Counter()
    for w in windows:
        for l, c in enumerate(w):
            for e, n in c.items():
                freq[l * N_EXPERT + e] += n
    return {key for key, _ in freq.most_common(slots)}


# ------------------------------------------------------------------ policies
class Result:
    def __init__(self, name: str):
        self.name, self.hit_e, self.n_e, self.hit_d, self.n_d, self.swaps, self.windows = name, 0, 0, 0, 0, 0, 0
        self.layer_hit = [0] * N_LAYER                            # distinct hits / lookups per layer
        self.layer_n = [0] * N_LAYER

    def add(self, w, resident) -> None:
        self.windows += 1
        for l, c in enumerate(w):
            base = l * N_EXPERT
            for e, n in c.items():
                hit = (base + e) in resident
                self.n_e += n
                self.n_d += 1
                self.layer_n[l] += 1
                self.layer_hit[l] += hit
                if hit:
                    self.hit_e += n
                    self.hit_d += 1

    def row(self) -> str:
        he = self.hit_e / self.n_e if self.n_e else 0.0
        hd = self.hit_d / self.n_d if self.n_d else 0.0
        sw = self.swaps / self.windows if self.windows else 0.0
        return (f"{self.name:34s} {100 * he:6.2f} % {100 * hd:6.2f} %  {sw:7.2f}  "
                f"{sw * BLOB_MB:7.1f} MB  {sw * BLOB_MB / PCIE_GBS:5.2f} ms")


def run_static(windows, start: set[int], name="static") -> Result:
    r = Result(name)
    for w in windows:
        r.add(w, start)
    return r


def run_adapt(windows, start: set[int], every=4, decay=0.7, swaps=96, cross=False, name=None) -> Result:
    r = Result(name or f"adapt every={every} decay={decay} swaps={swaps}{' cross' if cross else ''}")
    resident = set(start)
    usage: dict[int, float] = defaultdict(float)
    pending: list[int] = []
    for wi, w in enumerate(windows):
        resident.update(pending)                                  # the previous round's copies have landed
        pending = []
        r.add(w, resident)
        for l, c in enumerate(w):
            base = l * N_EXPERT
            for e, n in c.items():
                usage[base + e] += n
        if (wi + 1) % every:
            continue
        out = []
        if cross:
            cand = sorted(((u, k) for k, u in usage.items() if k not in resident and u >= 2.0), reverse=True)
            vict = sorted((usage.get(k, 0.0), k) for k in resident)
            for (cu, ck), (vu, vk) in zip(cand, vict):
                if cu < vu + 1.5:
                    break
                out.append((cu - vu, ck, vk))
        else:
            by_layer_res: dict[int, list] = defaultdict(list)
            for k in resident:
                by_layer_res[k // N_EXPERT].append((usage.get(k, 0.0), k))
            by_layer_cand: dict[int, list] = defaultdict(list)
            for k, u in usage.items():
                if k not in resident and u >= 2.0:
                    by_layer_cand[k // N_EXPERT].append((u, k))
            for l, cand in by_layer_cand.items():
                vict = by_layer_res.get(l)
                if not vict:
                    continue
                cand.sort(reverse=True)
                vict.sort()
                for (cu, ck), (vu, vk) in zip(cand, vict):
                    if cu < vu + 1.5:
                        break
                    out.append((cu - vu, ck, vk))
        out.sort(reverse=True)
        for _g, ck, vk in out[:swaps]:
            resident.discard(vk)                                  # evicted now: a miss from here
            pending.append(ck)                                    # resident from the next window
            r.swaps += 1
        assert len(resident) + len(pending) <= len(start), "the cache grew past its slots"
        for k in list(usage):
            usage[k] *= decay
            if usage[k] < 1e-3:
                del usage[k]
    return r


def run_lru(windows, start: set[int]) -> Result:
    r = Result("lru (per layer)")
    caches = [OrderedDict() for _ in range(N_LAYER)]
    for k in sorted(start):
        caches[k // N_EXPERT][k] = True
    budget = [len(c) for c in caches]
    for w in windows:
        resident = set(itertools.chain.from_iterable(caches))
        r.add(w, resident)
        for l, c in enumerate(w):
            cache = caches[l]
            for e in c:
                key = l * N_EXPERT + e
                if key in cache:
                    cache.move_to_end(key)
                elif budget[l] > 0:
                    if len(cache) >= budget[l]:
                        cache.popitem(last=False)
                    cache[key] = True
                    r.swaps += 1
    return r


def run_belady(windows, start: set[int], per_layer=False) -> Result:
    """Offline optimum with bypass: after each window, keep the experts (resident, or just routed) whose next use is
    soonest.  Global budget, or today's per-layer budgets."""
    r = Result("belady (ceiling, per layer)" if per_layer else "belady (ceiling, global)")
    INF = len(windows) + 1
    uses: dict[int, list[int]] = defaultdict(list)
    for wi, w in enumerate(windows):
        for l, c in enumerate(w):
            for e in c:
                uses[l * N_EXPERT + e].append(wi)
    ptr: dict[int, int] = defaultdict(int)

    def next_use(key: int, after: int) -> int:
        lst, i = uses.get(key, ()), ptr[key]
        while i < len(lst) and lst[i] <= after:
            i += 1
        ptr[key] = i
        return lst[i] if i < len(lst) else INF

    group = (lambda k: k // N_EXPERT) if per_layer else (lambda k: 0)
    resident = set(start)
    budget = Counter(group(k) for k in start)
    heaps: dict[int, list] = defaultdict(list)                   # per group: max-heap on next use (lazy)
    nu: dict[int, int] = {}
    for k in start:
        nu[k] = next_use(k, -1)
        heapq.heappush(heaps[group(k)], (-nu[k], k))
    for wi, w in enumerate(windows):
        r.add(w, resident)
        demanded = [l * N_EXPERT + e for l, c in enumerate(w) for e in c]
        misses = []
        for k in demanded:
            n = next_use(k, wi)
            if k in resident:
                nu[k] = n
                heapq.heappush(heaps[group(k)], (-n, k))
            else:
                misses.append((n, k))
        misses.sort()
        for n, k in misses:
            g = group(k)
            if budget[g] <= 0 or n >= INF:
                continue
            h = heaps[g]
            while h and (h[0][1] not in resident or -h[0][0] != nu.get(h[0][1])):
                heapq.heappop(h)                                  # stale entries
            if not h:
                continue
            far, victim = -h[0][0], h[0][1]
            if n < far:
                heapq.heappop(h)
                resident.discard(victim)
                resident.add(k)
                nu[k] = n
                heapq.heappush(h, (-n, k))
                r.swaps += 1
    return r


# ------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("trace")
    ap.add_argument("--slots", type=int, required=True, help="the engine's expert cache slots (log: expert cache auto)")
    ap.add_argument("--profile", help="the profile the engine starts from (default: the trace's own top pairs)")
    ap.add_argument("--max-windows", type=int, default=None)
    ap.add_argument("--every", type=int, default=4)
    ap.add_argument("--decay", type=float, default=0.7)
    ap.add_argument("--swaps", type=int, default=96)
    ap.add_argument("--grid", action="store_true", help="also sweep --adapt-every/--adapt-decay/--adapt-swaps")
    ap.add_argument("--per-layer", action="store_true", help="the deployed adapt's hit rate per layer")
    a = ap.parse_args(argv)

    windows = read_windows(a.trace, a.max_windows)
    if not windows:
        raise SystemExit("the trace has no windows")
    if a.profile:
        start = {l * N_EXPERT + e for l, e in read_profile(a.profile)[:a.slots]}
        sname = "static (profile)"
    else:
        start = oracle_static(windows, a.slots)
        sname = "static (oracle: the trace's top pairs)"
    body = windows
    tokens = sum(max((sum(c.values()) for c in w), default=0) for w in windows) // 10   # k = 10 entries per token
    print(f"{len(windows)} windows (~{tokens} tokens), {a.slots} slots, start: {sname}; "
          "counted from the first window")
    print(f"{'policy':34s} {'hit/entry':>8s} {'hit/dist':>9s}  {'swaps/w':>7s}  {'copied/w':>10s}  {'PCIe/w':>8s}")

    def report(res: Result):
        print(res.row(), flush=True)

    report(run_static(body, start, sname))
    deployed = run_adapt(body, start, a.every, a.decay, a.swaps)
    report(deployed)
    report(run_adapt(body, start, a.every, a.decay, a.swaps, cross=True))
    report(run_lru(body, start))
    if a.grid:
        for every, decay, swaps in itertools.product((2, 4, 8), (0.5, 0.7, 0.85, 0.95), (32, 96, 256)):
            if (every, decay, swaps) != (a.every, a.decay, a.swaps):
                report(run_adapt(body, start, every, decay, swaps))
    report(run_belady(body, start, per_layer=True))
    report(run_belady(body, start, per_layer=False))
    if a.per_layer:
        print("\nper layer (deployed adapt, distinct hits; every 4th layer from 3 is QSA):")
        print("  " + "  ".join(f"{l:2d}:{100 * h / n:4.0f}%" if n else f"{l:2d}:  -" for l, (h, n)
                               in enumerate(zip(deployed.layer_hit, deployed.layer_n))))
    print("\nbelady knows the future: it is the ceiling, not a policy.  The gap between `adapt` (deployed flags) and "
          "`belady (per layer)` is what any within-layer rule could still win; `belady (global)` adds moving slots "
          "between layers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
