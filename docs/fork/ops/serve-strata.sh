#!/usr/bin/env bash
# Lanza el servidor Strata con el modelo elegido en .current-model.
# Lo invoca el servicio systemd de usuario (strata.service).
#
# Strata y los modelos de llama.cpp comparten los 12 GB de la RTX 3060: solo
# uno puede estar cargado. Este script ESPERA a que haya VRAM libre en vez de
# morir en un crash-loop de cudaMalloc, asi que se puede dejar el servicio
# siempre habilitado: en cuanto :8080 suelta su modelo, Strata carga solo.
set -uo pipefail
F=/home/bazzite/Strata

# Expertos int8 fusionados en el prompt path (moe_fused_iq, pack nativo): medido en la 3060 +5,2% de
# lectura de prompt a 12,3K tokens, con la salida identica al camino MMQ (docs/fork/MEDICION_RONDA10.md).
# STRATA_PF_FUSED=0 lo apaga (A/B). El motor hereda el entorno de server.py (env = dict(os.environ)).
export STRATA_PF_FUSED="${STRATA_PF_FUSED:-1}"

NEED_MIB=${STRATA_NEED_VRAM_MIB:-6500}
M=$(cat "$F/.current-model" 2>/dev/null || echo swift-iq2_xs)
CFG="$F/strata-${M}.json"
[ -f "$CFG" ] || { echo "no existe $CFG (modelo: $M)"; exit 1; }

# El subconjunto de borradores del config ("draft_vocab": "en" | "cjk" | "cyrillic"): setup lo copia al
# arrancar con START/setup.sh, pero este script lanza server.py directamente, asi que lo hace aqui con la
# misma funcion de setup (solo copia los ficheros que vienen en data/; uno hecho a mano se respeta).
"$F/.venv/bin/python" - "$F" "$CFG" <<'PY' || echo "[strata] aviso: no se pudo refrescar draft_vocab" >&2
import json, sys
from pathlib import Path
root, cfg_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
import setup
cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8-sig"))
args = cfg.get("args", [])
if "--mtp" in args[:-1]:
    setup.refresh_draft_vocab(Path(args[args.index("--mtp") + 1]), cfg.get("draft_vocab", "cjk"))
PY

free_mib() { nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1; }

# Vision y --lazy son incompatibles ("lazy loading is text-only"): con el codificador
# de imagenes hay que cargar el modelo al arrancar, y para eso hay que dejarle VRAM
# antes. Sin vision, --lazy permite que Strata decida con before_load/min_free_vram.
HAS_VISION=$(python3 -c "
import json,sys
try: d=json.load(open('$CFG'))
except Exception: print('no'); raise SystemExit
print('yes' if d.get('vision') else 'no')")

if [ "$HAS_VISION" = "yes" ]; then
  "$F/free-vram.sh" >&2
  echo "[strata] $M con VISION: carga al arrancar (sin --lazy); libre: $(( $(free_mib) )) MiB" >&2
  cd "$F"
  exec "$F/.venv/bin/python" "$F/serve/server.py" \
    --engine strata --config "$CFG" --port 8081 \
    --idle-unload "${STRATA_IDLE_UNLOAD:-0}"
fi

echo "[strata] servidor arriba en :8081 (modelo $M en carga perezosa); libre ahora: $(( $(free_mib) )) MiB" >&2
cd "$F"
IDLE=${STRATA_IDLE_UNLOAD:-0}
exec "$F/.venv/bin/python" "$F/serve/server.py" \
  --engine strata --config "$CFG" --port 8081 \
  --lazy \
  --idle-unload "$IDLE"
