# Informe 04 - Bucle de decode, speculative decoding y contexto largo (KV) para RTX 3060 12 GB

Auditoría de solo lectura de `/home/user/Strata3060` (fork de Strata 0.1.38). No se modificó ningún archivo del repo; los
scripts de simulación y los binarios de prueba están en `simulaciones/`.
Sin GPU ni nvcc: todo lo que depende de la 3060 es **estimación**, con la aritmética a la vista.

Convenciones de etiqueta en todo el informe:

- **[M]** medido y publicado en el repo (con la fuente).
- **[C]** derivado exactamente del código o de constantes del código (aritmética, no medición).
- **[E]** estimación mía; siempre con los supuestos al lado.
- **[S]** resultado de una simulación Monte Carlo propia (modelo de aceptación y de coste *supuestos*; ver Apéndice B).

---

## 1. Resumen

1. **Un paso de decode** = ronda de verify (T tokens por las 48 capas, GPU y CPU en ping-pong capa a capa) + `commit` (reproduce
   el GDN de los tokens aceptados) + draft MTP (2-4 pasos de grafo, cada uno con `cudaStreamSynchronize`). En el 5070 una
   ronda de ~34 ms se reparte 14.6 GPU + 13.4 espera a CPU + 2.0 draft + 4.3 otros [M, paper Tabla 5]. No hay rollback de KV:
   las celdas rechazadas se sobrescriben; el GDN no se toca durante el window y `commit(n_keep)` lo avanza reproduciendo
   solo los aceptados desde entradas guardadas (`src/core/verify.cpp:956-1020, 1326-1354`).
2. **La longitud del draft no usa un modelo de coste para el MTP.** Es un tope fijo de 3 drafts (`--spec 4`) recortado por
   un umbral **por posición** sobre la probabilidad top-1 del drafter (`--spec-min-p 0.5`, `setup.py:3552`;
   `generate.cpp:5703-5707`). El único componente adaptativo con coste medido (`spec::DraftPolicy`, EMA del tiempo de ronda
   por tamaño de ventana) solo decide **lookup vs MTP** (`generate.cpp:5713-5720`). Existe además un `spec::Controller`
   analítico completo, pero **no se usa en el motor** (solo en su test) y sus constantes son las del 5070.
3. **El coste del 5070 está "grabado"** en: `min_p=0.5`, `--spec 4`, el prior `kShape` (`draft_policy.cpp:11`), el
   `CostModel` (`controller.hpp:25-37`) y el test (`draft_policy_test.cpp:17`, A=19 ms, B=10.5 ms/token, obsoleto: el
   split DMA actual da B≈5). `tools/calibrate.py` mide `spec_min_p` solo en {0.3, 0.5, 0.7} y con 3 prompts greedy de 128
   tokens (`calibrate.py:33-35`).
4. **Ganancia esperable de una longitud de draft adaptativa en la 3060: modesta en el caso central, grande si la CPU manda.**
   Simulación [S]: con B/A = 0.26 (lo más probable para 3060+Zen2/3 AVX2) +0.3 a +2.5 %; con B/A = 0.35-0.45 (CPU lenta
   y/o PCIe 3.0) +1 a +10 %. Un `--spec-min-p` estático retocado (0.6-0.8) captura 40-65 % de esa ganancia a B/A=0.26 y 70-95 % a B/A≥0.35: **primero
   ampliar `--calibrate`** (barato), **después** la política por modelo de coste (robusta a cambios de contenido y muestreo).
5. **Las requests muestreadas (la web app usa T=0.6/top_p 0.95/top_k 20 por defecto, `serve/web/app.js:464`) no están
   medidas.** Todas las cifras publicadas son greedy. En muestreo el draft es argmax y se acepta solo si coincide con el
   sorteo Philox del verify (`verify.cpp:1170-1187`); el "coupled draft sampling" que mejora eso está **apagado por defecto**
   (`coupled_draft.hpp:54`).
6. **Camino crítico en el host** (entre pasadas GPU): espera activa por capa (`verify.cpp:1074-1127`), PLE `gather_batch`
   síncrono antes de lanzar el grafo (`verify.cpp:1044-1053`), `cudaStreamSynchronize` por paso de draft (`mtp.cpp:849-867`),
   `printf` sin buffer por token (`generate.cpp:1068, 5770`) y, cada 4 rondas, `apply_pending` que espera a que **aterricen
   ~136 MB** de swaps de expertos (`generate.cpp:5732, 5756`): en PCIe 3.0 son ~11 ms de copia contra ~5 ms de draft+commit que
   los esconden [E]. El tokenizer Python **no** es relevante: medí 275K tok/s con un vocab de juguete (Apéndice C).
7. **KV**: bytes por token de contexto en VRAM (12 capas QSA + capa del drafter): **15.7 KB/token en int8 = 16.0 MB por 1K
   tokens** (incluye claves del indexer fp32, 128 B/celda/capa, y tablas rope); fp16 28.6 KB, q4_0 9.4 KB, K8V4 12.8 KB [C]. Las
   capas GDN no tienen KV (estado fijo 112 MiB). Con streaming (≥64K) el marginal baja a 2.9 KB/token + 450 MB fijos [C].
   Mi modelo reproduce la Tabla 4 del paper (expertos en VRAM a 4K/32K/64K/128K/262K) con error ≤3 % [C vs M].
8. **Contexto en 12 GB**: con streaming caben los 262K con ~3,7-3,9K expertos (vs 4,5K a 1K de contexto); el coste en
   tok/s medido en el 5070 es 93→82→76→74→60 (4K/32K/64K/128K/262K, Q2_0) [M]. El setup recomienda 32K para <14 GB
   (`setup.py:3266`): con ≥48 GB de RAM 64K cuesta ~7 % y duplica el contexto útil.
9. **KV q4_0 sí está medido** (`bench/results/2026-09-27-kv-q4/README.md`): KL 2.6-4.2x el de int8, perplexity +8-12 % en
   documentos. K8V4 solo tiene needles. Con la restricción "sin pérdida de calidad": **mantener int8**. Ahorros de q4/K8V4
   en 12 GB: +0.3-0.7 % de tok/s a 64K [E]; no compensan.
10. **Específico de Ampere (sm_86)**: los kernels de top-k de selección QSA y el argmax por clusters son **sm_90+**
    (`qsa_select.cu:988-1068`, `sampler.cu:960-990`). La 3060 usa los kernels de una sola CTA: 200 µs/llamada a 262K
    (medido en el 5070, `qsa_select.cu:656-657`; ~280 µs en la 3060 [E]) x 12 capas = ~3.4 ms por ventana = 5-6 % a 262K,
    ~1.7 % a 128K [E]. DETAILS.md:25-28 documenta +5 % (4K) y +18 % (128K) por los clusters en el 5070 [M] (cota superior de
    lo recuperable).
11. **Conversation cache barato**: checkpoint de 118 MB de RAM **pageable** (0 VRAM), hasta 6 = 0.7 GB [C]. El parking de
    conversaciones (opt-in) cuesta ~15 KB/token + checkpoints: **inviable** en un host de 32 GB con arena de 23-34 GB.
12. **Premisas que no se sostienen**: (a) el router de la capa MTP **no** predice los expertos de las 48 capas (la capa MTP
    tiene sus propios 512 expertos, en VRAM, `mtp.cpp:182-202, 568`); (b) drafts en árbol no pagan con coste marginal por
    token de 0.2-0.3 del coste base (aritmética en §2.9).

