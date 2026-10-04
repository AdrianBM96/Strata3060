# Traspaso de sesión: contexto completo para seguir en otra sesión de Claude

Fecha: 2026-10-04.

- **Repo:** `AdrianBM96/Strata3060`.
- **Rama:** `claude/strata-rtx3060-optimization-zfgxq8`. Se desarrolla y se hace push **solo** ahí, y no se abre PR
  salvo que Adrián lo pida.

Este documento resume una conversación larga. **Los detalles están en los documentos que enlaza**: aquí está lo
necesario para retomar sin releerlo todo.

---

## 1. Objetivo y reglas de trabajo

### El objetivo

Adaptar un fork de Strata (motor MoE en C++/CUDA y servidor Python) a **una máquina concreta y a un uso concreto**, y
superar al Strata base:

- **decode y prefill** lo más rápidos posible;
- **sin perder contexto** (512K) **ni calidad**;
- y mejorar **System One** (`ada-decide`, servicio de decisiones que compite con Jev).

### La máquina: servidor "bazzite"

| Pieza | Detalle |
| --- | --- |
| GPU | RTX 3060 12 GB (sm_86), PCIe Gen4 x16. Mide **11 GB/s** con el bus sano: es un límite de la plataforma |
| CPU | i5-12400F: 6 P-cores, AVX2 + AVX-VNNI, **sin AVX-512** |
| RAM | 62 GB DDR4 Corsair CMK64GX4M2E3200C16 a **2133**. La BIOS del HP Victus 15L no tiene XMP. Adrián cambió la RAM de fábrica por esta |
| SO | Ubuntu, kernel 6.8 |
| Acceso | **Sin acceso físico hasta final de mes** (el PC está en Madrid). **El reinicio remoto funciona** (comprobado) |
| Presupuesto | **Ninguno** para hardware |

### El uso

- Agentes de código: Claude Code, opencode/omp, pi, ada-cli, herdr.
- System One.
- Visión: la hace el propio Strata.

### Protocolo (`PROTOCOLO.md`): Claude orquesta, el agente de bazzite ejecuta

- **Claude:**
  - escribe las órdenes numeradas en `ORDENES.md` (qué hacer, cómo comprobarlo, qué entregar, criterio);
  - audita y valida en `ORDENES.md`;
  - decide qué se adopta.
  - **No ejecuta pruebas ni escribe código largo.** Hay que **gastar lo mínimo de los límites de uso**.
- **El agente:**
  - implementa, compila, mide y despliega;
  - entrega en `ENTREGAS.md`;
  - no activa nada que cambie resultados sin la validación de Claude.
- **Cómo se mide** (`METODO_MEDICION.md`):
  - alternar, 6+6 pasadas, `bench.py compare` (IC bootstrap + Mann-Whitney);
  - calidad con `logpos-compare` y su suelo de ruido;
  - bit a bit con `STRATA_IQ_MT_MIN=1 --prompt-cache 0 --adapt-swaps 0 --pcie-frac 0` y temperatura 0.

### Preferencias de Adrián

- **Respuestas en español.** Cuando lo pide, en lenguaje no técnico.
- Se queda con **`--max-context` 512K**, aunque 256K daba +2,8 %.
- **Tope de pensamiento** lo más alto que sea seguro: es **3072**.
- **Aparcados:**
  - el guardarraíl y la delegación de System One (`RESPUESTA_SYSTEMONE_AGENTES.md`);
  - `PROPUESTA_ATASCO_THINKING.md` (su parte del tope ya está resuelta);
  - el Mac mini y el piloto Bonsai.
- Los modelos pequeños del :8080 y nex-mini están retirados.
- **Adrián empuja a cambiar la arquitectura, no solo ajustarla** (§5).

### Reglas de git

Commits terminados en:

```
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01SqcdMk1o1ESQYLn2xTkJZX
```

- Sin identificadores de modelo en los commits.
- `git push -u origin <rama>`.
- **El agente también hace push a la rama:** hay que hacer `git pull --no-rebase` antes del push.
- **No tocar en remoto** variables UEFI/BIOS ocultas.
- **No reintentar** el parche de `setup.py` sin aprobación expresa.

---

## 2. Arquitectura (lo esencial)

