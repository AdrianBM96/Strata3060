# Ronda 9 medida: `idle=poll` no cambia nada, y la RAM de 3200 a 2133

Fecha: 2026-10-04, sobre `RESPUESTA_RONDA9.md` (commit `eb5cddc`). **[medido]** aquí.

---

## 1. `idle=poll`: A/B entre reinicios — **sin diferencia**

Se quitó `idle=poll` (con `intel_idle.max_cstate=1 processor.max_cstate=1`) de
`/etc/default/grub`, se reinició en remoto y se midió el mismo B1 (1.024 tokens, razonamiento alto):

| Brazo | pasadas (tok/s) | mediana | kernel |
| --- | --- | ---: | --- |
| **A: con `idle=poll`** | 40,4 · 42,0 · 41,8 | **41,80** | 6.8.0-139 |
| **B: sin `idle=poll`** | 45,6 · 41,8 · 41,7 | **41,80** | 6.8.0-**142** |

**Mediana idéntica.** Pero **el test no es limpio**: el reinicio también aplicó una actualización de
kernel pendiente (139 → **142**), así que B = sin `idle=poll` **y** con kernel nuevo. Dos cambios a la
vez. Con 3 pasadas por brazo tampoco se resuelve un efecto pequeño.

**Decisión: se restaura el `grub`** (vuelve `idle=poll`). No hay evidencia para cambiar la
configuración de Adrián; el `.bak` se perdió al vaciarse `/tmp`, pero el valor original era conocido y
se ha reescrito. Se aplica en el próximo arranque (el actual corre sin él, sin problema).

Si alguien quiere quitarlo de verdad, el beneficio esperado es **térmico y de consumo** (la CPU deja de
girar entre peticiones), no de velocidad — y para verlo hay que medir potencia/temperatura, no tok/s.

**Nota operativa:** `/tmp` **sí se vacía** al reiniciar (aunque `findmnt` dijera que está en el disco
raíz). Se perdieron los scripts, el clon y los `jsonl`. Lo importante sobrevive: el repo, `~/Strata/*.md`
y `~/idle-poll-AB.md`. Tailscale y todos los servicios volvieron solos (clave de nodo sin caducidad).

---

## 2. La RAM: módulos de 3200 corriendo a 2133

| | |
| --- | --- |
| Módulos | **Corsair `CMK64GX4M2E3200C16`** = kit **DDR4-3200** (2 × 32 GB) |
| `Configured Memory Speed` | **2133 MT/s** → **XMP desactivado**, ~34 GB/s en vez de ~51 |
| Medido ahora (`membw`, 6 hilos) | **29,8 GB/s** |
| Expertos de CPU | ~210 por ventana × ~1,4 MB en 18,45 ms ≈ **16 GB/s** = **el 54 %** de la RAM |

**No se arregla en remoto** (es BIOS; sin vPro/AMT en un H670 con i5-12400F; un fallo de arranque
dejaría el PC sin acceso). Queda para la visita física, **junto con `idle=poll`**, y hay `membw` para
medir antes y después.

El 54 % dice que la CPU no está contra el límite de RAM, así que XMP ayudaría a su parte, pero
**modestamente**; y a la copia PCIe, nada (11 < 29,8).

---

## 3. Lo que le devuelvo a Claude

1. `idle=poll` medido y descartado (con el confundido del kernel): restaurado.
2. La RAM a 2133 con módulos de 3200; `membw` = 29,8 GB/s; la CPU usa el 54 %. XMP para la visita física.
3. `/tmp` se vacía al reiniciar: los scripts de medida no sobreviven a un reinicio.
