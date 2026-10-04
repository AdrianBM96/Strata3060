# Entregas del agente de bazzite

Ver `PROTOCOLO.md`. Una sección por orden, con cifras y veredicto propuesto.

## Orden 1. Desplegar v0.1.39

Estado: desplegada y verificada; A/B contra la 0.1.38 en curso.

- Árbol: clon en `~/Strata-0139`, rama de Claude en `a78ef4b`; parches System One + suffix-draft aplicados limpio.
- Tests: `ctest parity|iq2s|profile` **46/49** (los 3 fallos son de entorno: 2 OOM por la VRAM del servicio
  desplegado, 1 fichero Q2_0 ausente); `iq2s_avx2_test` bit-idéntico OK; `expert_profile_save_test` OK;
  `serve/test_server.py` **140 OK**; `test_cache_sim.py` 6 OK; setup choices/config OK; golden 46 fallos
  **preexistentes** (fallan igual en el árbol 0.1.38).
- Despliegue: copias hechas (binario `engine/strata.bak-0138`, config y perfil en `/tmp/opencode/`).
- Verificado en producción: PCIe probe 11,1 GB/s → pcie_frac 0,30; caché 3.694 huecos; visión en CPU
  ("MEN WALK ON MOON" en 4,7 s); `strata_logprobs` OK; ada-decide S1 (4 preguntas, 5,88 s); B1 44-48 tok/s.
- **Línea nueva del contador**: `1536 experts swapped in over 66 windows, 0 kept from the PCIe share`
  (`kept` = 0 con la variable apagada, como debe).
- A/B en curso: `bench/ab-0139.sh` (B1+P2, 3+3 por brazo, alternando binarios; restaura 0.1.39 al final).
- **Resultado A/B (`bench.py compare`, 6+6 en B1, 5+6 en P2):**

| Prueba | métrica | 0.1.38 | 0.1.39 | Δ | IC 95% | p | veredicto |
| --- | --- | ---: | ---: | ---: | --- | ---: | --- |
| B1 | decode_tps | 46,55 | 49,85 | **+7,1 %** | +1,10/+4,75 | 0,013 | B mejor |
| P2 | prompt_tps | 910,8 | 992,9 | **+9,0 %** | +47,75/+109,35 | 0,008 | B mejor |

  **Veredicto propuesto: la 0.1.39 se queda** (gana en decode y en prefill; el +26 % de upstream era con prompt
  de 243K, el nuestro P2 es de 128K — el efecto escala con la longitud).
- Incidencia honesta: mi script `ab-0139.sh` tenía un bug (`BIN039` apuntaba al binario vivo); un brazo salió
  contaminado. Lo corté, quité sus filas, arreglé el script y completé la segunda mitad con swaps manuales y
  explícitos (md5 verificado en cada swap). Los números de arriba son solo de pasadas con binario verificado.
- **A/B de las 4 variables nuevas** (B1, off/on/off/on, 3 pasadas; medianas):

| Variable (=1) | off | on | veredicto |
| --- | ---: | ---: | --- |
| `STRATA_TOPK_OLD` | 45,7 | 45,7 | sin diferencia → **apagada (defecto)** |
| `STRATA_IQ_STAGE_GRID` (=0 la apaga) | 46,6 | 45,8 | ruido → **defecto (encendida)** |
| `STRATA_SH_STREAM` (=0 la apaga) | 45,0 | 45,4 | ruido → **defecto (encendida)** |
| `STRATA_RING_BYTES` | 45,9 | 46,0 | sin diferencia → **apagada (defecto)**; al cambiar bits en prompts largos, ni se plantea sin ganancia |

  **Veredicto propuesto orden 1: la 0.1.39 se queda con todo por defecto.** Ninguna variable resta ni aporta en
  decode; el +7,1 % / +9,0 % del motor nuevo es franco.
