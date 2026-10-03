#!/usr/bin/env bash
# strata-switch.sh <swift|coder|qwen|stop|status>
# Cambia el modelo de Strata que sirve ada-next (litellm :4000 -> :8081).
#
# IMPORTANTE: Strata y los modelos de llama.cpp se pelean por los 12 GB de VRAM.
# Solo uno puede estar cargado a la vez. Antes de arrancar Strata, comprueba que
# el router :8080 no tiene un modelo residency; si lo tiene, pidele que lo suelte.
set -euo pipefail
F=/home/bazzite/Strata
declare -A MAP=([swift]=swift-iq2_xs [swift262]=swift-iq2_xs-262k [coder]=coder-iq1_m [qwen]=iq2_xs)
case "${1:-status}" in
  stop)
    systemctl --user stop strata.service
    echo "Strata parado (litellm caera a ada-ethos por fallback)"
    ;;
  status)
    echo "modelo configurado: $(cat "$F/.current-model" 2>/dev/null || echo '?')"
    systemctl --user is-active strata.service || true
    curl -s --max-time 3 http://127.0.0.1:8081/health || echo "sin respuesta en :8081"
    echo
    ;;
  swift|swift262|coder|qwen)
    M=${MAP[$1]}
    [ -f "$F/strata-$M.json" ] || { echo "falta strata-$M.json"; exit 1; }
    USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits)
    if [ "$USED" -gt 2000 ]; then
      echo "AVISO: hay ${USED} MiB de VRAM ocupados (un modelo de llama.cpp sigue cargado)."
      echo "       Swapea a un modelo pequeno o para ese server antes de arrancar Strata."
    fi
    echo "$M" > "$F/.current-model"
    systemctl --user restart strata.service
    echo "Strata arrancando con $M (carga ~25 s). ada-next disponible en :4000"
    ;;
  *) echo "uso: $0 <swift|swift262|coder|qwen|stop|status>"; exit 1;;
esac
