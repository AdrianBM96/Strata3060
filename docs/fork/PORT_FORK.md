# Port del fork `architectds/Strata` — seguimiento

Estado: **2026-10-04, en curso**. Base: nuestro motor en **`bebb18d`** (snapshot del estado desplegado =
0.1.38 + kernel IQ2_S `row_dot_iq2s` + System One `--logprobs`). Fork de referencia: `architectds/Strata`,
rama `best` (`3946322`).

## Método (por cada port)

1. Rama `port/<nombre>` desde `bebb18d`.
2. `git cherry-pick <commit>`; resolver conflictos.
3. Compilar con `-DCMAKE_CUDA_ARCHITECTURES=86`.
4. `ctest -R "parity|iq2s|profile"` + los tests que traiga el commit.
5. **Bit a bit**: `STRATA_IQ_MT_MIN=1 --prompt-cache 0 --adapt-swaps 0 --pcie-frac 0`, B1 con temperatura 0
   idéntico con el cambio activado y sin él (no aplica al CPU assist).
6. **Velocidad**: A/B alternando 6+6. B1/B2/B4 para decode; P3/P4/`bench-prefill.py` para prefill.
7. Si no supera el ruido → se queda **apagado**. Si gana → se activa en `serve-strata.sh`.
8. Documentar commit de origen y cómo se resolvió cada conflicto.

**Aviso de entorno:** el repo era **shallow** (solo 4 commits). `git fetch --unshallow adesign` trae la
historia completa del fork. Remotos: `architectds` (URL de GitHub) y `adesign` (clon local).

**Aviso de conflictos:** `src/program/generate.cpp` lo tocan el parche de System One y el de contadores del
borrador. Tras cada cherry-pick, comprobar que siguen aplicando.

## Tabla de ports

