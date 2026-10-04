#!/usr/bin/env python3
"""A/B de STRATA_PF_FUSED con PUERTA DE CALIDAD.
Velocidad: prefill a ~12K tokens (prompt unico -> cache miss real).
Calidad: el MISMO prompt largo (>1024 tok, dispara el camino fusionado) con temperatura 0,
reiniciando el servicio entre brazos (la cache de conversacion es en memoria) y comparando
la salida exacta. Alterna off / on / off / on."""
import json, os, subprocess, time, urllib.request, random, string, hashlib

URL = "http://127.0.0.1:8081"
DROPIN = "/home/bazzite/.config/systemd/user/strata.service.d/pffused.conf"
OUT = "/home/bazzite/Strata/bench"

def set_env(on):
    os.makedirs(os.path.dirname(DROPIN), exist_ok=True)
    if on:
        open(DROPIN, "w").write("[Service]\nEnvironment=STRATA_PF_FUSED=1\n")
    else:
        if os.path.exists(DROPIN): os.remove(DROPIN)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "restart", "strata.service"], check=True)
    time.sleep(4)

def post(prompt, max_tokens):
    req = urllib.request.Request(URL + "/v1/chat/completions",
        data=json.dumps({"model": "strata",
                         "messages": [{"role": "user", "content": prompt}],
                         "max_tokens": max_tokens, "temperature": 0}).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=3600))

def prefill(nchars, label):
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    para = ("The quick brown fox jumps over the lazy dog while engineers measure "
            "the prompt reading speed of a local model. ")
    body = (para * (nchars // len(para) + 1))[:nchars]
    d = post(f"[{nonce}]\n{body}\n\nReply with the single word: ok", 1)
    t = d.get("timings", {}) or {}
    print(f"  {label:8s} prompt_n={t.get('prompt_n')} prefill={t.get('prompt_per_second',0):.1f} tok/s", flush=True)

def code_prompt():
    filler = "".join(f"# helper {i}: stage {i} of the pipeline\n" for i in range(600))
    return ("Here is a Python file:\n```python\n" + filler + "\n```\n"
            "Now write ONLY the body of `def add(a, b):` that returns a + b. "
            "Output just the code, no explanation.\n")

def quality(label):
    d = post(code_prompt(), 100)
    m = d["choices"][0]["message"]
    txt = m.get("content") or m.get("reasoning_content") or ""
    path = f"{OUT}/pffused-{label}.txt"
    open(path, "w").write(txt)
    h = hashlib.sha256(txt.encode()).hexdigest()[:12]
    first = (txt.strip().splitlines() or ["(vacio)"])[0][:50]
    print(f"  quality {label:5s}: {len(txt):3d} chars sha={h} 1a: {first}", flush=True)
    return h

def arm(on, label):
    set_env(on)
    print(f"===== BRAZO {label} =====", flush=True)
    prefill(6000, "warm")
    for i in (1, 2, 3):
        prefill(60000, f"{label}-{i}")
    return quality(label)

hashes = {}
hashes["off1"] = arm(False, "off1")
hashes["on1"] = arm(True, "on1")
hashes["off2"] = arm(False, "off2")
hashes["on2"] = arm(True, "on2")
set_env(False)
print("===== fin; drop-in retirado =====", flush=True)
print("hashes:", json.dumps(hashes), flush=True)
print("CALIDAD: " + ("IDENTICA entre brazos" if len(set(hashes.values())) == 1
                      else f"DIFIEREN ({len(set(hashes.values()))} salidas distintas)"), flush=True)
