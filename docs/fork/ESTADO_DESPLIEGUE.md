# Estado de despliegue de bazzite (para recuperación)

Última revisión: **2026-10-04**. Sirve para reconstruir todo tras un reinicio o una caída de sesión.

## La máquina

- **bazzite**: RTX 3060 12 GB (sm_86, PCIe Gen4 x16), i5-12400F (6 P-cores, AVX2 + **AVX-VNNI**, sin AVX-512),
  62 GB DDR4-2133 (XMP off; los módulos son de 3200), Ubuntu, kernel **6.8.0-142-generic**.
- Tailscale y `ssh` habilitados; los servicios de usuario vuelven solos tras un reinicio (linger=yes).
- **`/tmp` se vacía al reiniciar** (aunque `findmnt` diga que está en el disco raíz): los scripts de medida
  deben vivir fuera de `/tmp` → están en `~/Strata/bench/`.

## Motor y modelos

| Puerto | Qué | Notas |
| --- | --- | --- |
| 8081 | **Strata** `ada-next` | `swift-iq2_xs`, 512K ctx, KV `int8`, `--spec 4 --mtp`, `--ple-gguf`, **`STRATA_PF_FUSED=1`**, `--prefill auto` (elige 6144), caché de expertos auto (~3.700 slots) |
| 8080 | ~~**BeeLlama.cpp**~~ **RETIRADO** | Los modelos pequeños locales (tiel, qwopus, ornith, neohorse…) ya no se usan: servicio `ada-router.service` **deshabilitado y parado**, entradas de litellm retiradas. **Ficheros intactos** (`beellama/`, `models.ini`, GGUF) por si se quieren recuperar. |
| 4000 | **litellm** (`beellama/litellm/config.yaml`) | `ada-next`→strata:8081; **`ada-praxis`→strata:8081** (antes nex-mini:8080); + fallbacks cloud (MiniMax, Gemini, GLM…) |
| 8087 | **ada-decide** (System One) | `~/Strata/ada-decide.py` |

Servicios de usuario: `strata.service`, `ada-decide.service`, `litellm-proxy.service`.
Servicios de sistema: `gpu-fan-100.service` + `.timer` (ventilador al 100%).

## Cambios adoptados (con su medición)

| Cambio | Cómo se aplica | Medición |
| --- | --- | --- |
| `STRATA_PF_FUSED=1` | `export` en `serve-strata.sh` | **+5,2 % prefill**, puerta de calidad PASADA (`MEDICION_RONDA10.md`) |
| Ventilador GPU al 100 % | `gpu-fan-100.service` (systemd, `enabled`) + `.timer` cada 5 min | Quita el *thermal slowdown* (26-33 s → 0-1 s), 4-6 °C menos; **mismos tok/s** |
| `idle=poll` restaurado | `/etc/default/grub` (se aplica al próximo arranque) | Sin diferencia medible (`MEDICION_RONDA9.md`) |

## Estado del motor (git)

- Motor en **`~/Strata`**, rama `logprobs-endpoint`, HEAD **`bebb18d`** (snapshot del estado desplegado:
  0.1.38 + kernel IQ2_S + System One `--logprobs`).
- Remotos: `origin` = Niko1221/Strata (shallow, ahora completo), `architectds` (fork), `adesign` (clon local).
- **Antes de tocar el motor**, ver `PORT_FORK.md` (método y ramas de port).

## Claude Code (CLI) — apuntado a ada-next

Configurado para funcionar **solo con Strata** (ada-next), **sin pedir permisos** y con **ponytail** (código
mínimo → más rápido). Todo reversible.

| Qué | Dónde | Valor |
| --- | --- | --- |
| Modelo / endpoint | `~/.claude/settings.json` → `env` | `ANTHROPIC_BASE_URL=http://127.0.0.1:8081`, `ANTHROPIC_API_KEY=dummy`, `ANTHROPIC_MODEL=claude-sonnet-4-5`, `ANTHROPIC_SMALL_FAST_MODEL=claude-haiku-4-5` |
| Saltar permisos | `~/.claude/settings.json` → `permissions.defaultMode` | `"bypassPermissions"` (equivale a `--dangerously-skip-permissions`; solo vale desde settings de **usuario**) |
| Flag literal | alias en `~/.bashrc` | `alias claude='claude --dangerously-skip-permissions'` |
| Ponytail (más rápido) | `~/.config/ponytail/config.json` | `{"defaultMode": "full"}` (niveles: `off`/`lite`/**`full`**/`ultra`) |
| Ponytail habilitado | `~/.claude/settings.json` → `enabledPlugins` | `ponytail@ponytail: true` |

**Por qué Strata directo y no litellm**: litellm (`:4000`) falla en `/v1/messages` (sus fallbacks apuntan a
modelos que retiramos). Strata **sirve `/v1/messages` nativamente** (streaming, tools, system) → va directo a
`:8081`.

**Verificado**: Claude Code escribió y ejecutó código con ada-next, sin pedir permisos, y con ponytail activo
(el hook se dispara y escribe una sola línea donde antes iría un bucle).

**Revertir a tu cuenta Anthropic**: `cp ~/.claude/settings.json.bak-20261004-pre-ada-next ~/.claude/settings.json`
(y borrar el alias y `~/.config/ponytail/config.json` si se quiere).

