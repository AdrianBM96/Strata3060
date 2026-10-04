"""Tests for cache-sim.py on synthetic traces (no GPU).

    python3 docs/fork/ops/test_cache_sim.py
"""
from __future__ import annotations

import importlib.util
import random
import struct
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("cache_sim", Path(__file__).with_name("cache-sim.py"))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)
NE = S.N_EXPERT


def write_trace(path, windows_ids, k=10):
    """windows_ids: per window, per layer, a list of tokens, each a list of k expert ids."""
    with open(path, "wb") as f:
        for w in windows_ids:
            for layer, toks in enumerate(w):
                for ids in toks:
                    f.write(struct.pack("<ii", layer, k))
                    f.write(struct.pack("<%di" % k, *ids))
                    f.write(struct.pack("<%df" % k, *([0.0] * k)))


def topic_trace(rnd, n_windows, n_layers=S.N_LAYER, shift_every=60, hot=20, k=10, toks=2):
    """Routing that concentrates on a 'topic' of `hot` experts per layer, which changes every `shift_every` windows."""
    out = []
    topic = None
    for w in range(n_windows):
        if w % shift_every == 0:
            topic = [rnd.sample(range(NE), hot) for _ in range(n_layers)]
        win = []
        for l in range(n_layers):
            layer_toks = []
            for _ in range(toks):
                ids = rnd.sample(topic[l], 7) + rnd.sample(range(NE), 3)
                layer_toks.append(ids[:k])
            win.append(layer_toks)
        out.append(win)
    return out


class Parse(unittest.TestCase):
    def test_windows_split_when_the_layer_goes_back(self):
        with tempfile.TemporaryDirectory() as t:
            p = str(Path(t) / "r.bin")
            w = [[[list(range(10)), list(range(5, 15))] for _ in range(S.N_LAYER)] for _ in range(3)]
            write_trace(p, w)
            ws = S.read_windows(p)
            self.assertEqual(len(ws), 3)
            self.assertEqual(ws[0][0][5], 2)            # expert 5, routed by both tokens of layer 0
            self.assertEqual(sum(ws[1][47].values()), 20)


class Policies(unittest.TestCase):
    def setUp(self):
        rnd = random.Random(1)
        self.ws = None
        with tempfile.TemporaryDirectory() as t:
            p = str(Path(t) / "r.bin")
            write_trace(p, topic_trace(rnd, 240, n_layers=S.N_LAYER))
            self.ws = S.read_windows(p)
        self.slots = 24 * S.N_LAYER                     # room for one topic (+ a little) per layer
        self.start = S.oracle_static(self.ws, self.slots)

    def test_belady_is_the_ceiling(self):
        res = [S.run_static(self.ws, self.start), S.run_adapt(self.ws, self.start), S.run_lru(self.ws, self.start),
               S.run_adapt(self.ws, self.start, cross=True), S.run_fetch_admit(self.ws, self.start),
               S.run_fetch_admit(self.ws, self.start, adapt=False)]
        bl = S.run_belady(self.ws, self.start, per_layer=True)
        bg = S.run_belady(self.ws, self.start)
        for r in res:
            self.assertLessEqual(r.hit_d, bl.hit_d, r.name)
        self.assertLessEqual(bl.hit_d, bg.hit_d + 1)

    def test_adapt_follows_a_topic_shift_that_a_static_set_cannot(self):
        st = S.run_static(self.ws, self.start)
        ad = S.run_adapt(self.ws, self.start)
        self.assertGreater(ad.hit_d, st.hit_d)
        self.assertGreater(ad.swaps, 0)

    def test_budget_is_kept(self):
        # run_adapt asserts it after every round; a tight cache forces many swaps
        small = set(sorted(self.start)[: 8 * S.N_LAYER])
        for cross in (False, True):
            r = S.run_adapt(self.ws, small, cross=cross, every=2, swaps=500)
            self.assertGreater(r.swaps, 0)


class EngineMetric(unittest.TestCase):
    def test_the_pcie_share_is_left_out_like_the_engine_does(self):
        # one layer, 10 distinct misses, nothing resident: 77/256 of 10 -> 3 over PCIe, 7 on the CPU
        w = [S.Counter({e: 1 for e in range(10)})] + [S.Counter() for _ in range(S.N_LAYER - 1)]
        r = S.Result("x")
        fetched = r.add(w, set())
        self.assertEqual(fetched[0], [7, 8, 9])                  # the LAST misses in routing order
        self.assertEqual(r.cpu_e, 7)


def one_token_windows(layer_toks_per_window, k=3, dummy=7):
    """A window per entry: layer 0 (or the given layers) route the tokens, every other layer one dummy token.

    Every layer must emit a record per window, or read_windows collapses them into one. The dummy expert is never
    resident and has no victims in its layers, so it is never swapped in: it only splits windows."""
    out = []
    for lt in layer_toks_per_window:
        w = [[[dummy] * k] for _ in range(S.N_LAYER)]
        for layer, toks in lt.items():
            w[layer] = toks
        out.append(w)
    return out


