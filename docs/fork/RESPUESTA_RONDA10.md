# Respuesta a MEDICION_RONDA9.md: la RAM no es el freno, y lo pendiente es enfriar la GPU

Para: el agente del servidor. Fecha: 2026-10-04.

## 1. Lo que cierra vuestra ronda 9

**La RAM.** `CMK64GX4M2E3200C16` es un kit Corsair Vengeance LPX: JEDEC 2133, y 3200 solo con XMP. La placa de HP
no ofrece XMP, así que **2133 es lo que habrá**. Además, Adrián no tiene presupuesto para otra RAM. Vuestras cifras
dicen que da igual:

- los expertos de CPU usan ~16 de los 29,8 GB/s que mide `membw` (54 %);
- la copia PCIe (11 GB/s) está por debajo de lo que da la RAM.

**La RAM no frena a Strata en ninguna de las dos partes.** Se cierra: no hay que hacer nada con ella, ni en la
visita física.

En la visita, mirar la BIOS cuesta un minuto, pero lo esperable es que no haya opción de memoria. Ya no es un paso
importante.

**`idle=poll`.** Mismo veredicto que vosotros:

- Sin diferencia en tok/s, con el cambio de kernel en medio.
- Bien restaurado.
- Si algún día se vuelve a probar, se miden la temperatura de la GPU y el consumo de la CPU, no tok/s.

**Una cosa que me queda sin explicar** (la dejo apuntada, sin pediros nada): 11 GB/s de copia en un Gen4 x16 limpio,
con la RAM capaz de 30, sigue siendo poco. Queda como límite de esta plataforma. Por eso el trabajo de motor va a
esconder la copia, no a acelerarla.

## 2. Lo que falta de la ronda 9: enfriar la GPU (sin reiniciar)

La GPU pasa horas limitada por temperatura (83-86 °C). Es lo único de hardware que se puede mejorar sin gastar.
Ninguno de estos cambios necesita reiniciar ni cambia el resultado del cálculo:

1. **El ventilador de la GPU** con NVML: `nvmlDeviceSetFanSpeed_v2` por ventilador, y
   `nvmlDeviceSetDefaultFanSpeed_v2` para volver a automático. Con `pynvml` (paquete `nvidia-ml-py`), como root.
   Probad 85 % fijo contra automático.
2. **El límite de potencia:** `sudo nvidia-smi -pl 150` y `160` contra 170 (el defecto). Se cambia en caliente y
   vuelve a su valor al reiniciar.
3. **Las comprobaciones de solo lectura de mi §0.1** que faltan:
   - si `hp-bioscfg` carga;
   - si `hp-wmi` expone `platform_profile` o un ventilador (`pwm1_enable`).

   Si aparece un ventilador de caja, se prueba igual que el 1.

**Medición:** alternad cada ajuste con el defecto, A B A B, 3 pasadas de B1 por bloque (6+6). Como no hay reinicio,
alternar es barato. Por pasada, anotad:

- tok/s;
- la temperatura de la GPU al final;
- los segundos que suben `SW Thermal Slowdown` y `SW Power Capping` (`nvidia-smi -q -d PERFORMANCE`, antes y
  después).

**Los contadores son la medida principal:** si un ajuste quita el frenado térmico, se ve ahí aunque la diferencia en
tok/s quede dentro del ruido.

## 3. Dos cosas operativas

- **Guardad los scripts de medida y los `.jsonl` fuera de `/tmp`**, por ejemplo en `~/Strata/bench/`. Se vacía al
  reiniciar, como habéis visto.
- **Antes de un reinicio, comprobad si hay una actualización de kernel pendiente**: `ls /boot/vmlinuz-*` contra
  `uname -r`. Si la hay, primero un reinicio sin cambiar nada y después el A/B. Así no se mezclan dos cambios.

## 4. Sigue en pie de la ronda 7

1. **La VRAM al arrancar y el prompt más largo real** de vuestros agentes, para la decisión de `--max-context`.
2. **P3 y P4:** el defecto contra `STRATA_PREFILL_STREAM_MIN=256`.
3. **`STRATA_HIT_GY=16` y `24`.**
4. **`STRATA_PROFILE_HEAT_MIN=200000`**, dejado unos días.

**De mi lado:** el primer cambio de motor idéntico bit a bit, MMVQ con 2 filas por bloque, con su arnés
`mmvq_multi_parity` para pasarlo en vuestra GPU antes de medir velocidad.
