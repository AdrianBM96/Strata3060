# F5: afinar la especulativa del MTP (2026-10-10)

El subagente se cortó por el límite de cuota de la API a las ~19:55. El orquestador terminó los bloques con los mismos scripts (f5/blk5.sh) y restauró producción.

| Candidato | Texto a temperatura 0 igual que R | Resultado |
|---|---|---|
| --spec 3 / 5 / 6 | NO (cambia el sha del razonamiento) | DESCARTADO |
| --spec-min-p 0.3 | sí | B2 -7 %: DESCARTADO |
| --spec-min-p 0.7 | sí | ADOPTADO |

min-p 0.7 frente a R (0.5); n = 12 en R y 9 en 0.7, en bloques alternos con la GPU por debajo de 65 °C:

| Prueba | Δ | IC95 |
|---|---|---|
| B1 | -0,8 % | [-1,9, +1,7] |
| B2 | +1,8 % | [-0,4, +7,0] |
| B4 | +2,5 % | [+0,7, +5,0] |
| Media B1+B2+B4 | +1,2 % | [+0,1, +2,9] |
| Turno de agente (2,19 → 1,77 s) | -19 % | [-25,6, -16,0] |

Aplicado en /home/bazzite/Strata/strata-swift-iq2_xs.json: `--spec-min-p 0.7`. Copia en .bak-20261010-minp05.
Producción verificada: md5 6feee00c, --spec 4 --spec-min-p 0.7, /health OK, gateway "ok", drop-ins mtphist, precarga y profileheat.
Vuelta atrás: restaurar el .bak y `systemctl --user restart strata.service`.
