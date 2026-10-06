# CHANGELOG (norma permanente desde 2026-10-04)

Una entrada por orden o mensaje de Claude: fecha, nº de orden, qué se hizo, resultado (cifras), commit y estado.
Commit + push en cada entrada.

ESTADO: C26 kernel CPU IQ2 (wt-C26) | paso b en curso | opencode2 (yo) espera OK de Adrián | 2026-10-06 08:55 UTC
| Fecha | Orden | Qué | Resultado | Commit | Estado |
| --- | --- | --- | --- | --- | --- |
| 2026-10-04 | 1 | Desplegar v0.1.39 + A/B vs 0.1.38 + 4 vars | B1 +7,1 % (p 0,013), P2 +9,0 % (p 0,008); vars sin efecto | 82c9640 | VALIDADA |
| 2026-10-04 | 4 | Tope 3072 por defecto | Claude Code y opencode 25/25 | c41c564 | VALIDADA |
| 2026-10-04 | 7 | Línea base 6 pasadas | B1 50,70/B2 42,35/B4 56,15/P3 20,17/P4 26,09/S2 2,29 | edf0962 | Entregada |
| 2026-10-06 | CUP-dep | Deploy v0.1.40 (01856e5a): rollback da9a7a9b+config en bench/ | flags+FETCH_ADMIT+C34+precarga OK; humo corta | - | Desplegado+PAUSA |
| 2026-10-06 | C17-ven | Ventana B4 spec 2/4/8: T≈2,5 siempre; ~22 ms/token plano; 60-61 tok/s | árbol sin ventanas anchas que lo paguen | - | Medido |