Propuestas mejor rankeadas (detalle en §4): (1) ampliar y automatizar `--calibrate` (min_p hasta 0.9, profundidad MTP,
presupuesto de swaps, short_read); (2) política de longitud por modelo de coste con calibración online; (3) presupuesto de
swaps del tier adaptativo consciente de PCIe; (4) `--draft-vocab en` como preset de 12 GB; (5) medir y, si procede, activar
coupled draft para requests muestreadas; (6) presets de contexto 64K/128K con streaming; (7) top-k/argmax multi-CTA sin
clusters para sm_80-89.

---

## 2. Hallazgos (archivo:línea)

### 2.1 Un paso de decode de punta a punta

Bucle del servidor (`generate.cpp:5702-5807`; la variante no-serve repite la lógica en `6540-6660`):

| # | Paso | Dónde | Notas |
|---|---|---|---|
| 0 | Config: `S = o.spec`, `S_mtp`, `DraftPolicy policy(S)` | `generate.cpp:5034-5038` | Con `--spec 4` y lookup activo, `1588-1591` hace `mtp_max_t=4` y `spec=min(4+2,8)=6`: ventanas MTP ≤4 (3 drafts), ventanas lookup ≤6 (5 drafts). `kVerifyMaxT=8` (`verify_kernels.hpp:21`). |
| 1 | Elegir `T`: `T=S_mtp`; con `req_spec_min_p>0`, `T=1; while(T<S_mtp && dprob[T-1]>=min_p) ++T`. Primera ventana siempre `T=1`. | `5703-5708` | `dprob[j]` = prob. top-1 del drafter para el draft j (`row_top_prob`, `verify_kernels.cu:264-280`), **renormalizada sobre el subset del draft vocab** (40.5K-106K tokens). |
| 2 | Lookup (prompt lookup): `sfx.propose(S-1)`; solo se considera si `sbuf[0]==drafts[0]`; `policy.choose(T,k,match)` | `5713-5720` | Único uso de `DraftPolicy`. |
| 3 | Cargar ventana `[x, drafts...]`, `apply_pending(!adapt_nowait())` | `5724-5732` | Espera bloqueante a que aterricen los swaps de la ronda de adapt anterior (por defecto, `#463`, `generate.cpp:850`). |
| 4 | Penalizaciones: `penalty_rows` + `cudaMemcpy` síncrono por ventana | `5733-5743` | Solo con penalties. |
| 5 | **Verify**: `ver.run(T, ...)` | `5746`, `verify.cpp:1022-1210` | Detalle abajo. |
| 6 | Aceptación: `a` = prefijo donde `window[a+1]==outv[a]` | `5750-5751` | Igualdad exacta con el argmax (greedy) o con el sorteo Philox (muestreo). |
| 7 | Hilo `adapt()` cada `adapt_every=4` rondas | `5754-5757`, `4794-4862` | Hasta 96 swaps (≈136 MB Q2_0) por `cudaMemcpyAsync` en stream propio. |
| 8 | **Commit** `ver.commit(a+1)` (asíncrono, `set_commit_async`) | `5758`, `verify.cpp:1326-1354` | Lanza el grafo de commit; sin espera (evento). |
| 9 | Emitir tokens: `printf("T %d\n")` por token, `fflush` | `5769-5776` | `stdout` sin buffer (`generate.cpp:1068`): un `write` por token. |
| 10 | **Draft MTP** `mtp.draft(...)` | `5783-5784`, `mtp.cpp:826-878` | Grafo `round` (catch-up K/V de T filas + capa completa para la fila `a`) + `sync`; luego pasos de cadena `j=1..` con `sync` cada uno, mientras `pj >= min_p`. |
| 11 | `policy.observe(from_sfx, T, a, match, round_ms)` | `5800-5802` | `round_ms` incluye verify+commit+draft **y** las esperas de adapt. |
| 12 | `x = outv[a]; p += a+1` | `5805-5806` | |

**Dentro de `Verifier::run`** (`verify.cpp:1022-1210`): staging de pasos/posiciones (`1035-1043`); **gather PLE síncrono**
de T x 16 filas de la tabla n-gram (`1044-1053`, `ngram.cpp:359-372`: modo `Direct`, `issue`+`collect` bloqueantes); lanza
**un grafo CUDA por tamaño de ventana** (`1064`); y por cada una de las 48 capas el host hace spin hasta que la GPU toque el
doorbell (`1082-1100`), llama al pool de CPU (`1107-1109`, `expert_source.cpp:1855-2040`: plan de expertos distintos en
orden de ruteo; los últimos `(nmiss*pcie_num)>>8` fallos van por DMA, el resto a la CPU), y levanta el flag (`1124`). Al
final `cudaStreamSynchronize` x2 (`1134, 1137`). El argmax greedy va **dentro del grafo**; el muestreo corre fuera, con otro
`sync` (`1178-1187`).

**Rollback** [C]: (a) KV de celdas rechazadas: nada que deshacer, "se sobrescriben al reprocesar esas posiciones antes de
que cualquier query las lea" (`verify.hpp:15-17`); con KV streaming los writers escriben la copia host siempre y el slot solo
si está residente (`kv_stream.hpp:15-17`). (b) GDN: el window no toca el estado; `capture_commit` (`verify.cpp:956-1020`)
hace `gdn_conv_commit` + `gdn_step_norm_multi` sobre los `n_keep` tokens aceptados (lee/escribe una vez los 36 x 3.27 MB =
112 MiB: ~0.65 ms de tráfico a 360 GB/s [E]). (c) Indexer: `tail_snap_` restaurado y claves aceptadas re-añadidas
(`993-998`). (d) PLE history: snapshot (`1002`).

### 2.2 Cómo se elige la longitud del draft (¿fija o adaptativa?)

- **MTP**: tope fijo (`S_mtp-1 = 3` drafts) + corte por probabilidad **por posición**. El corte ocurre dos veces: en la cadena
  (`mtp.cpp:859`: no se computa el paso j+1 si `pj < min_p`) y al fijar `T` (`generate.cpp:5704-5707`). El `min_p` es
  global (`--spec-min-p`, 0.5 en `setup.py:3552`), por request (`strata_tune.spec_min_p`, `serve/server.py:488`,
  `generate.cpp:5102`) o calibrado (`tools/calibrate.py`).
- **Lookup vs MTP**: `DraftPolicy::choose` (`draft_policy.cpp:55-82`) compara `E_mtp/coste(T)` contra
  `E_lookup(k)/coste(k+1)` con `E_lookup = 1+q+q²+...` y `q` aprendido por 4 buckets de longitud de match (decay 0.97). Los
  costes son el **tiempo de ronda medido por tamaño de ventana** (EMA 0.1, `draft_policy.cpp:84-91`), con prior
  `kShape = {1, 1.35, 1.7, 2.05, ...}` para tamaños no vistos (`:11`) y 3 sondeos (`kProbes`) antes de que un coste
  adivinado pueda vetar. Es decir: **ya hay medición de coste por máquina**, pero no se usa para decidir la longitud MTP.
