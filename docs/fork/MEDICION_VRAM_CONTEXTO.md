# VRAM al arrancar y prompt más largo real (decisión de `--max-context`)

Fecha: 2026-10-04. **[medido]** en la 3060, `swift-iq2_xs` a 512K, KV `int8`, `--kv-resident 32768`,
caché de expertos `auto`.

## Reparto con todo cargado (del log de arranque)

| Consumo | VRAM |
| --- | ---: |
| Caché de expertos (3.696 slots) | 4,97 GiB |
| Capa del borrador (MTP) | 836 MiB |
| Ventana de verificación (hasta 6 tokens) | 59,4 MiB |
| Cabeza del borrador (40.525 tokens) | 52,6 MiB |
| **Libre con todo cargado** | **627 MiB** |

**El contexto de 512K casi no ocupa VRAM.** El KV va **streamed**: solo **32.768 celdas** por capa QSA en
VRAM; el resto vive en **6,19 GiB de RAM fijada**. Y el streaming funciona: **99,85 % de las lecturas de
bloque aciertan en VRAM** (2,4-13,3 MiB leídos de RAM). O sea, el contexto grande cuesta **RAM, no VRAM**, y
no frena el decode.

## Prompt más largo real (hasta ahora)

De las conversaciones aparcadas en la caché: la mayor fue **86.631 tokens**; varias de ~33.900. Bastante por
debajo de los 512K.

## Conclusión

**No hay que tocar `--max-context`.** Reducirlo:
- **no libera VRAM** (las 32.768 celdas residentes las fija `--kv-resident`, no el contexto; y la caché de
  expertos ya se lleva 4,97 GiB de los 5,71 libres);
- solo liberaría **RAM** (los 6,19 GiB de KV fijada bajarían a ~1,5 GiB con 128K), y de RAM vamos sobrados
  (62 GB, y el arena de expertos son 33 GiB);
- y nos quitaría la capacidad de 512K.

La VRAM libre (627 MiB) es ajustada, pero el margen lo da el **KV streaming** (las celdas van a RAM), que es
justo el mecanismo que el motor ya usa.

**Recomendación:** dejar 512K. Si algún día se quiere más VRAM para la caché de expertos (más aciertos →
decode más rápido), la palanca sería **subir `--kv-resident`** con un contexto menor, no bajar el contexto a
secas. No parece necesario hoy: el acierto de la caché ya va al 62-78 %.