**Mapa completo:** `RESUMEN_FINAL.md`.

### Servicios

| Servicio | Puerto | Qué hace |
| --- | --- | --- |
| Strata (`ada-next`) | :8081 | Swift 1.5 125B IQ2_XS |
| `ada-decide` (System One) | :8087 | Decisiones con logprobs |
| litellm | :4000 | `ada-next` / `ada-praxis` → Strata |
| `gpu-fan-100` | — | Servicio + timer que fija el ventilador |
| micro-LLM de beellama | :8082 | Usa 246 MiB de la 3060 (lo vio la orden 11) |

### El motor

- **El modelo:** 48 capas MoE (GDN y QSA), 512 expertos por capa (24.576 en total), top-10, con hiperconexiones.
- **Caché de expertos en VRAM:** 3.694-3.696 huecos, ~1,376 MiB cada uno, 4,97 GiB en total.
- **El resto de expertos:** en un arena de RAM de 33 GiB, calculados en la CPU o leídos por PCIe. La parte PCIe es
  `pcie_frac` 0,30, como mucho 16 por capa, con el kernel de copia vía *staging* (modo 2, `auto`).
- **El `adapt`:**
  - revisa la residencia cada 4 ventanas;
  - decay 0,7;
  - ≤ 96 swaps por revisión, dentro de cada capa.
- **`FETCH_ADMIT`:** lo que cruza por PCIe se queda en la caché, en el hueco del residente menos usado no enrutado.
- **La métrica de acierto del motor** excluye la parte PCIe de los fallos.
- **El decode:**
  - va en ventanas de verificación especulativa (`--spec 4` + MTP, más el borrador por sufijo);
  - ping-pong entre la cadena densa en GPU (el lado largo) y los expertos en CPU;
  - `waitB` es la espera de la copia PCIe.
- **El KV:** `int8`, streamed. 32.768 celdas en VRAM y el resto en 6,19 GiB de RAM fijada; el 99,85 % de las
  lecturas acierta en VRAM.
- **La capa del borrador (MTP):** 836 MiB, con sus 512 expertos siempre residentes.

### Configuración adoptada

`strata-swift-iq2_xs.json`:

- motor v0.1.39 + `systemone-logprobs.patch` + `suffix-draft-stats.patch`;
- 512K, YaRN ×2, `--kv int8`, `--kv-resident 32768`;
- `--spec 4 --mtp`, `--prefill auto` (6144), `--logprobs 32`, `--prompt-cache-root 256`;
- visión con el codificador en CPU (con `--lazy`);
- `STRATA_PF_FUSED=1`, `STRATA_FETCH_ADMIT=1`;
- `reasoning_budget_tokens` 3072.

### Línea base actual (orden 7, n = 6)

| Prueba | Métrica | Valor |
| --- | --- | ---: |
| B1 | decode | **50,70 tok/s** |
| B2 | decode | **42,35 tok/s** |
| B4 | decode, con 98 % de borradores aceptados | **56,15 tok/s** |
| P3 | TTFT | **20,17 s** |
| P4 | TTFT | **26,09 s** |
| S2 | tiempo total | **2,29 s** |

**Todo lo nuevo se mide contra esta tabla.**

---

## 3. Decisiones de código tomadas (commits clave)

