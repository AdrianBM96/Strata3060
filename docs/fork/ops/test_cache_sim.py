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


class Cli(unittest.TestCase):
    def test_main_runs(self):
        with tempfile.TemporaryDirectory() as t:
            p = str(Path(t) / "r.bin")
            write_trace(p, topic_trace(random.Random(2), 40))
            self.assertEqual(S.main([p, "--slots", "600", "--per-layer"]), 0)


if __name__ == "__main__":
    unittest.main()
