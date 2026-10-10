# Actualización a Strata 0.1.41+fork.1 (F0-F4, 2026-10-10)

Producción pasa de 0.1.40 (md5 01856e5a) a 0.1.41+fork.1 (md5 6feee00c, rama `fork/v041`, ggml 3cf03257 sin cambios).

## Fases
- F0/F1: port del fork sobre v0.1.41 (System One logprobs/lpids/S1_FOLD, FETCH_ADMIT, HEAT_MIN, cachelog, visión en CPU, versión 0.1.41+fork.1).
- F2 (brazo B frente a producción A, GPU real, `strata-upgrade/F2.md`):
  - bit a bit con CPU_SHARE=0: 3 peticiones deterministas idénticas (d81bd77c, baace273, 4ffcad6f);
  - calidad con CPU_SHARE por defecto: top-1 97,0 %, KL 0,0248, dNLL +0,0006 ±0,0058 sobre el ruido: sin diferencia;
  - decode: B1 +1,9 %, B2 +3,8 %, B4 +4,4 % (IC [+2,5, +7,0]); prefill frío 32K -0,4 %; **turno de agente TTFT 2,94 -> 2,36 s (-19,7 %)**, gracias a CPU_SHARE;
  - aceptación MTP igual (B2 -3,6 %, IC incluye 0).
- F3 (`strata-upgrade/F3.md`): se descartó todo candidato adicional: `STRATA_IQ2S_BLOCK=1` (idéntico, sin ganancia), `PREFILL_CPU_SHARE_MAX` 2048/3072 (igual), `--expert-cache-per-layer` (B1 -1,3 %), `--pool-tasks` 2/4 (-44 %/-14 %), `STRATA_MMVQ_IL_ROWS` emitida (-1,1 %). Los defectos de v0.1.41 ya sirven a la 3060.
- F4: despliegue. Cambios en `~/Strata`: `engine/strata`, `engine/strata-vision`, `serve/`, `tools/`, `setup.py`, `engine/BUILD.json` (version). Sin tocar unidad, drop-ins, `serve-strata.sh`, JSON de config, perfil aprendido, systemone.db*, bench/ ni data/.

## Cifras en producción tras desplegar (2 pasadas)
B1 54,5 / 56,9 tok/s; B4 53,8 / 53,0 (aceptación 0,97 / 0,95); turno de agente 2,40 / 2,16 s (527 nuevos, 34.350 cacheados). Precarga 30 s, GPU 11.291 MiB (11.411 tras imágenes, como en F2).

## Vuelta atrás
Dos vías, ambas verificadas con md5 01856e5a:
1. `bench/rollback-01856e5a/RESTORE.md` (SHA256SUMS, restaura motor, serve/, config, unidad y drop-ins).
2. Copia previa a F4 en `~/Strata/bench/pre-v041-20261010/` (engine/{strata,strata-vision,BUILD.json}, serve/, tools/, serve-strata.sh, JSON, setup.py): `systemctl --user stop strata.service`, copiar de vuelta, `start`.
Comprobar: `md5sum /proc/<pid>/exe` = 01856e5a6dbcde136e99723604705cdc y /health `loaded: true`.
Las cachés de conversación en disco se invalidan con el cambio de versión (esperado).

## Pendiente / vigilar
`IQ2S_BLOCK` sin ganancia; MTP B2 -3,6 % (vigilar); +120 MiB de VRAM tras imágenes; el remote `origin` de v041 es un clon local, no el fork de GitHub (sin push).