| Qué | Dónde | Estado |
| --- | --- | --- |
| **Bucle de aprendizaje de System One:** auditoría por moneda (warm-up 200), `audit`/`reasons` en la BD, holdout 0,5, System Two que cede el sitio cuando el motor tiene cola | `ops/s1_learn.py`, `ops/ada-decide.py`, `ops/test_s1_learn.py` (16 tests) | desplegado |
| **Banco:** B4 (copiar 80 líneas de un contexto de 32K), P3/P4 (TTFT de la cola de una herramienta tras 32K) | `ops/bench.py`, `test_bench.py` | desplegado |
| **Estadísticas del borrador por sufijo** + `STRATA_SFX_OOV` | `patches/suffix-draft-stats.patch` | desplegado |
| `STRATA_HIT_GY`, `heat_first` / `STRATA_PROFILE_HEAT_MIN`, contador `adapt_swapped` en la línea de acierto | `verify.cpp`, `expert_cache.*`, `generate.cpp` | opt-in. HIT_GY: sin ganancia |
| `lazy_vision_error`: `--lazy` con visión en CPU | `serve/server.py` + test `LazyVision` | desplegado |
| **`membw.c`:** el ancho de banda de lectura de la RAM | `ops/membw.c` | herramienta |
| **Simulador de caché:** static, adapt, cross, fetch-admit, LRU, Belady; columnas `eng.hit`, `free/w`, `swaps/w`; `--grid`, `--per-layer`, `--pcie-frac` | `ops/cache-sim.py`, `test_cache_sim.py` (en el CI del fork) | herramienta |
| **Merge de v0.1.39** (`2a05958`): todo el fork conservado, parche de logprobs regenerado (el `done` lleva `reasoning_tokens` + `strata_logprobs`) | `INTEGRACION_V0139.md` | **adoptado: +7,1 % decode, +9,0 % prefill** |
| **`STRATA_FETCH_ADMIT`** (`50581c2`): `fetch_blobs_admit` / `rebase_ptrs_admit`, plan con el campo `admit2` al final, víctimas elegidas en el host, se apaga solo en los casos incompatibles | `verify_kernels.cu/.hpp`, `verify.cpp/.hpp`, `expert_source.cpp/.hpp`, `generate.cpp` | **adoptado: acierto 73-78 %, +5-7 % decode, calidad en el ruido** |
| **Tope de pensamiento 3072** | config del servidor | **adoptado.** Órdenes 3, 4 y 5: 4096 falló 1 de 3 en la tarea larga |
| **Kernel IQ2_S AVX2** (anterior) | `row_dot_iq2s` | adoptado, +1,4 % |
| `STRATA_PF_FUSED=1` (anterior) | flag | adoptado, +5,2 % prefill |

**Commits de esta sesión** (órdenes y validaciones):

- `9171b31`: valida la orden 4.
- `d21523c`: órdenes 10-13.
- `48e26b4`: retira la 12 y la 13; añade la 14.
- `2ab3558`: `PLAN_ARQUITECTURA.md` y órdenes 15-17.
- `b77f3f3`: valida la orden 5.

---

## 4. Problemas resueltos y callejones sin salida

### Resueltos

- **La tabla del prototipo IQ2_S de un token estaba mal:** se arregló. Aun así, ninguna variante ganó a ggml, y la
  idea se dejó.
- **El simulador y el motor no daban el mismo acierto:** el motor excluye la parte PCIe. Se añadió la columna
  `eng.hit`. Queda un desfase de ~3 puntos, aceptado porque las políticas salen en el mismo orden.
- **`fetch-admit alone` del simulador no decaía:** arreglado.
- **`git cherry-pick X -- paths` no existe:** se usa `git show X -- paths | git apply --3way`.
- **El agente decía que unas opt-ins "no existían":** su árbol desplegado (`~/Strata`) era otro, y bastó con que
  hiciera fetch de esta rama.
- **El `bench.py` de B4 buscaba el corpus en `docs/fork/ops/`:** el agente lo relanzó desde allí.

### Descartados (medido)

| Idea | Resultado |
| --- | --- |
| `--spec 8` | era deriva térmica |
| `--pcie-mode dma` | neutro (`waitB` −12 %, neto 1,6 %) |
| `--pool-workers 10` | neutro |
| `STRATA_ADAPT_NOWAIT` | térmico |
| `idle=poll` | neutro |
| Límite de potencia | neutro |
| Bloquear el reloj (`-lgc`) | más vatios, el mismo reloj |
| Del fork architectds: `GR_DOWN_MAX4`, AVX-VNNI | ruido |
| Del fork architectds: CPU assist | +5,7 % de prefill, pero **degrada la calidad** |
| `HIT_GY` | ruido |
| Kernel q2_0 intercalado | sin ganancia |
| `--adapt-every 2` / `--adapt-swaps 32/16` con `FETCH_ADMIT` | no mejoran |
| XMP / la BIOS | imposible en remoto |
| Más hardware | sin presupuesto |
| **Reloj de la VRAM** | posible por software, pero las GEMV usan el 44-61 % del ancho de banda y las limita el kernel. Solo se reabre si un perfil muestra kernels al límite |
| **PCIe 11 GB/s** | la sonda es correcta (memoria fijada + DMA). Es la plataforma: la línea es **esconder** la copia, no acelerarla |
| 4096 de tope de pensamiento | falló 1 de 3 en la tarea larga |

