# Ronda 13: que Strata lea las imágenes (sin depender de nex-mini)

Para: el agente del servidor. Fecha: 2026-10-04.

**Decisión de Adrián:** las imágenes las lee el propio Strata, con el codificador del modelo
(`mmproj-Qwen3.8-Flash-Next-BF16.gguf`, el mismo que ya usa `strata-iq2_xs.json`), y no nex-mini en :8080.

Hay tres formas. Van por fases, de menos a más trabajo:

| Forma | Tiempo por imagen | Coste para el texto | Estado |
| --- | --- | --- | --- |
| **A. Codificador en la CPU** | 10-30 s (`docs/DETAILS.md`, hasta ~300 tokens de imagen) | **ninguno en la GPU** | se puede hoy, con el cambio de §1 |
| B. Codificador en la GPU, siempre | 0,1-0,5 s | ~1,4 GB de VRAM reservados → ~1.000 huecos de expertos menos de 3.696. En una tarjeta de 16 GB fue −4 % de decode; en la 3060 probablemente más [est.] | no recomendado |
| **C. `--vision-on-demand` del fork** | rápido (GPU) | presta VRAM de la caché de expertos **solo mientras hay una imagen** | por portar (§3) |

## 1. Fase A, hoy: codificador en la CPU y carga perezosa a la vez

Hasta ahora `server.py` rechazaba `--lazy` con cualquier visión ("lazy loading is text-only"). Vuestro
`serve-strata.sh` usa `--lazy` para compartir la GPU con :8080.

**Cambio en esta rama** (commit de esta nota, `serve/server.py`):

- `--lazy` solo se rechaza si el codificador va en la **GPU**, que cogería su VRAM al arrancar.
- Con `"gpu": false`, el codificador solo usa RAM, arranca con el servidor, y el motor sigue cargando en la primera
  petición.
- Tests: `serve/test_server.py` pasa (119, uno nuevo: `LazyVision`).

Pasos en vuestro motor (`~/Strata`):

1. **Traer el cambio:** `git fetch fork3060 claude/strata-rtx3060-optimization-zfgxq8`, y
   `git cherry-pick <este commit> -- serve/server.py serve/test_server.py docs/DETAILS.md`. Mejor por fichero, con
   `git checkout fork3060/... -- <fichero>` si choca con lo vuestro.
2. **En `strata-swift-iq2_xs.json`**, copiad la sección `"vision"` de `strata-iq2_xs.json` con:
   - `"gpu": false`;
   - `"max_tokens": 300`, que es lo que `setup.py` pone para la CPU (`VISION["cpu"]`).

   Copiad también de sus `"args"` el `--vision` del motor, y mantened `--vram-reserve-mib 700`.
   - Comprobad que `engine/strata-vision` existe (lo usa la config de `iq2_xs`).
   - Si `strata-vision` se compiló solo para GPU, `setup.py` lo recompila en modo CPU con `--vision cpu`.
3. **En `serve-strata.sh`:** hoy, con visión, arranca sin `--lazy` y llama a `free-vram.sh`. Cambiadlo para que solo
   haga eso si la visión va en la GPU (`d['vision'].get('gpu')`). Con `"gpu": false`, el camino perezoso de siempre.
4. **litellm:** que `ada-next` (strata:8081) acepte imágenes.
   - Mirad si la entrada del modelo necesita `supports_vision: true` en `beellama/litellm/config.yaml`.
   - Probad una imagen por :4000, no solo directa a :8081.
   - **No quitéis todavía `ada-praxis` → nex-mini:** se retira cuando §2 dé bien.

## 2. Comprobar que funciona y lo que cuesta

1. **Que lee de verdad:** las pruebas de `docs/DETAILS.md` ("MEN WALK ON MOON") y una captura de pantalla real de
   lo que suelen mandar vuestros agentes: código, un error en una terminal. Comparad la respuesta con la de nex-mini.
2. **Tiempo por imagen** en la CPU, y que la segunda vez que llega la misma imagen no se recodifica (la caché por hash
   del servidor).
3. **El texto no se resiente:** B1 y B4 con la visión en la config y sin ella, alternando. Debe salir igual: el
   codificador en la CPU no toca la VRAM.
4. **RAM:** el codificador son ~0,9 GB más en RAM. Vais sobrados.

## 3. Fase C: portar `--vision-on-demand` del fork `architectds`

Si 10-30 s por imagen se hace lento en el uso real, el siguiente paso es la versión del fork: el codificador en la
GPU, cogiendo VRAM de la caché de expertos solo mientras hay una imagen. Como en `PORT_FORK.md`:

1. Buscad los commits: `git log --oneline adesign/best -S"vision-on-demand"` y `-S"vision_on_demand"`.
2. **Antes de portar, decidme:**
   - qué ficheros toca;
   - si está entrelazado con el multi-GPU, como pasó con MTP chain;
   - cómo devuelve la VRAM: si rellena la caché después y cuánto tarda.
3. Si es limpio:
   - **Rama `port/vision-on-demand`.**
   - **Medid** el tiempo por imagen, B1 y B4 sin imágenes (debe ser igual que hoy), y B1 justo después de una imagen
     (el coste de rellenar la caché).
   - **Calidad:** el modelo de texto no cambia, así que basta con que las respuestas a las imágenes de §2 salgan
     iguales o mejores que con la CPU. Con la GPU el codificador usa hasta 1.024 tokens de imagen, frente a 300.

**El orden:** primero A (hoy, sin riesgo), y con A funcionando, C si hace falta. Así la dependencia de nex-mini se
quita en cuanto A pase §2, sin esperar al port.
