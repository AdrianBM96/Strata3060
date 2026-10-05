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

- [ ] **E1.** SO y hardware sin BIOS (recursos: motor; tras el lock). Checklist: `exploracion/E1-PREP.md`
  (cifras de explorer son estimaciones; medir). Reglas: 1) primero diagnosticar durante B1 (`nvidia-smi -q -d
  PERFORMANCE,TEMPERATURE` + turbostat: MHz real, PkgWatt, throttle); si no hay throttling, no aplicar `-lgc`;
  2) THP en `madvise`, no `always`; 3) cada palanca por separado con A/B 6+6 de B1, solo se combinan las que
  ganan con IC>0; 4) nada persistente (systemd) sin OK de Claude. +afinidad (beellama +10 %): Strata fija
  workers en físicos 1-5 (`pool.cpp:276`); probar 6 hilos 0-5 frente a actual.
- [ ] **E2.** Formato de expertos para la CPU (tras C23). Si la CPU limita por cálculo: (a) reempaquetar la copia
  CPU bit-exacto para AVX2/VNNI, o (b) Q2_K/Q3_K desde pesos originales (con puerta de calidad). Estudio: µs/experto
  por formato, bytes extra, RAM total.
- [ ] **E3.** KV persistente de prefijos para agentes (estudio; recursos: ninguno). Tamaño KV por 1K tokens,
  prefijo común por cliente (logs C10b), velocidad NVMe/RAM, si el motor restaura estado de fichero (file:line).
  Techo: segundos de TTFT ahorrados con prefijo de 20K.
- [ ] **E4.** Prefill por dos vías (estudio, tras C24). Repartir expertos no residentes entre PCIe (11 GB/s) y CPU
  (RAM) para terminar a la vez. Techo con C24+C23. Entrada: P11+P12 y `exploracion/E4-PREP.md`.

## Cola (por orden)

- [x] **C19** hecha (batería 2× HUMO + 2× CARGA, todo PASA) · [x] **C22** hecha (techo v2: precarga ≤1 ms, manda CPU).
- [ ] **C23** (cierre) · **C27** (palanca 1 a obrero, PRIORIDAD) · **C26** (CERRADO: P08 archivado en
  `patches/p08-interleave.patch`, se reabre sin round-trip; C26a cancelado) · **C28** (prototipo a obrero)
  · **C24** (simplificado + burbuja sync + palancas 2,4,5) · **E1** (+afinidad) · **C29** (a,b primero;
  d con gate) · **C14a** (código listo; medir a su turno) · **C16** (código listo; medir a su turno) ·
  **C20** · **C17** (script listo; falta CARGA + árbol) · **C15** (degradada) · **C8** (+P05 c) · **E3** ·
  **E4** (+3,7 %, al final) · **C21** · **C30** (oficina a obrero).
- **C30** (oficina agéntica, Adrián; obrero en paralelo, sin motor: `docs/fork/oficina/` server.py+index.html+
  strata-oficina.service :8090; ref.jpg ya copiada; METRICAS.json creado; se asigna al liberar un obrero).
- **N17.** `docs/fork/METRICAS.json` lo actualizo YO al adoptar algo y al terminar cada A/B (base orden 7,
  producción desplegada, candidato en prueba, historial). Decode % = (prod/base−1)·100; prefill % =
  (base_s/prod_s−1)·100.
- [x] **C18** absorbida por C27 (queda el diseño en `C18_DISENO.md`).
- Nota: `-b/-ub 128` de llama.cpp no aplica a los chunks de Strata (diseño distinto); sin acción.
- **N14.** Vigía diario (07:13 UTC) del Strata oficial: si sale versión nueva llega contrato CUP y se sigue tal
  cual (nada se despliega sin OK de Claude). Hoy al día (v0.1.39 = HEAD).
- [x] **E2** fusionado con C26 (decisión Adrián: nada de re-cuantizar).
- **N15.** Ningún cambio que altere los pesos del modelo (ni Q2_K ni ningún formato que cambie valores):
  solo reempaquetado bit-exacto (misma aritmética, otra disposición en memoria).
