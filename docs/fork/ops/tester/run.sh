#!/usr/bin/env bash
# Batería tester: HUMO (=T8) o CARGA (=T1-T7). Uso: run.sh HUMO|CARGA
# Recrea /tmp/tester-sbx desde plantilla/ antes de CADA prueba, manda el prompt a
# tester por herdr, espera, comprueba y escribe ops/tester/resultados/<fecha>-<juego>.md.
# PROHIBIDO correr CARGA a la vez que cualquier benchmark (comparten el motor).
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
SBX=/tmp/tester-sbx
LOCK=/tmp/strata-motor.lock
# N6 CANDADO DEL MOTOR: nadie toca el motor mientras tester corre (y viceversa).
if [ -e "$LOCK" ]; then echo "CANDADO activo ($(cat "$LOCK" 2>/dev/null | head -1)): espera a que termine."; exit 1; fi
{
echo "quien=run.sh $1"
echo "juego=$1"
echo "inicio=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo -n "config="; md5sum /home/bazzite/Strata/engine/strata 2>/dev/null | cut -d' ' -f1
} > "$LOCK"
trap 'rm -f "$LOCK"' EXIT
RES="$DIR/resultados/$(date -u +%Y%m%d-%H%M)-$1.md"
mkdir -p "$DIR/resultados"

PROMPT_T1='Si 3x+7=22, ¿cuánto vale x? Responde solo con el número, sin nada más.'
PROMPT_T2='Explica en unas 300 palabras qué hace el fichero /tmp/tester-sbx/fib.py. Solo prosa, sin código.'
PROMPT_T3='En /tmp/tester-sbx escribe mcd.py con una función mcd(a, b) (algoritmo de Euclides) y test_mcd.py con 3 tests unittest. Ejecuta los tests y dime si pasan.'
PROMPT_T4='En /tmp/tester-sbx/fib.py hay una función fib. Crea /tmp/tester-sbx/fib_par.py con la misma función renombrada a fib_par, idéntica por lo demás. No cambies nada más.'
PROMPT_T5='En /tmp/tester-sbx/fib.py: 1) lee el fichero, 2) busca la línea con "return a", 3) añade al final la línea "# revisado", 4) muestra el fichero. Confirma cada paso.'
PROMPT_T6='El fichero /tmp/tester-sbx/grande.py define 2000 funciones f0000 a f1999. Dime: ¿cuántas hay y cómo se llaman la primera y la última? Responde en una línea.'
PROMPT_T8='Lee el fichero /tmp/tester-sbx/fib.py y dime en una línea qué devuelve fib(10).'

TIMEOUT_T1=120000; TIMEOUT_T2=180000; TIMEOUT_T3=240000; TIMEOUT_T4=180000
TIMEOUT_T5=240000; TIMEOUT_T6=300000; TIMEOUT_T8=120000

recreate() { rm -rf "$SBX"; cp -r "$DIR/plantilla" "$SBX"; }
out() { herdr agent read tester --lines 40 --format text 2>/dev/null; }

run_one() { # $1 id  $2 timeout_ms  $3 prompt_var
  local id="$1" to="$2" prompt="$3" t0 t1 ok=0 detail=""
  recreate
  t0=$(date +%s)
  herdr agent prompt tester "$prompt" >/dev/null 2>&1
  herdr agent wait tester --until working --timeout 60000 >/dev/null 2>&1  # arranca de verdad (si sigue idle, el wait de abajo expira igual)
  if herdr agent wait tester --timeout "$to" >/dev/null 2>&1; then
    local o; o=$(out)
    case "$id" in
      T1) echo "$o" | grep -qE '(^|[^0-9])5([^0-9]|$)' && ok=1; detail="busca 5 aislado";;
      T2) { echo "$o" | grep -qi "fibonacci" && [ "$(echo "$o" | wc -w)" -gt 100 ]; } && ok=1; detail="menciona Fibonacci, >100 palabras";;
      T3) [ -f "$SBX/mcd.py" ] && [ -f "$SBX/test_mcd.py" ] && python3 -m pytest -q "$SBX/test_mcd.py" >/dev/null 2>&1 && ok=1; detail="mcd.py+test existen y pytest pasa";;
      T4) grep -q "def fib_par(n):" "$SBX/fib_par.py" 2>/dev/null && grep -q "a, b = b, a + b" "$SBX/fib_par.py" 2>/dev/null && ok=1; detail="fib_par.py con la función renombrada";;
      T5) tail -1 "$SBX/fib.py" 2>/dev/null | grep -q "# revisado" && ok=1; detail="última línea '# revisado'";;
      T6) echo "$o" | grep -q "2000" && echo "$o" | grep -q "f0000" && echo "$o" | grep -q "f1999" && ok=1; detail="2000+f0000+f1999";;
      T8) echo "$o" | grep -q "55" && ok=1; detail="menciona 55";;
    esac
  else
    detail="timeout esperando idle"
  fi
  t1=$(date +%s)
  echo "$id|$ok|$((t1-t0))s|$detail"
}

{
echo "# Resultado $1 — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo
if [ "$1" = HUMO ]; then TESTS="T8"; else TESTS="T1 T2 T3 T4 T5 T6"; fi
# T7 (visión) no se puede mandar por herdr agent prompt (solo texto): se anota y se salta.
[ "$1" = CARGA ] && echo "- T7: SALTADA (requiere adjuntar imagen; herdr agent prompt es solo texto)"
for t in $TESTS; do
  pv="PROMPT_$t"; tv="TIMEOUT_$t"
  r=$(run_one "$t" "${!tv}" "${!pv}")
  id="${r%%|*}"; rest="${r#*|}"
  echo "- $id: $([ "${rest%%|*}" = 1 ] && echo PASA || echo FALLA) ($rest)"
done
} | tee "$RES"
echo "guardado en $RES"
