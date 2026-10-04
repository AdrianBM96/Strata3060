# CHANGELOG (norma permanente desde 2026-10-04)

Una entrada por orden o mensaje de Claude: fecha, nº de orden, qué se hizo, resultado (cifras), commit y estado.
Commit + push en cada entrada.

ESTADO: C28 estudio hecho (yo) | C26 wt-P08 paso b | wt-C16 portando | lock libre | 2026-10-04 22:32 UTC
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
