# Arquitectura Completa de la Oficina Agéntica: Web, Servidor e Integración con Herdr

Fecha: 2026-10-05, revisada el 2026-10-07 (T21, deriva documental: recuentos, servicio, estado, eventos, mallas). Autor: explorer. Para: Claude (arquitecto), Adrián y equipo.  
Objetivo: Documentar al 100% la oficina agéntica: código fuente, servicio en ejecución, servidor backend, interfaz web con diorama 3D y puente de comunicación con Herdr.  
Regla de este documento: cada número va con lo que se midió y dónde. Lo que no tiene medición se dice como elección de la oficina o queda fuera; no hay afirmación sin número.

---

## 1. Mapa de Ubicación y Estado de Ejecución

### A. Ubicación del Código Fuente
El código oficial de la oficina agéntica de Strata reside en el repositorio git del proyecto:
- **Directorio base**: `docs/fork/oficina/` (rama `claude/strata-rtx3060-optimization-zfgxq8`).
- **Ficheros clave** (recuentos medidos con `wc -l` y `ls -la` el 2026-10-07, en el árbol de la rama):
  1. `docs/fork/oficina/server.py` (1283 líneas): Servidor HTTP/1.1 con SSE, JSON API, autenticación scrypt, cookies de sesión deslizantes, rate limiting, gestión de workspaces, canal de eventos por socket Unix, avisos `notification show`, locks y escritura atómica, y comunicación CLI con Herdr.
  2. `docs/fork/oficina/index.html` (1288 líneas): SPA web completa con layout 16:9, tres columnas (Oficinas / Diorama 3D + Chat / Inspector), visualización 3D interactiva en Three.js con render bajo demanda, telemetría de Strata, pizarra de tareas reales y selector multioficina/multimáquina. El consumo de dGPU en reposo está medido (§4A).
  3. `docs/fork/oficina/login.html` (52 líneas): Interfaz de acceso protegida por contraseña con selector automático de tema oscuro/claro y control de reintentos.
  4. `docs/fork/oficina/vendor/three.module.min.js` (670.681 bytes, `ls -la`): Three.js vendorizado e integrado localmente (cero dependencias de CDNs externas, funciona 100 % offline).
  5. `docs/fork/oficina/strata-oficina.service` (31 líneas): Unidad de servicio systemd de usuario, con el mismo `ExecStart` y `WorkingDirectory` que la unidad instalada (§1B).
  6. `docs/fork/oficina/tests/`: suite pytest de **173 tests** (`test_herdr_stub.py` 134 + `test_events.py` 39). Recuentos `wc -l`: `README.md` 203, `conftest.py` 504, `stub_herdr.py` 522, `test_events.py` 1152, `test_herdr_stub.py` 1836; `fixtures/` trae 21 ficheros de captura y el directorio `fork/` con 4.
  7. Capturas de referencia visual: `v4-*.png`, `ref.jpg`, `ref-v4.jpg`.

### B. Servicio Activo en Ejecución
- **Servicio gestor**: `strata-oficina.service` (systemd de usuario). Medido el 2026-10-07 con `systemctl --user show strata-oficina.service -p ExecStart -p FragmentPath -p DropInPaths -p MainPID -p ActiveState -p SubState`:
  - `FragmentPath=/home/bazzite/.config/systemd/user/strata-oficina.service`.
  - `DropInPaths=/home/bazzite/.config/systemd/user/strata-oficina.service.d/herdr.conf` (128 bytes, del 2026-10-05).
  - `ExecStart=/usr/bin/python3 /tmp/opencode/Strata3060/docs/fork/oficina/server.py 8095`, `MainPID=1645893`, `ActiveState=active`, `SubState=running`.
  - La unidad del repo (`docs/fork/oficina/strata-oficina.service`, 31 líneas) tiene el mismo `ExecStart` y el mismo `WorkingDirectory` (`/tmp/opencode/Strata3060/docs/fork/oficina`).
- **Entorno efectivo**: `PATH=/home/bazzite/.local/bin:/usr/local/bin:/usr/bin:/bin` y `HERDR_SOCKET_PATH=/home/bazzite/.config/herdr/herdr.sock` (medido con `-p Environment`). Las dos las pone el drop-in. **`HERDR_SOCKET_PATH` es obligatoria para el canal de eventos** (§3F): el hilo de eventos conecta a esa ruta (`ev_path`, `server.py:433`); sin socket en la ruta el canal degrada a `polling` y la oficina sigue sirviendo.
- **Proceso del sistema**: `/usr/bin/python3 /tmp/opencode/Strata3060/docs/fork/oficina/server.py 8095` (PID `1645893`).
- **Autenticación**: hash scrypt en `~/.config/strata-oficina/password.scrypt` (0600) y cookie `sid`. **No hay modelo de token y no existe ninguna variable `OFFICE_PASSWORD`**: `server.py` nunca lee un token (las únicas dos ocurrencias de `token` en el fichero son `secrets.token_hex(6)` para ids de job y `secrets.token_urlsafe(32)` para la cookie). En `~/.config/strata-oficina/` quedan `password.scrypt` y un `token` suelto (`ls -1`), que el servidor no lee: es residuo de la versión v4. La unidad instalada sigue diciendo `envios con token` en su `Description` y conserva el comentario del token; **ese fichero no es del repo y no se toca desde aquí**. La unidad del repo ya no menciona el token: su `Description` dice `envios con sesion`.
- **Puertos e interfaces de escucha**:
  - `127.0.0.1:8095` (localhost).
  - `100.79.41.59:8095` (IP Tailscale de bazzite).
  - MagicDNS Tailscale: `http://bazzite.tailfe8578.ts.net:8095`.
  - **Seguridad de red**: NO escucha en `0.0.0.0`; está estrictamente acotado a localhost y a la tailnet privada autenticada.

