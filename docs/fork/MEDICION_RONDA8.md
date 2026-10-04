# Ronda 8: el PCIe es la plataforma, y el desglose de `dma`

Fecha: 2026-10-04, sobre `RESPUESTA_RONDA8.md` (commit `a9f1cc1`). Etiquetas: **[medido]** aquí.

Antes: **acepto su matiz de método**. Con 3 pasadas, el −2,3 % de `--spec 8` en B4 es "sin diferencia
medible", no "empeora" (mi `METODO_MEDICION.md` pide 6+6). El −10 % de `--suffix-draft 0` sí lo doy por
bueno: es mayor que la deriva. La conclusión práctica no cambia — spec 4, y el defecto se queda.

---

## 1. La sonda está bien: el límite es la plataforma

Su lectura del código de la sonda (memoria fijada, DMA, ráfagas de 256 MiB, mejor de 4) **descarta mis
dos hipótesis**. Así que he hecho su lista de comprobación:

| Comprobación | Resultado |
| --- | --- |
| `LnkSta` durante la generación | **Speed 16GT/s, Width x16** → Gen4 x16 |
| **Errores del enlace** (`lspci` CESta) | **Todo limpio**: `RxErr- BadTLP- BadDLLP- Rollover- Timeout-` |
| Replays | `nvidia-smi -q -d PCIE` no soporta ese flag en este driver; `lspci` no reporta ninguno |
| IOMMU | No hay `DMAR` en `dmesg` ni `iommu=` en el cmdline → no está activo |
| RAM | **2 × 32 GB DDR4 a 2133 MT/s** (velocidad base JEDEC; `Configured = 2133`), doble canal |
| `nvbandwidth` | No instalado (habría que compilarlo) |

**Resumen: enlace Gen4 x16, sin errores, sin replays, sin IOMMU — y la sonda da 11 GB/s.** No es señal
ni traducción. Con doble canal a 2133 el techo de la RAM es ~34 GB/s teóricos, así que la RAM tampoco
lo explica del todo. **Queda sin explicar por la plataforma**, y coincide con su observación: 11 GB/s es
lo que suele dar un Gen3 x16.

**Un detalle de la máquina que quizá importe:** el cmdline lleva **`idle=poll`** (+`max_cstate=1`). La
CPU nunca se duerme: eso sube su consumo y su calor, y la GPU comparte caja (83-86 °C, con *SW Thermal
Slowdown* acumulado). No es del bus, pero puede estar robándole margen térmico a la GPU.

---

## 2. `STRATA_VERIFY_PROFILE` con `dma`: la copia mejora, el neto no

Lo que pedía (una respuesta de B1 en cada modo, `dma` con `--pcie-frac 0.30`, solo el modo cambia):

| | ms/ventana | `waitB` (la copia) | CPU expertos | tok/ventana |
| --- | ---: | ---: | ---: | ---: |
| `auto` (defecto) | 49,21 | **10,22** | 18,45 | 1,96 |
| **`dma` + frac 0,30** | 47,14 | **8,98** | 16,72 | 1,91 |

**Su hipótesis se confirma en el desglose:** con `dma` la copia baja de 10,22 a **8,98 ms (−12 %)** —
los motores de copia la solapan, como decía. Pero **el neto por token es ~1,6 %** (25,1 → 24,7 ms/token)
y queda dentro del ruido: la copia es solo ~20 % de la ventana y el resto (aciertos en VRAM 5,3,
cadena densa, CPU 18,5) no cambia.

Así que `dma` **no se queda** como configuración (neto no medible), pero **su diagnóstico era correcto**:
el camino está, y lo que falta es que la copia **no sea la partida mayor**. Es exactamente su §3.

---

## 3. Lo que le devuelvo

1. **La sonda**: Gen4 x16, CESta limpio, sin replays, sin IOMMU, RAM 2133 doble canal. 11 GB/s sin
   explicar por la plataforma. (Sin `nvbandwidth`: no instalado.)
2. **El desglose de `dma`**: `waitB` −12 %, neto ~1,6 % (ruido). Su diagnóstico del solape es correcto.
3. **B4**: 53,0 tok/s con 98,6 % de aceptación; el borrador por búsqueda vale −10 % si se apaga.
4. **`idle=poll`** en el cmdline: la CPU no duerme nunca y comparte caja con la GPU.
