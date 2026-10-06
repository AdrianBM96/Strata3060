# Arquitectura Completa de la Oficina Agéntica: Web, Servidor e Integración con Herdr

Fecha: 2026-10-05. Autor: explorer. Para: Claude (arquitecto), Adrián y equipo.  
Objetivo: Documentar al 100% la oficina agéntica: código fuente, servicio en ejecución, servidor backend, interfaz web con diorama 3D y puente de comunicación con Herdr.

---

## 1. Mapa de Ubicación y Estado de Ejecución

### A. Ubicación del Código Fuente
El código oficial de la oficina agéntica de Strata reside en el repositorio git del proyecto:
- **Directorio base**: `docs/fork/oficina/` (rama `claude/strata-rtx3060-optimization-zfgxq8`).
- **Ficheros clave**:
  1. `docs/fork/oficina/server.py` (458 líneas): Servidor HTTP/1.1 con SSE, JSON API, autenticación scrypt, cookies de sesión deslizantes, rate limiting, gestión de workspaces y comunicación CLI con Herdr.
  2. `docs/fork/oficina/index.html` (847 líneas): SPA web completa con layout 16:9, tres columnas (Oficinas / Diorama 3D + Chat / Inspector), visualización 3D interactiva en Three.js con render bajo demanda (GPU ~0 % en reposo, sin medir), telemetría de Strata, pizarra de tareas reales y selector multioficina/multimáquina.
  3. `docs/fork/oficina/login.html` (77 líneas): Interfaz de acceso protegida por contraseña con selector automático de tema oscuro/claro y control de reintentos.
  4. `docs/fork/oficina/vendor/three.module.min.js` (670 KB): Three.js vendorizado e integrado localmente (cero dependencias de CDNs externas, funciona 100 % offline).
  5. `docs/fork/oficina/strata-oficina.service` (17 líneas): Unidad de servicio systemd de usuario.
  6. Capturas de referencia visual: `v4-*.png`, `ref.jpg`, `ref-v4.jpg`.

### B. Servicio Activo en Ejecución
- **Proceso del sistema**: `/usr/bin/python3 /tmp/opencode/Strata3060/docs/fork/oficina/server.py 8095` (PID `1645893`).
- **Servicio gestor**: `strata-oficina.service` (systemd de usuario).
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
- **Hilos independientes**: Despacha peticiones concurrentes y mantiene conexiones de larga duración (SSE) sin bloquear las llamadas a la API.
- **Consumo**: 0,00 % de CPU en reposo.

### B. Autenticación, Sesiones y Seguridad
1. **Contraseña Scrypt**:
   - Hash almacenado en `~/.config/strata-oficina/password.scrypt` (permisos 0600, formato `scrypt$16384$8$1$salt$hash`).
   - Verificación con `hashlib.scrypt` y `hmac.compare_digest` para evitar ataques de temporización (*timing attacks*).
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
| `GET` | `/api/state[?ws=<id>]` | Sesión | Estado consolidado de la oficina: agentes, status, métricas, tareas, ticker, locks. |
| `GET` | `/api/kinds[?m=<maquina>]` | Sesión | Tipos de agentes disponibles y modelos instalados (local o remoto vía SSH). |
| `GET` | `/api/log?agent=<nombre>` | Sesión | Últimas 30 líneas de terminal del agente leídas en directo de Herdr. |
| `GET` | `/api/hilo?agent=<nombre>` | Sesión | Historial de órdenes enviadas al agente desde la oficina (`envios.log`) y menciones en CHANGELOG. |
| `GET` | `/api/offices` | Sesión | Listado de todas las oficinas (workspaces de Herdr) locales y remotas. |
| `GET` | `/api/office/job?id=<jid>` | Sesión | Estado de progreso de la creación asíncrona de una oficina. |
| `GET` | `/api/events` | Sesión | Canal Server-Sent Events (SSE) para actualización reactiva en tiempo real. |
| `POST` | `/api/login` | Libre | Valida la contraseña y establece la cookie `sid`. |
| `POST` | `/api/send` | Sesión | Envía un mensaje directo a un agente o a Claude vía Herdr. |
| `POST` | `/api/office` | Sesión | Crea un nuevo workspace en Herdr, particiona paneles y arranca agentes. |
| `POST` | `/api/office/delete` | Sesión | Cierra y destruye un workspace de Herdr (protege la oficina Strata3060). |

