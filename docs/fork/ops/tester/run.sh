#!/usr/bin/env bash
# Batería tester C19 (rev.2): HUMO (=T8) o CARGA (=T1-T7). Uso: run.sh HUMO|CARGA
# - Sandbox /tmp/tester-sbx recreado desde plantilla/ antes de CADA prueba.
# - Sesión nueva de pi antes de cada prueba (/new): aislamiento real.
# - Cada prompt ordena escribir la respuesta final en RESPUESTA.txt; se comprueba ESE fichero
#   (recién creado: sin falsos positivos de la pantalla).
# - T7 por curl directo a :4000 (imagen en base64); herdr agent prompt es solo texto.
# - Tiempos reales medidos alrededor de la llamada. Resultados en ops/tester/resultados/.
# PROHIBIDO correr CARGA a la vez que cualquier benchmark (comparten el motor).
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
SBX=/tmp/tester-sbx
LOCK=/tmp/strata-motor.lock
if [ -e "$LOCK" ]; then echo "CANDADO activo: espera a que termine."; exit 1; fi
{
echo "quien=run.sh $1"
echo "juego=$1"
echo "inicio=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo -n "config="; md5sum /home/bazzite/Strata/engine/strata 2>/dev/null | cut -d' ' -f1
} > "$LOCK"
trap 'rm -f "$LOCK"' EXIT
RES="$DIR/resultados/$(date -u +%Y%m%d-%H%M)-$1.md"
mkdir -p "$DIR/resultados"
LITELLM_KEY="${LITELLM_KEY:-sk-ada-local-2026}"

PROMPT_T1='Si 3x+7=22, ¿cuánto vale x? Escribe tu respuesta final en /tmp/tester-sbx/RESPUESTA.txt (solo el número, sin nada más).'
PROMPT_T2='Explica en unas 300 palabras qué hace el fichero /tmp/tester-sbx/fib.py. Solo prosa, sin código. Escribe tu respuesta final en /tmp/tester-sbx/RESPUESTA.txt.'
PROMPT_T3='En /tmp/tester-sbx escribe mcd.py con una función mcd(a, b) (algoritmo de Euclides) y test_mcd.py con 3 tests unittest. Ejecuta los tests. Escribe tu respuesta final en /tmp/tester-sbx/RESPUESTA.txt (una línea: pasan o fallan).'
PROMPT_T4='En /tmp/tester-sbx/fib.py hay una función fib. Crea /tmp/tester-sbx/fib_par.py con la misma función renombrada a fib_par, idéntica por lo demás. Escribe tu respuesta final en /tmp/tester-sbx/RESPUESTA.txt (una línea: hecho).'
PROMPT_T5='En /tmp/tester-sbx/fib.py: 1) lee el fichero, 2) busca la línea con "return a", 3) añade al final la línea "# revisado", 4) muestra el fichero. Escribe tu respuesta final en /tmp/tester-sbx/RESPUESTA.txt (una línea: hecho).'
PROMPT_T6='El fichero /tmp/tester-sbx/grande.py define 2000 funciones f0000 a f1999. Escribe tu respuesta final en /tmp/tester-sbx/RESPUESTA.txt (una línea con cuántas hay, la primera y la última).'
PROMPT_T8='Lee el fichero /tmp/tester-sbx/fib.py. Escribe tu respuesta final en /tmp/tester-sbx/RESPUESTA.txt (solo lo que devuelve fib(10), sin nada más).'

TIMEOUT_T1=120000; TIMEOUT_T2=180000; TIMEOUT_T3=240000; TIMEOUT_T4=180000
TIMEOUT_T5=240000; TIMEOUT_T6=300000; TIMEOUT_T8=120000

recreate() { rm -rf "$SBX"; cp -r "$DIR/plantilla" "$SBX"; }
fresh_session() { herdr agent prompt tester "/new" >/dev/null 2>&1; sleep 3; }

