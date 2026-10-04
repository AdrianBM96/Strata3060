# Norma de trabajo: CONTRATOS (permanente desde 2026-10-04)

## Normas permanentes

- **N1.** Tras cualquier compactación o reinicio del contexto: releer COLA.md y las 5 últimas entradas del
  CHANGELOG antes de hacer nada.
- **N2.** Push SOLO a `claude/strata-rtx3060-optimization-zfgxq8`. Nunca a `main`.
- **N3.** Al principio del CHANGELOG, una línea de ESTADO que se reescribe en cada commit:
  `ESTADO: <contrato en curso> | <paso> | <bloqueo o ninguno> | <fecha y hora>`.
- **N4.** Cada entrega lleva el hash del commit y el diffstat. Las cifras van a la tabla del CHANGELOG, no solo
  al mensaje.
- **N5.** Todo contrato declara qué recursos usa: motor/GPU, CPU y tester. Dos contratos que usan el motor nunca
  van a la vez. Mientras el motor esté ocupado, adelantar trabajo que no lo use (código, lectura, scripts).

## Formato de contrato (todos así)

- ID / Objetivo (una frase).
- Alcance: ficheros y funciones que se pueden tocar. Nada fuera de ahí.
- Recursos: motor/GPU, CPU, tester (exclusión mutua del motor).
- Hecho cuando: criterios verificables.
- Medición: siempre METODO_MEDICION.md (alternar, 6+6, `bench.py compare`; bit a bit con los ajustes de siempre;
  `logpos-compare` si puede cambiar texto).
- Prohibido: cambiar producción sin OK de Claude; tocar :8082, BIOS/UEFI o setup.py.
- Entrega: entrada en CHANGELOG.md + commit + push; a Claude, ≤10 líneas (veredicto, cifras con IC, commit,
  bloqueos).

## Autonomía

Al cerrar un contrato, empezar el siguiente de la cola sin esperar. Escribir a Claude solo para: (a) entregar;
(b) pedir OK para tocar producción; (c) un bloqueo real, con file:line y 2 opciones. Las dudas menores se deciden
y se anotan en el CHANGELOG.

## Norma permanente: agente de pruebas `tester`

Solo sirve cuando el contrato pide una SESIÓN REAL DE AGENTE con ada-next como carga (batería C19: HUMO para
"¿arranca y responde?", CARGA para generar tráfico mientras los contadores registran). tester NO valida ni testea
soluciones (eso se hace con bench.py, logpos-compare y los tests). tester y los benchmarks NUNCA a la vez
(comparten el motor y se contaminan las cifras).

## Norma permanente: adopción en el mismo día

Cuando algo se ADOPTE (gana con IC, pasa la calidad y OK de Claude), en el mismo día: 1) commit del código y la
configuración y push a la rama del fork `AdrianBM96/Strata3060` rama `claude/strata-rtx3060-optimization-zfgxq8`
(`git pull --no-rebase` antes del push); 2) documentarlo: entrada en el CHANGELOG (qué, por qué, cifras con IC,
commit), actualizar RESUMEN_FINAL.md (configuración y cifras vigentes) y, si cambia el despliegue,
ESTADO_DESPLIEGUE.md; 3) si es un parche nuevo, dejarlo también en `docs/fork/patches/`. No se da por adoptado nada
que no esté subido y documentado.

## Cola (por orden)

- [ ] **C19** (en curso). Batería tester en `docs/fork/ops/tester/`. Recursos: tester+motor (excluye benchmarks).
  Hecho cuando: HUMO y CARGA pasan 2 veces seguidas, CARGA ≤20 min.
- [ ] **C14a.** `STRATA_MTP_HIST=1`. Recursos: motor/GPU para compilar+medir; tester para CARGA. Donde decía
  "sesión de 30+ min", ahora es el juego CARGA más la comprobación de volumen. Hecho cuando: bit a bit igual
  apagada, cobertura top 128/256/384 y aceptación del borrador.
- [ ] **C15.** `STRATA_PREGATE_STATS=1` según ORDENES §15 (lo implementa el agente). Recursos: motor/GPU;
  tester para CARGA. Igual sustitución de la sesión por CARGA + volumen.
- [ ] **C16.** `STRATA_ROUTE_GAP_STATS=1` según §16. Recursos y sustitución iguales que C15.
- [ ] **C17.** Según §17 con script sobre logs + CARGA para el tráfico. Recursos: tester+motor.
- [ ] **C20** (antes paso 4 de la orden 11). `--kv-resident 16384`. Recursos: motor/GPU. Medir VRAM liberada y
  huecos ganados, B1/B2 6+6, acierto de bloques KV y decode con 32K y 86K de contexto. Solo con OK y sin OOM.
- [ ] **C8.** Rejilla del simulador a) b) a+b. Recursos: solo CPU. Hecho cuando: la tabla con el criterio de §8.
- [ ] **C18** (solo estudio, sin código). Recursos: ninguno (lectura). Entrega en `docs/fork/C18_DISENO.md`
  (máx. 1 página).
- [ ] **C21** (orden de Adrián). Nuestra versión contra Strata 0.1.39 virgen. Recursos: motor/GPU (A/B con swaps).
  Va al final, con la cola vacía.

- **N6.** CANDADO DEL MOTOR (`/tmp/strata-motor.lock`). 1) Antes de lanzar tester, crear el lock con: quién,
  qué juego, hora de inicio y config del motor. Borrarlo al terminar, incluso si falla (`trap` en run.sh).
  2) Mientras exista, PROHIBIDO: reiniciar, parar o redesplegar el motor o litellm; cambiar flags o variables;
  lanzar bench.py, logpos-compare o cualquier petición a :8081/:4000; compilar con más de 2 hilos (`-j2` máx. con
  `nice -n 19`); cache-sim o lo que use >1 núcleo minutos. Sí se puede: leer, editar, scripts, documentar.
  3) Antes de CUALQUIER acción de esa lista, comprobar el lock y esperar. 4) Lock de >40 min: avisar a Claude, no
  borrarlo. 5) Al revés igual: nada de tester mientras corre un benchmark propio.