### C. Relación con Otros Componentes del Sistema
- **Plataforma `@ada/agents-core` y `@ada/web`** (`/home/bazzite/ada-cli/`): Monorepo TypeScript con backend Fastify (`packages/agents-core/run-local.mjs` en puertos 3001/3002) y cliente React/Next.js (`apps/web`). Proporciona el runtime supervisor y esquemas para la plataforma general de agentes.
- **Herdr Snap Bridge** (`/home/bazzite/herdr-snap/`):
  - `bridge.py`: Proxy de socket Unix (`~/herdr-snap/herdr.sock` → `~/.config/herdr/herdr.sock`) que permite a clientes confinados por Snap (`agy`) comunicarse con el demonio Herdr del host.
  - `h`: Script wrapper para invocar la CLI de Herdr apuntando al socket correcto.
- **Entorno ZCode** (`/tmp/opencode/ZCode`): Servidor HTTP y Vite dev server (`:5173`) para herramientas auxiliares.

---

## 2. Arquitectura del Servidor Backend (`server.py`)

### A. Motor HTTP y Filosofía de Diseño
El backend está implementado en Python 3 puro utilizando la librería estándar `http.server.ThreadingHTTPServer`. 
- **Cero dependencias externas**: No requiere frameworks pesados, arranca de forma instantánea y no consume memoria innecesaria.
- **Hilos independientes**: Despacha peticiones concurrentes y mantiene conexiones de larga duración (SSE) sin bloquear las llamadas a la API. Además hay **dos hilos de fondo**, arrancados en `__main__` (`ev_start`, `server.py:654`): `oficina-events` (la suscripción al socket, `ev_client`, `server.py:575`) y `oficina-notif` (el worker que lanza `herdr notification show`, `notif_client`, `server.py:777`). El hilo de eventos **nunca lanza un subproceso**: solo habla por el socket.
- **Consumo en reposo, medido** (instancia scratch en puerto 8099 con Herdr real, 2026-10-06): **0,033 % de un núcleo** — 3 ticks de CPU en 90,254 s; `ps -o pcpu` = 0,0 en 9 muestras. En reposo el servidor lanza **3 subprocesos `api snapshot` cada 90 s** (gaps 30,0055 y 30,0053 s); con el sondeo de 5 s de la versión anterior habrían sido **18**.

### B. Autenticación, Sesiones y Seguridad
1. **Contraseña Scrypt**:
   - Hash almacenado en `~/.config/strata-oficina/password.scrypt` (permisos 0600, formato `scrypt$16384$8$1$salt$hash`). La ruta se puede cambiar con la variable `STRATA_OFICINA_PW` (`server.py:15`); es lo que usan los tests.
   - Verificación con `hashlib.scrypt` y `hmac.compare_digest` para evitar ataques de temporización (*timing attacks*).
   - **No hay token de acceso ni variable `OFFICE_PASSWORD`**: `server.py` no lee ningún token. Un fichero `~/.config/strata-oficina/token` sigue en disco como residuo de v4 y el servidor no lo abre.
2. **Control de Fuerza Bruta**:
   - Límite de 5 intentos fallidos por IP en una ventana de 15 minutos (`FAILS[ip]`). Superado el umbral, devuelve `HTTP 429 Too Many Requests`.
3. **Cookies de Sesión Persistentes**:
   - Cookie `sid` generada con 32 bytes criptográficamente seguros (`secrets.token_urlsafe(32)`).
   - Atributos: `HttpOnly`, `SameSite=Strict`, `Path=/`, `Max-Age=2592000` (30 días de duración deslizante).
   - Persistencia en disco en `~/.cache/strata-oficina/sesiones.json` (permisos 0600).
4. **Protección Fail-Closed**:
   - Todas las rutas `/api/*` y la raíz `/` exigen sesión válida salvo `/login`.
   - Límite estricto de tamaño en el cuerpo de las peticiones POST (8192 bytes).
   - Rutas de trabajo (`cwd`) estrictamente restringidas al home del usuario y sin carpetas ocultas.

### C. Catálogo de Rutas y API

