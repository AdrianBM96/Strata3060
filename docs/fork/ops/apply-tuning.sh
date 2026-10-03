#!/usr/bin/env python3
"""Reaplica nuestro tuneo a TODOS los configs de Strata.

setup.py reescribe strata-<model>.json cada vez que se ejecuta y borra las claves
nuestras (lo advierte su documentacion). Este script las vuelve a poner:

  - before_load        : aparta el modelo de llama.cpp antes de cargar
  - min_free_vram_mib  : 503 en vez de cudaMalloc fallido
  - expert_profile_save: aprende el perfil de expertos de nuestro uso
  - conversation-cache : parking de 8 GiB / 4 conversaciones (multi-cliente)
  - ENGINE_ARGS        : --kv int8 (precision gratis, medido), --prompt-cache-root 256 (System One),
                         --logprobs 32 (System One; necesita el motor con el parche)
  - draft_vocab "en"   : ~110 MiB de VRAM para expertos (serve-strata.sh copia el fichero)

Uso: ./apply-tuning.sh
"""
import json
import glob

ROOT = "/home/bazzite/Strata"
BEFORE = f"{ROOT}/free-vram.sh"

KEYS = {
    "before_load": BEFORE,
    "min_free_vram_mib": 10500,
}
ARGS_TAIL = ["--conversation-cache-mib", "8192",
             "--conversation-cache-slots", "4"]
# flags del motor que setup.py no escribe (o escribe con otro valor) y que hay que reponer
ENGINE_ARGS = {"--kv": "int8", "--prompt-cache-root": "256", "--logprobs": "32"}
DRAFT_VOCAB = "en"


def set_arg(args, flag, value):
    """`flag value` en args: cambia el valor si ya esta, lo añade si no."""
    if flag in args[:-1]:
        args[args.index(flag) + 1] = value
    else:
        args += [flag, value]

PROFILE = {
    "strata-swift-iq2_xs.json": "expert-profile-learned-swift.bin",
    "strata-coder-iq1_m.json": "expert-profile-learned-coder.bin",
    "strata-iq2_xs.json": "expert-profile-learned-qwen.bin",
    "strata-swift-iq2_xs-262k.json": "expert-profile-learned-swift-262k.bin",
}

changed = 0
for f in sorted(glob.glob(f"{ROOT}/strata-*.json")):
    name = f.rsplit("/", 1)[-1]
    try:
        d = json.load(open(f))
    except Exception as e:
        print(f"  saltado {name}: {e}")
        continue
    before = json.dumps(d, sort_keys=True)

    for k, v in KEYS.items():
        d[k] = v
    d["draft_vocab"] = DRAFT_VOCAB
    if name in PROFILE:
        d["expert_profile_save"] = PROFILE[name]
        d["expert_profile_save_every"] = 5

    a = d.get("args", [])
    # conservar --vision (es un flag REAL del motor: "--serve takes images too").
    # Solo se limpian duplicados de las opciones que gestionamos aqui.
    clean = []
    i = 0
    while i < len(a):
        if a[i] in ("--conversation-cache-mib", "--conversation-cache-slots"):
            i += 2
            continue
        clean.append(a[i])
        i += 1
    for flag, value in ENGINE_ARGS.items():
        set_arg(clean, flag, value)
    d["args"] = clean + ARGS_TAIL

    if json.dumps(d, sort_keys=True) != before:
        json.dump(d, open(f, "w"), indent=1)
        changed += 1
        print(f"  {name}: tuneo aplicado")
    else:
        print(f"  {name}: ya estaba")

print(f"listo ({changed} cambiados)")
