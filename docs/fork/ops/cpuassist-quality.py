#!/usr/bin/env python3
"""Puerta de calidad de STRATA_PF_FUSED (metodo de Claude, RESPUESTA_RONDA11 §2).

PF_FUSED solo actua en el camino por LOTES del prompt (>1024 tok); STRATA_LOGPOS escribe los
tokens que pasan por las VENTANAS. Se monta en dos peticiones con --short-read 4096:
  1) contexto largo (~9K tok), max_tokens=1  -> por lotes (aqui actua el flag)
  2) mismo contexto + continuacion fija (~2K tok), max_tokens=1 -> por ventanas; cada logprob
     depende del estado que dejo la lectura por lotes (cache de conversacion).
Brazos: A (PF_FUSED=0), B (PF_FUSED=1), A2 (PF_FUSED=0, suelo de ruido).
Compara:  python3 logpos-compare.py A.tsv B.tsv --floor A2.tsv
"""
import json, os, subprocess, time, urllib.request

F = "/home/bazzite/Strata"
URL = "http://127.0.0.1:8081"
BENCH = f"{F}/bench"
DROPIN = "/home/bazzite/.config/systemd/user/strata.service.d/quality.conf"
CFG_REAL = f"{F}/strata-swift-iq2_xs.json"
CFG_TEST = f"{F}/strata-swift-iq2_xs-test.json"
CUR = f"{F}/.current-model"

def build_context():
    code = "".join(f"def stage_{i}(x):\n    return x * {i} + {i*7 % 13}\n" for i in range(80))
    prose = ("A mixture-of-experts model routes each token to a few experts; the rest stay idle. "
             "The router is a small linear layer, and the expert cache holds the most used ones in VRAM. ") * 35
    return "```python\n" + code + "```\n\n" + prose

def build_continuation():
    return "".join(f"# continuation line {i}: the pipeline keeps reading the file\n" for i in range(80))

def make_test_config():
    d = json.loads(open(CFG_REAL, encoding="utf-8-sig").read())
    a = d["args"]
    if "--short-read" in a:
        a[a.index("--short-read") + 1] = "1500"
    else:
        a += ["--short-read", "4096"]
    if "--adapt-every" not in a:
        a += ["--adapt-every", "100000"]
    d["args"] = a
    open(CFG_TEST, "w").write(json.dumps(d, indent=2))
    open(CUR, "w").write("swift-iq2_xs-test\n")

def set_arm(pf, tsv):
    os.makedirs(os.path.dirname(DROPIN), exist_ok=True)
    open(DROPIN, "w").write(
        "[Service]\n"
        f"Environment=STRATA_PREFILL_CPU={pf}\n"
        f"Environment=STRATA_LOGPOS={tsv}\n"
        "Environment=STRATA_LOGPOS_TOPK=20\n")
    if os.path.exists(tsv):
        os.remove(tsv)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "restart", "strata.service"], check=True)
    time.sleep(5)

def post(messages, mt=1):
    req = urllib.request.Request(URL + "/v1/chat/completions",
        data=json.dumps({"model": "strata", "messages": messages,
                         "max_tokens": mt, "temperature": 0}).encode(),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=3600))

def lines(path):
    try:
        return sum(1 for _ in open(path, encoding="utf-8"))
    except FileNotFoundError:
        return 0

ctx, cont = build_context(), build_continuation()
print(f"contexto ~{len(ctx)} chars, continuacion ~{len(cont)} chars", flush=True)

def arm(name, pf):
    tsv = f"{BENCH}/cpuassist-{name}.tsv"
    set_arm(pf, tsv)
    print(f"===== brazo {name} (CPU assist={pf}) =====", flush=True)
    r1 = post([{"role": "user", "content": ctx}], 1)
    t1 = r1.get("timings", {}) or {}
    print(f"  peticion 1 (lotes): prompt_n={t1.get('prompt_n')} cache_n={t1.get('cache_n')}", flush=True)
    # peticion 2: MISMA conversacion + la continuacion como mensaje del asistente (teacher-forcing).
    # El prefijo [user: ctx] sale de la cache; la continuacion es un trozo corto -> por VENTANAS.
    r2 = post([{"role": "user", "content": ctx},
               {"role": "assistant", "content": cont}], 1)
    t2 = r2.get("timings", {}) or {}
    print(f"  peticion 2 (ventanas): prompt_n={t2.get('prompt_n')} cache_n={t2.get('cache_n')}  logpos={lines(tsv)} lineas", flush=True)
    return tsv

make_test_config()
import sys
want = sys.argv[1:] if len(sys.argv) > 1 else ["A", "B", "A2"]
try:
    res = {}
    if "A" in want:
        res["A"] = arm("A", "0")
    if "B" in want:
        res["B"] = arm("B", "auto")
    if "A2" in want:
        res["A2"] = arm("A2", "0")
finally:
    # restaurar
    if os.path.exists(DROPIN):
        os.remove(DROPIN)
    open(CUR, "w").write("swift-iq2_xs\n")
    if os.path.exists(CFG_TEST):
        os.remove(CFG_TEST)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "restart", "strata.service"], check=True)
print("===== fin; config y drop-in restaurados =====", flush=True)
print("ficheros:", res, flush=True)