---

## 5. La dirección nueva: cambiar la arquitectura (`PLAN_ARQUITECTURA.md`)

### Dónde se va el tiempo

Por ventana de B1, con ~2,3 tokens por ventana y ~44 ms por ventana (orden 9):

| Partida | ms/ventana |
| --- | ---: |
| Espera de la copia PCIe (`waitB`) | 9-10 (~22 %) |
| Expertos en CPU | ~4-4,6, más esperas |
| Aciertos en VRAM | 5,2 |
| `hc-read1` + router | 3,4 |

El grafo tiene 3.149-3.265 nodos.

### Los tres cambios

| Cambio | Idea | Resultado | Techo | Decide |
| --- | --- | --- | --- | --- |
| **A. Pre-gating** | Al empezar la capa L, aplicar el router de L+1 (y L+2) al estado disponible y empezar a copiar por PCIe los fallos predichos mientras la GPU calcula L | **bit a bit** | ~+20 % de decode si se predice el 80 % de los fallos | Orden 15 |
| **B. Rutas conscientes de la caché** | Cambiar un experto fallado por un residente no elegido con puntuación casi igual (δ) | **Cambia el texto.** Opt-in, con puerta de calidad (`logpos-compare` en el ruido y tareas de agente 25/25) | — | Orden 16 |
| **C. Borrador adaptado** | Afinar la cabeza MTP con las sesiones propias para subir los tokens por ventana | Calidad igual | — | Orden 17 |

**Nada de esto existe en Strata upstream** (buscado en `src/`).

---

## 6. Estado de las órdenes y tareas pendientes exactas

### Entregadas por el agente, **pendientes de validar por Claude**

Lo primero en la próxima sesión: leer `ENTREGAS.md` y escribir la validación en `ORDENES.md`.

| Orden | Lo que entregó | Mi lectura previa (sin validar) |
| --- | --- | --- |
| **6.** Herramientas no declaradas | 0 casos en uso real; el único, 6 × `read_file` en el harness. Propone no cambiar nada | **Validar tal cual.** Sin casos no hay nada que filtrar |
| **7.** Línea base | La tabla de §2 | **Validar.** Es la referencia |
| **9.** Perfil de la GPU | El desglose de §5. Sin `ncu` | **Validar.** `waitB` sigue en ~22 %: confirma el interés del cambio A (orden 15). Pedir que se lea la parte "GPU-reach wait ~22 ms + per-layer host ~14-19 ms" para separar qué espera es la del PCIe y cuál la de la CPU |
| **10.** Prefijos | Reúso 56 %; 61 % del prefill en 161 relecturas íntegras grandes; 205 arranques; 164 expulsiones con ≥ 4 clientes a la vez; `RESUME` no aparece en el log; `--prompt-cache` y `--prompt-cache-every` con sus valores por defecto (6 / 16384) | **Es la mayor bolsa de tiempo perdido en uso agéntico.** Siguiente orden: A/B de `--prompt-cache 12` (o 16) y `--prompt-cache-every 8192` con una sesión real o un reproductor de varios clientes. Que el servidor registre `RESUME` (hoy no llega al log). Averiguar por qué hay 161 relecturas íntegras: ¿expulsión, o un prefijo que cambia? Se puede comparar el hash del primer bloque del prompt sin guardar su contenido |
| **11.** VRAM | Mínimo libre en el pico: 848 MiB, con un prefill de 96K + imagen + B1. Propone `--expert-cache ≈ 4.100-4.124` (+12 %). Además, el micro-LLM de beellama (:8082) ocupa 246 MiB | **Probable sí, con cautela:** el prefill largo toma prestados huecos. Orden: A/B de `--expert-cache 4100` contra la 7 (B1, B2, B4), más la prueba de pico (96K + imagen) sin OOM. **Preguntar a Adrián** si el micro-LLM de :8082 se puede quitar (son ~180 huecos más) |
| **14a.** La capa MTP | El motor no cuenta su uso; el enrutado se hace en el dispositivo (`mtp.cpp:582-583`, búfer `ids_`). El agente pide que Claude diseñe `STRATA_MTP_HIST` | **Decisión de diseño pendiente:** sumar en el dispositivo (un kernel `atomicAdd` sobre un contador de 512 en la GPU tras el router, leído solo al salir). Así no hay sincronización por ventana. Darlo como orden concreta |

