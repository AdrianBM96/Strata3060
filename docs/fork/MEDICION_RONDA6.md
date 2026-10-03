# Ronda 6: V2 verificado, el bucle de aprendizaje desplegado y funcionando

Fecha: 2026-10-03, sobre `RESPUESTA_RONDA5.md` (commit `15243ca`). Etiquetas: **[medido]** aquí.

---

## 1. V2: correcto, y la velocidad no se distingue

**Corrección (correcta):** sus respuestas deben coincidir con V2 apagado. Verificado con su banco,
S2 (4 preguntas **distintas** sobre un estado en caché), `--keep-answers`:

| Pregunta | tipo | Sin V2 | Con V2 | |
| --- | --- | --- | --- | --- |
| security | noul | True | True | igual |
| channel | choice | status | status | igual |
| escalate | noul | True | True | igual |
| sentiment | score | 1,93-1,97 | 1,94-1,97 | difiere en el 2.º decimal |

Las **decisiones discretas coinciden exactamente**. El `score` (valor esperado, continuo) oscila en
el segundo decimal — y también oscila **dentro del mismo brazo** (1,93-1,97 sin V2), así que es el
redondeo que él anticipó: los tokens van en una ventana de otro tamaño.

**Velocidad** (`bench.py compare`, S2, 6+6):

| | n | mediana | Δ | IC 95 % | p |
| --- | ---: | ---: | ---: | --- | ---: |
| V2 apagado | 6 | 3,62 s | | | |
| V2 encendido | 6 | 3,56 s | **−1,7 %** | [−0,50, +0,40] | 0,689 |

**Sin diferencia medible.** Con 2+2 daba −14 %, que era ruido. Se **queda encendido**: no hace daño,
es su diseño y ahorra una ventana por decisión; el banco no tiene potencia para resolver un ~5 %
sobre 3,6 s.

---

## 2. Sus dos correcciones a mis medidas: las dos acertadas

**a) `waitB` incluye la copia PCIe.** En `auto` la copia la hacen las SM, y por eso pesa 9,3 ms. El
A/B que faltaba era **`dma` con `--pcie-frac` 0,35**, para solapar esa copia con los aciertos de
VRAM. Medido con el protocolo correcto (reinicio por medida, orden alterno, temperaturas 84-86 °C
en las 12):

| | n | mediana | mín. | máx. |
| --- | ---: | ---: | ---: | ---: |
| base (`auto`) | 6 | **40,05** | 38,30 | 40,70 |
| `dma` + `--pcie-frac 0.35` | 6 | **40,15** | 38,60 | 40,90 |

**+0,25 %, rangos solapados: sin diferencia.** Su hipótesis del solape no se materializa en esta
máquina. Con esto, **todos los A/B de configuración/decodificación que quedaban están agotados**:
lo que quede tiene que venir de cambios de motor (su D3) o de más VRAM.

**b) Mis preguntas de System One eran repetidas.** Tenía razón: `304 reused + 7 read` es una pregunta
ya en un punto de control. Con preguntas **distintas** (S2 de su banco):

| | mediana |
| --- | ---: |
| S2, 4 preguntas distintas, estado en caché | **3,56 s → ~0,9 s por pregunta** |
| S1, estado nuevo + 4 preguntas | 6,26 s (tras la carga) |

Así que los **0,37 s** que publiqué eran con preguntas repetidas. El número honesto es **~0,9 s por
pregunta** con el estado en caché, y coincide con su estimación (0,6-1 s).

**`--short-read 128`** (su sugerencia, porque la cola de una pregunta son 40-70 tokens): S2 mediana
**3,46 s** frente a 3,56 con el defecto (64) → **neutro-positivo**, se queda.

---

## 3. F0: el banco fijo, funcionando

`bench.py` corre aquí: S1 y S2 miden con tiempo de pared, `compare` da mediana, Δ %, IC bootstrap y
Mann-Whitney, y avisa de pocas pasadas. Es la herramienta que faltaba para no volver a caer en los
A/B por bloques.

---

## 4. F2: el bucle de autoaprendizaje, **desplegado y funcionando**

Activado con `--db`, en la unidad de `ada-decide` (base persistente, auditoría al 5 %):

```
--db /home/bazzite/Strata/systemone.db --audit-rate 0.05 --alpha 0.05 --delta 0.1
```

**Prueba con auditoría al 100 %** (para forzar etiquetas), dos decisiones sobre el caso del fallo:

| decisión | System One dijo | System Two (auditoría) | |
| --- | --- | --- | --- |
| 1 | `billing` (0,49) | `technical` | **no coincide** — el bucle cazó el error |
| 2 | `technical` | `technical` | coincide |

```
decisions: 2 · labels: {'audit': 2} · audit_agreement: 0.5 · system2: {done: 2, failed: 0}
```

Es exactamente lo que pedisteis: **se etiqueta solo desde el uso real, sin que nadie ponga etiquetas
a mano**, y la auditoría detecta cuándo el decisor se equivoca. Con pocas etiquetas no hay umbral
conformal, así que `auto` es 0 (todo escala) — como él diseñó.

Sus 11 tests pasan aquí. **Lo siguiente** es dejarlo correr con uso real y mirar
`GET /v1/systemone/stats`: con ~20 etiquetas por plantilla se dispara el campeón/aspirante.

---

## 5. Relojes de la GPU: los dos límites

`nvidia-smi -q -d PERFORMANCE` (contadores acumulados desde el arranque):

```
SW Power Capping      : 21.938 s  (~6,1 h)
SW Thermal Slowdown   : 24.449 s  (~6,8 h)
HW Thermal Slowdown   : 0
```

Así que limitan **los dos**: potencia y temperatura. Bloquear el reloj no sirve; con el flujo de aire
de la caja mejor, sí. (Ya estaba en `METODO_MEDICION.md`.)

---

## 6. Configuración final

```
kv int8 · spec 4 (V2 volvió a 4) · logprobs 32 · prompt-cache-root 256 · short-read 128
draft_vocab en · kernel IQ2_S por bloque · V2 (STRATA_S1_FOLD por defecto)
ada-decide: calibracion corregida, escalate por acuerdo, y el bucle de aprendizaje activo
```

~40-42 tok/s de decode · ~865 de prefill · **~0,9 s por decisión** (estado en caché, preguntas distintas).

---

## 7. Lo que sigue

1. **Dejar correr el bucle** una semana con uso real y mirar `stats` (es su §5.4). Con ~20-200
   etiquetas por plantilla entran la calibración aprendida y el conformal de verdad.
2. **F3-F4:** la cabeza de decisión (`emb=1` en el motor) y la memoria kNN — tienen sentido con unos
   cientos de etiquetas.
3. **`--pcie-mode dma` con `--pcie-frac` 0,35/0,45**: en medición.
