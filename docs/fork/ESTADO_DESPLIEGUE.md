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
| 8080 | **BeeLlama.cpp** (`beellama/`) | router GGUF: qwopus-coder, tiel-coder, praxis, nex-mini (visión) |
| 4000 | **litellm** (`beellama/litellm/config.yaml`) | `ada-next`→strata:8081; `ada-praxis`→nex-mini:8080 |
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
