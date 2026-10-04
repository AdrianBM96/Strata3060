# Análisis del fork `architectds/Strata` para **un solo RTX 3060**

Fecha: 2026-10-04. Autor: el agente del servidor. **Estado: análisis + primer A/B medido.**

## Qué es y de dónde parte

`github.com/architectds/Strata` es un fork de `Niko1221/Strata` "tuned for speed". Su rama `best` es
v0.1.36/0.1.38 + cambios. La **base común con nuestro árbol es `99f3dbd`** (0.1.38, rama `f-138-prs`); nuestro
HEAD es `b728839` (System One encima). Es decir: **no es un árbol ajeno, es el mismo motor con cambios nuevos.**

**Aviso importante:** buena parte del fork es **multi-GPU** — un merge de `Hardin22/Strata-DualGPU` (layer split,
`--peer-device`, `--pipeline-windows`, RAM residente con split, hand-off entre tarjetas). **Nada de eso aplica a
una sola 3060** y no hay que portarlo.

## Tabla de aplicabilidad a nuestro equipo

Nuestro equipo: **RTX 3060 12 GB (sm_86, PCIe 4.0 x16), i5-12400F (6 P-cores, AVX2 + AVX-VNNI, sin AVX-512),
DDR4-2133**, modelo **IQ2_XS nativo**, `--spec 4 --mtp`, `--ple-gguf`, `--kv int8`, `--prefill auto` (chunk 6144),
caché de expertos ~3.700 slots (el prompt pide ~3.020 prestados).

| Cambio del fork | ¿Aplica? | Por qué | Ganancia esperada en la 3060 |
| --- | --- | --- | --- |
| **`STRATA_PF_FUSED=1`** (expertos int8 fusionados en el prompt) | **Sí, ya lo tenemos (opt-in)** | Nuestro pack es nativo y `moe_fused_iq` cubre IQ2_XXS/IQ2_S | **+5,2% medido y puerta de calidad PASADA**, adoptado |
| **CPU assist en prefill** (`STRATA_PREFILL_CPU`) | **Sí** | Prompts **< 3.072 tokens** (incrementos de chat, decisiones) están atados a las copias PCIe | 1,35-1,49x en <1.000 tok (su medida); **no es bit-exacto** |
| **Kernels CPU AVX-VNNI + gather i-quant** (`f42c58f`) | **Sí** | Nuestra CPU tiene `avx_vnni` y es solo P-cores | **bit-idéntico**; en IQ2_XS ellos lo miden "dentro del ruido" |
| **MTP chain / MTP early** (`bce7fbb`) | **Sí** | Usamos `--spec 4 --mtp` | 1 lanzamiento+espera por ronda de borrador en vez de por paso |
| **`STRATA_GR_DOWN_MAX4=1`** (`4c3f599`, #443) | **Sí (no lo tenemos)** | Ventanas ≤4 tokens (nuestro spec) | +2,1/+2,4% decode medido en Blackwell; **mismos bits** |
| **Tier adaptativo asíncrono** (`11b1f25`) | **Sí, pero ya lo tocamos** | Tenemos el tier (`adapt_every=4`); el fork lo hace no bloqueante | Quita ~24 ms cada 4ª ventana (su caso); ya probamos `ADAPT_NOWAIT` y se revirtió |
| **Verify window** (`58ec619`) | **Parcial** | PDL es **sm_90+** (no); lo demás (mmvq por warp, atención por lotes, SwiGLU plegado) sí | Premisa "tarjeta rápida": en la 3060 los kernels no son el cuello de lanzamiento |
| **Chunks por tamaño de prompt** (`4711a9f`/`01ca5fd`) | **No nos cambia** | Nuestro `auto` ya elige **6144**; nada entre 6144 y 8192 cabe en nuestro caché | ~0% |
| **Images on demand** (`--vision-on-demand`) | **Sí, candidato** | Strata **sí** tiene visión (`engine/strata-vision` + `mmproj-Qwen3.8-Flash-Next-BF16.gguf`; `strata-iq2_xs.json` la usa). La apagamos **en swift** por VRAM y las imágenes van a nex-mini (:8080). Esta mejora **presta la VRAM del caché de expertos al codificador solo mientras hay imagen** | Permite visión en la 3060 **sin ralentizar el texto** |
| **PDL** (`STRATA_DF_PDL`) | **No** | sm_90+ (Hopper/Blackwell); la 3060 es sm_86 | — |
| **KV Q4_0 en el prompt** (`STRATA_DF_QB_Q4`) | **No** | Nosotros usamos `--kv int8` | — |
| **Layer split / peer / dual-GPU / RAM residente con split** | **No** | Una sola tarjeta | — |

## Los tres PRs del fork ya están en nuestro 0.1.38

El README del fork dice que sus tres PRs (#439 gathers por lotes en el prompt, #452 atención Q4_0 en tensor
cores, #453 K/V del borrador en anillo) **ya están en Strata 0.1.38**, que es nuestra base. No hay que portarlos.

## Qué merece la pena, por orden

1. **`STRATA_PF_FUSED=1`** — **+5,2% de prefill medido, sin recompilar y con la salida emparejada idéntica.
   ADOPTADO** (en `serve-strata.sh`). Ver `MEDICION_RONDA10.md`.
2. **CPU assist en prefill** — la palanca grande para prompts cortos/incrementales, pero **no es bit-exacta**
   (la aritmética del pool difiere 1,3-1,9% L2 por experto). Es un parche grande (`prefill.cpp`,
   `native_expert.cpp`, `pool.cpp`). **Proponer a Claude, con A/B de calidad además de velocidad.**
3. **Kernels AVX-VNNI/gather** — bit-idénticos y baratos; en IQ2_XS probablemente ruido, pero se prueban.
4. **`STRATA_GR_DOWN_MAX4`** — bit-idéntico, +2% según #443.
5. **MTP chain/early** — bit-idéntico, ahorra lanzamientos en el spec.
6. **`--vision-on-demand`** — para **traer la visión a Strata** en la 3060 (hoy la sirve nex-mini): presta la
   VRAM del caché al codificador solo mientras hay imagen. Relevante si se quiere una sola pila; ver el matiz de
   que la visión de Strata sí existe y solo está apagada en `swift`.

## Conflictos de integración (para quien lo porte)

- `src/kernels/cpu/iq_avx2.cpp`: **nosotros ya lo tocamos** (kernel IQ2_S `row_dot_iq2s`, un bloque
  autocontenido tras `row_dot_iq2xs`). El fork toca esa misma zona → conflicto de cherry-pick, pero resoluble.
- `src/prefill/prefill.cpp`, `src/kernels/cpu/native_expert.cpp`, `src/kernels/cpu/pool.cpp`: el CPU assist.
- `src/core/verify.cpp` / `include/.../verify.hpp`: MTP chain + verify window.
- `src/program/generate.cpp`: nosotros ya lo tocamos (`--logprobs`, System One) en zonas distintas.

Todo cambio que se porte debe **medirse alternando A/B** (protocolo en `METODO_MEDICION.md`): misma config da
40,25 y 41,50 por deriva térmica.