- **`spec::Controller`** (`controller.hpp/.cpp`): modelo analítico `T(n) = dense(n) + cpu(n) + sync + draft` con constantes
  del 23-sep en el 5070 (`dense_ms=11.0`, `cpu_all_miss_ms=15.8` para 480 expertos, `hit_rate=0.55`, `sync_ms=2.4`,
  `mtp_draft_ms=1.2`, razones de expertos distintos U(n)/U(1) = 1.70, 2.31, 2.88, 3.40, 3.89, 4.35, 4.80). **No se invoca
  desde `generate.cpp`** (`grep Controller src` solo da `controller.cpp` y `controller_test.cpp`). Ejecuté su test: con
  `hit_rate=0.72` (valor medido) predice 17.8 ms por token plano (56 tok/s) y 35.9 ms para k=3 (el paper mide 34.3 ms
  para T̄≈3.66): el modelo, bien parametrizado, ajusta al ~5 %. La salida del propio test del repo (`controller_test`, modelo por defecto) elige
  k=1 para p=0.6-0.8, k=3 para p=0.86-0.90 y k=5 para p=0.95.
- **Datos reales de aceptación** [M] (`bench/results/2026-09-29-speed-0126/matrix.json`, Q2_0 5070): `spec_accept`
  (aceptados/ofrecidos) 0.68 (1K), 0.84 (4K), 0.79 (32K), 0.71 (64K), 0.68 (128K); `tokens_per_round` 2.72 / 3.23 en 1K/4K.
  Con 3.23 tokens/ronda y 0.838 se deduce `T̄ ≈ 3.66` (de 4 máx.): con `min_p=0.5` el recorte quita ~0.34 drafts/ronda;
  la ventana de 4 es casi siempre la usada. Coder a 128K: acepta 0.55 y baja de 53.2 a 43.0 tok/s [M].
  La sonda del drafter (`mtp.hpp:3-5`) da 0.89/0.86/0.85 de aceptación condicional por paso 1-3 sobre el modelo BF16.
  Con p=0.86 iid por posición y 3 drafts, E[tokens/ronda] = 1+0.86+0.74+0.64 = 3.24 [C], igual al 3.23 medido a 4K.

**Conclusión 2.2**: la regla vigente (`dprob_j >= 0.5` por posición) ignora que el beneficio marginal de añadir el draft j es la
probabilidad **acumulada** `Π q_m` y que su coste es `ΔC(T)/C(T)` (≈0.19-0.26 de la ronda base). Con q=0.55 por posición
la regla óptima da T=3, la vigente T=4 (−5 % esa ronda) [C/E]. Para el rango de aceptación 0.75-0.85 medido en el 5070 la
regla vigente es casi óptima [S]: por eso las cifras publicadas se ven bien; el problema aparece con aceptación 0.55-0.70
(contexto largo, razonamiento "thinking high" por defecto, muestreo T>0) o con B/A alto (CPU lenta).

### 2.3 El coste de verificar k tokens (unión de expertos) y qué cambia en la 3060

- La ventana lee los pesos densos una vez para T tokens; la CPU computa la **unión** de expertos fallados: 1.75x / 2.4x /
  3.05x los fallos de un token para T=2/3/4 [M, `verify.hpp:10-11`]; 1.70/2.31/2.88/3.40/3.89/4.35/4.80 para n=2..8
  [M, `controller.hpp:30`]. El incremento marginal de CPU por token baja con T (0.75, 0.65, 0.65, 0.52, 0.49, 0.46, 0.45 de
  los fallos de un token).
- Modelo lineal ajustado al paper [E]: ronda `W(T) = A + B(T-1)` con A≈19 ms, B≈4.4-5.0 ms (de 34.3 ms con T̄=3.66 y 2.0 ms de
  draft: B=(34.3−2.0−19)/2.66=5.0). `B ≈ 3.3 (CPU) + 1.0 (GPU, expertos en caché) + 0.7 (host/commit)`. El `cost(t)=19+10.5(t-1)`
  de `draft_policy_test.cpp:17` es el coste **sin** DMA (todos los fallos a la CPU): obsoleto.
- 3060 + Ryzen 5 3600/5600 o i5-12400 (AVX2, DDR4-3200) [E]: GPU x1.5-1.9 (la lectura densa de 3.3 GB pasa de 4.9 a 9.2 ms
  al piso de ancho de banda; el resto de los 14.6 ms es latencia de kernels que escala con reloj/SM); CPU x1.5-2.0 (el paper
  dice que las i-quants ya son limitadas por aritmética y que Q2_0 canónico exige AVX-512, `generate.cpp:1839-1844`:
  en AVX2 usa el pack nativo). Resultado: A≈30-36 ms, B≈7.8-9.4 ms → **B/A ≈ 0.26-0.31**, casi igual que el 5070 (0.23-0.26).
  O sea: el cociente de costes de la 3060 no es radicalmente distinto, y por eso la ganancia central de re-sintonizar el draft
  es pequeña; crece si la CPU es peor de lo supuesto o el link PCIe más lento (B/A 0.35-0.55).
- `pcie_frac` ya se adapta al ancho de banda medido (`generate.cpp:1824-1838, 1058-1060`: `base*min(1, GB/s/20)`), pero la
  fracción por DMA (0.55 nativos, 0.2 canónico) y la cadencia de swaps del tier adaptativo no.
- PCIe 3.0 x16 (~12 GB/s) vs 4.0 (~25 GB/s) [E]: para IQ2_XS (hit 0.78, Tabla 5) un window T=4 tiene 0.22x480x3.05 ≈ 322 fallos distintos; con
  55 % por DMA son ~177 expertos x 1.44 MB ≈ 255 MB: ≈20 ms en 3.0, ≈10 ms en 4.0, contra 21 ms de espera de CPU en el 5070. La sonda reduce `pcie_frac`
  en enlaces lentos suponiendo una CPU como la del 5070 (`pcie_frac_for_gbps`, comentario `generate.cpp:1053-1057`); con una CPU 1.5-2x más
  lenta el óptimo se desplaza hacia **más** DMA, así que la heurística puede errar en ambos sentidos en un host barato: razón para medir
  (`--calibrate` barre 0, .2, .35, .55, .75) o ajustar online (propuesta 8).

### 2.4 Muestreo y camino crítico entre pasadas GPU

