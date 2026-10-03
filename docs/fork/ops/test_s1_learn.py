"""Tests del bucle de autoaprendizaje (s1_learn.py) y de su conexión con ada-decide, sin GPU ni Strata.

    python3 docs/fork/ops/test_s1_learn.py
"""
from __future__ import annotations

import importlib.util
import json
import math
import random
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("s1_learn", HERE / "s1_learn.py")
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

KEYS = ["billing", "technical", "sales"]
CRIT = {"billing": "Billing and refunds", "technical": "Bugs and outages", "sales": "Plans and upgrades"}


def biased_case(rnd: random.Random):
    """Una decisión simulada: la verdad, y unas logprobs del modelo con un sesgo fijo hacia 'billing' (+1,2) y ruido."""
    truth = rnd.randrange(3)
    z = [rnd.gauss(0, 1) + (2.0 if k == truth else 0.0) + (1.2 if k == 0 else 0.0) for k in range(3)]
    return z, truth


def fill(store, tid, n, rnd, source="system2", t0=None):
    ids = []
    for i in range(n):
        z, truth = biased_case(rnd)
        p = L.softmax(z)
        did = store.record("choice", tid, "which team?", KEYS, CRIT, f"state {i}", z, [], dict(zip(KEYS, p)),
                           KEYS[max(range(3), key=p.__getitem__)], max(p), False, None, None)
        store._q("UPDATE decisions SET ts = ? WHERE id = ?", ((t0 or 1000.0) + i, did))
        store.add_label(did, source, KEYS[truth])
        ids.append(did)
    return ids


class StoreTests(unittest.TestCase):
    def test_gold_wins_and_rollback(self):
        with tempfile.TemporaryDirectory() as t:
            st = L.Store(str(Path(t) / "s.db"))
            did = st.record("choice", "t1", "q", KEYS, CRIT, "s", [0, 0, 0], [], {}, "billing", 0.5, False, None, None)
            st.add_label(did, "system2", "sales")
            st.add_label(did, "human", "technical")
            self.assertEqual(st.labeled("t1")[0]["label"], "technical")
            self.assertEqual(st.labeled("t1")[0]["source"], "human")
            v1 = st.save_model("t1", "vector_scaling", {"b": [0, 0, 0], "log_t": 0}, {})
            v2 = st.save_model("t1", "vector_scaling", {"b": [1, 0, 0], "log_t": 0}, {})
            self.assertEqual(st.active_model("t1")["version"], v2)
            self.assertEqual(st.rollback("t1"), v1)
            self.assertEqual(st.active_model("t1")["version"], v1)
            st.keep_state_days = 0
            self.assertEqual(st.purge_states(), 1)
            self.assertIsNone(st.decision(did)["state"])


class Calibration(unittest.TestCase):
    def test_vector_scaling_removes_a_fixed_bias(self):
        rnd = random.Random(3)
        train = [biased_case(rnd) for _ in range(400)]
        test = [biased_case(rnd) for _ in range(400)]
        b, lt = L.fit_vector_scaling([z for z, _ in train], [y for _, y in train], [1.0] * 400)
        self.assertLess(b[0] - (b[1] + b[2]) / 2, -0.6)           # aprende a restar el sesgo hacia billing
        before = L.metrics([L.softmax(z) for z, _ in test], [y for _, y in test])
        after = L.metrics([L.predict_vs(z, b, lt) for z, _ in test], [y for _, y in test])
        self.assertLess(after["nll"], before["nll"])
        self.assertGreaterEqual(after["accuracy"], before["accuracy"])


class Conformal(unittest.TestCase):
    def test_binom_cdf(self):
        self.assertAlmostEqual(L.binom_cdf(0, 10, 0.5), 0.5 ** 10)
        self.assertAlmostEqual(L.binom_cdf(10, 10, 0.3), 1.0)
        self.assertAlmostEqual(L.binom_cdf(2, 5, 0.5), 0.5)

    def test_too_few_labels_gives_no_threshold(self):
        self.assertIsNone(L.select_threshold([0.995] * 10, [True] * 10, 0.05, 0.1))   # 0,95^10 = 0,60 > 0,1/19

    def test_guarantee_holds_by_simulation(self):
        """Un decisor perfectamente calibrado (acierta con prob = su confianza, confianza ~ U(0,34, 1)): el error real
        de las decisiones con confianza >= umbral es (1 - umbral) / 2.  La garantía dice: P(error real > alpha) <= delta."""
        rnd = random.Random(5)
        alpha, delta, fails, found = 0.1, 0.1, 0, 0
        for _ in range(300):
            conf = [rnd.uniform(0.34, 1.0) for _ in range(2000)]
            ok = [rnd.random() < c for c in conf]
            thr = L.select_threshold(conf, ok, alpha, delta)
            if thr is None:
                continue
            found += 1
            fails += (1 - thr["threshold"]) / 2 > alpha
        self.assertGreater(found, 200)
        self.assertLessEqual(fails / found, delta + 0.03)


