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


## Línea EXPLORACIÓN (hardware, no solo Strata)

Norma N9: al menos 1 contrato E de cada 3. Los E empiezan por un ESTUDIO barato (≤1 página, con cifras y techo)
y solo se implementan con OK de Claude. Mismos criterios: más tps de decode o de prefill, sin perder contexto
ni calidad.

- [ ] **E1.** SO y hardware sin BIOS (recursos: motor; tras el lock). turbostat//proc en B1 (frecuencia P-cores,
  throttling PL1/PL2, governor, C-states, THP en expertos CPU); probar reversible: governor performance,
  /dev/cpu_dma_latency=0, hugepages, `nvidia-smi -lgc`, persistence mode. A/B 6+6 B1/B2. Entrega: tabla con
  ganancia+IC y qué persistir con systemd.
- [ ] **E2.** Formato de expertos para la CPU (tras C23). Si la CPU limita por cálculo: (a) reempaquetar la copia
  CPU bit-exacto para AVX2/VNNI, o (b) Q2_K/Q3_K desde pesos originales (con puerta de calidad). Estudio: µs/experto
  por formato, bytes extra, RAM total.
- [ ] **E3.** KV persistente de prefijos para agentes (estudio; recursos: ninguno). Tamaño KV por 1K tokens,
  prefijo común por cliente (logs C10b), velocidad NVMe/RAM, si el motor restaura estado de fichero (file:line).
  Techo: segundos de TTFT ahorrados con prefijo de 20K.
- [ ] **E4.** Prefill por dos vías (estudio, tras C24). Repartir expertos no residentes entre PCIe (11 GB/s) y CPU
  (RAM) para terminar a la vez. Techo con C24+C23. Entrada: P11+P12 y `exploracion/E4-PREP.md`.

## Cola (por orden)

- [ ] **C19** (en curso) · **C23** (+barrido T_e=1,4,8,16,32,64 por tipo, 6 hilos; clave para E4) · **E1** · **C24**
  (+confirmar ~72 ms/capa PCIe: 440 expertos × 1,8 MB) · **E4** · **E3** · **C16** · **E2** · **C14a** · **C20** ·
  **C17** · **C15** · **C8** · **C21**.

- **N10.** Agente `explorer` (agy con Gemini 3.8 Flash, panel w1:pM). Solo investiga y propone a Claude.
  Preguntas de investigación cerradas (máx. 1 por contrato):
  `herdr agent prompt explorer "opencode2: <pregunta>"`; responde por herdr directo (el buzón queda
  retirado). Su salida está en `~/explorer/` (P<nn>.md). Propuestas P<nn>
  aprobadas por Claude → copiar de `~/explorer/` a `docs/fork/exploracion/` del repo con su commit. Nunca
  pedirle implementar nada ni aceptar de él trabajo nuevo. Empezar los mensajes a cualquier agente con
  `opencode2:`.

- **N11.** Vigilante automático: si paso más de 3 min idle, empuja a seguir con la cola. Cuando de verdad se
  espere el OK de Claude, poner `ESPERA: <qué>` en la línea ESTADO; si hay bloqueo, `BLOQUEO: <qué>`; si la cola
  está vacía, `COLA VACÍA`. No usarlo para descansar: si hay algo de la columna SÍ de N8, no se está esperando.
