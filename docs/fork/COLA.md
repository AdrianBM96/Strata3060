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

- [ ] **C19** (en curso). Batería tester. Recursos: tester+motor.
- [ ] **C23.** Eficiencia de expertos en CPU: GB/s por tipo e hilos vs STREAM; ISA; reparto; afinidad. Hasta
  3 candidatos. Recursos: CPU (lectura) + motor (bench, con lock libre).
- [ ] **C16.** `STRATA_ROUTE_GAP_STATS=1` según §16 (lo implementa el agente). Recursos: build aparte.
- [ ] **C14a.** Medir con CARGA (código hecho y compilado).
- [ ] **C24.** Perfil del prefill (recursos: motor; N5/N6).
- [ ] **C20.** `--kv-resident 16384` (recursos: motor/GPU).
- [ ] **C17.** Script sobre logs + CARGA.
- [ ] **C15.** Degradada (gana ≤1 ms): precarga solo esconde la PCIe; manda la CPU. Va aquí, sin prisa.
- [ ] **C8.** Rejilla del simulador (solo CPU).
- [x] **C18.** Cerrada como diseño, sin implementar (`C18_DISENO.md`).
- [ ] **C21** (orden de Adrián). Virgen vs nuestra, con la cola vacía.

Decisión tras C22 v2 (anotada): la precarga (C15) solo esconde la PCIe y gana ≤1 ms → BAJA. Mandan la CPU de
expertos y la parte densa.