1. **Muestreo** (`generate.cpp:5568-5589`, `verify.cpp:1170-1187`, `sampler.cu:994-1031`): greedy = argmax en el grafo (en sm_90+
   con clusters, `sampler.cu:960-990`; en sm_86 un bloque de 1024 hilos por fila). Con `temperature>0` o penalties: `sample_tokens`
   fuera del grafo (kernel `split` top-k: 61 bloques x filas) + `cudaStreamSynchronize` (+~0.1-0.2 ms/ventana [E]). El sorteo de
   la fila t es Philox(seed, pos0+t): la salida depende solo de la semilla, no de los drafts (exacto, `coupled_draft.hpp:3-10`).
   Los drafts son argmax; **aceptación muestreada = P(sorteo del target == draft)**, menor que la greedy por un factor
   ≈ E[p'_top | coincide]. Con p'_top≈0.85-0.9 a T=0.6: aceptación 0.84 → ~0.73, tokens/ronda 3.2 → ~2.9 (−9 %) [E, sin medir].
   El repo no tiene ninguna medición de velocidad con muestreo (`tools/calibrate.py:91` fuerza `temperature: 0`).
2. **Host síncrono por ronda**: ver tabla §2.1 (pasos 3, 5, 9, 10) y: PLE `gather_batch` antes del lanzamiento (con
   `--ple-io direct`, lectura SSD ~100-200 µs si no está en la caché de 1M filas; ~0.3-0.5 % de una ronda de 50 ms [E]);
   `printf` sin buffer (1 syscall por token, hasta 4 por ventana; ~0.05-0.2 ms en Windows [E]); `cudaStreamSynchronize` por paso
   de draft (~30-100 µs cada uno según SO [E]).
3. **`apply_pending` bloqueante** (`generate.cpp:5732`): cada 4ª ronda espera a que aterricen hasta 96 swaps. Copia en 4.0:
   136 MB/25 GB/s = 5.4 ms (casi oculta tras ~5 ms de commit+draft); en 3.0: 136/12 = 11 ms → ~6 ms de parada cada 4 rondas =
   1.5 ms/ronda = ~3 % de 50 ms [E]. Hay precedente de `--adapt-every 1` en la config del RX 6900 XT (`AMD_HIP.md:334-336`).
4. **Servidor**: la detokenización es incremental O(1) por token (`server.py:917-940`); las líneas `T n` se leen en un hilo aparte
   (`server.py:369-380, 542`): fuera del camino crítico salvo contención de CPU con el pool (el pool usa "todos los núcleos físicos menos el
   del host", `generate.cpp:260`). **Tokenizer**: Python puro sin memo (`strata_tokenizer.py:205-214`), llamado en `prepare()` **antes** del
   FIFO (`server.py:1341-1342, 1450`), así que está en la latencia de cada request. Medido con vocab de juguete (3,000 merges): 275K
   tok/s en un Xeon 2.8 GHz; con memo por pieza 1.0M tok/s en frío y 2.0M en caliente, ids idénticos (Apéndice C). Para
   100K tokens de historial: ~0.4-1 s [E] por turno: **no es cuello**.

### 2.5 KV: formatos, bytes y selección QSA

- Geometría QSA real (`qsa.hpp:109-117`): 24 cabezas Q, **2 cabezas KV**, head_dim 256, indexer 4 cabezas x 128, bloque=página=4 celdas,
  `idx_top_k=2048` celdas. Ancho de selección = `min(n_kv, 2048+3)` (`qsa.hpp:127-135`) → **~513 bloques de 4 celdas por capa y
  por token**. En int8, 2,051 celdas x 1,056 B = 2.17 MB/capa/token; x12 capas = **26 MB/token** = 0.07 ms a 360 GB/s: la lectura
  del KV **no** es un cuello, y los 3 MB de L2 de la 3060 son irrelevantes aquí (el reuso entre las T filas de un window sería
  pequeño de todas formas: 8.7 MB/capa/ventana > L2).
- Scoring del indexer: `block_scores_multi_kernel` lee cada clave pooled **una vez para todas las filas** de la ventana
  (`qsa_select.cu:600-646`). Coste: `ctx/4 x 512 B` por capa = 33.5 MB a 262K → 403 MB/ventana en 12 capas = **1.1 ms** a 360 GB/s
  (0.6 ms en el 5070) [C/E].
- Formatos [C] (`kv_q8.hpp:25-27`, `kv_q4.hpp:36-39`, `layer.cpp:514-523`): por celda y capa fp16 2,048 B; **int8 1,056 B**
  (K,V: 2x256 B de códigos + escala fp16 por 64 valores); q4_0 576 B (Hadamard 256 + bloques q4_0); K8V4 816 B (K int8 + V q4 rotado).
  El setup usa fp16 hasta `ctx<=8192` y int8 por encima (`setup.py:3314`; la documentación dice "above 4K": discrepancia menor).
  `--kv-resident N` mínimo 20,480 (`layer.cpp:528`); setup usa 32,768 desde ctx≥65,536 (`setup.py:3585-3586`).
- **Bytes por token de contexto en VRAM** [C] (`layer.cpp:495-547`, `session.cpp:54-73`): por capa (cell + indexer fp32 128 B +
  1 B de page table) x (12 capas QSA + 1 del drafter, que sin streaming guarda el KV completo y `pooled_rows` completas, `layer.cpp:497-507`)
  + 256 B de tablas rope (una vez, compartidas):

  | Formato | B/celda/capa | VRAM por token de contexto | por 1K tokens |
  |---|---:|---:|---:|
  | fp16 (ctx ≤ 8K) | 2,048 | 28,557 B | 29.2 MB |
  | **int8** | 1,056 | **15,661 B** | **16.0 MB** |
  | q4_0 | 576 | 9,421 B | 9.6 MB |
  | K8V4 | 816 (drafter int8) | 12,781 B | 13.1 MB |
  | int8 + streaming 32K resident | 1,056 | 2,866 B + **450 MB fijos** | 2.9 MB + 450 MB |

  Las capas GDN no tienen KV: estado fijo 36 x (128x48x128 + 10240x3) x 4 B = **112.2 MiB** (el "~118 MB" del checkpoint).
  En streaming el marginal por token es: claves del indexer fp32 (1,536 B, **no se streamean**: la selección puntúa todas), page
  tables (12 B), rope (256 B), el pool de staging de una capa para el prompt (1,056 B, `prefill.cpp:1769-1775`), buffers de
  scores del verify. El drafter usa un anillo de 32,768+... celdas (`mtp.cpp:214`).
- **Streaming**: autoridad en RAM pinned (13.7 KB/token: `setup.py:3567`), VRAM = pool de `n_slots` páginas con reemplazo CLOCK
  hecho en GPU dentro del grafo (`kv_stream.cu:70-150`); fallos copiados por kernel zero-copy sobre PCIe. Medido (RTX 5090, 128K de ctx,
  32K resident, `bench/results/2026-09-30-community-rtx-5090/engine.log`) [M]: **99.4 % de aciertos con prompt de 4K, 97.5 % con 32K,
  96.2 % con 128K**; 52-319 MiB leídos de RAM por request de 256 tokens = 0.2-1.25 MiB/token → en PCIe 3.0 ≈ 0.1 ms/token
  (≈0.3 ms/ventana, <1 %) [E]. El peor caso de ancho de banda (todo por PCIe) serían 26 MB/token = 2.2 ms (3.0).
- **Calidad medida** [M] (`bench/results/2026-09-27-kv-q4/README.md`, teacher-forced contra fp16): int8 → top-1 igual 92-98 %, KL
  0.009-0.114; q4_0 → top-1 igual 88-96 %, KL 0.028-0.297 (2.6-4.2x), **perplexity de documento +8 % (1K) y +12 % (8K)**, needles 5/5.
  K8V4: solo "mismos needles" en RTX 3090 (`DETAILS.md:81-85`), sin KL. Observación: incluso int8 a 8K tiene KL 0.114 y top-1 92.3 %:
  no hay holgura para bajar más sin romper la restricción de calidad.

### 2.6 Presupuesto de VRAM: contexto vs expertos vs tok/s (12 GB)

Supuesto: la 3060 12 GB (12,288 MiB) deja la misma VRAM útil que el 5070 12 GB (mismo `--vram-reserve-mib 700`, mismos buffers), así que el reparto es el mismo.
Calibración: 4,517 expertos Q2_0 a 1K de ctx [M, paper Tabla 4]; 1 experto ≈ 1.416 MB (ajustado: (4517−4174)/31K tokens x 16.0 MB/1K).
`expertos(ctx) = 4517 − (VRAM_ctx − VRAM_1K)/1.416 MB`. Resultado (script `sim/vram.py`):

| ctx configurado | KV VRAM sin streaming | expertos (modelo) | medido (Tabla 4, v0.1.14, sin streaming) | KV VRAM con streaming | expertos con streaming (modelo) | RAM pinned del KV | tok/s Q2_0 5070 [M, DETAILS:49] |
|---|---:|---:|---:|---:|---:|---:|---:|
| 4K | 64 MB | 4,483 | 4,464 | - | - | - | 93.0 |
| 32K | 513 MB | 4,166 | 4,174 | (no streamea: ctx ≤ resident) | - | - | 81.8 |
| 64K | 1,026 MB | 3,803 | 3,809 | 638 MB | **4,078** | 0.90 GB | 76.2 |
| 128K | 2,053 MB | 3,079 | 3,079 | 826 MB | **3,945** | 1.80 GB | 73.7 |
| 262K | 4,105 MB | 1,629 | 1,588 | 1,201 MB | **3,680** (medido con streaming: 3,872) | 3.60 GB | 60.3† |
| 512K (yarn) | 8,211 MB | negativo | - | 1,953 MB | 3,149 | 7.20 GB | - |

† 0.1.14; con streaming 62.6 [M, DETAILS:69-72]. Lo que cuesta es el contexto **configurado**, no el usado: configurar 128K en vez de 4K
quita ~12 % de expertos aunque los prompts sean cortos. Con la curva de la Fig. 3 del paper (hit 0.5→0.75 de 4.5K a ~10K
expertos, pendiente ~4.5e-5 por experto [M]), −520 expertos ≈ −2.3 puntos de hit ≈ +8 % de fallos de CPU ≈ −3 % de tok/s [E].

**Máximo contexto manteniendo N expertos** [E sobre el modelo]: N≥4,000: sin streaming ≤46K, con streaming ≤~100K; N≥3,500: sin
streaming ≤92K, con streaming ≤~350K (cubre los 262K nativos). En 12 GB, **con streaming caben los 262K**; el límite pasa a la RAM del host
(13.7 KB/token pinned: 1.8 GB a 128K) y, en hosts de 32 GB, a la arena de expertos (Coder 23 GB).

**¿Streaming desde más temprano?** Ahorro a 48K = 180 MB = +127 expertos (+2.8 %) ≈ +0.5-0.8 % tok/s; coste: 0.64 GB de RAM pinned y
~1-2 % de la ventana en fallos de KV por PCIe 3.0 (peor caso 128K) → neto ≈ 0. El umbral actual (64K) está cerca del punto de equilibrio:
**no cambiarlo**. Reducir `--kv-resident` a 20,480 en ≥64K libera 156 MB (+110 expertos) pero baja el hit rate (sin medir): neto ≈ 0.

### 2.7 Conversation cache, prefix reuse, snapshots

- **Checkpoints** (`ConversationCheckpoint`, `conversation_cache.hpp:22-38`): `std::vector<uint8_t>` de RAM **pageable**: GDN 112.2 MiB + PLE 368 KB +
  colas del indexer; **~118 MB**, 0 VRAM. Hasta `--prompt-cache 6` (0.7 GB). Se toman al inicio de cada turno del asistente y cada 16K
  tokens de prompt (`generate.cpp:5596-5611`); la raíz (fin del system prompt ≥2,048 tokens) está fija (`conv_cache.hpp:30-35`). Guardar/restaurar = una
  copia D2H/H2D de 118 MB: 10-20 ms [E].
- El KV es posicional y está en una sola arena: solo se retienen checkpoints que son prefijo del prompt actual (`generate.cpp:5322-5324`).
  Reutilización: `live` o checkpoint más largo (`5255-5262`); un mensaje nuevo ≤64 tokens se lee por ventanas de verify, no por el camino batched (`short_read`,
  `5397-5403`; el comentario `5389-5395` da ~300 ms fijos del camino batched + ~180 ms de refill de slots prestados (5070/PCIe 5) frente a ~16 ms por token en ventanas; es copia de expertos por PCIe, así que el
  umbral óptimo depende del enlace y de la CPU [E]).
- **Parking** (opt-in, `DETAILS.md:578-616`): K/V de todo el contexto (15.2 KB/token = 13 capas x 1,056 + 12 x 128 de indexer) + live + checkpoints. 32K tokens: 0.5 GB +
  0.8 GB = 1.3 GB; 128K: 2.0 + 0.8 = 2.8 GB. En un host de 32 GB con Coder (23 GB de arena, ~6 GB de SO) quedan <3 GB → el guard
  `--conversation-cache-min-free-mib 2560` lo salta. Con 64 GB (arena 34-36 GB) el presupuesto de 8 GiB admite 2-3 conversaciones de 128K.
- La restauración de snapshots mueve ~15 KB/token por PCIe: 32K tokens = 0.5 GB ≈ 40 ms (3.0) frente a ~30 s de re-leer el prompt.

### 2.8 Hallazgos específicos de Ampere / 3060

1. **Sin clusters (sm_90+)**: top-k de selección → `block_topk_reg_kernel` (claves en registros, hasta ~135K celdas) o `block_topk_kernel`
   (lee de memoria en cada pasada) en **una sola CTA por query** (`qsa_select.cu:1055-1109`). Datos del propio código para el 5070
   (`qsa_select.cu:656-657`): una CTA 21.9/58/200 µs a 32K/128K/262K frente a 15.6/18/22 con clusters. Por ventana: 12 capas x esos µs
   (CTAs de las T queries en paralelo) = 0.26/0.70/2.4 ms en el 5070; en la 3060 (reloj ~1.4x menor, kernel de una SM) 0.37/1.0/3.4 ms [E].
   DETAILS.md:25-28 [M]: clusters en top-k+argmax dieron +5 % (4K) y +18 % (128K) en el 5070.
2. `fused_gr.cu:316-319`: en Ampere (99 KB de smem opt-in) el hc-read v3 usa S=1; no afecta la ruta por defecto.
3. Arquitecturas compiladas: setup compila para el sm detectado (`setup.py:1975-2003`); la 3060 es sm_86 (compatibilidad con
   el código `>= 8.0`: `qsa_block_scores_tc` TF32 funciona en sm_80+, `qsa_select.cu:919-934`).
4. El drafter lee la cabeza de vocab (`row_bytes` ≈ 1,360 B IQ4_XS / 1,760 B Q5_K por token del subset) en **cada paso**: subset CJK
   (106,299 tokens) = 138-178 MiB por paso; subset `en` (40,525) = 53-68 MiB. A 360 GB/s la diferencia son 0.24-0.33 ms por paso x 3
   pasos = 0.7-1.0 ms/ronda (1.3-2 %), y 85-215 MiB de VRAM (`setup.py:2753`: cjk 348 MiB, en 133 MiB) = +60-150 expertos [C/E].

### 2.9 Premisas del encargo que el código desmiente

- **"El router de la capa MTP predice qué expertos necesitará el verify"**: no. La capa MTP es una capa MoE **adicional** con sus propios 512
  expertos (708 MB en VRAM, `mtp.cpp:182-202`) y su propio router (`mtp.cpp:568-571`); sus ids no corresponden a los 48x512 expertos del modelo
  principal. Predictores válidos: reuso temporal (ya explotado por el tier adaptativo: hit 0.72 vs 0.50 solo con perfil, paper Fig. 3) y *lookahead
  gate* (router de la capa l+1 aplicado a la entrada de la capa l): el motor lo implementa **solo** para calentar páginas del GGUF en modo mmap
  (`generate.cpp:3283-3300`, `DETAILS.md:165-168`). Para el pack pineado, sirve a lo sumo para adelantar el DMA del `pcie_frac`; en PCIe 3.0 el DMA ya es escaso y
  los falsos positivos gastan ancho de banda: prioridad baja.
- **Drafts en árbol**: con coste marginal por token ΔC/C = 0.19-0.31 (§2.3) añadir una rama de 1 token cuesta +19-31 % de ronda y, con aceptación
  de la 2ª opción ≈0.3 sobre el ~20 % de rechazos, aporta ≈+6 % de tokens esperados: pérdida neta. Además los kernels GDN del verify y del commit
  asumen cadena lineal (`gdn_conv_commit`, `gdn_step_norm_multi`).

---

## 3. Propuesta técnica principal: longitud de draft adaptativa con modelo de coste (sin pérdida, medida en la máquina)

### 3.1 Diseño

Todo es política de host: **solo cambia qué drafts se verifican; el verify decide cada token** (misma garantía que hoy).

```
estado (por proceso, como DraftPolicy):
  cost[T]        EMA del tiempo de ronda por tamaño de ventana T=1..S   (ya existe: draft_policy.cpp:84-91)
  cal[j][b]      (aceptados, probados) por posición j<=2 y bucket b de dprob  {<.3,<.5,<.7,<.85,>=.85}, con decay
q̂(j,p) = (ok + N0*κ0*p) / (n + N0)         # prior κ0=0.9*p, N0=4

al terminar mtp.draft con n drafts y probabilidades dprob[0..n):
  E=1; cum=1; best=1; rate=1/cost[1]
  para j en 0..n-1:
      cum *= q̂(j, dprob[j]);  E += cum
      r = E / cost[j+2]
      si r > rate*(1+margen)  (margen 0.02): best=j+2; rate=r
  T = best                                    # argmax de E/coste, sin suponer monotonía

cadena (dentro de MtpDrafter::draft): reemplazar `pj >= min_p` por un suelo `pj >= 0.35`
  (o por cum*q_típ > E*ΔC/C si se quiere pasar un callback); mantiene min_p como cota de seguridad.

después de verify: para cada draft j probado (j <= a): outcome = (j < a) → cal[j][bucket(dprob[j])]
```

Sitios de integración: `generate.cpp:5703-5708` (serve) y `6555-6558`; `mtp.cpp:859`; clase en `src/spec/draft_policy.{hpp,cpp}`
(función pura, testeable sin GPU). Flag `--spec-policy cost|minp` (default `minp` hasta validar), y clave por request (`strata_tune`) para A/B
intercalado con un solo motor como hace `calibrate.py`.

### 3.2 Qué predice la simulación [S] (Apéndice B, `sim/draft_len_sim.py`, `sweep.py`, `minp.py`, `simple_b.py`)

Modelo: dprob ~ Beta (media μ, correlación de ronda 0.5), aceptación real = κ·dprob (κ=0.95 greedy, 0.85 muestreo), coste de ronda
`A + B(T-1)` + 1.2 ms por paso de draft. Ganancia de tokens/ms respecto a la regla vigente (`dprob>=0.5` por posición, máx. 3):

| B/A | μ=0.6,κ=.85 | μ=0.7,κ=.85 | μ=0.8,κ=.85 | μ=0.8,κ=.95 | μ=0.9,κ=.95 |
|---:|---:|---:|---:|---:|---:|
| 0.20 | +1.5 | +1.2 | +0.8 | +0.3 | +0.1 |
| **0.26** (5070 / 3060 central) | +2.4 | +2.5 | +1.8 | +0.8 | +0.3 |
| 0.35 | +5.7 | +5.6 | +4.0 | +2.2 | +0.9 |
| 0.45 (CPU lenta o PCIe 3.0) | +10.1 | +10.4 | +7.7 | +4.2 | +1.8 |
| 0.55 | +16.1 | +16.2 | +12.3 | +6.8 | +3.0 |

- La versión simple (suelo de cadena 0.35 + argmax posterior de E/coste) retiene ~90 %: +2.0 a +2.3 % a B/A=0.26 con aceptación 0.6-0.7.
- Un **`--spec-min-p` estático** óptimo (0.6 para B/A=0.26; 0.7 para 0.35; 0.8 para 0.45) da: +1.0-1.6 % / +3.6-4.7 % / +7.1-9.8 %,
  es decir 40-65 % de la ganancia de la política por coste a B/A=0.26 y 70-95 % a B/A≥0.35: `tools/calibrate.py` ya barre {0.3,0.5,0.7} pero
  **no llega a 0.8-0.9** y mide con `MIN_GAIN=3 %` (una mejora de +1-2 % no se conserva).
- Cadenas más largas (hasta 5 drafts): solo pagan con aceptación ≥0.9 y B/A ≤0.2 (+2 a +7 %); con aceptación 0.7-0.85 son neutras o −1 a −2 %.
  Debe ser una dimensión de calibración, no un nuevo valor por defecto.
- Rango plausible en la 3060: **+1 a +4 % central, hasta +10 % si la CPU/PCIe son el cuello, ~0 % en el mejor caso (aceptación ≥0.85, B/A bajo)**.
  El beneficio mayor es de **robustez**: la política se re-sintoniza sola cuando cambia el contenido (código→prosa), el contexto (acepta menos a 128K+)
  o el muestreo, sin recalibrar.

### 3.3 Exactitud/lossless y cómo validarlo

- Greedy: la salida es la del argmax de cada fila sea cual sea la ventana (`verify.hpp:3-8`). **Cuidado con los packs IQ**: la CPU calcula un experto con
  `vec_dot` de ggml para un token y con kernels multi-token de Strata para varios, con redondeo distinto; "la misma entrada a temperatura 0 puede acabar
  distinta según los drafts" (`DETAILS.md:87-100`, #152). Para A/B bit a bit: `STRATA_IQ_MT_MIN=1` + `--prompt-cache 0 --adapt-swaps 0 --pcie-frac 0`.
  Con Q2_0 canónico (AVX-512) no aplica; en la 3060/AVX2 el pack es nativo y **sí** aplica.
- Pruebas existentes: `--spec-oracle`/`--spec-corrupt` (`generate.cpp:1216-1218`), `tools/conversation_cache_parity.py`, `STRATA_STATE_HASH`, teacher-forced con
  `STRATA_LOGPOS` (`generate.cpp:5428-5441`). Unit test CPU-only: `src/spec/draft_policy_test.cpp` (**compila y pasa aquí** con
  `g++ -std=c++20 -Iinclude src/spec/draft_policy.cpp src/spec/draft_policy_test.cpp`).

---

## 4. Propuestas priorizadas

Orden = (ganancia x certeza) / esfuerzo para la 3060. "Riesgo de calidad" = riesgo sobre la **salida del modelo**.

| # | Propuesta | Ganancia estimada | Riesgo de calidad | Esfuerzo | Validación en este repo |
|---|---|---|---|---|---|
| 1 | **Ampliar y automatizar `--calibrate`**: `SPEC_MIN_PS` hasta 0.9 (`calibrate.py:34`), nueva clave por request `spec_t`/`mtp_t` (profundidad MTP 3/4/5/6; `max_drafts_` es un entero en runtime, `mtp.hpp:50, 132`), `short_read`, `adapt_every/swaps`, y lanzarlo en el primer arranque si la GPU/CPU no es de clase 5070. | +1-4 % central; hasta +9 % con CPU lenta [S]; profundidad: 0 a +7 % solo con aceptación ≥0.9 [S]. | Ninguno (lossless; solo timing). | S-M (Python + 1 clave en el parser `generate.cpp:5079-5104` y `server.py:488`). | `tools/test_calibrate.py` (14 tests, **pasan aquí**), `serve/test_server.py` (118 tests, pasan aquí), luego en la 3060: barrido intercalado x3, `drafts accepted/offered` del log. |
| 2 | **Política de longitud por modelo de coste con calibración online** (§3). | +1-4 % central, +5-10 % si B/A≥0.35 o aceptación ≤0.7; protege el uso real (thinking, T>0). | Ninguno (lossless). Riesgo de oscilación: histéresis (`margen`) + suelo `min_p`. | M. Función pura + tests CPU; cableado en 2 sitios. | `draft_policy_test.cpp` ampliado (trazas sintéticas), A/B por request con un solo motor, `STRATA_IQ_MT_MIN=1` para identidad de tokens, contadores de aceptación por posición. |
| 3 | **Tier adaptativo consciente de PCIe**: swaps por ronda = `min(24, 0.8*GB/s*t_oculto/blob)` con `adapt_every=1`, en vez de 96 cada 4 rondas (misma tasa media, sin parada en `apply_pending`). | +0-1 % (4.0), **+2-4 % (3.0)** [E: parada ~6 ms/4 rondas de ~50 ms]. | Ninguno (cambia qué expertos están en VRAM; ya ocurre hoy y el redondeo GPU/CPU ya difiere, #463). | S. `generate.cpp:5754-5757, 4794-4862`. | `STRATA_DECODE_TIMING=1` (campo "GPU-reach wait"), hit rate del log, A/B con `STRATA_ADAPT_NOWAIT=1`; sonda PCIe de `generate.cpp:1824`. |
| 4 | **Preset `--draft-vocab en`** para ≤12-14 GB cuando el uso es inglés/código (hoy solo una nota de texto, `setup.py:2757-2769`). | +1.3-2 % tok/s (0.7-1.0 ms/ronda a 360 GB/s) +60-150 expertos [C/E]. | Ninguno para EN/código; CJK casi sin drafts (`DETAILS.md:102-114`). | S (setup + doc). | `tools/test_setup_draft_vocab.py` (CPU-only), `drafts accepted/offered` con prompts EN y CJK. |
| 5 | **Requests muestreadas**: (a) medir velocidad a T=0.6/0.8/1.0, thinking on/off; (b) si hay ganancia, activar `STRATA_SPEC_COUPLED` por defecto para `temperature>0`; (c) calibrar con T>0. | Sin medición: [E] +3-10 % a T≥0.6 por mayor tasa de acuerdo draft/target; **la web app usa T=0.6 por defecto**. | Ninguno por diseño (la salida es función de la semilla, `coupled_draft.hpp:3-10`); riesgo de implementación → tests. | S (flip) + M (validar). | `src/core/coupled_draft_test.cpp` (CPU-only), misma semilla con/sin coupled → texto idéntico, `spec_accept` de `DONE`. |
| 6 | **Presets de contexto para 12 GB**: recomendar 64K (RAM ≥48 GB) y ofrecer 128K con streaming; mantener 32K con ≤32 GB. | Contexto útil x2-4 por −7/−10 % de tok/s [M 5070] (no es ganancia de velocidad). | Ninguno (mismo KV int8). | S (`setup.py:3266-3270` + docs). | `tools/test_setup_choices.py`, `tools/needle_bench.py --lengths 32k,64k,128k --depths 10,50,90`. |
| 7 | **Top-k de selección y argmax multi-CTA sin clusters** (sm_80-89): histograma radix en memoria global entre CTAs o lanzamiento cooperativo. | 262K: hasta ~3 ms/ventana (5-6 %); 128K: ~0.8 ms (1.5 %); 32K: ~0.3 ms (0.5 %) [E]; cota superior documentada +5 %/+18 % (4K/128K) en el 5070 [M]. | Ninguno si los ids son idénticos (se exige hoy: `qsa_select.cu:659-665`). | M-L (CUDA; requiere GPU). | `decode_cluster_parity`, `qsa_topk_active_parity`, `qsa_select_bench` (`CMakeLists.txt:283-293`), ctest. |
| 8 | **`pcie_frac` online** (hill-climb ±0.05 cada N rondas usando espera GPU vs CPU de `STRATA_DECODE_TIMING`). | +1-3 % sobre `calibrate` [E]. | Ninguno. | M. | calibrate vs online, 3 prompts x 3 reps. |
| 9 | Memo del tokenizer por pieza + `short_read` por máquina | tokenizer: ahorra ~0.2-0.5 s por turno de 100K tokens [E sobre el micro-benchmark]; `short_read`: depende del enlace PCIe, sin medir | Ninguno (ids idénticos, verificado en juguete). | S. | `strata_tokenizer.py --check`, comparación contra la versión actual sobre un corpus. |
| 10 | Microoptimizaciones: una sola escritura por ventana de líneas `T n`; PLE rows de la fila 0 en vuelo durante el draft; decidir la parada de cadena en device. | ≤0.4 % cada una [E]. | Ninguno. | S. | `serve/test_server.py`, `STRATA_DECODE_TIMING`. |
| 11 | Profundidad MTP 5-6 | 0 a +7 % solo si aceptación ≥0.9 y B/A ≤0.2; si no −1/−2 % [S]. | Ninguno. | S (flag) | Incluir en #1; contadores por posición. |
| - | **No recomendadas** | | | | |
| | Árboles de draft | negativo (§2.9) | - | L | - |
| | Prefetch de expertos desde el router MTP | premisa inválida (§2.9) | - | - | - |
| | KV q4_0 / K8V4 como defecto | +0.3-0.7 % a 64K [E] | **medido peor** (KL x2.6-4.2, ppl +8-12 %) | - | `bench/results/2026-09-27-kv-q4` |
| | Claves del indexer en fp16 (−200 MB a 262K, +140 expertos, ~+1 %) | +1 % [E] | cambia la selección top-k; hay que medir la tasa de flips (`qsa.hpp` lo exige) | M | no recomendada |
| | Streaming más temprano o `kv-resident` 20K | ≈0 neto [E] | Ninguno | - | - |

---

## 5. Cambios de bajo riesgo implementables sin GPU

Todos verificados como "construibles/ejecutables sin GPU" salvo donde se indica. Ninguno cambia el resultado numérico del modelo.

1. `tools/calibrate.py:34`: `SPEC_MIN_PS = (0.3, 0.5, 0.6, 0.7, 0.8, 0.9)`; añadir barrido de `spec_t` y de `short_read`; evaluar bajar `MIN_GAIN` a 0.02 (la confirmación ya es intercalada x3, pero el ruido de una
   medición suelta es de unos pocos %). Test: `python tools/test_calibrate.py` (pasa aquí, 14 tests).
2. `src/spec/draft_policy.{hpp,cpp}`: `choose_mtp_window(dprob, n, cost[], cal)` + `observe_positions(...)` + prueba sintética en `draft_policy_test.cpp`
   (build con `g++ -std=c++20 -Iinclude ...`, **verificado**). Cableado detrás de `--spec-policy`.
3. Contadores host-side por posición de draft (aceptados/probados) y curva de calibración `dprob → aceptación` (5 buckets) impresos al final de cada request
   (`generate.cpp:5765-5767`) y añadidos al final de `DONE` (el servidor ya ignora campos extra). Es el dato que falta para decidir `min_p` y la profundidad.
4. Refrescar constantes obsoletas: `draft_policy_test.cpp:17` (`19+10.5(t-1)` → `19+5(t-1)`), `controller.hpp` (`hit_rate 0.55 → 0.72`) o **borrar** `Controller` si no se va a cablear.
5. Claves por request nuevas (`spec_t`, `mtp_t`, `adapt_every`, `adapt_swaps`) en `generate.cpp:5079-5104` + `serve/server.py:488-491` + prueba en `serve/test_server.py:570` (patrón existente).
6. Presupuesto de swaps por ronda derivado de la sonda PCIe (`generate.cpp:1824-1838` ya mide GB/s): `swaps_por_ronda = clamp(0.8*GBps*t_oculto/blob, 8, 96/adapt_every)`.
7. `setup.py`: `rec_ctx` para 12 GB (3266-3270); sugerir/activar `--draft-vocab en` con una pregunta; llamar a `--calibrate` tras instalar si la GPU no es de clase 5070. Tests: `tools/test_setup_choices.py`,
   `tools/test_setup_draft_vocab.py`, `tools/test_setup_golden.py`.
8. `tools/strata_tokenizer.py:205-214`: memo por pieza (diccionario acotado a 400K entradas); mismo resultado, medido 3.7x (frío) y 7.4x (caliente) en juguete.
9. `generate.cpp:5769-5776`: acumular las líneas `T n` de la ventana y escribir una vez (el `stdout` es sin buffer, `:1068`).
10. Documentación: `README/DETAILS` dicen "8-bit KV above 4K" pero `setup.py:3314` usa fp16 hasta `ctx<=8192`; aclarar "hasta 3 drafts MTP (S_mtp=4) y hasta 5 de lookup (S=6)".

---

## 6. Preguntas abiertas y plan de medición en la 3060

Preguntas que el repo no responde:

1. **A y B reales** de la ronda en 3060+host (el modelo da B/A 0.26-0.31 [E]). Plan: `STRATA_DECODE_TIMING=1` (`generate.cpp:5683, 5814-5827`) imprime por request
   ventanas, `T̄`, tokens/ventana y la descomposición verify (espera GPU, plan, actq, jobs, CPU) + commit + draft; forzar `T=1` y `T=4` con `spec_min_p=1.0`/`0` en el mismo motor.
2. **Aceptación por posición y calibración dprob→aceptación**, greedy vs T=0.6, con y sin thinking, a 4K/32K/128K (contadores del §5.3).
3. **Velocidad con muestreo**: ningún número publicado; medir con `coupled` off/on.
4. **PCIe real** de la 3060 en el host objetivo (3.0 o 4.0): línea `PCIe probe: x GB/s -> pcie_frac y` del log; parada de `apply_pending` (campo "GPU-reach wait").
5. **Hit rate del KV streaming** con `--kv-resident 20480` vs `32768` a 64K/128K (línea `KV streaming: x% of N block reads hit VRAM`): decide si vale bajar el resident.
6. **Calidad de int8 vs fp16 a ≥32K** (el único dato a 8K muestra KL 0.114): teacher-forced con `STRATA_LOGPOS` en 32K/64K si se quiere justificar fp16 en contextos medios.
7. Q2_0 en AVX2: setup solo usa el pack canónico con AVX-512 (`setup.py:3501`); en la 3060 el pack nativo es el camino. Su B (coste marginal de CPU por token) es lo que más desplaza todo lo anterior: depende del auditor de kernels de CPU.
8. ¿Windows (WDDM) o Linux? Cada `cudaStreamSynchronize`/lanzamiento de grafo cuesta más en WDDM; el paper lo señala (§7).

Plan de A/B recomendado (patrón `docs/COMMUNITY_BENCHMARKS.md`): 3 repeticiones intercaladas, tabla en `bench/results/<fecha>-rtx3060-<cpu>/`, mismas semillas, y para IQ
`STRATA_IQ_MT_MIN=1` cuando se compare identidad de tokens.

---

## Apéndice A. Aritmética de referencia

- Tamaños: KV int8 por celda y capa = 2 cabezas x 256 x 2 (K,V) + 2 x (256/64) x 2 B x 2 = 1,024 + 32 = 1,056 B (`kv_q8.hpp:25-27`). q4_0 = 2 x 144 x 2 = 576 B.
  Indexer fp32 = 128 x 4 B por bloque de 4 celdas = 128 B/celda (`qsa.hpp` bloque de comentario "512 B per block per layer = 128 B/token/layer = 1.5 KiB/token").
- GDN: 36 x (128x48x128 + 10,240x3) x 4 = 117,669,888 B = 112.2 MiB.
- Cabeza del drafter: 106,299 filas x 1,360 B (IQ4_XS, 4.25 bpw) = 137.9 MiB (coincide con el log de `bench/results/2026-09-30-community-rtx-5090/engine.log:32`).
- Selección QSA por token: 2,051 celdas x 1,056 B = 2.17 MB/capa → x12 = 26 MB; a 360 GB/s = 72 µs.
- Indexer scoring a 262K: 65,536 bloques x 512 B = 33.5 MB/capa → 403 MB (12 capas) → 1.12 ms a 360 GB/s.
- Fórmula de aceptación (E de tokens por ronda con 3 drafts de q=0.86 iid): 1+0.86+0.74+0.64 = 3.24 (medido 3.23 a 4K).
- Compare con el modelo del repo: `step_ms` con `hit_rate=0.72`: k=0 17.8 ms, k=1 23.0, k=2 30.1, k=3 35.9, k=4 44.4 (`simulaciones/cm.cpp`).

## Apéndice B. Simulación (scripts en `simulaciones/`)

`draft_len_sim.py` (modelo y escenarios), `sweep.py` (barrido B/A), `minp.py` (qué captura un `min_p` estático), `depth.py` (cadenas de 5 drafts),
`simple_b.py` (versión simple con suelo de cadena). Supuestos: dprob~Beta(conc=3), correlación 0.5 con una dificultad de ronda,
aceptación = κ·dprob, coste = A + B(T-1) + d·(pasos de draft). **Es un modelo, no una medición**: sirve para ordenar prioridades y dimensionar
la sensibilidad a B/A, no para prometer cifras.

## Apéndice C. Tokenizer (medición propia con vocab de juguete)

`sim/tokbench.py`: BPE greedy de 3,000 merges entrenado sobre el texto del repo (800 KB de muestra, 254,220 tokens), Xeon 2.8 GHz:
`strata_tokenizer.Tokenizer.encode` 0.92 s = 275K tok/s; con memo por pieza 0.25 s (1.0M tok/s) y 0.12 s en segunda pasada (2.0M tok/s); **ids idénticos**.
El vocab real (247,587 merges) tendrá piezas ligeramente más caras; orden de magnitud 100-300K tok/s → ≤1 s por 100K tokens.

## Apéndice D. Cobertura de lo pedido

(1) paso de decode, draft MTP/lookup, verify, aceptación, rollback, elección de longitud, coste: §2.1-2.3 y §3. (2) muestreo/camino crítico: §2.4.
(3) KV, formatos, selección QSA, VRAM por 1K, curva contexto/expertos/tok/s, q4 medido, streaming temprano, L2/PCIe: §2.5-2.6. (4) conversation cache:
§2.7. (5) mejoras con ganancia/riesgo/esfuerzo/validación: §4-§5.