class LearnerTests(unittest.TestCase):
    def test_retrain_promotes_and_gates(self):
        with tempfile.TemporaryDirectory() as t:
            st = L.Store(str(Path(t) / "s.db"))
            tid = L.template_id("which team?", KEYS, CRIT)
            fill(st, tid, 1500, random.Random(9))
            ln = L.Learner(st, alpha=0.1, delta=0.1)
            r = ln.retrain(tid)
            self.assertEqual(r["status"], "promoted")
            self.assertLess(r["candidate"]["nll"], r["previous"]["nll"])
            self.assertIsNotNone(r["conformal"])
            z = [1.2, 1.2, 0.0]                  # empate billing/technical que solo existe por el sesgo hacia billing
            p, m = ln.adjust(tid, KEYS, z, {})
            self.assertGreater(p["technical"], p["billing"])         # la calibración le quita el sesgo
            auto, g = ln.gate(m, 0.999)
            self.assertTrue(auto)
            self.assertEqual(g["source"], "system2")
            self.assertFalse(ln.gate(m, 0.34)[0])
            self.assertEqual(ln.retrain(tid)["status"], "kept (conformal refreshed)")   # mismo dato: no gana otra vez

    def test_gold_is_the_guarantee_source_when_there_is_enough(self):
        with tempfile.TemporaryDirectory() as t:
            st = L.Store(str(Path(t) / "s.db"))
            tid = L.template_id("which team?", KEYS, CRIT)
            fill(st, tid, 1500, random.Random(1), "human")
            r = L.Learner(st, alpha=0.1, delta=0.1, gold_min=50).retrain(tid)
            self.assertEqual(r["conformal"]["source"], "human")

    def test_few_labels(self):
        with tempfile.TemporaryDirectory() as t:
            st = L.Store(str(Path(t) / "s.db"))
            fill(st, "t", 10, random.Random(2))
            self.assertEqual(L.Learner(st).retrain("t")["status"], "few_labels")


class SystemTwoTests(unittest.TestCase):
    def test_waits_for_idle_and_labels(self):
        with tempfile.TemporaryDirectory() as t:
            st = L.Store(str(Path(t) / "s.db"))
            did = st.record("choice", "t", "which team?", KEYS, CRIT, "the API is down", [0, 0, 0], [], {},
                            "billing", 0.4, True, None, None)
            busy = iter([True, True, False])
            asked = []
            s2 = L.SystemTwo(st, None, "http://x", idle_poll=0.01, busy=lambda: next(busy, False),
                             ask=lambda sys_, user: asked.append(user) or "Thinking...\\nANSWER: technical")
            s2.enqueue(did, "system2")
            s2.q.join()
            self.assertEqual(s2.done, 1)
            self.assertIn("the API is down", asked[0])
            self.assertEqual(st.labeled("t")[0]["label"], "technical")

    def test_parse_answer(self):
        self.assertEqual(L.parse_answer("x\nANSWER: Technical.", KEYS), "technical")
        self.assertEqual(L.parse_answer("ANSWER: a\nANSWER: sales", KEYS), "sales")      # la última gana
        self.assertIsNone(L.parse_answer("ANSWER: maybe", KEYS))
        self.assertIsNone(L.parse_answer("no answer", KEYS))


class AdaDecideIntegration(unittest.TestCase):
    def test_decision_feedback_retrain_over_http(self):
        aspec = importlib.util.spec_from_file_location("ada_decide_int", HERE / "ada-decide.py")
        AD = importlib.util.module_from_spec(aspec)
        aspec.loader.exec_module(AD)
        import test_ada_decide as T                                  # el tokenizador y el Strata simulados
        AD.ASK = T.fake_model(letter_bias=0.0, content={"Bugs and outages": 1.0})
        with tempfile.TemporaryDirectory() as t:
            AD.STORE = L.Store(str(Path(t) / "s.db"))
            AD.LEARNER = L.Learner(AD.STORE, retrain_every=1000)
            AD.Handler.tokenizer = T.FakeTok()
            AD.Handler.defaults = {"permutations": 1}
            srv = ThreadingHTTPServer(("127.0.0.1", 0), AD.Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{srv.server_port}"

            def post(path, body):
                req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, json.load(r)
            try:
                _, d = post("/v1/systemone", {"state": "the API returns 500",
                                              "questions": {"dept": {"type": "choice", "instructions": "which team?",
                                                                     "criteria": CRIT}}})
                ans = d["answers"]["dept"]
                self.assertIn("id", ans)
                self.assertNotIn("_z", ans)
                code, f = post("/v1/systemone/feedback", {"id": ans["id"], "label": "technical"})
                self.assertEqual((code, f["stored"]), (200, 1))
                code, f = post("/v1/systemone/feedback", {"id": ans["id"], "label": "nope"})
                self.assertEqual(code, 207)
                code, w = post("/v1/systemone/warm", {"state": "another state"})
                self.assertEqual(code, 202)
                with urllib.request.urlopen(base + "/v1/systemone/stats", timeout=5) as r:
                    stats = json.load(r)
                self.assertEqual(stats["templates"][ans["template"]]["labels"], {"human": 1})
                _, rt = post("/v1/systemone/retrain", {})
                self.assertEqual(rt["results"][0]["status"], "few_labels")
            finally:
                srv.shutdown()
                srv.server_close()
                AD.STORE = AD.LEARNER = None


if __name__ == "__main__":
    unittest.main()
