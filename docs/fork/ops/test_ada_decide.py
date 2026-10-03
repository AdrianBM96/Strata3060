"""Tests de ada-decide.py sin GPU ni Strata: un modelo simulado con sesgo de letra.

    python3 docs/fork/ops/test_ada_decide.py
"""
from __future__ import annotations

import importlib.util
import json
import math
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location("ada_decide", Path(__file__).with_name("ada-decide.py"))
AD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AD)


class FakeTok:
    """ "A" -> [65]; " A" -> [1065]; "(A" -> ["(", A]; " (A" -> [" (", A]; "A)" -> [A, ")"]; "**A" -> ["**", A]."""

    def encode(self, text, parse_special=False):
        if len(text) == 1:
            return [ord(text)]
        if text.startswith(" ") and len(text) == 2:
            return [1000 + ord(text[1])]
        if text.startswith("(") and len(text) == 2:
            return [40, ord(text[1])]
        if text.startswith(" (") and len(text) == 3:
            return [41, ord(text[2])]
        if text.endswith(")") and len(text) == 2:
            return [ord(text[0]), 42]
        if text.startswith("**"):
            return [43, ord(text[2])]
        raise ValueError(text)


def fake_model(letter_bias=2.0, content={"technical": 0.5}, empty_state_content=None):
    """Un Strata simulado: logprob de la letra de cada opcion = sesgo de letra (A favorecida) + preferencia
    por su contenido; el resto de la masa se la lleva otro token.  Con el estado 'N/A' no hay contenido."""
    calls = []

    def ask(url, system, user, timeout, lpids):
        calls.append((system, user))
        cf = system.endswith("State:\n" + AD.CONTENT_FREE)
        opts = [l for l in user.split("\n") if l.startswith("(")]
        logits = {}
        for line in opts:
            letter, desc = line[1], line[4:]
            s = (letter_bias if letter == "A" else 0.0)
            if not cf:
                s += content.get(desc, 0.0)
            logits[letter] = s
        # 70 % de la masa en las letras, repartida segun los logits; la letra pura y " X" a medias
        lse = AD.logsumexp(list(logits.values()))
        out = {}
        for letter, s in logits.items():
            lp = math.log(0.7) + s - lse
            out[str(ord(letter))] = lp + math.log(0.5)
            out[str(1000 + ord(letter))] = lp + math.log(0.5)
        return {k: v for k, v in out.items() if int(k) in set(lpids)}, {"prompt_tokens": 10}

    ask.calls = calls
    return ask


CRIT = {"billing": "billing", "technical": "technical", "sales": "sales"}


