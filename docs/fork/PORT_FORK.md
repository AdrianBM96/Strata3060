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
| 3 | `f42c58f` | Kernels CPU AVX-VNNI + gather i-quant | **portable** (no depende de layer-split) | Choca con `row_dot_iq2s` en `iq_avx2.cpp`; mantener ambos y que `STRATA_IQ2S_BLOCK` siga vivo. Pasar `tests/core/iq2s_avx2_test.cpp`. |
| 4 | `9de6561`+`785d255`+`9f3069b`(+`62e8c06`) | CPU assist en prefill | **portable, no bit-exacto** (1,3-1,9 % L2/experto) | El último. Puerta de calidad (§2 de `RESPUESTA_RONDA11`) además del A/B. |

## NO portar (ver `ANALISIS_FORK_ARCHITECTDS.md`)

- Layer split / peer device / pipeline-windows / RAM residente con split → una sola tarjeta.
- PDL (`STRATA_DF_PDL`) → sm_90+; la 3060 es sm_86.
- KV Q4_0 en el prompt → usamos `--kv int8`.
- Chunks por tamaño de prompt → nuestro `auto` ya elige 6144 y nada entre 6144 y 8192 cabe.
- `--vision-on-demand` → **candidato** (no descartado): permitiría traer la visión a Strata en la 3060
  sin ralentizar el texto. Hoy la visión la sirve nex-mini (:8080) porque el codificador no cabe junto al 125B.

## Registro

- **2026-10-04**: base congelada en `bebb18d`. Historia del fork traída completa. Confirmado que el #1 ya
  está. A/B de `STRATA_GR_DOWN_MAX4` en curso.
