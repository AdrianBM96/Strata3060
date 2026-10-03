#!/usr/bin/env bash
# free-vram.sh — suelta la GPU que tenga el router de llama.cpp (:8080).
# Lo llama Strata antes de cargar el modelo ("before_load" en strata-<model>.json),
# para que el swap funcione aunque litellm no este delante.
#
# Mata SOLO a los hijos del router (binario llama-prism) que NO son el proceso
# padre (--models-preset) ni el 1.5B de :8082 (beellama/llama-server).
me=$$
killed=0
for d in /proc/[0-9]*; do
  pid=${d#/proc/}
  [ "$pid" = "$me" ] && continue
  cl=$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null) || continue
  case "$cl" in
    *llama-prism*llama-server*)
      case "$cl" in
        *--models-preset*) ;;          # el router padre: no se toca
        *) kill -9 "$pid" 2>/dev/null && killed=$((killed+1)) ;;
      esac
      ;;
  esac
done
echo "[free-vram] modelos de llama.cpp apartados: $killed"