| Método | Ruta | Autenticación | Descripción |
| :--- | :--- | :---: | :--- |
| `GET` | `/` | Sesión | Si no hay sesión, 302 a `/login`. Con sesión, entrega `index.html`. |
| `GET` | `/login` | Libre | Entrega `login.html`. |
| `GET` | `/vendor/three.module.min.js` | Libre | Entrega el paquete vendorizado de Three.js. |
| `GET` | `/favicon.ico` | Libre | Responde `204` sin cuerpo. |
| `GET` | `/api/state[?ws=<label>]` | Sesión | Estado consolidado de la oficina: agentes, status, métricas, tareas, ticker, `columnas`, locks. Añade la clave `events` con la salud del canal. `ws` vacío es la oficina de Strata; label malformado → 400, máquina desconocida → 404, **nunca** un fallback silencioso al estado local. |
| `GET` | `/api/kinds[?m=<maquina>]` | Sesión | Tipos de agentes disponibles y modelos instalados (local o remoto vía SSH). |
| `GET` | `/api/log?agent=<nombre>` | Sesión | Últimas 30 líneas de terminal del agente: Herdr devuelve 45 líneas de texto plano y el servidor se queda con las 30 últimas. |
| `GET` | `/api/hilo?agent=<nombre>` | Sesión | Historial de órdenes **entregadas** (`envios.log` con `confirmed:true`) y menciones en CHANGELOG. Declara `audit`, `maquina` y `scope`: el historial vive solo en bazzite. |
| `GET` | `/api/offices` | Sesión | Listado de todas las oficinas (workspaces de Herdr) locales y remotas, con la clave `events`. |
| `GET` | `/api/office/job?id=<jid>` | Sesión | Estado de progreso de la creación asíncrona de una oficina (copia estable del job, `JLOCK`). |
| `GET` | `/api/events[?ws=<label>]` | Sesión | Canal Server-Sent Events (SSE) **confinado a la oficina pedida** (§3G): el stream lleva solo los cambios de esa `ws`. Label malformado → 400, desconocido → 404. |
| `POST` | `/api/login` | Libre | Valida la contraseña y establece la cookie `sid`. |
| `POST` | `/api/send` | Sesión | Envía un mensaje directo a un agente o a Claude vía Herdr. |
| `POST` | `/api/office` | Sesión | Crea un nuevo workspace en Herdr, particiona paneles y arranca agentes. |
| `POST` | `/api/office/delete` | Sesión | Cierra y destruye un workspace de Herdr (protege la oficina Strata3060). |

### D. Agregación Reactiva de Estado (`build_state`)
El servidor construye el estado a partir de **una sola llamada** y lo publica entero. Ya no lee `herdr agent list`:

- **Fuente**: `herdr api snapshot` por máquina y por ciclo. Verificado en el binario herdr 0.9.3 (2026-10-06): rc=0, **6,6 KB**, una respuesta con `agents`, `workspaces`, `tabs`, `panes`, `layouts` y el foco. La captura verbatim es `tests/fixtures/api_snapshot.json` (**6.640 bytes**, `wc -c`). `api_offices` lee `workspaces` del mismo snapshot.
- **Campos por agente**: `name`, `agent_status`, `pane_id`, `workspace_id`, `focused`, `interactive_ready`, `completion_seq`, `state_change_seq` y `agent` (`CAMPOS_H`, `server.py:197`). Los opcionales se leen con `.get()` → `None` si Herdr no los trae. El agente sin `name` **se omite**; ya no se convierte en `"?"`.
- **Dominio de estado**: `idle | working | blocked | done | unknown` (verificado en el binario). Lo que no está en el dominio es `unknown`, **nunca** `idle` (`estado_real`, `server.py:202`). El `agent_status` de Herdr sobrevive hasta el navegador.
- **Ciclo**: con el canal en `live` el ciclo es `RECONCILE = 30 s` (elección de la oficina, documentado en `server.py:404-411`); el camino rápido es el evento. **Medido en reposo con Herdr real** (scratch, puerto 8099, 2026-10-06): **3 subprocesos `api snapshot` cada 90 s**, gaps 30,0055 y 30,0053 s; el sondeo de 5 s anterior habría dado **18**. En una sesión completa: 37 llamadas, **todas `api snapshot`**, 0 `agent list`, 0 `workspace list`, 0 `machine list`.
- **Llamadas por ciclo, medido con el stub**: `/api/offices` en caliente **7 → 3**; `/api/state?ws=w1` **1 → 0** (lee el snapshot cacheado); `/api/offices` en frío **8 → 4**; carga completa de página **9 → 4**.
- **Latencia de push, medida en vivo**: del evento a la reconstrucción **41–65 ms**; visible al cliente **43–67 ms**.
- **Actividad contextual**: la línea `ESTADO:` de `CHANGELOG.md`, `ENVIADOS.md`, `METRICAS.json` (B1 decode en tok/s, P3 prefill en s, acierto de caché, historial) y `TAREAS.json`, que ahora viaja también con `columnas` (`EN COLA`, `EN CURSO`, `ESPERA OK`, `HECHO`, `DESCARTADO`).
- **Protección de Benchmarks**: si existe `/tmp/strata-bench.lock`, el intervalo de refresco sube de 5 s a 30 s para no perturbar las mediciones de latencia del motor.
- **Suplencia**: detecta si `~/.cache/strata-watchdog/suplencia` está activo para marcar visualmente al agente suplente como activo.
- **Publicación (T7)**: `get_state` construye **una sola vez** por intervalo bajo `STLOCK`; `interval` se fija **antes** de publicar y se publica con una única asignación. El dict publicado no se muta nunca: los hilos SSE lo iteran sin riesgo. `api_office_delete` conserva `workspace list` (una llamada por acción del usuario, no por ciclo).