- **N16.** Agente `suplente` (agy, arquitecto suplente). Si Claude se queda sin cuota, el vigilante avisa con
  `SUPLENCIA ACTIVA`: desde ese momento entregas, avisos y preguntas van a suplente, con el mismo formato.
  Suplente valida según los criterios de cada contrato y da el siguiente de la cola, pero NO da OK a
  producción: eso se marca como ESPERA y se sigue con otra cosa. Con `SUPLENCIA FIN`, todo vuelve a Claude.
- **N18.** `docs/fork/TAREAS.json` (kanban de la oficina) lo mantengo YO: actualizado en cada cambio de
  estado de un contrato, en el mismo commit que el CHANGELOG.
- **N19.** Los mensajes que empiezan por `adrian:` son órdenes de Adrián, el dueño. Tienen prioridad, pero
  si chocan con una norma o con producción se pide confirmación a claude.
- **N20.** ACELERAR: sin locks, el obrero en camino crítico compila con `-j12` sin nice (si ya va a
  `-j2`, no pararlo: otro make `-j12` en el mismo build continúa); los demás, `-j2` con nice. Compilar SOLO
  el objetivo del motor que se mide. ccache 4.9.1 instalado. En el A/B: primero B1 6+6; si nulo/negativo
  claro, no gastar en B2.
- **N21.** Obreros SIEMPRE en segundo plano (background), nunca esperando en primer plano. Si uno se
  cuelga, se mata y se relanza.
- **N22.** El binario que sirve es `engine/strata` (no `build/strata`); todo A/B con binario se hace ahí
  (stop-swap-start con md5: en marcha da `Text file busy`). Las env SÍ llegan al engine vía drop-ins.
  `engine/strata` = prod (da9a7a9b, 0.1.39+adopciones).
- **C30** (oficina, AMPLIACIÓN UI/UX de Adrián: 16:9 letterbox + 'gira el dispositivo'; panel lateral con
  terminal 30 líneas + envío DIRECTO / VÍA CLAUDE (token POST, límite 3 s, ≤4000 chars, log envíos);
  tablero kanban desde TAREAS.json; pixel font embebida, ≤30 fps, atajos 1-5/T/Esc; capturas 1920×1080 y
  844×390 + prueba de envío a explorer).
- Verificado con file:line (no basar nada en lo contrario): `iq2s_grid` es `uint64_t[1024]` = 8 KB
  (`third_party/llama.cpp/ggml/src/ggml-common.h:758`, macro `:472`) → residente en L1, accesos escalares
  (`iq_avx2.cpp:155-158,404-405`); `vpshufb` solo indexa 16 entradas por lane (signos/escalas,
  `iq_avx2.cpp:314,325,394,406`) → no puede indexar tabla de 1024.

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

- **N12.** Trabajo en paralelo real: hasta 4 líneas = yo + 3 subagentes `obrero`
  (`~/.config/opencode/agents/obrero.md`). Cada obrero lleva UN contrato entero en su worktree
  (`/tmp/opencode/wt-<C>`, rama `wt/<C>`) con su build aparte (`build-<C>/`). El obrero puede: leer, editar,
  compilar en su worktree (`nice -n 19`, `-j2`), ejecutar tests de SOLO CPU, escribir scripts y documentar en
  `wt-<C>/ENTREGA.md`; al terminar avisa con rama, commit y qué falta medir. Solo mío (recursos serie): motor
  y GPU (incluidos tests o binarios que usen CUDA: con el motor cargado en 11 GB pueden provocar un OOM en
  producción), tester y lock, benchmarks, despliegue, merge a la rama principal, push, COLA/CHANGELOG/ESTADO.
  Candado de medida: mientras mido (bench, logpos, E1 o C23 bench) creo `/tmp/strata-bench.lock`; con ese lock
  los obreros NO compilan (siguen editando o leyendo) y lo borro al terminar. Yo integro, compruebo que con la
  variable apagada todo sale bit a bit igual y mido en serie.

- **N13.** (a) Avisar a Claude (≤3 líneas) de CADA resultado parcial que confirme o refute una hipótesis o
  cambie la cola, no solo de las entregas finales. (b) La hora de ESTADO sale SIEMPRE de `date -u +%H:%M`.
  (c) No quedarse esperando a los obreros: la línea propia sigue.
