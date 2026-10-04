# Ronda 10 medida: enfriar la GPU (ventilador y potencia), sin reiniciar

Fecha: 2026-10-04, sobre `RESPUESTA_RONDA10.md`. **[medido]** aquí.

## 1. Ventilador de la GPU: al 100% quita el *thermal slowdown*, pero no da tok/s

Controlado con NVML (`pynvml`, `nvmlDeviceSetFanSpeed_v2` / `nvmlDeviceSetDefaultFanSpeed_v2`), **como root**
(sin root da `NVMLError_NoPermission`). Mismo B1 (1.024 tokens, razonamiento alto), alternando
automático / 100% / automático / 100% / automático. La métrica que importa son los **contadores de throttling**
(delta de `SW Thermal Slowdown` por brazo de 3 pasadas):

| Brazo | ventilador | tok/s (3 pasadas) | temp | Δ slowdown térmico |
| --- | ---: | --- | ---: | ---: |
| auto | 84 % | 43,4 · 40,3 · 38,3 | 88 °C | **29 s** |
| **100 %** | 100 % | 40,5 · 38,6 · 44,8 | 82 °C | **0 s** |
| auto | 89 % | 43,1 · 42,4 · 42,9 | 89 °C | **26 s** |
| **100 %** | 100 % | 44,3 · 41,4 · 41,4 | 85 °C | **1 s** |
| auto (restore) | 88 % | 41,4 · 38,6 · 40,3 | 89 °C | **33 s** |

**Mediana de tok/s: auto 41,35 · 100% 41,40 → idénticas.** El ventilador al 100% **sí** elimina el estado de
*slowdown* térmico (26-33 s → 0-1 s) y baja 4-6 °C, pero **no cambia los tok/s** en 3 pasadas. Es una ganancia de
**salud/consistencia** (y de ruido: el 100% fijo suena), no de velocidad. **No se deja fijo**: el ventilador vuelve
a automático.

> Nota: 3 pasadas son demasiado cortas para ver si el *slowdown* acumulado cuesta tok/s en una sesión larga de
> horas. Si algún día se quiere cerrar, medir una tanda larga (30-60 min) con y sin ventilador fijo.

## 2. Ventilador del sistema: no es controlable

`hp_wmi` expone un `hwmon` `hp` con **solo `pwm1_enable`** (valor 2 = automático): **no hay `pwm1` ni lecturas**
(`fan1_input`), así que no se puede fijar la velocidad. `platform_profile` no existe en `/sys/firmware/acpi/`
(lista vacía). `hp-bioscfg` está como módulo pero sin herramienta. **Nada que tocar por software en remoto.**

## 3. Límite de potencia de la GPU: tampoco ayuda

`nvidia-smi -pl` (170 W por defecto, mínimo 100). Alternando 170 / 150 / 170 / 150, 3 pasadas cada uno:

| Vatios | tok/s | mediana | Δ slowdown térmico | Δ power capping |
| ---: | --- | ---: | ---: | ---: |
| 170 | 41,7 · 43,0 · 40,6 / 42,8 · 40,1 · 40,5 | **41,15** | 0 s | 0 / 44 s |
| 150 | 41,4 · 41,8 · 41,9 / 42,0 · 41,4 · 42,5 | **41,70** | 2-3 s | 19-33 s |

Dentro del ruido, y bajar a 150 W **aumenta** el *power capping* sin bajar la temperatura (86-88 °C). Restaurado a
**170 W**. La temperatura la manda el **ventilador/disipación**, no el límite de potencia.

## 4. `STRATA_PF_FUSED=1` (expertos int8 fusionados en el prompt): **+5,2 % prefill, sin recompilar**

Nuestro pack es nativo (`moe_fused_iq`, cubre IQ2_XXS/IQ2_S) y el flag viene **apagado** (el default `enabled()`
es solo para el pack Q2_0; el nativo es `requested()` = `STRATA_PF_FUSED=1`). Se activa con un drop-in de systemd,
sin tocar el motor. A/B alternando **off / on / off**, prompt único de **12.330 tokens** (para forzar `cache_n=0`,
prefill real; la caché de conversación es en memoria y se vacía al reiniciar):

| Brazo | prefill (tok/s) | mediana |
| --- | --- | ---: |
| off (1) | 868,0 · 845,2 · 834,6 | 845,2 |
| **on** | **909,4 · 886,3 · 865,6** | **886,3** |
| off (2) | 863,2 · 840,0 · 831,2 | 840,0 |

Off combinado: 831,2 · 834,6 · 840,0 · 845,2 · 863,2 · 868,0 → mediana **842,6**. On: **886,3**.
**+5,2 %** de lectura de prompt, y el motor lo confirma en el log: `prompt experts on the fused int8 kernels
(STRATA_PF_FUSED=1, #136)`. Los dos brazos off (antes y después) quedan por debajo, así que no es deriva térmica.

**Estado: ADOPTADO** (`serve-strata.sh` exporta `STRATA_PF_FUSED=1`; `STRATA_PF_FUSED=0` lo apaga). Segundo
A/B independiente, a 11,4K tokens y **con puerta de calidad**:

| Brazo | prefill mediana | salida (sha) |
| --- | ---: | --- |
| off1 | 914,6 | `363a0d01` |
| **on1** | **949,9** | **`363a0d01`** (igual) |
| off2 | 889,5 | `673c62c4` |
| **on2** | **948,3** | **`673c62c4`** (igual) |

Off combinado 902,5 → on 949,1 → **+5,2 %** (coincide con el primer A/B). Y **la calidad no se mueve**: cada
brazo `on` coincide **exactamente** con el `off` que tiene al lado (`on1==off1`, `on2==off2`). Lo que difiere es
`off1` vs `off2`, dos ejecuciones *off* entre sí: es **no-determinismo del propio motor** (con el mismo prompt y
`temperature 0`, en la misma instancia, el `content` sale idéntico pero la **longitud del razonamiento** varía,
236 vs 253 tokens). El tier adaptativo ya avisa de que no es bit-exacto run-to-run. **El flag no añade deriva.**

**Pendiente (para Claude):** la puerta de calidad fuerte es *teacher-forcing* (log-verosimilitud de una
continuación fija con y sin el flag), como validó upstream el camino Q2_0 ("tan cerca de FP16 como MMQ"). El test
de salida emparejada es una señal, no una garantía.