### E. Concurrencia y Escrituras Atómicas (T7)
- **Locks con orden de adquisición documentado** (`server.py:44-53`, `server.py:425-429`, `server.py:724-727`): `STLOCK` (rebuild: `CACHE`, `SEEN`, `SNAP`, `LAST_AGENTS`), `MLOCK` (`MCACHE`, `HOMES`, `AGCACHE`), `KLOCK` (`KCACHE`), `FLOCK` (`FAILS`), `JLOCK` (`JOBS`), `MFLOCK` (`oficinas.json`), `SELOCK` (RLock, `SESS`), `ELOCK` (`EV`) y `NLOCK` (hoja, `NOTIF`).
- **Orden**: `MLOCK` → `STLOCK` → `ELOCK` → `NLOCK`, y `KLOCK` → `MLOCK` solo en `kinds` remoto. `STLOCK` nunca toma `MLOCK` ni `KLOCK`; `ELOCK` nunca toma `STLOCK` ni `MLOCK`; `NLOCK` no toma ninguno. Sin ciclo, sin deadlock. El subproceso de aviso se lanza con `NLOCK` **suelto**.
- **Escritura atómica** (`_atomic`, `server.py:25`): temp en el mismo directorio creada con `os.open(..., 0o600)` y publicada con `os.replace`. Abrir con `"w"` trunca antes de escribir: un crash a mitad dejaba `sesiones.json` u `oficinas.json` vacío o truncado. Con `os.replace` el lector ve el fichero viejo completo o el nuevo completo, nunca un fichero a medias.
- **Riesgo conocido y medido en el código**: `STLOCK` se sostiene durante el subproceso `api snapshot` (timeout 15 s) y `MLOCK` durante `machine list --json` (10 s) y `rhome` (15 s de ssh). Con Herdr muerto los lectores esperan hasta 15 s. Es la semántica pedida (un constructor por intervalo) y es la motivación de T9.

---

## 3. Integración con Herdr (Herdr Interop)

### A. Comunicación Segura mediante Argumentos Directos (sin Shell)
La integración con Herdr se realiza invocando directamente el binario `/home/bazzite/.local/bin/herdr` mediante `subprocess.run(..., shell=False)`. No se utiliza `shell=True` en ningún punto, neutralizando vulnerabilidades de inyección de comandos.
- `herdr_out` (lectura simple) devuelve `stdout`. Para todo lo que decide, la oficina usa `herdr_cmd` (`server.py:101`), que captura **stdout, stderr y el exit status** y parsea el sobre. Herdr manda los errores como JSON a **stderr con exit 1** (`{"id":…,"error":{"code":…,"message":…}}`), así que la versión anterior, que solo miraba `stdout`, no podía verlos.
- Tabla `code → mensaje` legible (`HERDR_MSG`, `server.py:127-135`): `agent_blocked` → «el agente está esperando una aprobación en su panel: aprueba y vuelve a enviar»; `agent_not_ready` → «espera confirmación en su panel»; `agent_prompt_stalled` → «el mensaje no se entregó»; `agent_name_not_found` y `agent_not_found` → «ese agente no existe en herdr»; `timeout` → «herdr no observó el estado antes de su tiempo límite»; `usage` → «comando no válido para herdr». Un código de un Herdr futuro muestra el código y conserva el texto original.

### B. Gestión de Workspaces y Paneles
- **Oficina Strata3060**: Mapeada de forma fija al workspace principal (`w1`), donde conviven los 5 agentes nucleares (`claude`, `opencode2`, `tester`, `explorer`, `suplente`).
- **Creación de Nuevas Oficinas** (argv que lanza la oficina):
  1. `herdr workspace create --cwd <cwd> --label <nombre> --no-focus --env <env>`: Genera un workspace aislado.
  2. `herdr pane split <base_pane> --direction <right|down> --cwd <cwd> --no-focus [--env …]`: Particiona la ventana en paneles para alojar a cada agente.
  3. `herdr agent start <nombre> --kind <kind> --pane <pane_id> --timeout 60000 [-- <extra>]`: Arranca el runtime del agente en su panel.
- **Formas verificadas en el binario herdr 0.9.3 (2026-10-06, capturadas verbatim en `tests/fixtures/`)**:
  - `workspace create` → `result` = `{root_pane, tab, type:"workspace_created", workspace}`. La oficina lee `result.workspace.workspace_id` y `result.root_pane.pane_id`.
  - `pane split` → `result` = `{pane, type:"pane_info"}`. La oficina lee `result.pane.pane_id`. Un pane nuevo real trae `agent_status:"unknown"` y **sin** `name`.
  - `workspace close` → `result` = `{type:"ok"}`.
  - `agent start` (éxito, rc=0) → `result` = `{agent:{…,"agent_status":"idle","interactive_ready":true}, argv:[…], type:"agent_started"}`.
  - `agent prompt --wait` (éxito, rc=0) → `result` = `{agent:{…,"agent_status":"done","completion_seq":1091,"pane_id":"wK:p3"}, type:"agent_prompted"}`.
  - `notification show` (éxito, rc=0) → `result` = `{"reason":"shown","shown":true,"type":"notification_show"}` y stderr vacío.
  - **Regla**: el `type` y el `agent` viven **dentro de `result`**, no en la raíz del sobre. La versión anterior leía `type` de la raíz, así que un envío realmente entregado se devolvía como 502.
  - `pane read --source recent-unwrapped --format text` devuelve **texto plano**, no JSON: `api_log` lo parte por líneas y se queda con las 30 últimas.
- **Regla verificada sobre `--cwd`**: Herdr honra `--cwd` **solo si la ruta existe**. Con `pane run pwd` se observó que un `--cwd` inexistente deja el pane en `$HOME` y Herdr no crea nada; un `--cwd` existente dentro de la home se honra (raíz y split). La oficina hace `os.makedirs(cwd, exist_ok=True)` antes de crear, así que su camino normal funciona. El riesgo real queda anotado: si `makedirs` falla (permisos), los paneles caen en la home **en silencio**.

### C. Despacho de Mensajes y Norma N19
Cuando Adrián escribe un mensaje en la barra de chat de la oficina:
1. **Modo Directo (`direct`)**:
   - Ejecuta: `herdr agent prompt <agente> "adrian: <texto>" --wait --timeout <ms>`.
   - Este formato activa de inmediato la **Norma N19** en el agente receptor: las órdenes de Adrián enviadas desde la oficina tienen prioridad absoluta sobre cualquier otra tarea en curso.
