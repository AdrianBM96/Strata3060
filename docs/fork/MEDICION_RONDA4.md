# Ronda 4: calibración arreglada, y YaRN **sin diferencia medible**

Fecha: 2026-10-03. Verifica el commit `05305b5` en el i5-12400F / RTX 3060.
Etiquetas: **[medido]** aquí · **[est.]** estimación.

---

## 1. Su corrección de la calibración: **funciona** en el modelo real

El arreglo (corrida sin contenido con estado `N/A`, **pregunta neutra y todas las opciones con el
mismo texto**) mide solo el sesgo de letra, como pretendía. Probado en la 3060 con el caso que
fallaba:

| Config | Respuesta (correcta `b`) | margin | agreement | escalate |
| --- | --- | --- | --- | --- |
| sin corregir | **b** ✅ | 0,045 | 1,0 | false |
| **calibración (corregida)** | **b** ✅ | **0,900** | 1,0 | false |
| 2 perm + calibración | **b** ✅ | 0,364 | 0,5 | **true** |
| 3 perm + calibración | a ❌ | 0,077 | 0,67 | **true** |

Dos cosas buenas: la calibración **ya no mata la señal** (el margen sube de 0,045 a 0,900 en vez de
voltear la respuesta), y **`escalate` por acuerdo funciona**: los dos casos malos (2 y 3
permutaciones) escalan porque `agreement < 1`.

Sus **14 tests pasan** aquí (`Ran 14 tests ... OK`), incluido el nuevo
`test_calibration_removes_letter_bias_but_keeps_the_options_prior`.

---

## 2. `STRATA_DECODE_TIMING=1`: la CPU es el **37,8 %** de la ronda

Una respuesta de 1.024 tokens, con su instrumentación:

```
48,45 ms/ventana (551 ventanas, T medio 2,33, 1,86 tokens/ventana) =
   verify 43,02  =  espera GPU 21,43  +  host por capa 19,15 [CPU expertos 18,32]  +  stage 0,31
 + commit/emit 0,03 + borrador 2,57
por capa-ventana: CPU expertos 4,41 ms, aciertos VRAM 16,69 ms, PCIe 1,28 ms
```

| Parte | ms/ventana | % |
| --- | ---: | ---: |
| **CPU expertos** | 18,32 | **37,8 %** |
| **Espera de la GPU** | 21,43 | **44,2 %** |
| Borrador MTP | 2,57 | 5,3 % |
| resto (plan, actq, jobs, stage, commit) | ~6,1 | 12,6 % |

**Respuesta a su pregunta**: la CPU de expertos es **37,8 %**, justo por debajo del 40 % que él marcó
como umbral. Confirma que **tocar más kernels de CPU dará pocos puntos**: el kernel IQ2_S tocaba un
subconjunto de ese 38 % y dio +1,4 % (dentro del ruido).

Lo que domina es la **espera de la GPU (44 %)**, que baja con **más expertos en VRAM** — es decir, la
palanca sigue siendo la VRAM, no los kernels.

Matiz honesto: el reparto suma la espera de GPU y el trabajo de host como si fueran secuenciales; si
solaparan mejor, el margen de la CPU sería menor todavía. En cualquier caso, no es donde está el
tiempo.

---

## 3. YaRN: **sin diferencia medible** (método `logpos-compare`)

Su método fuerte, sobre 4.934 tokens de `llama-model.cpp` (código real), con suelo de ruido (dos
corridas del mismo perfil):

```
A (262K, sin YaRN) vs B (512K, YaRN):
  posiciones 4.933 | top-1 igual 99,0 % | top-5/10 0,902/0,897 | KL 0,0119 | ΔNLL +0,0008 ± 0,0020
A vs A2 (mismo perfil, ruido):
  posiciones 4.933 | top-1 igual 99,1 % | KL 0,0080 | ΔNLL -0,0010 ± 0,0013

ΔNLL de B sobre el ruido: +0,0018 ± 0,0024 nats/token  ->  SIN DIFERENCIA MEDIBLE
```

**Conclusión:** YaRN ×2 cambia la salida (es un modelo ligeramente distinto, ya lo medimos: diverge
a los ~195 caracteres), pero **en calidad no se distingue del original** a este tamaño de muestra:
99,0 % de acuerdo en el top-1 y una diferencia de NLL dentro del ruido.

**Por tanto: el perfil diario se queda en 512K con YaRN.** No hace falta renunciar al contexto por
miedo a perder calidad. `swift262` queda como alternativa, no como recomendación.

**Caveat:** medido con **un solo texto** (código). La recomendación de Claude era repetirlo con un
documento y una conversación; queda pendiente si se quiere cerrar del todo. Con 4.934 posiciones, el
error estándar ya es pequeño.

---

## 4. Estado desplegado

```
kv int8 · spec 8 · mtp-max-t 4 · logprobs 32 · prompt-cache-root 256 · draft_vocab en
kernel IQ2_S por bloque · ada-decide con calibración corregida y escalate por acuerdo
decode ~40-42 tok/s · prefill ~865 tok/s · decide 0,37 s (2.ª en adelante)
perfil diario: swift (512K, YaRN)  — YaRN no cuesta calidad medible
```

## 5. Pendiente

1. `logpos-compare` con un **documento** y una **conversación** (cerrar el YaRN del todo).
2. **Etiquetar 100-200 decisiones reales** (`--log` + `fit-calibration.py`): es lo único que da
   número a la temperatura y a los umbrales. Y ahora que `calibrate` está arreglado, conviene
   volver a probarlo con `cf` (su §2): si el sesgo por letra sale casi uniforme, se apaga.