run_one() { # $1 id  $2 timeout_ms  $3 prompt
  local id="$1" to="$2" prompt="$3" t0 t1 ok=0 detail="" r
  recreate
  fresh_session
  t0=$(date +%s)
  herdr agent prompt tester "$prompt" >/dev/null 2>&1
  herdr agent wait tester --until working --timeout 60000 >/dev/null 2>&1  # arranca (si sigue idle, el siguiente expira igual)
  if herdr agent wait tester --timeout "$to" >/dev/null 2>&1; then
    r="$SBX/RESPUESTA.txt"
    case "$id" in
      T1) [ -f "$r" ] && [ "$(tr -d ' \n\r' < "$r")" = "5" ] && ok=1; detail="RESPUESTA==5";;
      T2) if [ -f "$r" ] && grep -qi "fibonacci" "$r" && [ "$(wc -w < "$r")" -gt 100 ]; then ok=1; fi; detail="Fibonacci, >100 palabras";;
      T3) if [ -f "$SBX/mcd.py" ] && [ -f "$SBX/test_mcd.py" ] && python3 -m pytest -q "$SBX/test_mcd.py" >/dev/null 2>&1 && [ -f "$r" ]; then ok=1; fi; detail="mcd+test existen, pytest pasa";;
      T4) grep -q "def fib_par(n):" "$SBX/fib_par.py" 2>/dev/null && grep -q "a, b = b, a + b" "$SBX/fib_par.py" 2>/dev/null && ok=1; detail="fib_par renombrada";;
      T5) [ "$(tail -1 "$SBX/fib.py" 2>/dev/null)" = "# revisado" ] && ok=1; detail="última línea '# revisado'";;
      T6) [ -f "$r" ] && grep -q "2000" "$r" && grep -q "f0000" "$r" && grep -q "f1999" "$r" && ok=1; detail="2000+f0000+f1999";;
      T8) [ -f "$r" ] && [ "$(tr -d ' \n\r' < "$r")" = "55" ] && ok=1; detail="RESPUESTA==55";;
    esac
  else
    detail="timeout/fallo en prompt"
  fi
  t1=$(date +%s)
  echo "$id|$ok|$((t1-t0))s|$detail"
}

run_t7() { # visión por curl directo a :4000
  local t0 t1 ok=0 b64 ans
  recreate
  t0=$(date +%s)
  b64=$(base64 -w0 "$SBX/imagen.png")
  ans=$(curl -s --max-time 280 http://127.0.0.1:4000/v1/chat/completions \
    -H "Authorization: Bearer $LITELLM_KEY" -H 'Content-Type: application/json' \
    -d "{\"model\":\"ada-next\",\"max_tokens\":60,\"temperature\":0,\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"What text is in the image? Only the text.\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/png;base64,$b64\"}}]}]}" \
    | python3 -c "import sys,json;print((json.load(sys.stdin)['choices'][0]['message'].get('content') or '')[:200])" 2>/dev/null)
  echo "$ans" | grep -q "MEN WALK ON MOON" && ok=1
  t1=$(date +%s)
  echo "T7|$ok|$((t1-t0))s|respuesta contiene MEN WALK ON MOON"
}

{
echo "# Resultado $1 — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo
if [ "$1" = HUMO ]; then
  r=$(run_one "T8" "$TIMEOUT_T8" "$PROMPT_T8")
  id="${r%%|*}"; rest="${r#*|}"
  echo "- $id: $([ "${rest%%|*}" = 1 ] && echo PASA || echo FALLA) ($rest)"
else
  for t in T1 T2 T3 T4 T5 T6; do
    pv="PROMPT_$t"; tv="TIMEOUT_$t"
    r=$(run_one "$t" "${!tv}" "${!pv}")
    id="${r%%|*}"; rest="${r#*|}"
    echo "- $id: $([ "${rest%%|*}" = 1 ] && echo PASA || echo FALLA) ($rest)"
  done
  r=$(run_t7)
  id="${r%%|*}"; rest="${r#*|}"
  echo "- $id: $([ "${rest%%|*}" = 1 ] && echo PASA || echo FALLA) ($rest)"
fi
} | tee "$RES"
echo "guardado en $RES"
