# Ronda 3: kernel IQ2_S medido, y la calibración que empeora las decisiones

Fecha: 2026-10-03. Verifica el commit `743f633` (kernel IQ2_S + `escalate` + `logpos-compare`) en la
RTX 3060 / i5-12400F. Etiquetas: **[medido]** aquí · **[est.]** estimación.

---

## 1. Kernel AVX2 de IQ2_S: bit-idéntico, y **+1,4 %** (no +3-8 %)

### Test bit a bit — pasa
```
g++ -O2 -std=c++20 -mavx2 -mfma -mf16c -Iinclude -Ithird_party/ggml tests/core/iq2s_avx2_test.cpp -o iq2s
./iq2s
  → iq2s_avx2_test: block kernel bit-identical to the generic one (NT 1-8, 3 seeds, 4 patterns)
```
Corre **sin GPU** [medido]. Swift IQ2_XS usa IQ2_S: **155 tensores** de tipo 22 en el pack.

### Microbench en el i5-12400F — **mejor que en su Xeon**
`taskset -c 0-5 ./iq2s --bench`:

| Tokens por fila | Genérico | Bloque | |
| ---: | ---: | ---: | ---: |
| 1 | 2,28 GB/s | 3,78 GB/s | **x1,66** |
| 2 | 2,26 GB/s | 3,78 GB/s | **x1,67** |
| 3 | 1,34 GB/s | 1,53 GB/s | x1,14 |
| 4 | 1,23 GB/s | 1,45 GB/s | x1,17 |
| 8 | 0,81 GB/s | 0,90 GB/s | x1,11 |

Supera su criterio (≥1,3 a 1-2 tokens) y su Xeon (×1,55) [medido].

### End-to-end — **+1,4 %**, con el mismo binario
`STRATA_IQ2S_BLOCK=0` (genérico) contra el defecto (bloque), 4+4 pasadas alternas:

| | muestras (tok/s) | mediana |
| --- | --- | --- |
| **por bloque** (defecto) | 38,5 · 42,7 · 40,4 · 40,4 | **40,40** |
| genérico (`=0`) | 40,1 · 40,7 · 38,8 · 39,6 | 39,85 |

Se comprobó que la variable llegaba al motor (`Environment=... STRATA_IQ2S_BLOCK=0` y su
`/proc/<pid>/environ`) [medido].

**+1,4 %**, no el +3-8 % estimado. La razón es la tabla de arriba: el ×1,66 vive en **1-2 tokens por
fila**, y en la ronda real el kernel es solo una parte. Aun así es **gratis** (bit-idéntico) y se
queda **por defecto**.

---

## 2. `escalate` al revés: probamos su predicción, y **no se cumple**

Con `--permutations 3 --calibrate`, como él pidió, el caso **claro** (que antes acertaba) pasa a
**fallar**:

| Config | Respuesta (correcta: `b`) | margin | agreement |
| --- | --- | --- | --- |
| sin corregir | **b** ✅ | 0,098 | 1,0 |
| 2 permutaciones | **b** ✅ | 0,241 | 1,0 |
| 3 permutaciones | a ❌ | 0,150 | **0,33** |
| **solo calibración** | a ❌ | 0,476 | 1,0 |
| 2 perm + calibración | a ❌ | 0,428 | 1,0 |
| 3 perm + calibración | a ❌ | 0,583 | 1,0 |

Su predicción era: con corrección, el vago y el vacío escalarían y el claro no. Lo que pasó es lo
contrario: **el claro se volvió incorrecto y con margen alto (0,58), así que no escala**.

### La causa: la calibración resta señal, no sesgo

`CONTENT_FREE = "N/A"` sustituye **solo el estado**; la **pregunta y las opciones siguen ahí**, y
llevan la respuesta. Con `State: N/A` el modelo ya deduce que "Bugs o caídas" es lo plausible, así
que `p_cf(b)` es alta; restarla **penaliza la respuesta correcta** y empuja a `a`. No es un fallo de
código: es la técnica de Zhao et al. aplicada sin quitar la pregunta.

**Recomendaciones:**
- **No activar `calibrate`** (hoy es el defecto: `false`). Tal cual está, degrada.
- Si se quiere calibración contextual, la corrida sin contenido debe neutralizar **también la
  pregunta** (o usar la variante de "unknown option"), no solo el estado.
- El **`agreement`** sí funciona: detectó el fallo de las 3 permutaciones (0,33 < 1). Es la señal
  útil que ya trae.
- **2 permutaciones** fue la única variante que mantuvo el acierto (y subió el margen de 0,098 a
  0,241). Si se quiere corregir algo, empezar por ahí.

---

## 3. Lo que queda

1. **`logpos-compare` para YaRN** (§2 de su respuesta): pendiente. Es el método serio (miles de
   posiciones con error estándar) y decide el perfil diario.
2. **Etiquetar decisiones** (`--log`) para `fit-calibration.py`: sin eso ni la temperatura ni los
   umbrales tienen número.
3. **Con la calibración desactivada**, revisar si `escalate` vuelve a tener sentido por
   `option_mass` (que fue estable: 0,92-0,94 en los tres casos).

---

## 4. Estado desplegado

```
kv int8 · spec 8 · mtp-max-t 4 · logprobs 32 · prompt-cache-root 256 · draft_vocab en
kernel IQ2_S por bloque (defecto) · ada-decide: calibracion DESACTIVADA por defecto
decode ~40-42 tok/s · prefill ~865 tok/s · decide 0,37 s (2.ª en adelante)
```
