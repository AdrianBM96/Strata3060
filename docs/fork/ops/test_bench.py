"""Tests de bench.py sin GPU: un servidor de Strata y un ada-decide simulados, y la estadística.

    python3 docs/fork/ops/test_bench.py
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location("bench", Path(__file__).with_name("bench.py"))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)


class Fake(BaseHTTPRequestHandler):
    max_context = 65536
    seen: list = []

    def log_message(self, *a):
        pass

    def _send(self, d):
        b = json.dumps(d).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send({"status": "ok", "max_context": self.max_context, "model": "fake"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.seen.append((self.path, body))
        if self.path == "/v1/systemone":
            return self._send({"answers": {k: {"choice": "x"} for k in body["questions"]}})
        n = sum(len(m["content"]) for m in body["messages"]) // 4
        self._send({"usage": {"prompt_tokens": n, "completion_tokens": body["max_tokens"]},
                    "timings": {"cache_n": 0, "prompt_n": n, "prompt_per_second": 900.0,
                                "predicted_per_second": 40.0, "predicted_n": body["max_tokens"]}})


class Run(unittest.TestCase):
    def test_suite_against_fake_servers(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_port}"
        try:
            with tempfile.TemporaryDirectory() as t:
                out = str(Path(t) / "b.jsonl")
                B.main(["run", "--server", url, "--decide", url, "--tag", "A", "--runs", "2", "--bulk", "3",
                        "--tests", "B1,B2,B3,P1,S1,S2,S3", "--out", out])
                rows = B.load(out)
        finally:
            srv.shutdown()
            srv.server_close()
        by = {(r["test"], r["run"]): r for r in rows}
        self.assertEqual(len(rows), 14)
        self.assertEqual(by[("B1", 0)]["decode_tps"], 40.0)
        self.assertEqual(by[("P1", 1)]["prompt_tps"], 900.0)
        self.assertIn("skipped", by[("B3", 0)])                      # 128K no cabe en 64K
        self.assertGreater(by[("S2", 0)]["first_s"], 0)
        self.assertEqual(by[("S3", 0)]["items"], 3)
        # B2: el contexto se lee una vez sin medir y luego el mismo prompt con 512 tokens
        b2 = [b for p, b in Fake.seen if p == "/v1/chat/completions" and b["max_tokens"] in (1, 512)
              and "coding agent" in b["messages"][0]["content"]]
        self.assertTrue(any(b["max_tokens"] == 512 for b in b2))
        # S2 pregunta cosas distintas de S1 sobre el mismo estado
        s = [b for p, b in Fake.seen if p == "/v1/systemone"]
        firsts = [x for x in s if "department" in x["questions"] and len(x["questions"]) == 4]
        seconds = [x for x in s if "security" in x["questions"]]
        self.assertTrue(firsts and seconds)
        self.assertEqual(firsts[-1]["state"], seconds[-1]["state"])
        # los prompts nuevos llevan nonce: dos pasadas de P1 no comparten el principio
        p1 = [b["messages"][0]["content"][:30] for p, b in Fake.seen
              if p == "/v1/chat/completions" and "Summarize in one line" in b["messages"][-1]["content"]]
        self.assertEqual(len(set(p1)), len(p1))


class Stats(unittest.TestCase):
    def test_mann_whitney(self):
        self.assertLess(B.mann_whitney_p([1, 2, 3, 4, 5], [6, 7, 8, 9, 10]), 0.02)   # exacto: 0,008
        self.assertGreater(B.mann_whitney_p([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]), 0.9)

    def test_compare_verdicts(self):
        rows = [{"tag": "A", "test": "B1", "decode_tps": v} for v in (39.6, 40.1, 40.3, 39.9, 40.0, 40.2)]
        rows += [{"tag": "B", "test": "B1", "decode_tps": v} for v in (42.0, 42.4, 41.9, 42.2, 42.6, 42.1)]
        rows += [{"tag": "A", "test": "S2", "wall_s": v} for v in (1.5, 1.6, 1.4, 1.5, 1.55, 1.45)]
        rows += [{"tag": "B", "test": "S2", "wall_s": v} for v in (1.5, 1.4, 1.6, 1.45, 1.55, 1.5)]
        res = {r["test"]: r for r in B.compare(rows, "A", "B")}
        self.assertEqual(res["B1"]["verdict"], "B mejor")
        self.assertEqual(res["S2"]["verdict"], "sin diferencia medible")
        rows += [{"tag": "B", "test": "S1", "wall_s": 2.0}, {"tag": "A", "test": "S1", "wall_s": 1.0},
                 {"tag": "A", "test": "S1", "wall_s": 1.1}, {"tag": "B", "test": "S1", "wall_s": 2.1}]
        r = {x["test"]: x for x in B.compare(rows, "A", "B")}["S1"]
        self.assertEqual(r["verdict"], "B peor")                      # más segundos es peor


if __name__ == "__main__":
    unittest.main()