| # | Commit(s) | Qué | Estado | Notas |
| --- | --- | --- | --- | --- |
| 1 | `4c3f599` + `92883e8` | `STRATA_GR_DOWN_MAX4` (#443): proyección GR para ventanas ≤4 tokens | **YA EN NUESTRO ÁRBOL** | Lo trae el 0.1.38 (con el "read once"). No hay que portar: solo A/B con la variable. |
| 2 | `bce7fbb` | MTP chain / early: una espera por ronda de borrador | **ENTRELAZADO con layer-split** (8 hunks "split") | Cherry-pick suelto traería código multi-GPU. Extraer solo la parte de 1 GPU, o pedir a Claude. |
| 3 | `f42c58f` | Kernels CPU AVX-VNNI + gather i-quant | **portado y verificado bit a bit, pero SIN ganancia → NO se activa** (rama `port/avx-vnni` guardada, no desplegada) | A/B 3+3: on 42,80 · off 43,25 tok/s → dentro del ruido (y con sesgo térmico a favor del on). Coincide con el fork: "con IQ2_XS dentro del ruido". |
| 4 | `9de6561`+`785d255`+`9f3069b` | CPU assist en prefill | **portado limpio** (`port/cpu-assist`), **+5,7 % prefill corto PERO degrada la calidad** → **NO se activa** | A/B: on 547,8 · off 518,05 tok/s. Puerta de calidad: ΔNLL **+0,0026 ± 0,0012 sobre el ruido** (peor); PPL 1,109→1,113. Revertido. |

## NO portar (ver `ANALISIS_FORK_ARCHITECTDS.md`)

- Layer split / peer device / pipeline-windows / RAM residente con split → una sola tarjeta.
- PDL (`STRATA_DF_PDL`) → sm_90+; la 3060 es sm_86.
- KV Q4_0 en el prompt → usamos `--kv int8`.
- Chunks por tamaño de prompt → nuestro `auto` ya elige 6144 y nada entre 6144 y 8192 cabe.
- `--vision-on-demand` → **candidato** (no descartado): permitiría traer la visión a Strata en la 3060
  sin ralentizar el texto. Hoy la visión la sirve nex-mini (:8080) porque el codificador no cabe junto al 125B.

## Conclusión del port (2026-10-04)

De todo el fork, **lo único adoptable es `PF_FUSED`** (que ya estaba en nuestro árbol). Los cuatro ports que
Claude propuso:

| # | Cambio | Veredicto |
| --- | --- | --- |
| 1 | `GR_DOWN_MAX4` | ya presente; +1,2 % (ruido) → **apagado** |
| 2 | MTP chain | entrelazado con layer-split → **no portable suelto** (para Claude) |
| 3 | AVX-VNNI + gather | bit-idéntico, **sin ganancia** (ruido) → **revertido** |
| 4 | CPU assist | +5,7 % prefill corto, pero **degrada** (ΔNLL +0,0026) → **revertido** |

**Por qué:** el fork está afinado para tarjetas rápidas y configuraciones multi-GPU. En una 3060 con IQ2_XS, sus
ganancias de CPU (AVX-VNNI) quedan dentro del ruido, y su gran palanca (CPU assist) cambia la aritmética. El único
cambio que aporta sin coste es el que ya teníamos.

## Registro

- **2026-10-04**: base congelada en `bebb18d`. Historia del fork traída completa. Confirmado que el #1 ya
  está.
- **2026-10-04 — #3 `f42c58f` (AVX-VNNI) cherry-pick:** rama `port/avx-vnni`, commit `f77a640`. Conflictos:
  `docs/DUAL_GPU.md` (borrado: doc del fork, no lo queremos) y `src/kernels/cpu/iq_avx2.cpp`. El fork añade un
  parámetro de plantilla `bool VN` (AVX-VNNI) a `row_dot_any`/`row_dot_iq2xs`/`row_dot`; nosotros teníamos ahí
  el despacho de nuestro kernel IQ2_S. **Resuelto quedándonos con los dos**: `row_dot_any<TY,NT,VN>` mantiene
  `if constexpr (TY==22){ if (iq2s_block) row_dot_iq2s<NT>(...); else row_dot<TY,NT,VN>(...); }`. `row_dot_iq2s`
  no usa VN (ya está vectorizado con madd/maddubs), así que no se le añade. `iq_avx2.hpp`, `q2_avx2.cpp` y
  `native_expert.cpp` se fusionaron solos. **Compilado OK.**
- **2026-10-04 — #3 verificación bit a bit:** `tests/core/iq2s_avx2_test.cpp` que citaba Claude **no existe**
  (ni en el fork). `iq_parity` cubre solo los kernels de **GPU** (no llama a los de CPU). El test que **sí** cubre
  el camino CPU es **`native_expert_parity`** (no registrado en ctest; se ejecuta a mano). Con nuestro shard,
  capa 0: `iq2_s avx2 gate rows vs ggml vec_dot: rel 3,23e-08`, `gpu decode-once vs per-entry: bitwise equal`,
  **0 fallos**. Comparado **con** VN/gather (defecto) y **sin** ellos (`STRATA_NO_AVXVNNI=1 STRATA_IQ_GATHER=0`):
  el diff del volcado completo **solo difiere en las líneas de tiempo** (230 vs 266 us) → los resultados
  aritméticos son **idénticos** → **bit-idéntico confirmado**. Binario nuevo desplegado (backup en
  `engine/strata.bak-pre-avxvnni`). A/B de velocidad en curso.
- **2026-10-04 — #3 A/B de velocidad: SIN ganancia → revertido.** on 42,80 · off 43,25 tok/s (3+3) → ~1 %
  por debajo, dentro del ruido; y el brazo `on1` salió a 69 °C (más frío, sesgo a favor del on) y aun así no
  ganó. Coincide con el fork ("con IQ2_XS la diferencia queda dentro del ruido"). Por la regla ("si no supera
  el ruido, se queda apagado"): **binario viejo restaurado**, fuente en `bebb18d`, rama `port/avx-vnni`
  guardada para el registro (no mergeada).
- **2026-10-04 — #4 CPU assist: portado limpio, +5,7 % prefill corto, PERO degrada → revertido.** Los tres
  commits (`9de6561`+`785d255`+`9f3069b`) cherry-pickearon **sin conflictos** (no dependían del troceado). A/B
  de prefill a ~1.800 tok: on **547,8** · off **518,05** tok/s → **+5,7 %**, brazos agrupados. Puerta de calidad
  (contexto 2.975 tok por lotes + continuación 1.123 por ventanas, `--short-read 1500`): A vs B top-1 100 %,
  KL 0,0043, PPL 1,109→1,113, ΔNLL **+0,0041 ± 0,0011**; ruido A-A2 +0,0015 ± 0,0006 → **B es PEOR por
  +0,0026 ± 0,0012**. Con la regla de no degradar: **binario bueno restaurado**, fuente en `bebb18d`, rama
  `port/cpu-assist` guardada. (El fork medía "dentro del error estándar"; en nuestro equipo sale fuera.)
- **2026-10-04 — #1 `STRATA_GR_DOWN_MAX4` medido: NO se activa.** A/B 3+3+3+3 (off/on/off/on), B1:
  off mediana **42,05 tok/s** (41,0-45,0), on **42,55** (40,9-44,0) → **+1,2 %, dentro del ruido** (el
  protocolo marca ~3 % de deriva; off1 salió a 71 °C y on2 a 78 °C). En Blackwell daba +2,1/+2,4 %, pero en
  la 3060 no se distingue. Se queda **apagado** (por defecto).
