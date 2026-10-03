# Auditoría: ¿puede este fork correr GLM-5.3-Flash en hardware barato?

Fecha: 2026-10-03. Estado: **auditoría, nada implementado**. Fuentes: el `config.json` y las formas de tensores
reales de [zai-org/GLM-5.3-Flash](https://huggingface.co/zai-org/GLM-5.3-Flash), el PR de llama.cpp que lo soporta,
y la lectura del código de este repositorio (Strata 0.1.38). **Ninguna cifra de velocidad de GLM de este documento
está medida en una RTX 3060**: son estimaciones, con la aritmética a la vista, y así se marcan.

Volver al [plan del fork](README.md).

## La respuesta corta

1. **GLM-5.3-Flash no es un modelo pequeño.** "Flash" no quiere decir pequeño: tiene **320 B de parámetros** (18 B
   activos por token), frente a los 125 B de Qwen3.8-Flash-Next. Licencia MIT, publicado el 2026-08-26.
2. **Su arquitectura es "hermana" de la de Qwen3.8-Flash-Next**, y eso es una buena noticia para el motor. Las
   mismas cuatro ideas, con otras fórmulas: atención lineal de la familia DeltaNet, atención dispersa con indexador
   cada 4 capas, residual de 4 flujos y MoE con capa MTP para adivinar tokens.
3. **El problema no es el código, es la memoria.** Sus expertos ocupan **~88-90 GB a 2,3 bits** (los de Qwen,
   34-36 GB). No caben en la RAM de un PC de 32 o 64 GB a ningún tamaño publicado: el GGUF completo más pequeño pesa
   74,9 GB (IQ1_S), y los de 2 bits 96-109 GB.
4. **Velocidad estimada en una RTX 3060 12 GB:** ~1-2 tokens/s con 32 GB de RAM, ~2-7 con 64 GB y un NVMe, y
   11-26 tokens/s con 128 GB. Qwen IQ2_XS con 64 GB, en comparación: 79 tokens/s medidos en una RTX 5070, y
   30-35 tokens/s medidos por un usuario en una RTX 3060 (Swift IQ3_XXS, 128K).
5. **Esfuerzo para portarlo: ~51-80 semanas-persona** (±40 %), unos 5-7 meses con 3 ingenieros que ya conozcan el
   motor y tengan GPU.

**Recomendación:** no empezar el porte de GLM-5.3-Flash hasta que el fork tenga GPU de pruebas y un PC de 64 y otro
de 128 GB para medir. Para "GLM en hardware barato" hay tres caminos realistas, en la sección
[Opciones](#opciones-para-glm-en-un-pc-barato).

## Qué es GLM-5.3-Flash (datos verificados)

| | Qwen3.8-Flash-Next (lo que corre Strata) | GLM-5.3-Flash |
| --- | --- | --- |
| Parámetros totales / activos | 125 B / ~3 B | **320 B / 18 B** |
| Capas | 48 | 45 + 1 capa MTP |
| Ancho (`hidden`) | 2.560 | 4.096 |
| Atención lineal | Gated DeltaNet, 36 capas | **KDA** (Kimi Delta Attention), 34 capas |
| Atención completa | QSA (dispersa, con indexador), 12 capas: 3, 7, ..., 47 | **DSA** (MLA sin RoPE, con indexador), 11 capas: 3, 7, ..., 43 |
| Residual | 4 flujos ("gated residual", `hc=4`) | 4 flujos **mHC** (matriz 4x4 con Sinkhorn de 20 iteraciones) |
| Expertos por capa / por token | 512 / 10 (softmax) | 288 / 8 (**sigmoide** + sesgo, x2,5) |
| Tamaño de un experto | 4,9 M parámetros | **25,2 M** (5,1x) |
| Experto compartido | 1, con puerta | 1, sin puerta |
| Capas densas | ninguna | las 3 primeras |
| Tabla n-gram (PLE) | sí, 28,8 GB en el SSD | no tiene |
| Contexto | 262K | 1M |
| Vocabulario | 248.320 | 154.880 |

Detalles que el servidor tendría que cambiar: el formato de llamadas a herramientas es
`<tool_call>nombre<arg_key>k</arg_key><arg_value>v</arg_value></tool_call>` (no el de Qwen), el razonamiento
("thinking") no se puede apagar (`reasoning_effort` low/high/max), y los tokens de parada son 154820, 154827 y
154829.

Soporte en otros motores (a 2026-10-03): llama.cpp lo fusionó el 2026-09-30 (arquitectura `glm5-next`; su MTP sigue
en un PR abierto); ik_llama.cpp lo tiene con MTP; vLLM y SGLang también. El llama.cpp que fija este repo
(`third_party/ggml/VERSION.txt`, 2026-09-20) es anterior a ese soporte.

## Cuánta memoria necesita (aritmética)

Un experto tiene 3 x 4.096 x 2.048 = 25.165.824 parámetros. Hay 42 capas MoE x 288 expertos, más la capa MTP.

| Cuantización | Bits/peso | MB por experto | Expertos (42 capas + MTP) | Leído por token (42 x 8 expertos) |
| --- | ---: | ---: | ---: | ---: |
| IQ1_S | 1,56 | 4,9 | 60,8 GB | 1,65 GB |
| IQ2_XS | 2,31 | 7,3 | **90,0 GB** | **2,44 GB** |
| IQ3_XXS | 3,06 | 9,6 | 119,2 GB | 3,23 GB |
| Q4_K_M | 4,8 | 15,1 | 187,0 GB | 5,07 GB |

Comprobado contra archivos publicados: IQ2_XS calculado ~100 GB con los no-expertos, publicado 101,4 GB
(bartowski). Para Qwen el mismo cálculo da 0,66 GB leídos por token, lo mismo que el paper de Strata.

- **Pesos que viven en la GPU** (no-expertos): 8,3 B de parámetros = 4,7 GB en Q4_K o 8,8 GB en Q8_0 (Qwen: ~3,5 GB).
  En una 3060 de 12 GB quedan **~4,8 GB para expertos = ~660 expertos IQ2_XS, el 5,5 %** de 12.096 (Qwen cachea ~18 %).
- **KV cache**: pequeña. ~17 KB por token de contexto (latente MLA de 512 + indexador); 128K = 2,2 GB, 1M = 17,7 GB.
  El estado KDA es fijo: 143 MB.
- **La CPU haría el 60-85 % del trabajo de expertos** (estimación; en Qwen con 12 GB es ~28 %), y el rutado de GLM
  parece casi uniforme según una de las dos mediciones públicas, así que la caché de expertos ayuda menos.

## Velocidad estimada en una RTX 3060 12 GB

Modelo de cálculo (de `docs/UNSLOTH_Q4.md`: Strata lee del SSD a 1,5-2,0 GB/s efectivos con Qwen UD-Q4_K_XL,
medido en una RTX 5070 + 64 GB): tiempo por token = lectura del SSD / velocidad del SSD + ~60 ms de CPU, GPU y
sincronización. **Todo es estimación; nadie ha publicado una medición de GLM-5.3-Flash en una GPU de 12 o 16 GB.**

| RAM del PC | Expertos que caben en RAM | tokens/s a 1,5-2 GB/s de SSD | tokens/s con un NVMe PCIe 4 a 5 GB/s |
| --- | ---: | ---: | ---: |
| 32 GB | ~15 % | 0,7-1,7 | 2,1-3,8 |
| 48 GB | ~35 % | 0,9-2,6 | 2,6-5,3 |
| 64 GB | ~54 % | 1,3-3,9 | 3,5-7 |
| 96 GB | ~93 % | ~5-12 | ~6-12 |
| 128 GB | 100 % | 11-26 (limitado por la CPU y la DDR4) | igual |

La adivinanza MTP (que en Qwen da 1,6-1,8x) ayuda poco aquí: con 288 expertos casi uniformes, una ventana de 3 tokens
toca 23 de los 24 expertos posibles por capa, así que no ahorra lecturas. ik_llama.cpp mide +20 % con el modelo en
RAM.

Línea base de otros motores (medida por terceros): llama.cpp con expertos en CPU, RTX 4090 + 128 GB DDR5, IQ3_S:
~9 tokens/s; un fork de llama.cpp con caché de expertos, 2x RTX 3090 + 125 GB DDR4-3200: 11,9 -> 22,4 tokens/s.

**Calidad a 2 bits:** Unsloth publica que IQ2_XXS conserva el 76,3 % del top-1 del modelo completo (KLD 0,45) e
IQ4_XS el 88,2 %. A diferencia de Qwen (los GSQ-RCO de ISTA-DASLab igualan al modelo BF16 en los benchmarks
publicados), no hay un 2 bits de GLM-5.3-Flash con calidad medida cercana a la original. Esto choca con el objetivo
del fork de "no perder inteligencia".

## Qué se puede reutilizar del motor

El bloque de capa de Qwen y el de GLM tienen la misma forma: `leer residual -> mezclador -> escribir residual ->
leer residual -> MoE -> escribir residual`, con 4 flujos y una capa de atención completa cada 4. Por eso no hace
falta duplicar los bucles de decode, verificación, prefill y MTP: basta con poder cambiar **las operaciones** dentro
de ellos.

| Pieza de Strata | En GLM-5.3-Flash | Trabajo | Semanas-persona |
| --- | --- | --- | ---: |
| Reparto de capas (`include/strata/core/layout.hpp:56-65`) | la regla `capa % 4 == 3` ya da 11 DSA + 34 KDA | añadir las 3 capas densas y la MTP | 1 |
| Gated DeltaNet (`src/kernels/cuda/fused_gdn.cu`, `native_gdn.cu`, prefill) | KDA: la misma regla delta, decaimiento por canal en vez de por cabeza, 64 cabezas | generalizar 6 puntos de decaimiento y el preproceso de la puerta | 4-7 |
| Indexador QSA, top-k, streaming de KV (`qsa_select.cu`, `kv_stream.hpp`) | mismo pool de 4 celdas, mismo top-k de 2048, misma dimensión 128; 32 cabezas con peso, pooling aprendido, sin RoPE | generalizar | 4-6 |
| Atención QSA (`qsa_decode_attn.cu`, `qsa_prompt_attn.cu`) | MLA absorbida sin RoPE: 64 cabezas sobre un latente de 512 donde K = V | **nueva** (decode y prefill con tensor cores) | 6-10 |
| Gated residual (`gr.hpp`, `fused_gr.cu`) | mHC: 4 flujos, matriz 4x4 con Sinkhorn | kernels nuevos (más baratos); se reutiliza el resto | 3-5 |
| Router softmax top-10 de 512 (`router_top10.cu`, `native_router.cu`) | sigmoide + sesgo, top-8 de 288, x2,5 | generalizar los 3 caminos (1 token, ventana, prefill) | 1,5-2 |
| Expertos 640 x 2.560 | 2.048 x 4.096, con `swiglu_limit` 10 | constantes, buffers y el límite en CPU y GPU | 2,5-4 |
| Tipos de cuantización de los kernels | los GGUF publicados usan K-quants | ampliar la cobertura (`iq_kernels.cu:510-512`) | 2-3 |
| Capa MTP | DSA + MoE de 288 expertos (~2,1 GB a IQ2_XS) | sus expertos no caben en VRAM como los de Qwen (708 MB) | 6-9 |
| Caché de expertos, arena en RAM, pool de CPU, lectura desde SSD, servidor, API, web | igual | se reutiliza | - |
| Tokenizador, plantilla, parser de herramientas, paradas (`serve/`) | otros | perfil de modelo en el servidor | 4-6 |

Lo que hoy impide cargar otro modelo: `ModelGeometry` (`include/strata/core/layout.hpp:27-59`) tiene los valores de
Qwen por defecto y solo `expert_count`, `expert_used_count` y `rope.*` se leen del GGUF
(`src/program/generate.cpp:1845-1868`); los kernels, el prefill y el código de expertos en CPU llevan las formas como
`constexpr` (`src/prefill/prefill.cpp:90-91`, `src/kernels/cpu/expert.hpp:34-45`); y el lector de GGUF exige la
arquitectura `qwen4exp` (`include/strata/artifact/gguf_reader.hpp:575-603`).

## Plan por fases (si se decide portarlo)

| Fase | Qué | Semanas-persona |
| --- | --- | ---: |
| 0 | `ModelArch` leído del GGUF + "ranuras de operación" en los bucles existentes; **Qwen bit-idéntico** (se comprueba con los grafos CUDA y los logits) | 6-10 |
| 1 | Forward de GLM en GPU + CPU con una referencia en Python (`ref/`) para la paridad: KDA, DSA, mHC, router, expertos, tipos | 32-50 |
| 2 | Caché de expertos, lectura desde SSD con anillos en bytes, prefetch para router sigmoide, perfil de expertos de GLM | 3-5 |
| 3 | MTP con MoE y modelo de costes con término de SSD | 6-9 |
| 4 | Servidor y setup: tokenizador, plantilla, herramientas, catálogo | 4-6 (se puede empezar sin GPU) |
| 5 (opcional) | Visión | 2-3 |
| **Total** | | **51-80** |

Riesgos: no se puede comprobar sin GPU que Qwen siga dando los mismos tokens; varios detalles de GLM no están
verificados (la fórmula exacta de mHC, la puerta de KDA con cota -5, la compresión del indexador, el alcance de
`swiglu_limit`); y el oráculo de paridad (llama.cpp) necesita un checkout más nuevo que el fijado.

## Opciones para "GLM en un PC barato"

1. **GLM-5.3-Flash completo en un PC de 128 GB de RAM** (DDR4 barata: 4 x 32 GB). Es el único caso donde la
   estimación da una velocidad cómoda (11-26 tokens/s) sin perder calidad más allá de la cuantización. Exige el
   porte completo (51-80 semanas-persona).
2. **GLM-5.3-Flash desde el SSD con 64 GB** (~2-7 tokens/s estimados): usable para tareas pacientes, no para chatear
   ni para agentes. Mismo porte, más la Fase 2 de lectura desde SSD.
3. **Una versión podada (REAP-50, 144 de 288 expertos, ~44 GB a IQ2_XS)** cabría en 64 GB (13-26 tokens/s
   estimados), **pero no tiene ninguna evaluación de calidad publicada**, y una de las dos mediciones de rutado dice
   que GLM-5.3-Flash "no tiene expertos de sobra". Va contra "no perder inteligencia" hasta que se mida.
4. **Fuera de este motor:** si el objetivo es "un modelo GLM en una 3060 ya", GLM-4.7-Flash (30B-A3B, 18,3 GB en
   Q4_K_M) corre hoy en llama.cpp con 12 GB + 32 GB. No necesita la técnica de Strata (pocos expertos grandes que
   caben casi enteros en RAM) y es otro modelo, más pequeño y menos capaz que GLM-5.3-Flash.

**Lo que haría primero, sin GPU y sin decidir aún el porte** (2-3 semanas-persona):

- la Fase 4 del servidor: el perfil de modelo con tokenizador, plantilla, parser de herramientas y paradas, que
  también ordena el código de Qwen;
- una referencia en Python de una capa KDA, una DSA y una mHC contra los pesos reales, para fijar las fórmulas que
  hoy no están verificadas;
- y medir el sesgo del rutado de GLM con trazas reales (decide si la caché de expertos sirve de algo). Esto último
  necesita una GPU grande o una máquina con 128 GB.