### D. Agregación Reactiva de Estado (`build_state`)
El servidor sondea el sistema y Herdr para construir un snapshot unificado:
- **Agentes**: Consulta `herdr agent list` para extraer `agent_status` (`working` / `idle`), pane, cwd y terminal title.
- **Actividad contextual**: Mapea la última actividad desde la línea `ESTADO:` del `CHANGELOG.md` y el fichero `ENVIADOS.md`.
- **Telemetría Strata**: Lee `METRICAS.json` (B1 decode en tok/s, P3 prefill en s, acierto de caché, historial).
- **Tablero de Tareas**: Lee `TAREAS.json` (tareas en columnas "EN CURSO", "ESPERA OK", "EN COLA", "HECHO").
- **Protección de Benchmarks**: Si existe `/tmp/strata-bench.lock`, el intervalo de refresco se eleva automáticamente de 5 s a 30 s para no perturbar las mediciones de latencia del motor.
- **Suplencia**: Detecta si `~/.cache/strata-watchdog/suplencia` está activo para marcar visualmente al agente suplente como activo.

---

## 3. Integración con Herdr (Herdr Interop)

### A. Comunicación Segura mediante Argumentos Directos (sin Shell)
La integración con Herdr se realiza invocando directamente el binario `/home/bazzite/.local/bin/herdr` mediante `subprocess.run(..., shell=False)`. No se utiliza `shell=True` en ningún punto, neutralizando vulnerabilidades de inyección de comandos.

### B. Gestión de Workspaces y Paneles
- **Oficina Strata3060**: Mapeada de forma fija al workspace principal (`w1`), donde conviven los 5 agentes nucleares (`claude`, `opencode2`, `tester`, `explorer`, `suplente`).
- **Creación de Nuevas Oficinas**:
  1. `herdr workspace create --cwd <cwd> --label <nombre> --no-focus --env <env>`: Genera un workspace aislado.
  2. `herdr pane split <base_pane> --direction <right|down> --cwd <cwd>`: Particiona recursivamente la ventana en paneles para alojar a cada agente en su propia consola.
  3. `herdr agent start <nombre> --kind <kind> --pane <pane_id> ...`: Arranca el runtime del agente en su panel correspondiente.

### C. Despacho de Mensajes y Norma N19
Cuando Adrián escribe un mensaje en la barra de chat de la oficina:
1. **Modo Directo (`direct`)**:
   - Ejecuta: `herdr agent prompt <agente> "adrian: <texto>"`.
   - Este formato activa de inmediato la **Norma N19** en el agente receptor: las órdenes de Adrián enviadas desde la oficina tienen prioridad absoluta sobre cualquier otra tarea en curso.
2. **Modo Vía Claude (`viaclaude`)**:
   - Ejecuta: `herdr agent prompt claude "adrian (oficina): <texto>"`.
   - Se utiliza para encargar nuevas tareas al arquitecto, quien las desglosa en contratos técnicos y las encola.
3. **Control de Tasa y Auditoría**:
   - `SEND_LOCK`: Solo se permite 1 envío cada 3,0 segundos para evitar saturar el socket de Herdr.
   - Todo envío queda registrado en `~/.cache/strata-oficina/envios.log` con marca temporal UTC.

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

---

## 4. Frontend Web (`index.html`) — Diorama 3D y Experiencia de Usuario

