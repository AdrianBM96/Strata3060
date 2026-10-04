#!/usr/bin/env bash
# A/B del CPU assist en prefill (STRATA_PREFILL_CPU; default ON en CUDA). Alterna off/on/off/on.
# La ganancia es en prompts CORTOS (<3072 tok), asi que se mide el prefill con bench-prefill.py.
set -uo pipefail
F=/home/bazzite/Strata
PY="$F/.venv/bin/python"
DROPIN="$HOME/.config/systemd/user/strata.service.d/cpuassist.conf"
URL=http://127.0.0.1:8081
CHARS="${CHARS:-9000}"   # ~2.2K tokens: dentro del rango del CPU assist

prefill() { "$PY" "$F/bench/bench-prefill.py" "$URL" "$CHARS" "$1"; }

arm() { # $1 on|off  $2 etiqueta
  if [ "$1" = off ]; then
    mkdir -p "$(dirname "$DROPIN")"; printf '[Service]\nEnvironment=STRATA_PREFILL_CPU=0\n' > "$DROPIN"
  else rm -f "$DROPIN"; fi
  systemctl --user daemon-reload; systemctl --user restart strata.service; sleep 4
  "$PY" "$F/bench/bench-prefill.py" "$URL" 4000 "warm-$2" >/dev/null 2>&1
  echo "  --- brazo $2 ---"
  for i in 1 2 3; do prefill "$2-$i"; done
  printf "  temp: %sC\n" "$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader)"
}

echo "=== A/B CPU assist (CHARS=$CHARS) ==="
arm on on1; arm off off1; arm on on2; arm off off2
rm -f "$DROPIN"; systemctl --user daemon-reload; systemctl --user restart strata.service
echo "=== fin; CPU assist por defecto (ON) ==="
