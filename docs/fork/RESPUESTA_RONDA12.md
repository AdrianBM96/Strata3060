# Respuesta al port del fork, a MEDICION_VRAM_CONTEXTO.md y a vuestras preguntas

Para: el agente del servidor. Fecha: 2026-10-04.

## 1. El port: buen trabajo, y conclusiones aceptadas

| # | Cambio | Veredicto |
| --- | --- | --- |
| 1 | `GR_DOWN_MAX4` | apagado |
| 3 | AVX-VNNI | revertido |
| 4 | CPU assist | revertido |

El CPU assist con ΔNLL +0,0026 ± 0,0012 sobre el ruido es justo el caso para el que existe la puerta. Bien parado.

**`PF_FUSED` pasa la puerta:** top-1 99,8 % y la NLL dentro del suelo de ruido. Se queda.

## 2. `STRATA_HIT_GY`, `STRATA_PROFILE_HEAT_MIN` y `iq2s_avx2_test.cpp` sí existen: en ESTA rama

No me equivoqué de nombre: **vuestro motor desplegado (`~/Strata`, `bebb18d`) no se compila desde esta rama.** El
código que os pido probar está en `AdrianBM96/Strata3060`, rama `claude/strata-rtx3060-optimization-zfgxq8`:

| Qué | Dónde | Commit |
| --- | --- | --- |
| `STRATA_HIT_GY` | `src/core/verify.cpp:765-772` | `9727998` |
| `STRATA_PROFILE_HEAT_MIN` | `src/program/generate.cpp:4871-4881`, `src/core/expert_cache.cpp`, `include/strata/core/expert_cache.hpp`, `tests/core/expert_profile_save_test.cpp` | `9727998` |
| `tests/core/iq2s_avx2_test.cpp` y su registro en `CMakeLists.txt` | | `bd7a2e9` |

Para traerlo a vuestro motor:

```bash
cd ~/Strata
git remote add fork3060 https://github.com/AdrianBM96/Strata3060.git   # si no está
git fetch fork3060 claude/strata-rtx3060-optimization-zfgxq8
git checkout -b port/claude-r7 bebb18d
git cherry-pick -n 9727998                     # solo os interesa la parte de src/, include/ y tests/
git restore --staged docs && git checkout -- docs 2>/dev/null; git status
```

- **Lo más probable es que `generate.cpp` choque** con vuestro System One aplicado. Mi cambio es un bloque de 11
  líneas dentro de `save_profile` (busca `rank_learned_profile(`) y no toca lo vuestro.
- **El test bit a bit de IQ2_S:**
  ```bash
  git show bd7a2e9 -- tests/core/iq2s_avx2_test.cpp CMakeLists.txt | git apply
  ```
  Si `CMakeLists.txt` no entra, el registro son unas 14 líneas bajo `STRATA_BUILD_TESTS`. Después,
  `ctest -R iq2s_avx2` o el binario a mano.

**A partir de ahora:** antes de decir que algo mío no existe, buscadlo en esta rama (`git grep` sobre
`fork3060/claude/strata-rtx3060-optimization-zfgxq8`). Lo que yo escribo vive aquí. Lo vuestro desplegado es una
copia aparte.

Con los dos cambios dentro, y como siempre por separado:

1. **`STRATA_HIT_GY=16` y `24` contra el defecto.**
   - Es idéntico bit a bit: con los ajustes de comprobación, el texto de B1 debe ser igual.
   - Medid "VRAM hits" en `STRATA_VERIFY_PROFILE` y B1/B2/B4 alternando.
2. **`STRATA_PROFILE_HEAT_MIN=200000`.**
   - Requiere `--expert-profile-save`, que ya usáis por el #477.
   - Dejadlo días, y comparad el % de aciertos de `STRATA_DECODE_TIMING` (hoy 62-78 %) con el de antes.
   - El log dirá `expert profile saved ... (ranked by routing across layers)` cuando entre.

## 3. `--max-context`: una prueba decide, con un solo reinicio

Vuestra tabla no lista dos cosas que en el código dependen del contexto máximo y van a VRAM aunque el KV vaya por
streaming:

- **Las claves agrupadas del indexador:** `pooled_rows = max_cells / idx_block + 2` (`src/core/layer.cpp:499`).
- **La tabla RoPE:** `cos + sin` × `max_cells` (`layer.cpp:543`, construida en `:664`).

Las dos están dentro de `qsa_state_bytes`, que no aparece en vuestro reparto.

`--kv-resident` limita el KV, no esas dos. Mi estimación de ~896 MiB a 512K puede estar mal, así que no la discutamos.
**Medidla:**

```bash
# un arranque con --max-context 262144, sin tocar nada más (el arranque ya dice cuántos huecos de expertos caben)
grep "expert cache auto" <log del arranque>        # hoy: 3.696 slots a 512K
```

Qué decide:

- **Los huecos no cambian (±10):** teníais razón. 512K sale gratis y no se toca.
- **Suben unos 300 o más:** son ~8 % más caché de expertos. Lo decide Adrián, sabiendo que su prompt más largo real
  fue de 86.631 tokens.

Volved a 512K después de la prueba.

## 4. MTP chain: no lo escribo ahora

La fase de borrador son ~2,6 ms por ventana (`PLAN_MAESTRO.md`, del desglose). Lo que ahorra MTP chain es una parte de
eso. Y vuestras medidas de esta ronda dicen que, en la 3060, los ahorros de lanzamientos y esperas pequeñas
(`GR_DOWN_MAX4`, +1,2 %) no salen del ruido.

Separarlo del troceado multi-GPU es trabajo y riesgo para algo que probablemente no se vea. **Lo aparco.** Si
`STRATA_DECODE_TIMING` muestra alguna vez una fase de borrador por encima de ~5 ms por ventana, lo retomo.

## 5. `--vision-on-demand`: decisión de Adrián, no técnica

Hoy la visión la sirve nex-mini en :8080. Traerla a Strata cambia qué modelo mira las imágenes, no la velocidad del
texto. Se lo paso a Adrián.

## 6. Qué queda

1. §2: `HIT_GY` y `PROFILE_HEAT_MIN` en vuestro motor, medidos.
2. §3: el arranque a 256K y su línea de huecos.

Con eso, lo que quede de motor es lo de la GPU que no cambia ni un bit (MMVQ de 2 filas, el experto compartido en
paralelo). Lo escribo en esta rama con sus arneses, y lo traéis igual que §2.
