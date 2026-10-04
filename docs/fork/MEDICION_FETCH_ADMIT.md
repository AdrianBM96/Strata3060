# `fetch-admit` probado con nuestra traza: GANA, y no cuesta PCIe

Para: Claude. Fecha: 2026-10-04. Respuesta a `RESPUESTA_CACHE_EXPERTOS.md`.

## 1. El desajuste del acierto: tu explicación encaja (casi del todo)

Tu fórmula predice **67,2 %** y el motor dio **67,5 %**. Pero la columna nueva **`eng.hit`** del simulador, con
nuestra traza larga, da **64,58 %**. Es decir: **el arreglo va en la dirección correcta** (58,97 → 64,58, se come
la mayor parte de los 8,5 puntos), pero **queda ~3 puntos** (64,58 vs 67,5).

No sé si es la parte PCIe exacta (¿77>>8 y tope 16?), el `pcie_frac` real de esa sesión (¿0,30 o algo distinto?), o
que el motor cuenta además algo del prefill. **Decídmelo tú**; con eso quedaría clavado.

## 2. La tabla (traza larga, 7.086 ventanas, `--pcie-frac 0.30`)

```
policy                                   hit/entry  hit/dist  eng.hit  swaps/w free/w    copied/w    PCIe/w
static (profile)                          36.33 %  35.92 %  41.89 %     0.00   0.00      0.0 MB   0.00 ms
adapt every=4 decay=0.7 swaps=96          58.97 %  55.09 %  64.58 %    23.79   0.00     32.9 MB   2.99 ms
adapt ... cross                           58.94 %  55.27 %  64.52 %    23.74   0.00     32.8 MB   2.98 ms
fetch-admit alone                         63.09 %  60.18 %  68.19 %     0.00  86.44      0.0 MB   0.00 ms
fetch-admit + adapt                       66.74 %  63.07 %  71.65 %    22.94  73.23     31.7 MB   2.88 ms
lru (per layer)                           72.92 %  68.60 %  77.25 %   331.75   0.00    458.6 MB  41.69 ms
belady (ceiling, per layer)               85.02 %  82.53 %  87.34 %   118.15   0.00    163.3 MB  14.85 ms
belady (ceiling, global)                  85.97 %  83.74 %  88.05 %   111.30   0.00    153.9 MB  13.99 ms
```

## 3. La respuesta a tus tres preguntas

1. **¿`eng.hit` de `adapt` ≈ 67 %?** → da **64,58 %** (≈, con los ~3 puntos de §1).
2. **¿Sube `fetch-admit + adapt`, y cuánto?** → **SÍ, y mucho**: `eng.hit` **64,58 → 71,65 %**, **+7,1 puntos**.
   (`fetch-admit alone` ya sube a 68,19 % **sin copiar nada**.)
3. **¿Suben los `swaps/w`?** → **NO, bajan**: **23,79 → 22,94** por ventana. Y los MB copiados **bajan**
   (32,9 → 31,7). **No cuesta cable: es ganancia gratis.**

Y acerca al techo: el `eng.hit` del techo es 87,34 %; `fetch-admit + adapt` llega a **71,65 %** (frente a 64,58 %),
cerrando **~3 de los ~23 puntos** que faltaban.

## 4. Mi lectura

- **La política gana con nuestro uso, y no copia más.** Cumple la restricción que tú mismo pusiste ("ganar aciertos
  sin copiar más"): de hecho copia **menos**.
- **`fetch-admit alone`** es interesante por sí sola: +3,6 puntos **sin ninguna copia** (0 swaps, 0 MB). Pero
  `+ adapt` es claramente mejor (+7,1) al mismo coste.
- Los números de `free/w` (73-86 por ventana) dicen que **hay mucho que admitir gratis** — el *staging* del PCIe ya
  trae ~30 % de los fallos.

## 5. Qué te pido

1. **Decidir §1**: los ~3 puntos que quedan del acierto (¿`pcie_frac`, la fórmula del 77>>8/16, o el prefill?).
2. **Escribir el cambio de motor de §4** (tu "copiar el blob PCIe al hueco del que sale") **con variable de A/B**,
   ya que gana con nuestra traza. Cuando esté, lo pruebo: A/B con B1/B2/B4, y las líneas de acierto y swaps del
   motor para confirmar en producción los +7 puntos y el 0 de coste.
3. Y si quieres, **el tope de pensamiento** (`PROPUESTA_ATASCO_THINKING.md`) — ya tengo el dato de que **2048 hace
   que el modelo actúe** y sin tope no; te lo dejo para que decidas el diseño.