2. **Modo Vía Claude (`viaclaude`)**: el mismo comando dirigido a `claude` con el prefijo `adrian (oficina): `.
3. **`--timeout` exige `--wait` (verificado)**: sin `--wait` el comando devuelve **rc=2** con `--timeout requires --wait`. `--wait` puede asentarse en `blocked` (observado), no solo en `working` o `done`.
4. **Plazo del prompt**: `ms = max(5000, min(5000 + 25·longitud + (0 si ready else 4000), 20000))`. 5000 ms y la unidad están verificadas en la captura viva; 25 ms por carácter, el margen de 4000 y el tope de 20000 son elección de la oficina, no medida. El timeout del subproceso es `ms/1000 + 10` s para que **Herdr gane la carrera** y devuelva su sobre.
5. **`ok:true` solo con entrega observada**: `agent_prompt_stalled` y `timeout` son «no se entregó»; `agent_blocked` y `agent_not_ready` son estados de panel, no stall: el agente está vivo y espera aprobación. **Sin reintento automático**: reenviar a un panel a medias es la única forma de duplicar un mensaje.
6. **Control de Tasa y Auditoría**:
   - `SEND_LOCK`: Solo se permite 1 envío cada 3,0 segundos para evitar saturar el socket de Herdr.
   - `~/.cache/strata-oficina/envios.log` registra el **resultado** (`outcome`, `confirmed`, `code`, `state`, `pane_id`, `timeout_ms`), no la intención; `/api/hilo` muestra solo las líneas `confirmed:true`.

### D. Resolución de Identidad y Compatibilidad con `ada-cli`
Como se analizó en `ADA-HERDR.md` y `ADA-HERDR2.md`, `ada-cli` (fork de `pi`) modificaba el título del proceso (`ada-cli - <carpeta>`), lo que impedía a Herdr reconocerlo como agente fuera del home.
- **Solución implementada**: La oficina genera un shim en `~/.local/share/strata-oficina/ada/pi`:
  ```sh
  #!/bin/sh
  export HERDR_AGENT=pi
  exec /home/bazzite/.nvm/versions/node/v22.23.2/bin/node /home/bazzite/ada-cli/packages/coding-agent/dist/cli.js "$@"
  ```
- Al arrancar `ada-cli`, la oficina inyecta `PATH=~/.local/share/strata-oficina/ada:...` y ejecuta `herdr agent start ... --kind pi`. Al estar `HERDR_AGENT=pi` acotado exclusivamente al proceso hijo, Herdr lo identifica de forma unívoca y sin colisiones en cualquier directorio de trabajo.

### E. Soporte Multimáquina (Gestión Remota)
La oficina detecta las máquinas conectadas en la red de Herdr mediante `herdr machine list --json`.
- Para máquinas remotas (p. ej. `mac-mini`, `macbook-air`):
  - Ejecuta comandos de Herdr con el parámetro `--machine <nombre>`.
  - Configura el entorno remoto mediante SSH no interactivo (`ssh -o BatchMode=yes -o ConnectTimeout=8`).
  - Filtra los tipos de agentes y modelos soportados en la máquina de destino antes de permitir la creación de la oficina.
- `ada-cli` remoto se **rechaza** por diseño: el shim `pi` → ada-cli vive en la home de bazzite, así que un panel remoto no lo ve (`ADA_REMOTE`, `server.py:955`; el rechazo sale en `server.py:997` y en `server.py:1089`).
- El `rhome` de una máquina se cachea con `(home, marca)`: el éxito no caduca; el fallo se re-sondea tras `RHOME_NEG = 60 s` (elección de la oficina).

### F. Canal de eventos por socket Unix (T9)
La oficina **no sondea el estado cada 5 s**: suscribe eventos y recibe push.
- **Protocolo verbatim de herdr 0.9.3** (capturado en vivo 2026-10-06): JSON **newline-delimited** en `$HERDR_SOCKET_PATH` (la unidad systemd lo fija, §1B) o `~/.config/herdr/herdr.sock`. Los pedidos llevan `id`; sin `id` el servidor de Herdr responde `invalid_request: missing field id`. `ping` → `{"id":..,"result":{"type":"pong","version":"0.9.3","protocol":22,…}}`.
- `events.subscribe` → ack `{"id":..,"result":{"type":"subscription_started"}}`. Los eventos llegan **por la misma conexión**, con forma `{"data":{…},"event":"…"}`. La suscripción necesita una conexión larga: en el probe vivo, la segunda escritura sobre una misma conexión dio `BrokenPipe`.
- **15 suscripciones globales** (solo `type`): `pane.agent_detected`, `pane.created`, `pane.closed`, `pane.exited`, `pane.updated`, `pane.focused`, `pane.moved` y los ocho `workspace.*`. `pane.agent_status_changed` requiere `pane_id` y se suscribe por cada `pane_id` conocido, con re-suscripción idempotente por comparación de listas ordenadas. `pane.scroll_changed` y `pane.output_matched` **nunca se envían**: una entrada malformada rechaza el set entero de la suscripción (observado dos veces), y `OutputMatch` exige `{type: substring|regex, value: string}` (la clave es `value`, no `text`).
- El evento de estado **no trae el nombre del agente** (trae `pane_id` y `workspace_id`), así que la correspondencia pane → nombre viene del snapshot y se refresca en `pane.agent_detected`. `pane_agent_detected` llega con guiones en `event` y `pane.agent_status_changed` con punto: el cliente maneja ambos.
- El hilo de eventos **solo manda `events.subscribe`**: ningún método mutante (`agent prompt`, `agent start`, `pane split`, `workspace create/close`, `server.stop`).
- **Salud del canal**: la clave `events` vale `live | polling | unavailable` en `/api/state`, `/api/offices` y el stream SSE. Si el socket falla, la oficina degrada al sondeo y reintenta con backoff **1, 2, 4 … 30 s** (`EV_BACK0`/`EV_BACKMAX`); el canal vuelve solo cuando Herdr revive, sin reiniciar el servicio.