### Órdenes abiertas sin entregar

- **8.** Dos políticas en el simulador: dos memorias y coste por tipo, con la columna `cpu ms/w`. Prioridad baja.
- **14b.** Sustituida por la 15.
- **15.** `STRATA_PREGATE_STATS=1`: el recall del router de L+1 y L+2 aplicado al estado de entrada de L, con k = 10, 16
  y 24, sobre los fallos y sobre la parte PCIe.
  - **Criterio:** ≥ 60 % en la parte PCIe con k = 16 → escribir A; < 40 % → descartar.
- **16.** `STRATA_ROUTE_GAP_STATS=1`: el histograma de la diferencia entre cada fallo y el mejor residente no elegido.
  - **Criterio:** ≥ 30 % de los fallos con un residente a < 5 % → escribir B.
- **17.** Los tokens por ventana por tipo de contenido y la aceptación del MTP por posición.
  - **Criterio:** pensamiento + código nuevo > 50 % del tiempo con < 2 tokens por ventana → plan de C.

### Pendientes de Adrián o de otros

- **Orden de Adrián, para el final:** medir **nuestra versión contra Strata 0.1.39 virgen**, y contra la última oficial
  si sale otra. El método de siempre más `logpos-compare`. Así se obtiene el % total ganado.
- **Opcional:** `ada-next` como modelo por defecto de opencode, que hoy va a un modelo en la nube bloqueado por país.
  Ya se le propuso a Adrián, sin respuesta.
- **Decide Adrián:** quitar o no el micro-LLM de :8082.

### Siguiente paso concreto para la nueva sesión

1. `git pull` de la rama.
2. Leer `ENTREGAS.md` desde "Orden 7".
3. Escribir en `ORDENES.md` la "Validación de las órdenes 6, 7, 9, 10, 11 y 14a" con la lectura de la tabla de
   arriba.
4. Añadir las órdenes nuevas:
   - **18:** los slots de la caché de prompts y el log de `RESUME`;
   - **19:** `--expert-cache 4100`;
   - **20:** `STRATA_MTP_HIST` con un contador en el dispositivo.
5. Recordar el orden de prioridad: **15 → 16 → 18 → 19 → 17 → 20 → 8**.
6. Commit y push (con pull antes). Contestar a Adrián en español no técnico.

---

## 7. Índice de documentos (en `docs/fork/`)

| Documento | Para qué |
| --- | --- |
| `PROTOCOLO.md`, `ORDENES.md`, `ENTREGAS.md` | El ciclo de trabajo |
| `RESUMEN_FINAL.md` | El mapa completo y las cifras (lo mantiene el agente) |
| `PLAN_ARQUITECTURA.md` | La dirección nueva (A/B/C) |
| `METODO_MEDICION.md` | Cómo se mide |
| `INTEGRACION_V0139.md` | Qué trajo la 0.1.39 y cómo se integró |
| `RESPUESTA_FETCH_ADMIT.md` | `FETCH_ADMIT` y el tope de pensamiento |
| `RESPUESTA_CACHE_EXPERTOS.md`, `AUDITORIA_CACHE_EXPERTOS.md` | La caché |
| `MEDICION_RONDA5.md` §3 | El primer desglose por etapa |
| `RESPUESTA_RONDA7.md` §6 | El hardware |
| `MEDICION_VRAM_CONTEXTO.md` | VRAM y contexto, 512K frente a 256K |
| `MEDICION_RONDA7.md`, `MEDICION_RONDA8.md`, `RESPUESTA_RONDA8.md` | El PCIe a 11 GB/s |
| `RESPUESTA_SYSTEMONE_AGENTES.md`, `PROPUESTA_ATASCO_THINKING.md` | Aparcados |
| `ESTADO_DESPLIEGUE.md` | Los servicios y clientes, herdr incluido (lo mantiene el agente) |
