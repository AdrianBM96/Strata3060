# Ronda de caché de expertos: simulador pasado con una traza propia

Para: Claude. Fecha: 2026-10-04. Respuesta a `AUDITORIA_CACHE_EXPERTOS.md`.

## 0. Qué traje (lo que pediste, §4)

1. **Contador (§3) aplicado y desplegado**: `git apply --3way` limpio; la línea de cada petición ya trae
   `..., N experts swapped in over W windows`. Compilado y en producción.
2. **Traza de enrutado** con `--dump-routing`: **14,9 MB**, **1.101 ventanas (~3.543 tokens)**, de **12 peticiones
   variadas** (código, SQL, regex, bash, explicaciones). **Aviso: no son "horas de uso real"** — es una sesión
   corta y controlada, no el uso diario de los agentes. Si hace falta la traza larga, la saco dejándolo puesto
   mientras trabajáis (con `--dump-routing` fuera de `/tmp`).
3. **Simulador** (`ops/cache-sim.py`), normal + `--per-layer` + `--grid`, con el perfil aprendido
   (`expert-profile-learned-swift.bin`) y **3.696 huecos**.

## 1. La tabla del simulador

```
1101 windows (~3543 tokens), 3696 slots, start: static (profile); counted from the first window
policy                             hit/entry  hit/dist  swaps/w    copied/w    PCIe/w
static (profile)                    31.79 %  30.20 %     0.00      0.0 MB   0.00 ms
adapt every=4 decay=0.7 swaps=96    56.36 %  51.16 %    23.92     33.1 MB   3.01 ms   <- la desplegada
adapt ... cross                     56.51 %  51.41 %    23.92     33.1 MB   3.01 ms
lru (per layer)                     70.80 %  65.99 %   386.14    533.8 MB  48.53 ms
belady (ceiling, per layer)         83.29 %  80.45 %   143.19    197.9 MB  17.99 ms
belady (ceiling, global)            84.25 %  81.70 %   134.55    186.0 MB  16.91 ms
```

## 2. Validación (§2): ¿el `adapt` simulado se parece al motor?

**Los swaps: coinciden casi exacto.** El contador del motor, en esas mismas peticiones:

| | motor | simulador |
| --- | ---: | ---: |
| **swaps/ventana** (mediana) | **24,0** | **23,92** |
| **MB/ventana** | **33,1** | **33,1** |

→ **El modelo de copias del simulador es correcto.** (Confirma el rasgo 3: ~24 swaps/ventana = ~33 MB = ~3 ms de
PCIe por ventana, **un tercio de los 9,3 ms** de la copia del decode. Compite, pero no es el dominante.)

**El acierto: NO coincide del todo.** El simulador da **56,4 %**; el motor, en las mismas peticiones, da
**60-73 %** (por petición; la línea `decode expert cache hit rate`). **Hay ~4-11 puntos de diferencia.**

No sé si es:
- que la traza es corta (1.101 ventanas, no horas) y no representa el régimen;
- que el motor cuenta algo distinto (p. ej. el warm-up del perfil, o cómo cuenta el prefill que presta huecos);
- o un desajuste real del simulador en el acierto (los swaps sí cuadran).

**Decídmelo tú**: ¿la traza corta lo explica, o el simulador está mal en el acierto y hay que arreglarlo antes de
seguir?

## 4. Traza LARGA (2ª pasada): el desajuste del acierto NO era la traza corta

Repetí con **~40 peticiones (2 rondas de 20), respuestas de 400 tokens**: **86,9 MB, 7.086 ventanas (~20.581
tokens)**.

| | motor | simulador (`adapt`) |
| --- | ---: | ---: |
| **acierto (agregado)** | **67,5 %** (5.306.471 / 7.865.839) | **58,97 %** |
| **swaps/ventana** (mediana) | **24,0** (33,1 MB) | **23,79** (32,9 MB) |

**Los swaps siguen clavados. El acierto sigue con ~8,5 puntos de diferencia** — igual que con la traza corta. Así
que **no es la traza corta**: el simulador **subestima el acierto del motor ~8 puntos** de forma sistemática.

Hipótesis a decidir por Claude:
- el motor cuenta el **prefill** (que presta ~3.020 huecos y luego los rellena) dentro del acierto del decode;
- los "lookups" del motor incluyen filas/posiciones que el simulador no modela (QSA, ventanas del borrador);
- o el simulador cuenta `hit/entry` distinto de cómo el motor cuenta `hits/lookups`.

Tabla de la traza larga:

```
7086 windows (~20581 tokens), 3696 slots, start: static (profile)
static (profile)                    36.33 %  35.92 %     0.00      0.0 MB   0.00 ms
adapt every=4 decay=0.7 swaps=96    58.97 %  55.09 %    23.79     32.9 MB   2.99 ms
lru (per layer)                     72.92 %  68.60 %   331.75    458.6 MB  41.69 ms
belady (ceiling, per layer)         85.02 %  82.53 %   118.15    163.3 MB  14.85 ms
belady (ceiling, global)            85.97 %  83.74 %   111.30    153.9 MB  13.99 ms
```

**Las conclusiones aguantan** (y con más datos): margen ~26 puntos en la política (59 → 85), LRU simple al 73 %,
y los swaps del simulador fiables.

## 5. La rejilla de flags (`--grid`), lo mejor

| Config | hit/entry | swaps/w | MB/w |
| --- | ---: | ---: | ---: |
| every=4 decay=0.7 swaps=96 (**hoy**) | 56,36 % | 23,9 | 33,1 |
| every=4 decay=0.7 swaps=256 | 58,45 % | 58,4 | 80,7 |
| **every=2 decay=0.7 swaps=256** | **62,78 %** | 83,3 | 115,1 |
| every=2 decay=0.7 swaps=96 | 58,18 % | 44,1 | 60,9 |

**Lo mejor de la rejilla gana ~6,4 puntos** (56,4 → 62,8 %) pero **copia 3,5× más** (33 → 115 MB/ventana). Como
dice tu aviso, una política que gana puntos copiando el triple puede salir perdiendo en el motor real.

## 4. Lo que veo (para tu decisión)

1. **Hay margen grande en el acierto**: la desplegada (56 %) está a **27 puntos del techo** (83 %), y **una LRU
   simple llega al 71 %**. O sea: **el margen NO está en afinar flags** (dan +6), sino en **la política**.
2. **El techo copia mucho** (198 MB/ventana, 18 ms). El compromiso acierto↔copias es real, y el simulador no
   cobra las copias: hay que mirar las dos columnas juntas.
3. **`cross` (mover huecos entre capas) no cambia casi nada** (56,51 vs 56,36): con vuestro uso, el reparto fijo
   por capas no es el problema.
4. **Los swaps del simulador son fiables** (clavados al motor). El acierto, no del todo — a decidir.

**Mi lectura:** lo que falta es una **política mejor dentro de la capa** (dos memorias, o un LRU con presupuesto de
copias), no flags. Pero antes, arreglad/decidid el desajuste del acierto para no diseñar sobre un simulador que no
reproduce al motor.
