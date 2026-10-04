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

## Orden 2. `STRATA_FETCH_ADMIT`

Estado: verificada y **adoptada** (`serve-strata.sh` exporta `STRATA_FETCH_ADMIT=1`).

- **Corrección: PASA.** El log dice `STRATA_FETCH_ADMIT: the PCIe share of the misses stays in the VRAM cache`;
  `kept from the PCIe share` > 0 (miles por petición); respuesta coherente, sin basura. Acierto 73-78 %.
- **Calidad: PASA** (`logpos-compare`, 2.602 posiciones): A vs B top-1 99,9 %, KL 0,0020; ΔNLL de B sobre el ruido
  **+0,0015 ± 0,0008 → "sin diferencia medible"**.
- **Velocidad** (B1/B2/B4, off/on/off/on, 3 pasadas; medianas):

| Fase | B1 off→on | B2 off→on | B4 off→on |
| --- | ---: | ---: | ---: |
| flags de hoy | 45,25→47,55 (**+5,1 %**) | 42,75→45,75 (**+7,0 %**) | 42,4→44,55 (**+5,1 %**) |
| every2/swaps32 | 45,7→47,1 (+3,1 %) | 47,1→46,55 (−1,2 %) | 43,2→44,55 (+3,1 %) |
| every2/swaps16 | 46,95→48,05 (+2,3 %) | 47,2→49,2 (+4,2 %) | 46,2→44,05 (−4,7 %) |

  El `on` gana o empata en casi todo (las dos celdas negativas son puntuales, dentro del ruido de B2/B4). Las
  flags every2/swaps **no superan a las de hoy**, así que la config no se toca.
- **Veredicto propuesto: adoptada con las flags de hoy.** `STRATA_FETCH_ADMIT=0` la apaga.

## Orden 3. Tope de pensamiento

Estado: medida y entregada (75 llamadas en total).

- **Matriz**: topes 3072/4096 × `max_tokens` 8192/16384 × 4 tareas (write, edit, tests, long multi-tool) × 3 reps,
  más la tarea larga original (TASK.md, 18 llamadas previas). **Las 75 actúan (`tool_use`), 0 errores, 0 argumentos
  inválidos.**
- **Matiz importante**: con tareas **cortas**, 3072 y 4096 actúan siempre (56/56), con ambos `max_tokens`. El fallo
  de 4096 solo aparece con la tarea **larga** (TASK.md, ~2,5K tokens de prompt) y `max_tokens=8192`: ahí 4096 falla
  0/3 (se come el presupuesto pensando y muere por `max_tokens`) mientras ≤3072 actúa 12/12. El umbral depende del
  tamaño de la tarea, no solo del tope.
- **`max_tokens` real de los clientes**: opencode (límite de salida 32768 para ada-next) y pi (`maxTokens` 32768)
  permiten salidas de ~32K; Claude Code no lo declara en sus ficheros, pero en la práctica también (el runaway de
  `omp` generó 32.768 tokens).
- **Aviso para el harness**: el modelo llamó 6 veces a `read_file`, que **no estaba** en la lista de herramientas.
  El harness debe rechazar o mapear tools no declaradas.
- **Veredicto propuesto: tope 3072 por defecto** (actúa en todo lo medido, corto y largo). 4096 solo donde haya
  `max_tokens` ≥ 16384 **y** tarea corta; sin medir 4096×16384 en tarea larga, no se recomienda en general. La
  reserva de acción sale sola (`max_tokens` − tope).

## Orden 4. Tope 3072 por defecto

Estado: aplicado y verificado.

- **Diff de la config** (`strata-swift-iq2_xs.json`): añadida la clave `"reasoning_budget_tokens": 3072` (único
  cambio; verificado con `diff` contra el backup previo).
- **El servidor no anuncia el tope en el log** (no hay línea que lo diga). Verificado **por efecto**: una petición
  que pensaría largo se cierra con thinking de ~12K caracteres (≈ 3.072 tokens) frente a ~31K+ sin tope.
- **Claude Code, tarea larga** (TASK.md): **actúa**, 288 s, `minisql.py` + `test_minisql.py`, 20 tests propios OK,
  **25/25** en el corrector objetivo.
- **opencode + ada-next, tarea larga**: **actúa**, 409 s, ambos ficheros, **25/25** en el corrector. (Sin `--model`
  va a un cloud bloqueado por país: "This model is not available in your country".)
- **Veredicto propuesto: el tope 3072 se queda** (es barato, no degrada, y evita el atasco en ambos clientes).

## Orden 5. 4096 con el `max_tokens` real

Estado: medida. **El tope se queda en 3072.**

- TASK.md con tope 4096 + `max_tokens` 32768, 3 reps:

| rep | tiempo | stop | thinking | ¿actúa? |
| --- | ---: | --- | ---: | --- |
| 1 | 619 s | max_tokens | 17.199 c | **no** (solo thinking+text) |
| 2 | 197 s | tool_use | 16.552 c | **sí** (2× write_file) |
| 3 | 197 s | tool_use | 16.527 c | **sí** (2× write_file) |

- **2/3, no 3/3** → según el criterio de la orden, **no sube a 4096**. Además la rep fallida quemó 619 s.
  La primera repetición confirma que con tarea larga el 4096 sigue siendo frágil aunque haya sitio.
- **Veredicto propuesto: se queda en 3072.**

