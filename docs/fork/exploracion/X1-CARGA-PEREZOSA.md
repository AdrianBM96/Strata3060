# X1. Carga Perezosa y Descarga del Motor en Inactividad (~11 GB)

**Autor:** explorer  
**Destino:** claude (para opencode2 / adrian)  
**Fecha:** 2026-10-05 04:45 UTC  
**Ficheros analizados:** `serve/server.py`, `serve-strata.sh`, `/home/bazzite/Strata/strata-swift-iq2_xs.json`, `src/program/generate.cpp`, `/home/bazzite/Strata/strata-swift-iq2_xs.log`.

---

## 1. Dónde se Configura y Ejecuta la Descarga (File:Line)

### A. Parámetros y timeouts de inactividad (`idle_unload`)
- **Definición de argumentos CLI:** `serve/server.py:3938-3942`.
  - Argumento: `--idle-unload SECONDS` (por defecto `None`).
  - Ayuda: *"unload the model after this many seconds without requests, so other programs can use the VRAM; the next request loads it again"*.
- **Carga de configuración:** `serve/server.py:4062`.
  - Lee `a.idle_unload` o `cfg.get("idle_unload_s")` (o `0` por defecto si no está definido).
  - En `serve/runconfig.py:32`: `("idle_unload_s", "num>=0", "Unload the model after this many seconds without requests (0 or empty: never)")`.
- **Bucle de comprobación de inactividad:** `serve/server.py:1909-1921` (`start_idle_unload`).
  - Si `self.idle_unload_s <= 0` o no está definido: el hilo **no se arranca** (nunca se descarga solo).
  - Si está activo: duerme `max(1.0, min(30.0, self.idle_unload_s / 4))` y llama a `self.unload(idle_for=self.idle_unload_s)` (`serve/server.py:1916-1918`).
  - Comprueba: `time.time() - (self.last_request_at or self.started_at) < idle_for` (`serve/server.py:1898`). Si ha habido peticiones recientes o hay una en curso (`fifo.acquire`), devuelve `"busy"` y no descarga.

### B. El método de descarga destructiva (`unload` y `close`)
- **Disparo del unload:** `serve/server.py:1885-1907` (`Service.unload`).
  - Llama a `self.engine.unload()` (`serve/server.py:1900`).
- **Implementación en el motor:** `serve/server.py:570-574` (`StrataEngine.unload`).
  - Ejecuta `self.close()` y marca `self.unloaded = True`.
- **Terminación del proceso C++ (`engine/strata`):** `serve/server.py:1226-1260` (`StrataEngine.close`).
  - Envía la línea `"QUIT\n"` por `stdin` y espera con timeout de **20 s** (`serve/server.py:1234-1237`).
  - Si no termina: envía `terminate()` (`SIGTERM`) y espera **20 s** (`serve/server.py:1239-1240`).
  - Si no termina: envía `kill()` (`SIGKILL`) y espera **20 s** (`serve/server.py:1242-1245`).
  - Si aún sigue vivo: lanza `EngineStuck` (`serve/server.py:1246`).
  - Limpia handles y establece `self.proc = None`, `self.ended = True` (`serve/server.py:1257-1258`).

### C. Por qué el servidor está descargado hoy (`--lazy` en producción)
- **Definición de `--lazy`:** `serve/server.py:3934` (`ap.add_argument("--lazy", action="store_true")`).
- **Efecto de `--lazy`:** En `serve/server.py:440-441`:
  ```python
  if lazy:
      return
  ```
  `StrataEngine.__init__` sale inmediatamente sin lanzar el binario `engine/strata`. El proceso no existe (`self.proc = None`), pero la API HTTP escucha en el puerto 8081 reportando status `"unloaded"` (`serve/server.py:3066-3067`).
- **El gatillo en producción (`serve-strata.sh`):**
  - En `/home/bazzite/Strata/serve-strata.sh:42-63`:
    ```bash
    HAS_VISION=$(python3 -c "
    import json,sys
    try: d=json.load(open('$CFG'))
    except Exception: print('no'); raise SystemExit
    print('yes' if (d.get('vision') or {}).get('gpu') else 'no')")

    if [ "$HAS_VISION" = "yes" ]; then
      ...
    fi

    # Si vision.gpu es false (visión en CPU), cae aquí:
    IDLE=${STRATA_IDLE_UNLOAD:-0}
    exec "$F/.venv/bin/python" "$F/serve/server.py" \
      --engine strata --config "$CFG" --port 8081 \
      --lazy \
      --idle-unload "$IDLE"
    ```
  - Como la visión se pasó a CPU (`"gpu": false` en `strata-swift-iq2_xs.json:68`), `HAS_VISION` evalúa como `"no"`.
  - **Consecuencia directa:** El servidor se lanza **siempre con `--lazy`**. Cada vez que arranca o reinicia el servicio systemd (`strata.service`), el motor C++ **no está cargado**.

---

## 2. Cuánto Tarda la Carga de la Primera Petición (Medido)

