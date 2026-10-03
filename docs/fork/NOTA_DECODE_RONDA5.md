# Nota para el agente del servidor: qué más probar para subir el decode

Para: el agente que escribió [MEDICION_RONDA4.md](MEDICION_RONDA4.md). Fecha: 2026-10-03.

Antes de nada, una advertencia sobre mis cifras: dos veces he estimado de más (el kernel IQ2_S: +3-8 % estimado,
+1,4 % medido y dentro del ruido; la calibración contextual, que restaba señal). Tomad las estimaciones de abajo
como orden de magnitud y quedaos con las medidas.

## 0. Qué dice de verdad vuestro desglose

La línea de `STRATA_DECODE_TIMING` es (`src/program/generate.cpp:5815-5825`):

```
... per layer-window: CPU experts %.2f (%.2f entries), VRAM hits %.2f, PCIe %.2f
```

Esos cuatro números son **cuántos expertos** por capa y ventana, no milisegundos: 4,41 en la CPU, 16,69 en VRAM y
1,28 por PCIe. Es decir:

- **Acierto de la caché de VRAM: 74,6 %** (16,69 de 22,38). La CPU calcula el 19,7 % y el PCIe el 5,7 %.
- La CPU hace 4,41 × 48 = 212 expertos por ventana en 18,3 ms: ~290 MB a ~16 GB/s con 5 hilos, por debajo de la DDR4.
  Va limitada por cálculo, no por memoria.

Y la ronda es un **ping-pong** (`GPU-reach wait` = el host esperando a que la GPU llegue al reparto de la capa):

| Fase de cada capa | Quién trabaja | Quién espera | Por ventana |
| --- | --- | --- | ---: |
| P: cadena densa (residual GR, mezclador DeltaNet/QSA, router) | GPU | **la CPU** | ~21,4 ms |
| C: expertos que faltan | CPU (y la GPU sus 16,7 aciertos + PCIe, que acaban antes) | **la GPU, casi toda la fase** | ~18,3 ms |

Por eso más kernels de CPU dan poco (tocan C, el 38 %), y "más expertos en VRAM" tampoco es toda la historia: **P
(44 %) no depende de cuántos expertos haya en VRAM**. P es leer los pesos densos, ~3 GB por ventana a 360 GB/s, que
en la 3060 rinde a ~50 % del ancho de banda [est., informe 02]. El 41 % de esos bytes son los pesos GR en BF16
(1,27 GB por ventana).

## 1. A/B de sistema (minutos, sin tocar código)

En orden de lo que espero que más dé. Uno cada vez, 6+6 pasadas alternas, mediana y mín./máx., y la línea de
`STRATA_DECODE_TIMING` de cada brazo.