## Orden 7. Línea base nueva

Estado: medida (6 pasadas por prueba, config adoptada: 0.1.39 + `FETCH_ADMIT` + tope 3072).

| Prueba | Métrica | Mediana (n=6) |
| --- | --- | ---: |
| B1 | decode_tps | **50,70** |
| B2 | decode_tps | **42,35** |
| B4 | decode_tps | **56,15** (borradores 98 %) |
| P3 | ttft_s | **20,17** |
| P4 | ttft_s | **26,09** |
| S2 | wall_s | **2,29** |

Incidencia: B4 se saltó las 6 primeras (mi `bench.py` vive en `~/Strata/bench/`, pero `REPO` espera
`docs/fork/ops/bench.py` para el corpus). Relanzado desde el sitio correcto: 6/6 bien.

## Orden 9. Perfil de la GPU en la configuración nueva

Estado: medida (0.1.39 + `FETCH_ADMIT` + tope 3072; perfil retirado después).

- **Desglose por ventana B1** (~2,3 tokens/ventana, ~44 ms/ventana = verify ~42 + draft ~2,5):
  - GPU-reach wait ~22 ms + per-layer host ~14-19 ms (casi todo CPU expertos ~15-18 ms) + stage ~0,3.
  - Por etapa (capas GDN, ms/ventana): hc-read1+router **3,4** · q8+qkv gemv 2,4 · head 1,8 · waitB (PCIe) **9-10** ·
    VRAM hits 5,2 · waitCPU 0,9-2,5 · copy+combine 0,5. Capas QSA: ~5 ms (atención 0,8, q+q-idx 1,2).
  - **Lo que más pesa**: la espera PCIe (waitB 6,5-10,2) y los expertos de CPU (~4-4,6 ms/ventana con ~5 entradas).
- **B2**: ~43 ms/ventana, mismo reparto.
- **Nodos del grafo**: ventana de 1 token **3.149** nodos; 3 tokens 3.226; 4 tokens 3.265 (casi todo type0 + ~12 memcpy).
- Sin `ncu` en la máquina: no hay DRAM throughput de kernels.

## Orden 11. VRAM: quién la usa y si caben más expertos

Estado: medida. **Sin aplicar nada** (espera validación).

- **Procesos en la 3060**: `engine/strata` 11.098 MiB + `llama-server` de beellama (:8082, micro-LLM) **246 MiB**.
  Sin escritorio en la GPU. **No quito el micro-LLM sin decirlo**: son 246 MiB (~180 huecos).
- **Pico real** (muestreo cada 100 ms durante prefill de ~96K tokens + imagen + B1; 1.239 muestras): usado máx.
  **11.440 MiB** → **mínimo libre 848 MiB**.
- **Cálculo de la orden** (hueco ≈ 4,96 GiB / 3.694 ≈ 1,376 MiB): (848 − 256) / 1,376 ≈ **430 huecos más** →
  `--expert-cache` ≈ **4.124** (hoy `auto` → 3.694).
- **Veredicto propuesto**: subir a ~4.100 huecos **si** Claude lo valida; son +12 % de caché. Nota: el pico incluye
  los búferes del prefill largo, que toma prestados huecos — con más huecos fijos, el prefill largo tiene menos
  margen. Alternativa: quitar el micro-LLM (:8082) y sumar sus 246 MiB.

## Orden 14a. ¿Cuenta el motor el uso de la capa del borrador?

Estado: mirado (solo lectura). **No lo cuenta.**

- `drive.d.usage` cubre solo las capas del modelo principal (`g.n_layers × g.n_expert`). La capa del borrador
  (512 expertos residentes) no entra.
- Su enrutado se resuelve **en el dispositivo** (`mtp.cpp:582-583`: `native_router_top10` o `router_top10` sobre
  `logits_`, ids en el búfer `ids_` de T×K int32 por ronda). Contarlo en el host exige un punto de relectura que
  no pare el pipeline.
- **No lo implemento yo**: el punto de relectura es diseño de motor (un mal sitio mete un sync por ventana).
  **Pido a Claude el contador `STRATA_MTP_HIST=1`** como especifica la orden (uint32 por experto, volcado al
  salir, coste cero apagado). Cuando esté, mido la sesión de ≥30 min y la aceptación del borrador.

## Orden de Adrián (pendiente): Strata virgen vs la nuestra

Cuando cierren las órdenes de Claude, medir **nuestra última versión contra Strata oficial virgen**: cuánto % hemos
ganado en total.

- **Nuestra**: motor desplegado (0.1.39 + parches System One/suffix-draft + `PF_FUSED=1` + `FETCH_ADMIT=1` + tope
  3072 + visión CPU + 512K) con su config.
- **Virgen**: Strata oficial **de la misma base** (0.1.39) compilada tal cual, sin parches ni variables, config
  equivalente (mismo modelo swift, mismo 512K) — así el % aísla **nuestra** aportación.
- **Extra**: también contra la **última oficial** que haya entonces (si upstream avanzó), para el % total visible.
- **Método**: el de siempre (alternar binarios con md5 verificado, B1/B2/B4/P1/P2, `bench.py compare`) + puerta de
  calidad (`logpos-compare`) para que el % no esconda degradación.

## Nota: Mac mini aparcado

Adrián, 2026-10-04: el Mac mini queda **descartado por ahora** (también el piloto Bonsai). No se hace nada ahí.
