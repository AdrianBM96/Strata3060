#!/usr/bin/env python3
"""Banco de PREFILL: manda un prompt largo (unico, para que no acierte en la cache de
conversacion) y lee los timings del servidor (nombres de llama.cpp). Uso:
  bench-prefill.py URL NCHARS [etiqueta]
NCHARS ~ caracteres; ~4 car/token, asi que 128000 ~ 32K tokens."""
import json, sys, random, string, urllib.request

url = sys.argv[1].rstrip("/")
nchars = int(sys.argv[2])
label = sys.argv[3] if len(sys.argv) > 3 else ""

nonce = "".join(random.choices(string.ascii_letters + string.digits, k=16))
para = ("The quick brown fox jumps over the lazy dog while the engineers measure "
        "the prompt reading speed of a local language model on a gaming card. ")
body = (para * (nchars // len(para) + 1))[:nchars]
prompt = f"[{nonce}]\n{body}\n\nReply with the single word: ok"

req = urllib.request.Request(
    f"{url}/v1/chat/completions",
    data=json.dumps({"model": "strata",
                     "messages": [{"role": "user", "content": prompt}],
                     "max_tokens": 1, "temperature": 0}).encode(),
    headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=3600) as r:
    d = json.load(r)
t = d.get("timings", {}) or {}
pn = t.get("prompt_n")
pps = t.get("prompt_per_second")
cn = t.get("cache_n")
print(f"{label:8s} prompt_n={pn} cache_n={cn} prefill={pps:.1f} tok/s"
      if pps is not None else f"{label:8s} sin timings: {json.dumps(t)}")