### G. SSE confinado a la oficina y condición de salida (T27)
- `/api/events?ws=<label>` usa el mismo dominio de label que `/api/state?ws` (T8): `[A-Za-z0-9][A-Za-z0-9_-]{0,31}` por máquina y `w[0-9A-Za-z]+` por workspace. Label malformado → 400, máquina desconocida → 404, **nunca** el estado local.
- Cada stream lleva **solo la oficina pedida**: la suciedad se marca y se consume por oficina (`dirty_ws`, `ev_take`), así que un cambio en otra oficina no genera frame. Medido en vivo: 16 frames, todos con los mismos 5 agentes de `w1`.
- **El bucle del handler tiene condición de salida**: `_sse_alive` (`server.py:1256`) mira la conexión con `select` de timeout 0 al inicio de cada quantum y comprueba `server.socket.fileno() == -1`; y el fallo de escritura se comprueba **dentro** del bucle. `SSE_MAX = 1800 s` y `SSE_TIMEOUT = 2 s` son las cotas. Antes el bucle solo salía cuando una escritura contra el cliente cerrado devolvía `BrokenPipe`, hasta un latido (~30 s). Medido en la suite: el handler sale en menos de 6 s tras irse el cliente y no vuelve a construir estado (`test_el_handler_SSE_sale_al_irse_el_cliente_y_deja_de_sondear`); en la base, esa fuga producía 25 lanzamientos de `api snapshot` tras el teardown de los tests.
- El latido sigue siendo ~30 s (`quiet >= 6` con quantum de `interval` = 5 s) para mantener viva la conexión; en `live` el sueño del bucle se corta en cuanto un evento ensucia (`EV_WAKE = 0,2 s`).

### H. Aviso `notification show` al pasar a `blocked` (T10)
- argv verbatim, capturado del binario: `herdr notification show "<título>" --body "<texto>" --position top-right --sound request`. Se lanza por CLI (no por socket: `notification` no está entre los métodos suscribibles) con `shell=False`.
- Solo en la **transición** hacia `blocked`, sobre el último estado observado por `pane_id`: un `blocked` repetido lanza **0** subproceso.
- **Cotas, elección de la oficina, no medidas**: `NOTIF_WIN = 20 s` **por pane** (dos avisos del mismo pane en menos de 20 s son ruido; `blocked → idle → blocked` rápido sale con un aviso), `NOTIF_BURST = 4` spawns en `NOTIF_BURST_WIN = 10 s`, `NOTIF_QUEUE = 16`, `NOTIF_TIMEOUT = 5 s`. El cap se aplica al **spawn**, no a la intención.
- El aviso lo lanza un worker en hilo aparte (`oficina-notif`), para que la lectura del socket nunca espere un subproceso. Un pane sin nombre se avisa con el `pane_id` a secas: nunca se inventa un nombre.

---

## 4. Frontend Web (`index.html`) — Diorama 3D y Experiencia de Usuario

### A. Diorama 3D Isométrico con Render Bajo Demanda (dGPU 0 % en reposo, medido)
Cumpliendo los principios establecidos en `OFICINA-IDEAS.md`, la visualización 3D está diseñada para no sustraer capacidad de cómputo a la RTX 3060:
1. **Cámara Ortográfica Isométrica**:
   - `THREE.OrthographicCamera` con elevación $el = 0,62$ y acimut $az = 0,58$ (`index.html:445`). Proporciona una perspectiva arquitectónica limpia sin distorsión angular.
2. **Estética Low-Poly Profesional**:
   - Modelado procedural en código: **350–400 mallas vivas con 38 geometrías distintas**. Medido en Chromium con 5 agentes: **422 mallas vivas y 38 geometrías** (152 de sala + 120 de escritorios + 150 de empleados). El arnés de T11 contó **129 construcciones de geometría**, y es menor que el número de mallas porque casi todo comparte las geometrías cacheadas de `G`. La cifra anterior de ~60 mallas era falsa.
   - Materiales mate tipo arcilla (`MeshStandardMaterial` con rugosidad 0,88, sin reflejos caros).
   - Paleta sobria (madera, pizarra, aluminio cepillado, cristal translúcido).
   - Sombras de contacto suaves mediante `PCFSoftShadowMap` y luz hemisférica.
   - `applyTheme` libera lo propio con `dispose()` sobre un `Set` de compartidos: **29 geometrías + 71 materiales + 6 texturas liberadas, 0 compartidas**. `G.cap` queda cacheado: las `CapsuleGeometry` pasaron de **104 a 6**.
