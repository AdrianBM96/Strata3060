# Auditoría: oficina agéntica web (2026-10-05, solo lectura + pruebas sin sesión)

## Dónde vive
- Código referencia: `docs/fork/oficina/` (server.py 458 lín, index.html 847, login.html, vendor/three.module.min.js 670 KB, capturas v4-*.png, ref.jpg/ref-v4.jpg).
- En ejecución: `strata-oficina.service` (usuario) → `/usr/bin/python3 docs/fork/oficina/server.py 8095` (directo del repo; verificado instalado).
- Escucha verificado: SOLO `127.0.0.1:8095` + `100.79.41.59:8095` (nada en 0.0.0.0). MagicDNS `bazzite.tailfe8578.ts.net:8095` resuelve y responde (401 sin sesión ✓).

## Front (index.html + three.js vendorizado)
- Tres columnas 16:9 (oficinas / diorama+chat / inspector), vista Métricas y Campus; diorama 3D real con three.js (MeshStandardMaterial roughness .88, HemisphereLight), render bajo demanda (~30 fps solo al trabajar).
- Llama a `/api/state|log|hilo|offices|kinds|send|office|office/delete|office/job|events(SSE)`; 401 → redirige a `/login`. Sin CDN ni imágenes externas.

## Server (rutas y datos)
- GET: `/` (302 a login sin sesión), `/login`, `/vendor/three…`, `/api/state[?ws]` (Strata u otra oficina), `/api/kinds`, `/api/log` (30 líneas `herdr agent read … recent-unwrapped`), `/api/hilo` (envíos + menciones en CHANGELOG), `/api/offices` (workspaces locales + máquinas herdr), `/api/office/job`, `/api/events` (SSE, solo si cambia).
- POST con sesión: `/api/login`, `/api/send` (direct→`adrian: …` al agente; viaclaude→`adrian (oficina): …` a claude; límite 1/3 s, ≤4000 chars, log en `envios.log`), `/api/office` (CREA workspace+paneles+agentes, máx 8), `/api/office/delete` (protege la de Strata, confirmación por nombre).
- Estado cada 5 s (30 s con bench.lock), cacheado: herdr `agent list`, segmento ESTADO del CHANGELOG, ramas `wt-*`, lock tester (`progreso=`), ENVIADOS.md, suplencia, METRICAS.json, TAREAS.json, últimas 8 filas del CHANGELOG, logs upstream/vigía, conteo COLA.

## Integración herdr (todo por CLI, argv, sin shell)
- `agent list/read/prompt/start`, `workspace create/list/close`, `pane split`, `machine list`; remoto por `--machine` + SSH BatchMode. Timeouts 8-90 s. Crea configs opencode por agente y un shim `pi`→ada-cli. Puede arrancar agentes con `--dangerously-skip-permissions` (claude/agy en modo auto): potente y previsto, pero que conste.

## Seguridad (verificado)
- Login scrypt (`password.scrypt` 600, `hashlib.scrypt`+`compare_digest`, fail-closed) + cookie `sid` aleatoria HttpOnly+SameSite=Strict 30 días deslizantes (`sesiones.json` 600); 5 fallos/IP/15 min (en memoria: se pierde al reiniciar); TODO exige sesión salvo `/login`; POST cap 8192 B.
- `cwd` confinado al home, sin ocultas; modelos restringidos a los instalados; sin `shell=True` en ningún sitio.
- Matices: sin flag `Secure` (http en tailnet, red de confianza); sin token CSRF (lo mitiga SameSite); SSE sondea herdr cada 5 s por cliente; versión de three.js vendorizado sin registrar.

## Deriva documental (corregir)
- `README.md` desfasado: habla de `/tmp/herdr-office-v4.py`, modelo token y `OFFICE_PASSWORD` (hoy: scrypt en fichero, sin esa env). La unit del repo apunta a `wt-C30` y menciona token (la instalada está bien).
- Tras editar el repo hay que reiniciar el servicio (sirve ficheros de disco al arrancar).

## Rendimiento (medido por el obrero, no re-medido aquí)
- Frame diorama 0,2-0,6 ms, dGPU 0 %, server 0,00 % en reposo. Capturas en repo (claro/oscuro/zoom/Métricas/Campus/animación).

## Abiertos
- `Aprobar` probado solo hasta el confirm (no disparado en vivo, correcto); prueba `Estado`→explorer pendiente de presenciar; sesiones persistentes 30 d sin logout visible.