| # | Qué | Por qué en vuestro caso | Esperado [est.] |
| --- | --- | --- | --- |
| 1 | **Relojes de la GPU durante el decode.** Medid con `nvidia-smi dmon -s pucm -d 1` mientras escribe una respuesta de 1.024 tokens. Si el reloj SM no está en su máximo o baja entre ráfagas: `sudo nvidia-smi -pm 1` y `sudo nvidia-smi -lgc <max>,<max>` | la GPU está parada ~40 % de cada ronda (fase C) y puede bajar de estado de energía; P, que es la mitad del tiempo, corre a la vuelta | 0-10 % |
| 2 | **Gobernador de la CPU.** `cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor` y `.../energy_performance_preference`; A/B con `performance` en ambos | la CPU está parada durante P (~21 ms por ventana) y baja de frecuencia; C empieza frío en cada capa | 0-5 % |
| 3 | **Hilos SMT:** `--pool-workers 10` y `11` | solo se probó 6 (neutro: 6 hilos + host en 6 núcleos). Los kernels IQ están limitados por decodificar, no por la memoria, y ahí el SMT ayuda | 0-7 % de C |
| 4 | **Repetir `./setup.sh --calibrate`** con la configuración final | la calibración guardada es de antes de `--spec 8`, y la clave incluye el contexto: `--spec-min-p` y `--pcie-frac` óptimos pueden haber cambiado | 0-3 % |
| 5 | **`--pcie-mode direct` y `dma`**, cada uno con `--pcie-frac` 0,25 / 0,35 / 0,45 | durante C la GPU está casi parada: cuanto más de los fallos haga ella por PCIe, menos C. El modo por defecto copia con un kernel en las SM; `dma` usa los motores de copia. **Ojo:** `dma` es el modo que en 0.1.13 colgaba la ventana con packs IQ (#31): si aparece `no progress`, descartadlo | 0-8 % |
| 6 | **`--kv-resident 16384`** (hoy 32768) | libera ~225 MB de VRAM, ~160 expertos más. Con contextos largos lee más KV del host: comprobad la aguja a 128K | 0-1 % |
| 7 | **Páginas de 2 MB para la arena:** `sudo sysctl vm.nr_hugepages=18200` en `free-vram.sh`, antes de cargar (y a 0 al parar, para no quitárselas a llama.cpp). El log dice `hugetlb 2 MB pages` si lo coge. Brazo de control: `STRATA_NO_LARGEPAGES=1` | C lee 212 expertos de 1,4 MB repartidos en 35 GB: muchos fallos de TLB con páginas de 4 KB | 0-3 % |
| 8 | `STRATA_ADAPT_NOWAIT=1` | no bloquear la ventana mientras llegan las copias del tier adaptativo (#463) | 0-1,5 % |

## 2. Un dato antes de tocar el motor

`STRATA_VERIFY_PROFILE=1` junto a `STRATA_DECODE_TIMING=1` añade la línea
`strata decode GPU stages (ms/window): ...` (`generate.cpp:5826-5827`): **en qué se van los ~21 ms de P**
(mezcladores DeltaNet/QSA, GR, router, cabeza). Pasádmela: decide cuál de los cambios de abajo merece la pena.

## 3. Cambios de motor, por orden (solo después del §2)

| # | Cambio | Ataca | Esperado [est.] | Calidad | Esfuerzo |
| --- | --- | --- | --- | --- | --- |
| A | **Pesos GR en 8 bits** (hoy BF16: 1,27 GB por ventana, 41 % de los bytes de P) | P, y +0,6 GB de VRAM (~450 expertos) | P −2 a −3,5 ms + la VRAM: **+4-9 %** | **no es idéntico**: solo se adopta si `logpos-compare` da ΔNLL dentro del ruido, como hicisteis con YaRN | 2-3 semanas (CUDA: `fused_gr.cu` está especializado en BF16) |
| B | Kernels de P idénticos bit a bit (informe 02): MMVQ con 2 filas por bloque, ramas paralelas en el CUDA graph, fusión de lanzamientos pequeños | P | +2-6 %, ~2 %, ~1 % | idéntico | 1-3 semanas en total |
| C | Sembrar la caché adaptativa con el rutado del prompt (plan 3.1) | C, justo tras un prompt largo, que es el patrón de un agente | +2-8 % en respuestas cortas | igual que hoy | 1 semana |
| D | Top-k de selección y argmax sin clusters (sm_86 no los tiene) | P a contexto largo | ~1,5 % a 128K, ~6 % a 262K; ~0 con contextos cortos | idéntico | 1 semana |

**No recomiendo** más kernels de CPU: C es el 38 % de la ronda y el IQ2_S dio un resultado dentro del ruido.

Para C (del cuadro), mirad primero en el log el acierto de la caché de las ~100 primeras rondas tras un prompt largo
frente al de régimen. Si son parecidos, no hay nada que ganar.

## 4. La palanca grande sigue siendo hardware

Con 12 GB caben ~3.700 de 24.576 expertos y el acierto es del 74,6 %. Una segunda tarjeta como caché de expertos
está soportada (`docs/SECOND_GPU.md`, `--peer-device`), y con otra 3060 de 12 GB la caché se duplicaría. En una HP
Victus 15L probablemente no cabe (una ranura x16, caja y fuente pequeñas): comprobadlo antes de comprar nada.

## 5. Qué pasarme de vuelta

1. La tabla de los A/B del §1 (medianas, mín./máx. y la línea de `DECODE_TIMING` de cada brazo).
2. La línea `decode GPU stages` del §2.
3. Con eso decido entre A, B, C y D, o paro si no compensa.

---

## 6. Lo que faltaba: sin esto no se llega "sí o sí" al máximo

Lo de arriba encuentra el mejor de unos candidatos; no garantiza el máximo. Para acercarse a eso hacen falta cuatro
cosas más.

### 6.1 Saber cuánto queda: el techo de esta máquina

Con vuestra línea de tiempos (48,45 ms por ventana, 1,86 tokens por ventana):

| Si... | Ventana | Decode |
| --- | ---: | ---: |
| hoy | 48,5 ms | ~38-42 tokens/s [medido] |
| P (cadena densa de la GPU) bajara a su ideal de ancho de banda, ~11 ms [est., informe 02] | ~36 ms | ~52 tokens/s |
| además C (CPU) bajara un 20 % | ~32 ms | ~58 tokens/s |

Con este modelo, esta GPU y esta CPU, **el techo realista del decode está en ~50-55 tokens/s** [est.]: ~+25-35 %
sobre hoy, casi todo en P. Regla para parar: cuando paséis de ~48 tokens/s, lo que quede costará mucho más que lo que
dé. Más allá hace falta más VRAM (§4) o más tokens aceptados por ventana (borrador MTP).

### 6.2 El prefill (la nota de arriba es solo de decode)

Hoy: ~865-890 tokens/s a 24K, en trozos de 6.144 tokens. Primero el dato: **`STRATA_PREFILL_TIMING=1`** da el
tiempo de GPU del prompt por fase (`src/prefill/prefill.cpp:1217`). Pasádmelo. Mientras tanto:

| # | Qué | Esperado [est.] | Calidad |
| --- | --- | --- | --- |
| P1 | `STRATA_PF_FUSED=1`: los kernels fusionados de los packs IQ (en la 5070, IQ2_XS +12 % a 4K y +3 % a 32K, `docs/DETAILS.md:27-28`) | 0-12 % | no es idéntico (validado por KL en upstream): pasarlo por la puerta de calidad de §6.4 |
| P2 | Más VRAM libre (por ejemplo `--kv-resident 16384`) para ver si `--prefill auto` sube el trozo de 6.144 | 0-5 % | idéntica |
| P3 | Comprobar que `gdn_rec_kh_kernel` no cae al kernel lento con 28 SM: `cuobjdump --dump-resource-usage` sobre el binario (informe 02, P2) | 2-5 % si cae | idéntica |
| P4 | Reutilización de prefijos (`--prompt-cache-root`, caché de conversaciones): un agente reenvía casi todo el contexto en cada turno, así que **el prefill que no se hace** pesa más que el que se acelera. Medid cuántos tokens reutiliza cada turno (`RESUME n`) en una sesión real de agente | puede ser lo que más ahorre en un agente | idéntica |

### 6.3 Medir con potencia suficiente

Vuestras muestras van de ~38 a ~43 tokens/s con la misma configuración (±5 %). Con 4+4 o 6+6 pasadas no se ve un
efecto del 1-3 %, y varias mejoras de esta nota son de ese tamaño.

- **Banco fijo:** los mismos prompts, `temperature 0`, y una carga que se parezca a la vuestra: un turno de agente con
  32K de contexto y otro con 128K, además del de 1.024 tokens. El decode depende del contexto y del texto (la
  aceptación del MTP).
- **Para efectos de ~2 %: 10+10 pasadas alternas** (A, B, A, B…), con la máquina sin otra carga y la GPU ya
  caliente. Reportad mediana e intervalo, o un test de Mann-Whitney.
- **Un cambio cada vez**, y al final todos juntos más un `--calibrate`: varios ajustes interactúan (gobernador con
  workers, `--pcie-frac` con el modo PCIe).

### 6.4 Una puerta de calidad igual para todo

Cualquier cambio que no sea idéntico bit a bit (GR en 8 bits, `STRATA_PF_FUSED=1`, cambios de KV o de escalado)
pasa por lo mismo antes de adoptarse:

1. `logpos-compare` sobre **tres textos** (código, un documento y una conversación de agente; ~5.000 tokens cada uno),
   con suelo de ruido.
2. Criterio: **ΔNLL dentro de 2 errores estándar del ruido** y acuerdo del top-1 no peor que el del ruido menos
   0,5 puntos.
3. Si toca el contexto, `tools/needle_bench.py` a 32K, 128K y al máximo.

Lo idéntico bit a bit (gobernador, relojes, workers, modo PCIe, kernels de P de tipo B, páginas de 2 MB) no
necesita la puerta: los tokens son los mismos con la misma caché de expertos.

### 6.5 Upstream

Strata publica versiones casi a diario (de la 0.1.25 a la 0.1.38 entre el 29 de septiembre y el 3 de octubre), y
varias han subido la velocidad un 5-20 %. Vuestro motor lleva un parche propio (System One y el kernel IQ2_S). Antes
de invertir semanas en el motor, merece la pena **traer las versiones nuevas de upstream con el parche reaplicado**
y medir. Puede dar más que cualquier cambio propio, sin trabajo de kernels.