Cuando llega la primera petición a `POST /v1/chat/completions`:
1. `Service.load()` (`serve/server.py:1864`) detecta que no está cargado e invoca `self.ensure_loaded()` (`serve/server.py:1784`).
2. Se ejecuta el comando `before_load` (`/home/bazzite/Strata/free-vram.sh`, `serve/server.py:1790-1795`) para limpiar VRAM.
3. Se invoca `self.engine.restart()` (`serve/server.py:1816`) que reejecuta `self.__init__(*self.spawn)`, lanzando `engine/strata --serve ...`.
4. El proceso C++ ejecuta toda la inicialización y bloquea hasta emitir `READY <context> stop` (`src/program/generate.cpp:5779`).

### Desglose cronológico medido en `strata-swift-iq2_xs.log` (líneas 31941–31975):

| Fase | Qué hace | Datos / Ancho de banda medido | Tiempo medido |
| :--- | :--- | :--- | ---: |
| **Hook `before_load`** | `free-vram.sh` comprueba y libera VRAM | Mínimo libre exigido: 10.500 MiB | **~0,2 s** |
| **PCIe Bandwidth Probe** | 4 transferencias de 128 MB para fijar `pcie_frac=0.30` | 11,0 GB/s medidos en la RTX 3060 | **~0,5 s** |
| **Pesos Base y Proyecciones** | Carga 300 matrices de proyección nativas + token emb | 1.475 MiB de pesos leídos de disco | **~0,45 s** |
| **KV Streaming Pinned RAM** | Reserva y pinnea 32K celdas de contexto en RAM | 6,19 GiB de pinned RAM | **~0,2 s** |
| **Capa Borrador MTP (GPU)** | Carga 512 expertos residentes y capa densa en VRAM | 836 MiB de VRAM a 1.329 MiB/s | **0,59 s** |
| **Expert Arena (Carga Host)** | Lectura masiva de expertos de disco a memoria host | **33,02 GiB leídos a 3,32 GiB/s** | **9,95 s** |
| **Pinning de RAM (`cudaHostRegister`)** | Registro PORTABLE de la arena (sin HugePages) | 33,02 GiB registrados (`MADV_HUGEPAGE`) | **~1,0 s** |
| **Pre-llenado Caché VRAM** | Envío de 3.867 slots de expertos calientes a VRAM | 5,21 GiB transferidos por PCIe a 11 GB/s | **~0,47 s** |
| **Compilación grafos / Warmup** | Preparación de buffers de verify (59,4 MiB) y HC | Emisión de `READY 524288 stop` | **~0,2 s** |
| **TOTAL COLD START** | **Tiempo total de bloqueo de la primera petición** | **`load_s` registrado en `server.py`** | **~11 a 13 s** |

*(Nota de concordancia: coincide exactamente con los **11 s** medidos por opencode2 en C19: `HUMO 2x (11s)`, `CHANGELOG.md:27`).*

---

## 3. ¿Se Puede Mantener Residente?

**SÍ, de forma inmediata y sin tocar C++.**

### Acción en `serve-strata.sh`:
- Actualmente, `serve-strata.sh:42-63` impone `--lazy` forzoso porque asume que solo los modelos con visión en GPU necesitan arrancar en caliente.
- Si se modifica `serve-strata.sh` para **no pasar `--lazy` por defecto** (o pasarlo únicamente si existe una variable explícita `STRATA_LAZY=1`):
  ```bash
  # En serve-strata.sh:
  LAZY_FLAG=""
  if [ "${STRATA_LAZY:-0}" = "1" ]; then
    LAZY_FLAG="--lazy"
  fi
  exec "$F/.venv/bin/python" "$F/serve/server.py" \
    --engine strata --config "$CFG" --port 8081 \
    $LAZY_FLAG \
    --idle-unload "${STRATA_IDLE_UNLOAD:-0}"
  ```
- **Resultado:**
  - El servicio de systemd (`strata.service`) carga el motor C++ en segundo plano al arrancar el equipo.
  - Los 11–13 s de carga se pagan **durante el arranque del sistema operativo**, no en el turno del usuario o del agente.
  - La primera petición entra con el motor ya en `READY`: **latencia de carga = 0 ms**.

### Convivencia en VRAM con otros procesos:
- `engine/strata` en reposo ocupa **11.098 MiB** de VRAM (`ENTREGAS.md:162`).
- `beellama` en `:8082` (micro-LLM) ocupa **246 MiB**.
- Total: 11.344 MiB usados en la RTX 3060 de 12.288 MiB (quedan ~944 MiB libres).
- Al tener `STRATA_IDLE_UNLOAD=0` (defecto), el motor no se descarga nunca mientras no haya colisión de VRAM.

---

## 4. ¿Se Puede Precargar de Forma Barata o Descargar Parcialmente?

Si se desea conservar la posibilidad de compartir la GPU con juegos u otros procesos pesados sin sufrir los 11–13 s de carga en cada sesión, existen tres soluciones técnicas:

### Solución 1. Precarga Asíncrona vía HTTP Ping (`POST /load` o `POST /v1/load`)
- **Implementación existente:** `serve/server.py:3106-3108` y `3129-3136`.
  - El servidor web responde inmediatamente al endpoint de control:
    ```bash
    curl -s -X POST http://127.0.0.1:8081/v1/load
    ```
- **Aplicación para agentes:**
  - El script que arranca un entorno de trabajo o sesión de agente (`START-HERE.sh`, `h agent start`, etc.) puede lanzar un `curl -s -X POST http://127.0.0.1:8081/load >/dev/null &` en segundo plano nada más abrirse.
  - Para cuando el usuario o el agente termina de formular su prompt, los 11 segundos de carga ya han transcurrido en paralelo.

### Solución 2. Elasticidad de VRAM sin matar el proceso (`--vram-elastic` / `POST /v1/vram`)
- **El gran problema del `unload` actual:**
  - `server.py:1900` llama a `close()`, que **mata el proceso entero**.
  - Al matar el proceso se destruyen:
    1. Los 33,02 GiB del arena de expertos ya leídos en memoria RAM.
    2. Las páginas registradas con `cudaHostRegister`.
    3. Las tablas de YaRN, KV streaming y grafos CUDA capturados.
  - La siguiente petición tiene que volver a leer 33 GB de NVMe a 3,3 GB/s (10 s).
- **La alternativa elástica nativa (`src/program/generate.cpp:620-622`, `serve/server.py:1826-1854`):**
  - Si el motor se arranca con `--vram-elastic`:
    - El servidor expone `POST /v1/vram` con parámetro `reserve_mib`.
    - Para liberar la GPU: se pide reservar p. ej. 6.000 MiB para otra aplicación.
    - El motor C++ **NO muere**: únicamente libera los bloques de VRAM de la caché de expertos mediante `cudaFree` por segmentos.
    - **Los 33 GiB de pesos siguen residentes en la RAM del sistema (DDR4) y registrados.**
  - **Tiempo de recuperación:**
    - Cuando se vuelve a necesitar la GPU (`POST /v1/vram` con reserva normal):
    - Repoblar los 5,21 GiB de la caché de expertos desde la RAM del host a través de PCIe Gen4 x16 (11,0 GB/s) tarda:
      $$\frac{5,21\text{ GiB}}{11,0\text{ GB/s}} \approx \mathbf{0,47\text{ segundos}}$$
    - Se pasa de un cold-start de **11–13 s** a un warm-start de **<0,5 s**.

### Solución 3. Mantener el Arena en Cache de Páginas del SO (`vmtouch`)
- En Linux, con los 62 GB de RAM física del equipo de bazzite:
  - 33 GiB de arena caben holgadamente en la caché de disco del kernel (`page cache`).
  - Incluso si el proceso `engine/strata` se cierra y se reabre, si las páginas siguen en la cache de RAM de Linux, la tasa de lectura de `arena_src.open` no está limitada por el bus PCIe del SSD NVMe (3,3 GB/s), sino por copia en memoria RAM, reduciendo el tiempo de relectura de 10 s a ~3 s.

---

## 5. Resumen de Respuestas Concretas a la Tarea X5/X1

1. **¿Cuánto tarda la carga (~11 GB)?**  
   Exactamente **11 a 13 segundos** medidos (9,95 s de lectura de 33,02 GiB de NVMe a 3,32 GB/s + 1,0 s de `cudaHostRegister` + 0,59 s MTP + 0,47 s poblado de caché GPU a 11 GB/s).
2. **¿File:line del unload y su timeout?**  
   - Bucle y comprobación de inactividad: `serve/server.py:1909-1921` (`start_idle_unload`).
   - Timeout de inactividad: configurable vía `--idle-unload SECONDS` (`serve/server.py:3938-3942`, `4062`) o `idle_unload_s` en JSON. Defecto: `0` (desactivado).
   - Ejecución del cierre y timeouts de terminación: `serve/server.py:1226-1260` (`StrataEngine.close`). Secuencia: `QUIT` (20 s) $\to$ `SIGTERM` (20 s) $\to$ `SIGKILL` (20 s).
3. **¿Por qué ocurre en producción hoy?**  
   Por el flag `--lazy` incondicional en `serve-strata.sh:61`, provocado porque la visión está en CPU (`HAS_VISION="no"`).
4. **¿Se puede mantener residente?**  
   **Sí:** eliminando `--lazy` de `serve-strata.sh:61`. El motor arrancará con systemd y la primera petición responderá de inmediato.
5. **¿Se puede precargar de forma barata?**  
   - **Barata inmediata:** `curl -s -X POST http://127.0.0.1:8081/v1/load` al arrancar sesión.
   - **Estructuralmente óptima:** Activar `--vram-elastic` (`POST /v1/vram`) para soltar VRAM sin matar el proceso; la recarga pasa de **11 s a 0,47 s**.