class DualMemory(unittest.TestCase):
    def test_blended(self):
        self.assertEqual(S.blended(3.0, 10.0, 0.0), 3.0)
        self.assertEqual(S.blended(3.0, 10.0, 2.0), 23.0)

    def test_w0_is_todays_rule(self):
        rnd = random.Random(1)
        with tempfile.TemporaryDirectory() as t:
            p = str(Path(t) / "r.bin")
            write_trace(p, topic_trace(rnd, 60, n_layers=S.N_LAYER))
            ws = S.read_windows(p)
        start = S.oracle_static(ws, 24 * S.N_LAYER)
        a = S.run_fetch_admit(ws, start)
        b = S.run_fetch_admit(ws, start, w_long=0.0, long_decay=0.98)
        for f in ("hit_e", "hit_d", "n_e", "n_d", "cpu_e", "cpu_w", "swaps", "free"):
            self.assertEqual(getattr(a, f), getattr(b, f), f)

    def test_the_long_memory_keeps_a_steady_specialist_through_a_burst(self):
        # layer 0 has ONE slot with the specialist S. Phase A routes S steadily (40 windows); phase B adds a heavy
        # burst of F (2 windows, one review: F's short usage beats S's); phase C routes S again. Short-only evicts S
        # for F and misses S in phase C; with w=2 and a slow long decay S's score (short + 2 x long) wins.
        sb, fb, k = 5, 400, 3
        wins = []
        wins += one_token_windows([{0: [[sb] * k]}] * 40, k=k)
        wins += one_token_windows([{0: [[sb] * k, [fb] * k, [fb] * k, [fb] * 2 + [sb]]}] * 2, k=k)
        wins += one_token_windows([{0: [[sb] * k, [sb] * k]}] * 6, k=k)
        with tempfile.TemporaryDirectory() as t:
            p = str(Path(t) / "r.bin")
            write_trace(p, wins, k=k)
            ws = S.read_windows(p)
        self.assertEqual(len(ws), 48)
        kw = dict(adapt=True, every=2, decay=0.5, swaps=10)
        short = S.run_fetch_admit(ws, {sb}, **kw)
        dual = S.run_fetch_admit(ws, {sb}, w_long=2.0, long_decay=0.995, **kw)
        self.assertEqual(short.swaps, 2)        # F in, then S back: the short memory flaps
        self.assertEqual(dual.swaps, 0)         # S never leaves
        self.assertGreater(dual.hit_e - short.hit_e, 8)


class CostWeight(unittest.TestCase):
    def test_read_layer_costs(self):
        costs = S.read_layer_costs("/home/bazzite/Strata-data/packs/swift-iq2_xs/native_experts.txt")
        self.assertEqual(len(costs), S.N_LAYER)
        self.assertEqual(costs[0], 1.0)                                  # layer 0: IQ2_S, the biggest blob
        self.assertAlmostEqual(costs[1], 1305600 / 1510400)              # IQ2_XXS
        self.assertAlmostEqual(costs[8], 1177600 / 1510400)              # IQ1_M
        self.assertEqual(sorted(set(costs)), sorted({1.0, 1305600 / 1510400, 1177600 / 1510400}))
        # the file's type mix: 34 IQ2_S, 11 IQ2_XXS, 3 IQ1_M
        n_xxs = sum(1 for c in costs if abs(c - 1305600 / 1510400) < 1e-12)
        n_m = sum(1 for c in costs if abs(c - 1177600 / 1510400) < 1e-12)
        self.assertEqual((n_xxs, n_m), (11, 3))

    def test_cpu_w_counts_weighted_distinct_misses_not_entries(self):
        # layer 0 (cost 2): experts 1 (2 entries) and 2 (1 entry) miss -> 2 distinct x 2.0; layer 1 (cost 0.5):
        # expert 3 (5 entries) misses -> 1 distinct x 0.5. Single-expert windows => no PCIe share.
        cost = [2.0, 0.5] + [1.0] * (S.N_LAYER - 2)
        w = [S.Counter({1: 2, 2: 1}), S.Counter({3: 5})] + [S.Counter() for _ in range(S.N_LAYER - 2)]
        r = S.Result("x", cost)
        r.add(w, set())
        self.assertEqual(r.cpu_e, 2 + 1 + 5)
        self.assertAlmostEqual(r.cpu_w, 2 * 2.0 + 1 * 0.5)
        self.assertTrue(r.row().strip().endswith(f"{4.5 * S.CPU_US_PER_EXPERT / 1000.0:.2f} ms"))

    def test_the_swap_budget_goes_to_the_expensive_layer_first(self):
        # layers 0 (cost 2) and 1 (cost 0.5) each have one resident (e0/c0) and one candidate (e1/c1) with the SAME
        # raw usage gap; swaps=1. Unweighted, the tie breaks to the bigger key (layer 1); weighted, layer 0's gain
        # (x2.0) beats layer 1's (x0.5). The probe window then routes e1 x6 entries and c1 x2: the policy that
        # swapped the expensive layer scores 4 more hit entries.
        e0, e1, c0, c1 = 10, 20, 10, 20
        pre = [{0: [[e0], [e1], [e1], [e1], [e1]], 1: [[c0], [c1], [c1], [c1], [c1]]}] * 2
        probe = [{0: [[e1]] * 6, 1: [[c1]] * 2}]
        with tempfile.TemporaryDirectory() as t:
            p = str(Path(t) / "r.bin")
            write_trace(p, one_token_windows(pre + probe, k=1), k=1)
            ws = S.read_windows(p)
        self.assertEqual(len(ws), 3)
        cost = [2.0, 0.5] + [1.0] * (S.N_LAYER - 2)
        kw = dict(every=2, decay=0.5, swaps=1)
        plain = S.run_adapt(ws, {e0, 1 * S.N_EXPERT + c0}, **kw)
        weighted = S.run_adapt(ws, {e0, 1 * S.N_EXPERT + c0}, cost=cost, **kw)
        self.assertEqual(plain.swaps, 1)
        self.assertEqual(weighted.swaps, 1)
        self.assertEqual(weighted.hit_e - plain.hit_e, 4)


class Cli(unittest.TestCase):
    def test_main_runs(self):
        with tempfile.TemporaryDirectory() as t:
            p = str(Path(t) / "r.bin")
            write_trace(p, topic_trace(random.Random(2), 40))
            self.assertEqual(S.main([p, "--slots", "600", "--per-layer"]), 0)


if __name__ == "__main__":
    unittest.main()