## Otros agentes (pi, omp, ada-cli) — también con ada-next

| Herramienta | Config | Cómo queda |
| --- | --- | --- |
| **pi** | `~/.pi/agent/models.json` (+ `settings.json`) | modelo `ada-next` añadido al proveedor `litellm-gateway`; `defaultProvider=litellm-gateway`, `defaultModel=ada-next` |
| **omp** | `~/.omp/agent/models.yml` (+ `config.yml`) | `ada-next` añadido al proveedor `adaserver`; `modelRoles.default: adaserver/ada-next:high` |
| **ada-cli** | `~/.ada/agent/models.json` (+ `settings.json`) | `ada-next` añadido al proveedor `litellm-local`; `defaultProvider=litellm-local`, `defaultModel=ada-next` |

Los tres hablan con **litellm (:4000)**, que enruta `ada-next` a Strata. Verificado: `pi` → "PI OK" (`ada-next`),
`omp` → "OMP OK" (`adaserver/ada-next`), `ada-cli` → "ADA-CLI OK" (`model: ada-next` en el log).

**`ada-cli` estaba roto**: su wrapper ejecutaba un Node 22 que vivía en `/tmp` (borrado al reiniciar). Se apuntó
al Node de nvm. Copias `.bak-20261004-ada-next` de cada config.

## Reconstrucción rápida tras un reinicio

```bash
tailscale status                                   # debe estar conectado
systemctl --user is-active strata.service ada-decide.service litellm-proxy.service
systemctl is-active gpu-fan-100.timer              # ventilador al 100%
nvidia-smi --query-gpu=fan.speed --format=csv,noheader   # ~100%
curl -s localhost:8081/health                      # motor
grep STRATA_PF_FUSED ~/Strata/serve-strata.sh      # la mejora de prefill
```

## Pendiente / notas

- El motor **no es bit-exacto run-to-run** (con `temperature 0`, el `content` coincide pero la longitud del
  razonamiento varía; el tier adaptativo ya lo avisa). Afecta a comparar salidas por igualdad.
- `~/Strata/systemone.db` = registro del bucle de autoaprendizaje.
- Copias de la config con fecha `.bak-YYYYMMDD-*` en `~/Strata/`.

## Claude Code: dos perfiles

| Comando | Modelo | Permisos |
| --- | --- | --- |
| `claude` | **suscripción OAuth** (Opus/Sonnet reales; ada-next revertido a petición) | según alias `--dangerously-skip-permissions` en terminal interactivo |
| `claude-sub` | **suscripción OAuth** (fuerza OAuth aunque el entorno herede ada-next) | saltados siempre (limpia `ANTHROPIC_*` + flag) |
| `herdr-ada [pane]` | **ada-cli en herdr** (`--kind pi`, verificado extremo a extremo) | las del pane |

`claude-sub` = `claude --dangerously-skip-permissions --setting-sources project,local --settings ~/.claude/settings-subscription.json` (alias en `~/.bashrc`). El pane de herdr «Servidor IA» corre `claude-sub` equivalente. Nota herdr: la lista de agentes es cerrada (no admite `ada-cli` como tipo propio); va con `--kind pi` y nombre `ada-cli`.

## ada-cli en herdr (compatible via `pi`)

`ada-cli` está basado en pi (misma TUI y marcadores), así que herdr lo maneja con `--kind pi` pasando el binario:

```bash
herdr agent start <nombre> --kind pi --pane <pane> -- /home/bazzite/.local/bin/ada-cli
```

Verificado de extremo a extremo (responde por ada-next → Strata). En la web sale como agente `pi`.

## Máquinas en herdr (`herdr machine`)

| Máquina | Acceso | Estado |
| --- | --- | --- |
| mac-mini (100.98.211.55) | SSH `nicoaisdr@` con clave (instalada) + herdr 0.9.3 remoto | añadida y enabled |
| macbook-air (100.99.86.60) | SSH `31017423Z@` con clave (instalada) + herdr 0.9.3 remoto | añadida y enabled |

Desde herdr se controlan sus panes/agentes igual que en local.

## C32 (2026-10-05): precarga post-arranque
- `strata.service.d/precarga.conf`: `ExecStartPost=/home/bazzite/Strata/precarga.sh` (espera :8081 + POST
  `/v1/load`). `--lazy` intacto. Verificado: GPU 11.281 MiB a los ~25 s del restart (objetivo ≤20 s, no
  llegado por 5 s: mandan los 21 s de expertos a RAM); primera petición 1,35 s (sin penalización perezosa).
- Incidencia: el primer arranque tras el cambio falló (BrokenPipeError, exit 1) y systemd rearrancó solo;
  vigilar si se repite. Los scripts de A/B deben llamar a `precarga.sh` en su paso 'restaurar producción'.

## Motor 2026-10-06: v0.1.40 (CUP)
- `engine/strata` md5 **01856e5a** (01856e5a); rollback **da9a7a9b** en `bench/`.
- Flags en /proc: FETCH_ADMIT=1, PF_FUSED=1 (+ resto producción); C34 12/12288/4096; precarga C32.