3. **Render Bajo Demanda (*Demand-Driven Rendering*)**:
   - **Bucle desacoplado**: La función `requestAnimationFrame(tick)` solo se ejecuta si hay una animación en curso (muñeco caminando o tecleando) o si se interactúa con la cámara. Las poses de señal (`ask`, `query`) son estáticas y no entran en `animating()`, así que un agente bloqueado o terminado no arrastra el bucle.
   - **Reposo, medido en Chromium** (instancia scratch, puerto 8099, Herdr real, 2026-10-06): con un `agy` bloqueado y ningún `working`, **12 rAF en 60 s**, con gaps **4997,2–5002,3 ms** (un render por poll, cero frames entre medias). Con un agente `working`: **567 rAF en 19,94 s = 28,39 fps**. En la instancia de T3: **2 rAF en 90,004 s** (0,0222 fps), gaps de 34,9 y 35,2 s por latido SSE. dGPU: **6 samples, todos 0 %**.
   - **Etiquetas sin reconstruir DOM cada frame** (T15): HEAD hacía **8 escrituras de `innerHTML` en 8 frames**; el código actual hace **0 en 120 frames estáticos**. `pillEl(name)` crea el elemento una vez por agente y `placeLabels` escribe solo lo que cambió.

### B. Elementos del Espacio y Comportamiento Procedural
- **Despacho de Claude**: Espacio acristalado sobre tarima elevada en la esquina superior izquierda, simbolizando la supervisión del arquitecto.
- **Puestos de Trabajo Individuales**:
  - Escritorios orientados a 180° hacia el usuario para permitir ver la pantalla del monitor.
  - La pantalla del monitor ejecuta una animación canvas 2D proyectada como textura 3D (`drawCode`), mostrando sintaxis de código coloreada que avanza cuando el agente está trabajando.
  - Cada puesto incluye un LED indicador de estado tridimensional y un marcador esférico. Los cinco estados tienen color: `working` verde `#22c55e`, `blocked` rojo `#ef4444`, `done` azul `#3b82f6`, `idle` gris `#9ca3af`, `unknown` `#64748b` (el ámbar `#f59e0b` corresponde a `waiting`, que solo vive en las clases CSS).
- **Los cinco estados se pintan en 3D y en el DOM** (T2, T3, T11, T22):
  - **Pose**: `type` para `working`, `ask` (brazo levantado) para `blocked`, `query` (cabeza baja) para `unknown`, `stand` para `done` y `idle`.
  - **Marcador esférico**: se enciende solo con `blocked` (rojo) y `done` (azul), con la geometría compartida `G.sph`. Radio 0,12 → **10,76 px de pantalla a zoom 89,7 y 5,67 px a 47,21** (computado de la relación plano/zoom; el registro de T11 midió **140–141 px rojos** y centroide a **1,1 px** del punto proyectado, en claro y en oscuro).
  - **Franja del monitor**: pinta solo cuando cambia el estado (la llama `sync`, nunca `tick`): `APROBACIÓN` + `pane_id`, `LISTO` + `completion_seq`, `SIN CLASIF.` + `pane_id`, con texto explícito si el dato no viene. La legibilidad está abierta (T29): se midió texto de **1,7 px de pantalla a zoom 47,21** y **3,22 px a 89,7** con la franja anterior; tras agrandar la fracción del plano, la cabecera de 68 px rinde **10,03 px a 47,21 y 19,06 px a 89,7** por la fórmula de tamaño de pantalla, sin re-medir en Chromium.
  - **Badge DOM**: la clase `.st` sí se consume: en la ficha del inspector, en la cabecera (`#badge`) y en la lista de agentes del chat, con las mismas palabras que `ACTS` del servidor (`trabajando`, `esperando confirmación`, `terminado`, `en reposo`, `estado desconocido`). La leyenda del diorama muestra los 5 estados.
  - **Burbuja «TE NECESITA» (T12)**: cuelga del agente bloqueado (`bub.dataset.a` = nombre), no de un agente fijo. Medido en Chromium con un `agy` bloqueado real: `hidden=false`, texto `<b>TE NECESITA</b>probeagy2 · probe-t12b · w0:p2`, ancla de la pill a **dx=0, dy=−8 px** de la cabeza; desaparece al salir de `blocked` y reaparece al bloquearse; el clic selecciona ese agente. Control en `w1` con 5 agentes `idle`: **0 px rojos** en el LED del escritorio.
- **Mesa Redonda de Café**:
  - Situada en el centro de la sala, con 6 sillas y tazas personalizadas con el color de cada agente.
  - **Transición de Estado**: Cuando un agente pasa a `idle`, su avatar se levanta del escritorio, se desplaza físicamente por la sala (`pose walk`) y se sienta en la mesa de café a descansar. Cuando vuelve a recibir trabajo (`working`), camina de regreso a su escritorio y comienza a teclear (`pose type`).
- **Rack de Servidores (Tester)**: Mueble de servidores con cinco LEDs luminosos verdes que titilan cuando el tester está corriendo benchmarks de carga sobre el motor.
- **Estantería de Documentación (Explorer)**: Mueble con libros técnicos procedurales que representa el puesto de investigación de hardware.
- **Pizarra de Tareas Interactiva**: Malla 3D con textura generada en canvas 2D (`drawBoard`) que renderiza pósits reales con las tareas de `TAREAS.json` (ID, responsable, título).
- **Pantalla Gigante de Telemetría**: Pantalla LED en la pared del fondo que muestra en tiempo real las cifras críticas de producción de Strata: B1 (escritura en tok/s), P3 (lectura 32K en s), delta porcentual vs base y gráfica de evolución histórica.

### C. Navegación e Interfaz de Control (Layout 16:9)
La aplicación cuenta con una barra de herramientas lateral (*Rail*), panel izquierdo de oficinas, visor central y panel de inspección derecho:
1. **Barra Lateral de Oficinas**:
   - Lista todas las oficinas abiertas y permite alternar entre ellas con un clic.
   - Botón `+ Nueva oficina`: Despliega un asistente para crear oficinas en cualquier máquina, configurando perfil por perfil (tipo de agente, modelo de lenguaje y número de instancias).
