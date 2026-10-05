# CHANGELOG (norma permanente desde 2026-10-04)

Una entrada por orden o mensaje de Claude: fecha, nº de orden, qué se hizo, resultado (cifras), commit y estado.
Commit + push en cada entrada.

ESTADO: C16 CARGA (motor) | C37 archivado | 2026-10-05 06:34 UTC
| Fecha | Orden | Qué | Resultado | Commit | Estado |
| --- | --- | --- | --- | --- | --- |
| 2026-10-04 | 1 | Desplegar v0.1.39 + A/B vs 0.1.38 + 4 vars | B1 +7,1 % (p 0,013), P2 +9,0 % (p 0,008); vars sin efecto | 82c9640 | VALIDADA |
| 2026-10-04 | 2 | `STRATA_FETCH_ADMIT` (corrección, calidad, velocidad) | +2-7 % B1/B2/B4; ΔNLL +0,0015 (ruido); adoptada | 3ff40d6 | VALIDADA |
| 2026-10-04 | 3 | Tope 3072/4096 × 8192/16384, 75 llamadas | 3072 actúa siempre; 4096 falla en larga | 9f749fa | VALIDADA → 3072 |
| 2026-10-04 | 4 | Tope 3072 por defecto | Claude Code y opencode 25/25 | c41c564 | VALIDADA |
| 2026-10-04 | 5 | 4096×32768 en larga, 3 reps | 2/3 (1ª: 619 s sin actuar) → se queda 3072 | b8e0dd4 | VALIDADA |
| 2026-10-04 | 6+10 | Logs: no-declaradas + reúso prefijos | 0 casos; reúso 56 % | 3aeec1b | Entregada |
| 2026-10-04 | 7 | Línea base 6 pasadas | B1 50,70/B2 42,35/B4 56,15/P3 20,17/P4 26,09/S2 2,29 | edf0962 | Entregada |
| 2026-10-04 | 9 | Perfil GPU | waitB 6,5-10 ms; CPU 14-19 ms (corregido: 4-5 eran conteos) | ae8ec6d/07abceb | Entregada |
| 2026-10-04 | 11 | Pico VRAM (medida) | 848 MiB libres; +430 huecos; micro-LLM intacto | 22ee7a8 | Entregada, sin aplicar |
| 2026-10-04 | 14a | ¿Cuenta el borrador usos? | No (enrutado en device); contador pedido a Claude | 5d7b59b | Entregada |
| 2026-10-04 | — | Orden Adrián: virgen-vs-nuestra | Registrada; stock 0.1.39 compilada | 9a62b00 | Pendiente |
| 2026-10-04 | 11 | A/B 4124 vs auto (B1/B2/B4 6+6) | En curso | — | En curso |
| 2026-10-04 | C11-bis | Qué limita los huecos a 3732 | auto=3283, tope=free-700 en 3356-3374; 848 del pico menos reserva+LOW | - | C11 en pausa |
| 2026-10-04 | — | Norma tester (verificado: responde TESTER OK) | - | - | Vigente |
| 2026-10-04 | C10b | cachelog por peticion + prompt-cache 12 (verificado) | api/model/UA, reused, resume, evictions, first_diff, phash | - | 48 h de datos pendientes |
| 2026-10-04 | C11 | CERRADA sin adoptar (+38 huecos; reserva intacta); A/B parado, auto restaurado | - | Cerrada |
| 2026-10-04 | C22 | Techo teórico (sin STREAM) | decode real 44ms vs 30 suma / 15-18 max; +5pts~-2ms | - | STREAM pendiente |
| 2026-10-04 | E3 | KV persistente (estudio) | 12MB/1K int8; NVMe 3,7GB/s; motor no restaura de fichero; techo ~20s/sesion | - | Estudio hecho |
| 2026-10-04 | C19 | Batería validada (timing real) | HUMO 2x (11s); CARGA 2x T1-T7 todo PASA (T1 10s, T2 45s, T3 26-36s, T4 20-26s, T5 20s, T6 26-35s, T7 6-12s) | - | Entregada |
| 2026-10-04 | E1 | Checklist E1-PREP al repo (reglas: diagnosticar, madvise, A/B por palanca, nada persistente) | 252 líneas copiadas a exploracion/ | - | Checklist listo; mide tras C23 |
| 2026-10-04 | C25 | Subagentes lector+codigo + N12 | lector (deny edit/shell salvo lectura) y codigo (solo wt-*) creados; lector verificado sin escritura | - | Hecho |
| 2026-10-04 | C25rev | Paralelo real: agente obrero + N12 nueva + wt-C16/wt-C14a (base cb8c2ff) + 2 obreros en marcha | obrero.md (edit solo wt-*, shell compilacion nice, deny serie); worktrees listos | - | Obreros trabajando |
| 2026-10-04 | C24 | Simplificado con C24-PREP (timer ya existe, verificar sync 2340-2356) | copiado a exploracion/; cola actualizada | - | Tras E1 |
| 2026-10-04 | C23-Te | Barrido T_e=1,4,8,16,32,64 × IQ2_S/XXS, 6 hilos (te_sweep, best-of-20) | lineal: ~70+26·T_e µs (T_e=12→~380, no 60: E4-PREP refutado); S≈XXS; 6 hilos | - | Clave para E4 |
| 2026-10-04 | C14a-cod | Obrero2 entrega wt/C14a fb4b62b (idéntico a dedda7a, build OK) | verificado diff vacío + binario; integración a su turno | - | Código listo |
| 2026-10-04 | N13+E4 | N13 (parciales ≤3 líneas, hora date -u, línea propia sigue); E4 tras E3 con corte T_e≤3 | - | - | Vigente |
| 2026-10-04 | prod-ck | Tras restart mtmin-ab (22:08): config producción exacta | mtmin.conf borrado, sin MT_MIN en environ; binario intacto 13:32 md5 056819f9 (0 refs MTP_HIST: var inerte); activos PF_FUSED=1, FETCH_ADMIT=1, PROFILE_HEAT_MIN, MTP_HIST_FILE | 056819f9 | Producción OK |
| 2026-10-04 | C26 | Kernel CPU IQ2 (tras C23): VNNI, R2/R4, P09 con te_sweep+parity; CPU-KERNELS al repo; E4 al final (+3,7%) | grid 8KB L1 (ggml-common.h:758) y vpshufb lane-local (iq_avx2:314...) verificados; wt-P08 hace paso b | - | En curso |
| 2026-10-04 | beellama | BEELLAMA-TRUCOS al repo + N14 (vigía 07:13, CUP) + E2 tras C26 + C17 spec-primero + E1 afinidad | spec existe (generate:459, prod spec4); workers en físicos 1-5 (pool:276); sin pesos BF16 en disco | - | Anotado |
| 2026-10-04 | E2+N15 | E2 fusionado con C26 (Adrián: nada de re-cuantizar); N15 (ningún cambio que altere pesos) | - | - | Vigente |
| 2026-10-04 | C27 | waitB en decode (tras C26, absorbe C18); DECODE-PCIE al repo | citas con deriva: HostFunc verify:1703 (prod:1265), 1 memcpyAsync en verify (copias en s2_expert_grouped); CUDA 12.0 SIN BatchAsync (palanca 3 como está: imposible); pinned:388 loguea, sin línea en log prod | - | Paso 1 tras P02 |
| 2026-10-04 | C23-P02 | MT_MIN=1 A/B 6+6 ×2 runs (12+12): +0,38 % (ruido; patrón bloque = artefacto térmico) | def 43,93±1,29 vs mt 44,10±1,61; deriva 67→77C; P02 muerto como palanca B1 | - | Descartado B1 |
| 2026-10-04 | C28 | Prefill por capas (tras C24, ante C26); PREFILL-PCIE al repo; E4 ya al final | - | - | Estudio paso 1 |
| 2026-10-04 | C28-est | Estudio: R=1,31GB@32K (D=10240 FP32, no cabe en VRAM: RAM fijada); KV causal OK; bit-exacto por construcción | techo: expertos −17,3 s menos hand-off 11,5 s = neto −6 s @32K (−13 s @86K) | - | Prototipo solo con OK |
| 2026-10-04 | C16-cod | Obrero1 entrega wt/C16 21e9091 (portado a mano, build OK, off=1 if) | verificado 4 fich 138+/1- + binario; medir (paridad GPU + B1) a su turno | - | Código listo |
| 2026-10-04 | C28-ver | LAYER-MAJOR verificado: R es FP32 (2×1,31GB, no BF16); KV int8 (384MB); GDN 118MB; total 3,1/8,2GB; solo cabe en VRAM hasta ~10,8K | confirma dirección y techo expertos; neto −6 s @32K | - | PIDO OK prototipo |
| 2026-10-04 | C26-b | P08 verificado con te_sweep en GGUF real: con despacho MT_MIN el proto da 79,2→66,1 µs (−16,5% a Te=1); sin MT_MIN no se alcanza (ggml fallback) | bit-idéntico; hallazgo: P02 murió por kernel viejo, no por despacho | - | A/B motor pdte |
| 2026-10-04 | C17-cod | Obrero1 entrega wt/C17 e261b25 (script+fixture, funciona) | verificado con fixture; falta tráfico CARGA ≥30 min (dueño) | - | Código listo |
| 2026-10-04 | C26c | Paso-c P09 a obrero1 (wt-C26c; otros ficheros, sin pisar P08/VNNI) | con dato en contra (8,7 GB/s: CPU-bound); si no gana, refuta | - | En curso |
| 2026-10-04 | C28-ok | OK prototipo en wt-C28 (préstamo slots + superbloques, R FP32, sin hand-off) | techo corregido ~−17 s @32K; asignar al liberar obrero | - | Prototipo pdte |
| 2026-10-04 | C29 | GPU densa en decode (tras C27); DECODE-GPU al repo; cola nueva | (d) no incondicional: temp>0 por request (server:300, verify:2126) + logprobs; solo con gate greedy | - | Medir a,b primero |
| 2026-10-04 | C17-tree | MTP-DRAFT.md al repo; medida árbol: ms/ventana vs 2,3,4,6,8 + expertos CPU/PCIe | con resto de C17, sin cola nueva | - | Motor tras P08 |
| 2026-10-04 | C28-rie | C28-RIESGOS al repo (para briefing prototipo): R T×10240 FP32 ✓, checkpoint bifásico root 2048, zig-zag >32K | coherente con mi estudio; va al briefing wt-C28 | - | Archivado |
| 2026-10-04 | C26-AB | A/B B1 6+6 P08+MTMIN vs prod (binarios con md5, producción restaurada 056819f9) | def 45,05±1,36 vs p08 45,00 (−0,1 % sin 1ª fría): kernel −16,5 % NO mueve B1 → CPU fuera del crítico (C22); NO se despliega | - | A producción no |
| 2026-10-04 | C30-v4 | Dirección visual definitiva (ref-v4 abierta: SaaS claro, clay, 3 col); relay; TAREAS con pasos | - | - | En curso |
| 2026-10-04 | C23-fin | Cierre en ENTREGAS (Te, hilos, ISA, P02/P08/P09/VNNI); TAREAS→HECHO | veredicto: CPU con margen pero manda waitB | - | Entregado |
| 2026-10-04 | C26-bis | bench.py 6+6: B1 −0,16%±1,51, B2 +0,08%±2,91 (IC incluye 0); prod restaurada md5 OK | P08 se queda archivado; METRICAS actualizado (N17) | - | Archivado |
| 2026-10-04 | C27-AB | FLAGB B1 6+6: −1,25%±1,68 (IC incluye 0, sin B2); perfil FLAGB: waitB 10,2 (no baja de 8-9) | palanca 1 refutada como está; prod restaurada | - | Revisar |
| 2026-10-05 | C28-ab | P3/P4 off/on: P3 12,8/12,8, P4 18,9/19,0 (+0%): el proto no cambia chunks (techo intacto, sin efecto) | revisar planner (lending real); prod restaurada | - | Revisar |
| 2026-10-05 | C31-tab | Timeline 850 vent (B1/B2/B3, 0 fallos): d10 ~0,02ms, d21 0,004ms, d32 0, d43 ~0,17ms; viaje+issuer REFUTADO; 8ms solo con fallos | prod restaurada da9a7a9b | - | Falta tráfico con fallos |
| 2026-10-05 | C27-redo | FLAGB B1 6+6 (N23 OK): +2,12%±3,79, medianas +1,2% (IC incluye 0, sin B2); waitB no baja | revisar (adoptar exige IC>0) | - | P08-bis en marcha |
| 2026-10-05 | P08-redo | B1 6+6 N23 (P08+MTMIN vs prod): −1,62%±2,11 (IC incluye 0) | los tres redos nulos; P08 archivado final; sin candidato | - | Cerrado |
| 2026-10-05 | C28-dg | Diagnóstico sin motor: plan_superblocks MUERTO en motor (solo enabled()); chunks 7936 iguales on/off | falta lending real, no descartar; C24 sigue en cola | - | Rehacer proto |
| 2026-10-05 | C29-scr | Cribado N23 off/a/b x3: (a) 23,7 (−56% ROTO, a debug), (b) 51,8 (−4,5% térmico?, a 6+6 alterno) | off 54,27 (frío) | - | (b) a 6+6 |
| 2026-10-05 | C28-re | No refutada (a medio hacer): obrero a lending + planner vivo + log bytes; bit-bit y A/B después | - | - | En curso |
| 2026-10-05 | C28-ab2 | N23 OK; P3/P4 +0%; log c28: superbloques [2,3] pero fetch intacto por chunk (65-85 GiB) | premio real, mecanismo ausente: falta cirugía del bucle | - | Devolver |
| 2026-10-05 | C27-12 | 12+12 prod-vs-FLAGB N23: +0,62%±2,55 INCLUYE 0 → archivado (con P08) | prod intacta | - | Archivado |
| 2026-10-05 | C28-ab3 | Rewrite a medida (off/on P3/P4 + bytes c28 + bit-bit) | - | - | En curso |
| 2026-10-05 | C29b-rep | Réplica 6+6 en marcha (lock); C28 a cirugía (obrero); C30 v4-fix en :8095 para revisión | capturas v4-*.png en docs/fork/oficina/ | - | En curso |
| 2026-10-05 | C28-ring | RING=512: log on real pero bytes sin reúso (fetch por chunk persiste); pgrep-N23 poco fiable en inline (usar scripts) | prod restaurada | - | Devolver con métrica B/leído |
| 2026-10-05 | C33 | Paso1 (windows_ok generate:6789, short_read=128) + paso2 (CKPT_REREAD): ventanas 0,51s vs prefill 1,92s | camino actual correcto; ideas 1-2 aparcadas | - | HECHO |
| 2026-10-05 | C28-fin | Cerrada archivada (patches/c28-layermajor.patch, 1345 lín); TAREAS→DESCARTADO | - | - | Archivada |
| 2026-10-05 | C36 | Cómputo 32K: MoE-gemm 24% (~20 TFLOPS, ~10% pico INT8T), GDN 13,6%, HC-read 10,2% (puro movimiento), attn 9,2% | margen en alimentar cores, no en kernels (shapes flacos) | - | Medido |
| 2026-10-05 | C34-blq | RAM: 17GB libres < 20GB del criterio (mib 8192→16384 = +8GB) | no aplico 16/16384; opciones: parcial u otra vía | - | BLOQUEO |
| 2026-10-05 | C30-fin | Oficina v5 terminada por Claude (799074e) en :8095; TAREAS→HECHO | - | 799074e | HECHO |
| 2026-10-05 | C32 | Precarga ExecStartPost + POST /v1/load (OK prod, --lazy intacto) | GPU 11,3GB ~25s (obj ≤20s, +5s por RAM); 1ª petición 1,35s; un arranque falló y rearrancó solo | - | Desplegado |
| 2026-10-05 | C33-C35 | X2/X3/X4 al repo; cola con C32-C35 | - | - | En cola |
| 2026-10-05 | C28-ab4 | Préstamo N23 OK; P3 +0,5%, P4 −0,2%; bytes iguales on/off (fetch persiste) | prod restaurada | - | Devolver |
| 2026-10-05 | C28-mot | Motivo RING (512/wave vs 384, no hand_in_): obrero a PRÉSTAMO de huecos + repoblado | prod restaurada da9a7a9b | - | En curso |
| 2026-10-05 | C34-par | Parcial 12/12288/4096 desplegado: B1 −0,85% (ruido), available 15,7GB (free-col 0,6: usar available) | 24h cachelog en marcha; C35 a obrero | - | Desplegado |
| 2026-10-05 | C24-tim | TIMING 32K/86K: 976/1004 tok/s; wait copy 4,6%/2,1%, grouping 20ms; prefill COMPUTE-bound (gemm 24%) | C28 sin techo: las copias van solapadas; prod restaurada | - | HECHO |
| 2026-10-05 | C30-r2 | Rev2 aplicada (6434bc8) y desplegada en :8095; TAREAS al día (N18); METRICAS B1 prod→51,40 | capturas nuevas + zoom en docs/fork/oficina/ | - | A revisar |
| 2026-10-05 | C29b-fin | Réplica +0,5%; 12+12 +1,23%±2,09 INCLUYE 0 → archivado (sin despliegue) | prod intacta | - | Archivado |
| 2026-10-05 | C28-P4 | Cirugía = rewrite ~1400 lín (rompe diff mínimo, bit-exacto solo con GPU); mapa P4 en ENTREGA | requiere OK explícito + logpos-compare | - | ESPERA OK |
| 2026-10-05 | C29-b | ROWS2 6+6 N23: +1,91%±1,82 (IC95 +0,1/+3,7, justo; medianas +1,7%) | bit-exacto+parity; (a) roto; (c)(d) pdtes | - | Decide Claude |
| 2026-10-05 | C29-cod | 4 palancas + parity (1153+/32-, binario listo); (b) NO descartada (bit-exacta lane-0) | GPU+A/B a su turno (tras C28/C27/P08) | - | Código listo |
| 2026-10-05 | C30-dep | Oficina en :8095 (local+tailnet 200, v4-fix); N21; C31 creado (obrero) | BLOQUEO resuelto | - | Desplegada |
| 2026-10-05 | C28-redo | N23 OK ambos brazos; P3 +1%, P4 +0,6% (chunks 7936 iguales: el techo no muerde) | proto sin efecto sin lending real | - | Revisar |
| 2026-10-04 | C27-C28 | C27 OK (compila sin lock, A/B motor primero); C28 a desformatear (layer_major.cpp + gancho) | - | - | En curso |
| 2026-10-04 | N20 | Acelerar: -j12 en crítico sin locks (C27 en marcha), solo target motor, ccache→Adrián, A/B B1 primero | - | - | Vigente |
| 2026-10-04 | ccache | Instalado ccache 4.9.1 (apt, con sudo); binario C27 2ffae239 listo (falta commit obrero para A/B) | - | - | A/B pdte |
| 2026-10-04 | C30+N17 | Oficina agéntica (ref.jpg copiada, METRICAS.json creado, N17); a obrero al liberar slot (2 ocupados) | herdr OK (5 agentes), caches vigía/upstream existen | - | En cola |
| 2026-10-04 | C26-c | P09 refutada en RAM (x0,985-1,029, 2,6-5,1 GB/s: CPU-bound); C30 a obrero (wt-C30 staging) | bit-idéntico; archivar P09 salvo integración loader | - | C26a en curso |
| 2026-10-04 | C30-amp | Ampliación Adrián (letterbox, panel+envío con token, kanban, N18/N19); TAREAS.json (21) creado; relay al obrero | - | - | En curso |
| 2026-10-04 | C27-p1 | Perfil VERIFY_PROFILE (sin código): waitB 8-9, PCIe 1,0, waitA 0,37, waitCPU 1,1 ms/vent | waitB≫bytes → confirma palanca 1 (sin host callback); palanca 3 menor | - | Paso 2 obrero |
| 2026-10-04 | ALARMA | B1 bench.py 6 en frío: 51,40 (+1,4% vs 50,70), sin throttling; binario+config idénticos | NO hay regresión: el −11% era harness (bench-chat temp 0,7) + calor | - | Producción sana |
| 2026-10-04 | C26-fin | C26 cerrado: P08 en patches/p08-interleave.patch; C26a cancelado; N12→3 obreros; C27+C28 asignados | - | - | Cola nueva |
| 2026-10-05 | E1 | Diag (35W, 3,8GHz, gov/EPP/THP óptimos) + afinidad −1,22%±2,45 + dma −5,01%±3,00 (rechazado) | sistema ya óptimo; hugepages requiere código; -lgc sin throttling | - | HECHO |
| 2026-10-05 | C35-ab | (b) B4 6+6 +1,05%±4,63 y B1 +0,65%±1,77 (ambos IC0); bit-bit OK (fence en suelo) | (a)(b) nulos; parche en patches/c35-suffix.patch | - | Archivado |
| 2026-10-05 | C37 | Curva chunk efectivo 5120-8192 (fijos recortados a buffers): P3 5,86/5,82/5,00/4,36, B1 plano | 8192 −13% P3-short; proponer A/B fijo-8192 vs auto | - | Curva lista |
| 2026-10-05 | C37-limpio | Frío 6+6 tok/s: 32K +3,97%±3,46 (borde) y 86K −1,97%±5,52 (contradictorio) | sin OOM/puerta ptes; a decisión | - | Borde |
| 2026-10-05 | C29-cd | Cribado (c) 44,9 (−15% ROTO) y (d) 52,5 (−0,8% nulo); prod restaurada | (a)(c) rotos, (b)(d) nulos: C29 archivado entero | - | Archivado |
| 2026-10-05 | C37-fin | Archivado (frío +4%/−2% contradictorio; +19% era caché; auto se queda) | - | - | Archivado |
| 2026-10-05 | PAUSA | Sesión: ADOPTADO C32 (precarga), C34-parcial (12/12288/4096); DESCARTADO P02/P08/C26/C27/C28/C29/C35/C37/E2/C18; HECHO C19/C22/C23/C24/C33/E1/C30 | prod da9a7a9b, flags limpios, GPU 11,4GB, locks fuera | - | PAUSA |