class Decide(unittest.TestCase):
    def setUp(self):
        AD.CALIBRATOR.cache.clear()

    def run_choice(self, ask, **cfg):
        AD.ASK = ask
        return AD.decide_choice("u", FakeTok(), "a router is down", "which department?", CRIT, 1.0, cfg)[0]

    def test_label_ids_take_only_distinguishing_first_tokens(self):
        self.assertEqual(AD.label_ids(FakeTok(), "A"), [65, 1065])

    def test_variants_are_summed_not_maxed(self):
        ans = self.run_choice(fake_model(letter_bias=0.0, content={}))
        self.assertAlmostEqual(ans["option_mass"], 0.7, places=6)        # 0,35 + 0,35 de cada letra, sumadas
        for p in ans["probabilities"].values():
            self.assertAlmostEqual(p, 1 / 3, places=6)

    def test_letter_bias_wins_without_calibration(self):
        ans = self.run_choice(fake_model())                               # billing es la (A)
        self.assertEqual(ans["choice"], "billing")

    def test_permutations_remove_the_letter_bias(self):
        ask = fake_model()
        ans = self.run_choice(ask, permutations=3)
        self.assertEqual(ans["choice"], "technical")
        self.assertEqual(len(ask.calls), 3)

    def test_contextual_calibration_removes_it_in_one_order(self):
        ask = fake_model()
        ans = self.run_choice(ask, calibrate=True)
        self.assertEqual(ans["choice"], "technical")
        self.assertEqual(len(ask.calls), 2)                               # la decision + el sesgo de la plantilla
        self.run_choice(ask, calibrate=True)
        self.assertEqual(len(ask.calls), 3)                               # el sesgo ya esta en memoria

    def test_temperature_softens(self):
        a1 = self.run_choice(fake_model(), calibrate=True)
        a2 = self.run_choice(fake_model(), calibrate=True, temperature=2.0)
        self.assertLess(a2["confidence"], a1["confidence"])
        self.assertEqual(a2["choice"], a1["choice"])

    def test_escalate_on_low_margin_and_low_mass(self):
        ans = self.run_choice(fake_model(letter_bias=0.0, content={"technical": 0.05}))
        self.assertTrue(ans["escalate"])
        self.assertEqual(ans["escalate_reasons"], ["margin"])
        ans = self.run_choice(fake_model(letter_bias=0.0, content={"technical": 5.0}), min_mass=0.9)
        self.assertEqual(ans["escalate_reasons"], ["option_mass"])
        ans = self.run_choice(fake_model(letter_bias=0.0, content={"technical": 5.0}))
        self.assertFalse(ans["escalate"])

    def test_missing_ids_are_reported(self):
        def ask(url, system, user, timeout, lpids):
            return {"65": -0.1}, {}
        ans = self.run_choice(ask)
        self.assertEqual(ans["missing_label_ids"], [66, 67, 1065, 1066, 1067])
        self.assertEqual(ans["choice"], "billing")

    def test_score_and_noul(self):
        AD.ASK = fake_model(letter_bias=0.0, content={"high": 3.0, "Yes": 3.0})
        ans, _ = AD.decide_score("u", FakeTok(), "s", "urgency", ["low", "mid", "high"], 1.0, {})
        self.assertGreater(ans["score"], 1.5)
        self.assertNotIn("choice", ans)
        ans, _ = AD.decide_noul("u", FakeTok(), "s", "outage?", 1.0, {"permutations": 2})
        self.assertTrue(ans["value"])


class Http(unittest.TestCase):
    def test_request_options_and_log(self):
        import tempfile
        AD.ASK = fake_model()
        AD.CALIBRATOR.cache.clear()
        with tempfile.TemporaryDirectory() as t:
            AD.Handler.tokenizer = FakeTok()
            AD.Handler.log_path = str(Path(t) / "log.jsonl")
            AD.Handler.defaults = {"permutations": 1}
            srv = ThreadingHTTPServer(("127.0.0.1", 0), AD.Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                body = {"state": "a router is down", "options": {"permutations": 3},
                        "questions": {"dept": {"type": "choice", "instructions": "which department?",
                                               "criteria": CRIT}}}
                req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/v1/systemone",
                                             data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
                d = json.load(urllib.request.urlopen(req, timeout=5))
            finally:
                srv.shutdown()
                srv.server_close()
            ans = d["answers"]["dept"]
            self.assertEqual(ans["choice"], "technical")
            self.assertNotIn("_passes", ans)
            rec = json.loads(Path(AD.Handler.log_path).read_text().splitlines()[0])
            self.assertEqual(len(rec["passes"]), 3)
            self.assertIsNone(rec["label"])


class FitCalibration(unittest.TestCase):
    def test_overconfident_log_gets_t_above_one(self):
        import random
        import tempfile
        fspec = importlib.util.spec_from_file_location("fit_cal", Path(__file__).with_name("fit-calibration.py"))
        FC = importlib.util.module_from_spec(fspec)
        fspec.loader.exec_module(FC)
        rnd = random.Random(1)
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "log.jsonl"
            with open(p, "w") as f:
                for _ in range(400):                    # logits 3x mas seguros de lo que aciertan
                    lab = rnd.choice("abc")
                    raw = {k: rnd.gauss(0, 1) * 3 + (2.4 if k == lab else 0) for k in "abc"}
                    f.write(json.dumps({"passes": [{"raw": raw}], "label": lab}) + "\n")
                f.write(json.dumps({"passes": [{"raw": {"a": 0, "b": 0}}], "label": None}) + "\n")
            recs = FC.load(str(p))
            table = FC.fit(recs)
        self.assertEqual(len(recs), 400)                # la linea sin label no cuenta
        t1 = next(r for r in table if r["T"] == 1.0)
        self.assertGreater(table[0]["T"], 1.0)
        self.assertLess(table[0]["nll"], t1["nll"])
        self.assertLess(table[0]["ece"], t1["ece"])


if __name__ == "__main__":
    unittest.main()
