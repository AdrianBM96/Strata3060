# CHANGELOG (norma permanente desde 2026-10-04)

Una entrada por orden o mensaje de Claude: fecha, nº de orden, qué se hizo, resultado (cifras), commit y estado.
Commit + push en cada entrada.

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