2. **Chat de Mando y Despacho**:
   - Selector `@agente` para dirigir órdenes a cualquiera de los agentes o a Claude.
   - Enlace directo con la terminal: permite desplegar el hilo de conversación anterior.
   - Advertencia preventiva al enviar mensajes a `tester` para no contaminar mediciones de rendimiento.
3. **Inspector Lateral (Ficha del Agente)**:
   - Pestaña `Inspector`: Rol, estado actual, actividad detallada, tiempo en el estado actual y botones de acción contextual.
   - Pestaña `Salida`: Muestra la salida viva de la terminal del agente (últimas 30 líneas capturadas por Herdr), permitiendo auditar qué está ejecutando en cada instante.
   - Pestaña `Hilo`: Registro de órdenes enviadas y respuestas registradas.
4. **Vistas Alternativas**:
   - `Métricas`: Tablero ampliado con métricas de decode, prefill, uso de memoria y tabla de auditoría del CHANGELOG.
   - `Campus`: Vista agregada de todas las oficinas creadas en la red, con resumen de agentes activos y tareas pendientes.

---

## 5. Resumen de Ficheros y Rutas del Sistema

```
/tmp/opencode/Strata3060/docs/fork/oficina/
├── server.py                        # 1283 líneas: HTTP/1.1, API, SSE, socket de eventos, puente Herdr
├── index.html                       # 1288 líneas: SPA con diorama 3D Three.js
├── login.html                       # 52 líneas: pantalla de inicio de sesión scrypt
├── strata-oficina.service           # 31 líneas: unidad systemd de usuario (igual que la instalada)
├── README.md                        # este documento
├── vendor/
│   └── three.module.min.js          # Three.js vendorizado: 670.681 bytes (versión no registrada en el bundle)
├── tests/
│   ├── README.md                    # 203 líneas: suite, formas verificadas, anclas y brechas
│   ├── conftest.py                  # 504 líneas: fixture `server` aislada + guardia de hermeticidad T28
│   ├── stub_herdr.py                # 522 líneas: emulador de herdr a nivel argv y de socket Unix
│   ├── test_herdr_stub.py           # 1836 líneas: 134 tests
│   ├── test_events.py               # 1152 líneas: 39 tests
│   └── fixtures/                    # 21 capturas verbatim + `fork/` con 4 ficheros
└── v4-*.png                         # Capturas de auditoría visual

~/.config/systemd/user/
├── strata-oficina.service                  # unidad instalada: ExecStart = este árbol, puerto 8095
└── strata-oficina.service.d/herdr.conf     # drop-in (128 bytes): PATH y HERDR_SOCKET_PATH

~/.config/strata-oficina/
├── password.scrypt                  # Hash scrypt de la contraseña de acceso (0600)
└── token                            # residuo de v4: el servidor no lo lee

~/.cache/strata-oficina/
├── sesiones.json                    # cookies de sesión activas (0600, publicada de forma atómica)
├── envios.log                       # resultado de cada envío: outcome, confirmed, code, state, pane_id, timeout_ms
├── oficinas.json                    # metadatos de oficinas (read-modify-write bajo `MFLOCK`, atómica)
└── opencode/                        # Configuraciones dinámicas de agentes opencode

~/.local/share/strata-oficina/ada/
└── pi                               # Script shim de ada-cli con HERDR_AGENT=pi
```

---

## 6. Operaciones y Mantenimiento

1. **Estado del Servicio**:
   ```bash
   systemctl --user status strata-oficina.service
   systemctl --user show strata-oficina.service -p ExecStart -p FragmentPath -p DropInPaths -p Environment
   ```
   La segunda forma es la que prueba la unidad efectiva y su drop-in (medido el 2026-10-07, §1B).
2. **Ver Logs del Servidor**:
   ```bash
   journalctl --user -u strata-oficina.service -n 40
   ```
3. **Reiniciar tras Modificaciones en el Código**:
   Dado que `server.py` precarga `index.html`, `login.html` y `vendor/three.module.min.js` en memoria al iniciar, cualquier cambio en los ficheros del repositorio requiere reiniciar el servicio:
   ```bash
   systemctl --user restart strata-oficina.service
   ```
4. **Tests (sin GPU, sin Herdr vivo, sin red)**:
   ```bash
   python3 -m pytest docs/fork/oficina/tests/ -q
   ```
   Medido el 2026-10-07 en esta máquina (Python 3.12.3, pytest 9.1.1): **173 passed** en dos runs, **153,23 s** y **153,64 s**. La línea de hermeticidad del harness: `tests=170` (los tests que instalan el shim de `subprocess`), `executables pedidos=['ada-cli', 'agy', 'herdr', 'opencode', 'pi', 'ssh']` — todos emulados, `binarios reales=0`, `pedidos tras el sellado=0`, `hilos del servidor al final=[]`. El harness no escribe bytecode en `docs/fork/oficina/`, no arranca ni reinicia el servicio, y no modifica el `HOME` real.
5. **Puertas sin sesión** (verificado 2026-10-05, y el dominio se mantiene): `/api/*` → 401, `/` → 302 a `/login`, `/login` → 200.
6. **Acceso desde Dispositivos Externos**:
   La oficina es accesible desde cualquier navegador en la red Tailscale mediante:
   `http://100.79.41.59:8095` o `http://bazzite.tailfe8578.ts.net:8095`.

