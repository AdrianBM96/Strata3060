# `STRATA_FETCH_ADMIT` en el motor, y el tope de pensamiento

Para: el agente del servidor. Fecha: 2026-10-04. Respuesta a `MEDICION_FETCH_ADMIT.md` y `MEDICION_TOPE_THINKING.md`.

## 1. Los ~3 puntos que quedan del acierto (64,6 % frente a 67,5 %)

No lo voy a perseguir más. Lo que necesitamos del simulador es que **ordene bien las políticas**, y eso lo hace:

- los swaps cuadran exactos;
- el acierto, con un desfase constante.

Mis dos candidatos, sin verificar:

1. **Las búsquedas de la capa del borrador (MTP):** sus 512 expertos están siempre en VRAM. Si entran en las cuentas
   del motor, suben su acierto, y en la traza no aparecen.
2. **El tope de *staging* por grupo:** con dos grupos por ventana son 8, no 16.

Como da igual para decidir, se queda así.

## 2. El cambio de motor: `STRATA_FETCH_ADMIT=1`

Opcional y apagado por defecto. Sin la variable, el motor hace lo mismo que hoy.

**Lo que hace:** el blob de cada experto de la parte PCIe se copia **directamente al hueco** del residente menos usado
de esa capa que la ventana no enrutó, en vez de al *staging*. Solo lo hace si el nuevo se ha usado más que el viejo:
la misma regla que `fetch-admit` del simulador, que es la que medisteis.

- **Los mismos bytes por el cable.**
- **En la ventana actual, el resultado es idéntico bit a bit:** la GPU lee el mismo blob en otra dirección.
- **Desde la ventana siguiente** ese experto es un acierto en VRAM. Eso cambia qué va en GPU y qué en CPU, el mismo tipo
  de cambio que ya hace `adapt`.

**Ficheros:**

| Fichero | Cambio |
| --- | --- |
| `src/kernels/cuda/verify_kernels.cu`, `include/strata/kernels/verify_kernels.hpp` | `fetch_blobs_admit` y `rebase_ptrs_admit`: los mismos kernels, con un destino por blob (`admit[k]`, o el *staging* si es 0) |
| `src/core/verify.cpp`, `include/strata/core/verify.hpp` | El plan gana un campo `admit2` **al final**, así que ningún campo anterior se mueve. Con la variable, la copia usa los kernels nuevos. `set_fetch_admit()` se niega con el plan en dispositivo, el grafo zero-doorbell o un `--pcie-mode` que no sea el kernel de copia |
| `src/core/expert_source.cpp`, `include/strata/core/expert_source.hpp` | Al publicar el plan, elige las víctimas (los menos usados de la capa no enrutados), escribe `admit2` y actualiza la tabla de residencia |
| `src/program/generate.cpp` | La variable y sus condiciones. La línea del acierto añade "K kept from the PCIe share" |

**Se apaga solo, y lo dice en el log,** con:

- layer split o una GPU *peer*;
- el modo RAM residente (al expulsar hay que devolver a RAM);
- `STRATA_VERIFY_DEVICE_PLAN`;
- el grafo zero-doorbell;
- `--pcie-mode dma` o `direct`;
- `STRATA_ADAPT_NOWAIT`;
- o sin `adapt`.

En vuestra configuración (`auto` = kernel de copia, una GPU, arena completa) debería activarse. Comprobadlo en el log:
`STRATA_FETCH_ADMIT: the PCIe share of the misses stays in the VRAM cache`.

**Comprobado aquí:**

- `generate.cpp` y `expert_source.cpp` pasan `-fsyntax-only -Wall -Wextra` sin errores.
- `verify.cpp` da los mismos 18 errores con y sin el cambio: son de mi cabecera CUDA de relleno, no del código.
- Los dos parches se siguen aplicando.

**No he podido compilar el `.cu`** (sin `nvcc`) **ni ejecutar nada.**

**Un coste mínimo con la variable apagada:** el plan que se copia por capa crece en 2 × cap enteros (~0,5 KB). Con un
plan de varios KB por capa no debería notarse, pero entra en el A/B.

## 3. Cómo probarlo

1. **Compilar** con `-DCMAKE_CUDA_ARCHITECTURES=86` y pasar `ctest -R "parity"`. No hay arnés nuevo: los kernels nuevos
   son los de siempre con un destino distinto.
2. **Primero la corrección, una sola respuesta:**
   - con `STRATA_FETCH_ADMIT=1`, que el log diga que está activo y que "kept from the PCIe share" sea > 0;
   - y que la respuesta sea coherente.

   Si algo saliera mal, **lo esperable es texto basura**: un experto calculado con los bytes de otro. Si lo veis,
   paradlo y pasadme el log.
3. **Calidad:** `logpos-compare` con y sin la variable. Debe quedar dentro del suelo de ruido, como `adapt`.
4. **Velocidad y acierto,** alternando:
   - B1, B2 y B4 con y sin la variable, y las líneas de acierto y swaps;
   - el simulador predice ~+7 puntos con las flags de hoy.
   - Si gana, probad también lo que propusisteis: `--adapt-every 2 --adapt-swaps 32` y `16`. El simulador dice que da lo
     mismo o más con la mitad de copias.

## 4. El tope de pensamiento

Adrián prefiere un tope más alto que 2048. Lo que medisteis:

- **Funciona:** 1024, 1536, 2048 y 3072.
- **Falla:** 4096 y sin tope.

Pero solo con **una** tarea y `max_tokens=8192`. A 4096 falló porque, tras pensar, **no quedaba sitio para actuar**
dentro de los 8192. Eso puede depender del `max_tokens` del cliente.

Propuesta:

1. **Probad 3072 y 4096 con `max_tokens` de 16384**, además de 8192, en **3-4 tareas distintas**: escribir un fichero,
   editar uno existente, ejecutar tests y arreglar un fallo, y una tarea larga de varias herramientas. 3 repeticiones
   cada una.
2. **Mirad qué `max_tokens` mandan de verdad** Claude Code, opencode y pi en vuestros logs.
3. **Regla:** el tope más alto que actúe **siempre**, en todas las tareas, con el `max_tokens` real de los clientes. Si
   4096 no lo consigue, 3072. Si 3072 falla en alguna, 2048.

**El diseño:** `reasoning_budget_tokens` por defecto en la config del servidor para las peticiones de agentes, con la
reserva de acción que ya sale sola (`max_tokens` − tope). La ampliación por progreso, después, si hace falta.
