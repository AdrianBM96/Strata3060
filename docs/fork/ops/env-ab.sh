#!/usr/bin/env bash
# A/B generico de una variable de entorno del motor Strata. Uso: env-ab.sh STRATA_VAR [pasadas]
# Alterna off / on / off / on (control de deriva termica), reiniciando el servicio en cada brazo,
# y mide decode B1 (1024 tok, razonamiento alto). Deja la variable retirada al final.
set -uo pipefail
F=/home/bazzite/Strata
VAR="${1:?uso: env-ab.sh STRATA_VAR [pasadas]}"
N="${2:-3}"
DROPIN="$HOME/.config/systemd/user/strata.service.d/ab-${VAR}.conf"
URL=http://127.0.0.1:8081

one() { bash "$F/bench/bench-chat.sh" "$URL" "$1" 1024 high 2>&1 | grep decode | grep -oE "[0-9.]+ tok/s"; }

arm() { # $1 on|off  $2 etiqueta
  if [ "$1" = on ]; then
    mkdir -p "$(dirname "$DROPIN")"; printf '[Service]\nEnvironment=%s=1\n' "$VAR" > "$DROPIN"
  else
    rm -f "$DROPIN"
  fi
  systemctl --user daemon-reload; systemctl --user restart strata.service; sleep 4
  bash "$F/bench/bench-chat.sh" "$URL" warm 256 high >/dev/null 2>&1
  printf "  %-5s" "$2"
  for i in $(seq 1 "$N"); do printf " %s" "$(one "$2-$i")"; done
  printf "  %sC\n" "$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader)"
}

echo "=== A/B de $VAR ($N pasadas por brazo) ==="
arm off off1; arm on on1; arm off off2; arm on on2
rm -f "$DROPIN"; systemctl --user daemon-reload; systemctl --user restart strata.service
echo "=== fin; $VAR retirada (estado original) ==="
