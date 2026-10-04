# Integración de Strata v0.1.39: qué trae, cómo desplegarla sin perder nada, y qué medir

Para: el agente del servidor. Fecha: 2026-10-04.

Upstream sacó la **v0.1.39** (`6f32ec0`): 217 commits sobre la 0.1.38. **Está integrada en esta rama** con un merge
(`2a05958`): ninguna historia reescrita, y todo lo del fork conservado. Las pruebas y medidas son vuestras: aquí no
hay CUDA. Adrián quiere que **no se pierda nada de lo avanzado**.

## 1. Lo que trae y nos afecta

| Cambio | Para nosotros | Bits | Variable para apagarlo (A/B) |
| --- | --- | --- | --- |
| **`cce52db` top-k de `qsa_select`** | **Sí, siempre a 512K.** Medido por upstream **en una RTX 3060 12 GB**: 524K celdas 37,5 → 3,2 ms por llamada; un prompt de 243K tokens 743 → 934 tok/s (+26 %); top-k en decode a 262K 0,309 → 0,127 ms | mismos ids | `STRATA_TOPK_OLD=1` |
| **#646 tabla IQ en memoria compartida** de los kernels agrupados (tipos 16/17/22/29: nuestros IQ2_XXS, IQ2_S, IQ1_M) | sí, por defecto | bit a bit (`055122c`) | `STRATA_IQ_STAGE_GRID=0` |
| **#646 `down` Q2_0 sub-warp para n_ff 640** | sí: nuestro `down` es q2_0 con n_ff 640 | bit a bit | (va con #646) |
| **#646 experto compartido en otro stream**, división de GDN, SwiGLU+q8_1 fusionado | sí; es justo nuestro lado largo (la GPU) | bit a bit, según upstream | `STRATA_SH_STREAM=0` |
| **#583 anillo del prefill por bytes** | en parte | **cambia bits en prompts largos** | `STRATA_RING_BYTES=0` |
| **`6d51272` huge pages (THP) en el arena** de Linux | sí | idéntico | — |
| **`19de7f4`** q8_1 finito | sí: arregla un "un token para siempre" | idéntico salvo el caso roto | — |
| **Servidor:** corta 256 tokens idénticos seguidos; `<think>` literal como texto; `tools` mal formado → 400; cliente que deja de leer; el motor muerto se reinicia de verdad | sí, uso agéntico | solo servidor | `repeat_stop_tokens` |

No nos afecta: SYCL/Intel, sm_7x, HIP, varias GPU, CPUs sin AVX2, el grafo "zero-doorbell" (necesita todos los
expertos en VRAM), y `--parallel`.

## 2. Qué se ha conservado del fork (comprobado línea a línea)

**Todas las líneas que el fork añadió desde `99f3dbd` siguen en el árbol**, en 113 ficheros. Las tres que no aparecen
tal cual son las que reescribí para la estructura nueva:

- **`src/core/verify.cpp`:** upstream separó el lanzamiento en `if (all_resident_) … else …` y añadió `dst_buf`.
  `STRATA_HIT_GY` va ahora en el lanzamiento de los aciertos de la rama `else`, que es el caso normal.
- **`src/program/generate.cpp`:**
  - `adapt_swapped` va junto al `pin_live` nuevo de upstream.
  - La línea del acierto es la de upstream (#588, que ahora también dice la parte PCIe) más nuestro "N experts
    swapped in over W windows".

Siguen, sin tocar:

- el kernel IQ2_S y su test;
- `heat_first` y `STRATA_PROFILE_HEAT_MIN`;
- `lazy_vision_error` (visión en la CPU con `--lazy`);
- todo `docs/fork/` y el CI del fork.

**Parches:**

- **`patches/systemone-logprobs.patch`: regenerado para la 0.1.39.** El trozo del evento `done` une lo nuestro
  (`strata_logprobs`) con lo nuevo de upstream (`reasoning_tokens`).
- **`patches/suffix-draft-stats.patch`:** sin cambios.
- **Comprobado:** los dos se aplican en orden sobre el árbol integrado, y `server.py` parsea.

## 3. Desplegar sin perder lo que funciona

1. **Guardar lo de hoy:**
   - una copia del binario (`engine/strata` → `engine/strata.bak-0138`);
   - `strata-swift-iq2_xs.json`;
   - el perfil aprendido;
   - y la rama/commit que tenéis desplegada (`bebb18d` + `port/claude-r7`).
2. **Árbol nuevo:** en `~/Strata`, una rama desde esta (`fork3060/claude/strata-rtx3060-optimization-zfgxq8`, commit
   `2a05958` o posterior). Sobre ella:
   ```bash
   git apply docs/fork/patches/systemone-logprobs.patch
   git apply docs/fork/patches/suffix-draft-stats.patch     # si lo usáis
   ```
3. **Compilar** con `-DCMAKE_CUDA_ARCHITECTURES=86`. Nota: `setup.py` exige ahora la 0.1.39 (`MIN_ENGINE`).
4. **Pruebas, antes de servir nada:**
   - `python3 serve/test_server.py`;
   - `docs/fork/ops/test_*.py`;
   - `tools/test_setup_*.py`;
   - `ctest -R "parity|iq2s|profile"`;
   - `iq2s_avx2_test`;
   - `expert_profile_save_test`.
5. **Arrancar con la configuración de siempre** y comprobar en el log:
   - `PCIe probe`;
   - `expert cache auto`;
   - que `--logprobs` funciona: un `ada-decide` de prueba;
   - la visión: una imagen;
   - y B1 rápido.

**Volver atrás** es restaurar el binario y la config del paso 1.

## 4. Qué medir (alternando, como siempre)

1. **La versión entera contra la de hoy:** B1, B2, B4, P3, P4 y `bench-prefill.py` a 32K y, si podéis, 128K, porque el
   top-k nuevo gana más cuanto más largo el contexto. Más S1/S2 de System One.
2. **Por novedad,** apagando una cada vez con su variable: `STRATA_TOPK_OLD=1`, `STRATA_IQ_STAGE_GRID=0`,
   `STRATA_SH_STREAM=0` y `STRATA_RING_BYTES=0` (este último solo prefill). Si alguna resta, se queda apagada en
   `serve-strata.sh`.
3. **Calidad:**
   - **Bit a bit:** con temperatura 0 y los ajustes de comprobación de siempre (`STRATA_IQ_MT_MIN=1 --prompt-cache 0
     --adapt-swaps 0 --pcie-frac 0`), B1 debe dar el mismo texto con y sin cada variable de las bit a bit.
   - **`logpos-compare`** contra la 0.1.38 en prompts largos, por #583, que sí cambia bits.
4. **Lo que puede haber cambiado sin avisar:**
   - la aceptación de borradores (#646 tocó MTP y PLE);
   - el acierto de la caché (ahora la línea trae también la parte PCIe);
   - y que `ada-decide` siga dando las mismas decisiones en el banco S2 (`--keep-answers`).

**Pasadme:** la tabla de `bench.py compare` de 1, lo que salga de 2 y 3, y cualquier prueba que falle, tal cual.

## 5. Lo de la caché sigue en pie

La 0.1.39 no cambia la política de la caché (`adapt`), así que `RESPUESTA_CACHE_EXPERTOS.md` sigue valiendo. Pasad
el simulador nuevo (columna `eng.hit` y `fetch-admit`) por vuestra traza larga. Si `fetch-admit` gana, lo escribiría
ya sobre la 0.1.39.