### A. Diorama 3D Isométrico con Render Bajo Demanda (0 % GPU en Reposo)
Cumpliendo los principios establecidos en `OFICINA-IDEAS.md`, la visualización 3D está diseñada para no sustraer capacidad de cómputo a la RTX 3060:
1. **Cámara Ortográfica Isométrica**:
   - `THREE.OrthographicCamera` con elevación $el = 0,62$ y acimut $az = 0,58$. Proporciona una perspectiva arquitectónica limpia sin distorsión angular.
2. **Estética Low-Poly Profesional**:
   - Modelado procedural en código (~60 mallas en total).
   - Materiales mate tipo arcilla (`MeshStandardMaterial` con rugosidad 0,88, sin reflejos caros).
   - Paleta sobria (madera, pizarra, aluminio cepillado, cristal translúcido).
   - Sombras de contacto suaves mediante `PCFSoftShadowMap` y luz hemisférica.
3. **Render Bajo Demanda (*Demand-Driven Rendering*)**:
   - **Bucle desacoplado**: La función `requestAnimationFrame(tick)` solo se ejecuta si hay una animación en curso (muñeco caminando o tecleando) o si se interactúa con la cámara.
   - **Reposo absoluto**: Cuando los agentes están inactivos (`idle`), el bucle de render se detiene por completo. El consumo de dGPU es ~0 % en reposo, sin medir, evitando que los ventiladores de la tarjeta gráfica se activen o que se degraden los tokens/segundo de Strata.

### B. Elementos del Espacio y Comportamiento Procedural
- **Despacho de Claude**: Espacio acristalado sobre tarima elevada en la esquina superior izquierda, simbolizando la supervisión del arquitecto.
- **Puestos de Trabajo Individuales**:
  - Escritorios orientados a 180° hacia el usuario para permitir ver la pantalla del monitor.
  - La pantalla del monitor ejecuta una animación canvas 2D proyectada como textura 3D (`drawCode`), mostrando sintaxis de código coloreada que avanza cuando el agente está trabajando.
  - Cada puesto incluye un LED indicador de estado tridimensional (verde = trabajando, ámbar = esperando confirmación, rojo = bloqueo, gris = inactivo).
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
├── server.py                        # Servidor HTTP/1.1, API REST, SSE y puente Herdr
├── index.html                       # Frontend SPA con diorama 3D Three.js
├── login.html                       # Pantalla de inicio de sesión scrypt
├── strata-oficina.service           # Unidad systemd de usuario
├── vendor/
│   └── three.module.min.js          # Three.js r160 vendorizado (670 KB)
└── v4-*.png                         # Capturas de auditoría visual

~/.config/strata-oficina/
└── password.scrypt                  # Hash scrypt de la contraseña de acceso (0600)

~/.cache/strata-oficina/
├── sesiones.json                    # Base de datos de cookies de sesión activas (0600)
├── envios.log                       # Registro de auditoría de mensajes enviados por la UI
├── oficinas.json                    # Metadatos de oficinas creadas
└── opencode/                        # Configuraciones dinámicas de agentes opencode

~/.local/share/strata-oficina/ada/
└── pi                               # Script shim de ada-cli con HERDR_AGENT=pi
```

---

## 6. Operaciones y Mantenimiento

1. **Estado del Servicio**:
   ```bash
   systemctl --user status strata-oficina.service
   ```
2. **Ver Logs del Servidor**:
   ```bash
   journalctl --user -u strata-oficina.service -f
   ```
3. **Reiniciar tras Modificaciones en el Código**:
   Dado que `server.py` precarga `index.html` y `login.html` en memoria al iniciar, cualquier cambio en los ficheros del repositorio requiere reiniciar el servicio:
   ```bash
   systemctl --user restart strata-oficina.service
   ```
4. **Acceso desde Dispositivos Externos**:
   La oficina es accesible desde cualquier navegador en la red Tailscale mediante:
   `http://100.79.41.59:8095` o `http://bazzite.tailfe8578.ts.net:8095`.

