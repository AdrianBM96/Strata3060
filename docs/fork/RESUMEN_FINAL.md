# Strata3060: cómo funciona todo y qué conseguimos

Fecha: 2026-10-04. Este documento es el mapa completo: la máquina, cómo funciona el sistema por dentro, y
todo lo que hemos cambiado y medido desde el Strata original.

---

## 1. La máquina

| | |
| --- | --- |
| GPU | **RTX 3060 12 GB** (sm_86, PCIe Gen4 x16) |
| CPU | **i5-12400F** — 6 P-cores, AVX2 + **AVX-VNNI**, sin AVX-512 |
| RAM | **62 GB** DDR4-2133 (módulos de 3200, XMP off) |
| SO | Ubuntu, kernel 6.8.0-142 |
| Red | Tailscale + ssh; los servicios vuelven solos tras un reinicio |

---

## 2. Cómo funciona (por dentro)

### 2.1 Puertos y servicios

| Puerto | Qué | Servicio | Rol |
| --- | --- | --- | --- |
| **8081** | **Strata** (`ada-next`) — Swift 1.5 125B IQ2_XS | `strata.service` (usuario) | **El modelo principal**: texto, código y **visión** |
| **8087** | **System One** (`ada-decide`) | `ada-decide.service` (usuario) | Decisiones rápidas (sí/no/opciones) leyendo logprobs del 125B |
| **4000** | **litellm** | `litellm-proxy.service` (usuario) | Router: los clientes piden `ada-next` / `ada-praxis` (ambos → Strata) |
| ~~8080~~ | ~~BeeLlama.cpp (router GGUF)~~ | ~~router :8080~~ | **RETIRADO**: los modelos pequeños ya no se usan (ficheros intactos) |
| — | **Ventilador GPU al 100%** | `gpu-fan-100.service` + `.timer` (sistema) | Salud de la tarjeta; se re-aplica cada 5 min |

### 2.2 El flujo de una petición

```
cliente/agente → litellm :4000 (ada-next) → Strata :8081 → motor C++/CUDA (engine/strata, v0.1.39)
                                                  ├─ texto: 48 capas MoE, caché de expertos en VRAM,
                                                  │         resto por PCIe/RAM, KV int8 streamed
                                                  └─ imagen: engine/strata-vision (mmproj) en CPU,
                                                            embeddings → motor
System One: una decisión → :8087 → motor (--logprobs) → elige entre opciones en 1 pase
```

### 2.3 La configuración del motor (`strata-swift-iq2_xs.json`)

| Ajuste | Valor | Por qué |
| --- | --- | --- |
| Motor | **v0.1.39** (+ parches System One) | +7,1 % decode / +9,0 % prefill medidos contra la 0.1.38 |
| `--max-context` | **524288 (512K)** | Trabajar en repos grandes (el prompt más largo real: ~87K) |
| `--kv` / `--kv-resident` | `int8` / 32768 | KV en 8 bits; solo 32K celdas en VRAM, el resto en RAM (**99,85% de lecturas aciertan en VRAM**) |
| `--rope-scaling` | `yarn` ×2 | Para llegar a 512K sin degradar (verificado) |
| `--spec 4` + `--mtp` | on | Decodificación especulativa (el borrador acelera) |
| `--prefill auto` | on (elige 6144) | Trocea el prompt por tamaño |
| `--logprobs 32` | on | System One |
| `--vision` | on (`gpu:false`) | Lee imágenes, codificador en **CPU** |
| `STRATA_PF_FUSED=1` | on | Expertos int8 fusionados en el prompt (**+5,2% prefill**) |
| `STRATA_FETCH_ADMIT=1` | on | Los expertos que cruzan por PCIe se quedan en caché (**+2-7% decode**) |
| `reasoning_budget_tokens` | 3072 | El pensamiento se cierra y el modelo actúa (sin tope se atasca) |

---

## 3. De Strata original a la nuestra

Partimos de **Niko1221/Strata 0.1.38**. Esto es lo que añadimos o activamos, **todo medido**:

| # | Mejora | Qué es | Ganancia / veredicto |
| --- | --- | --- | --- |
| 1 | **System One** (`--logprobs N` + `ada-decide` :8087) | El motor emite los logprobs de los N tokens más probables; `ada-decide` responde preguntas de decisión en **1 pase** (contrato Jev: `choice`/`score`/`noul`) | **~0,9 s/pregunta**, sin generar JSON y validarlo |
| 2 | **Bucle de autoaprendizaje** (`systemone.db`, `s1_learn.py`) | Registra cada decisión, audita el 5%, **System Two** etiqueta solo (sin etiquetas humanas), calibración aprendida | Funcionando; **cazó una decisión errónea** en pruebas |
| 3 | **Kernel IQ2_S por bloques** (`row_dot_iq2s`) | Kernel AVX2 nuevo para el tipo IQ2_S (34 de las 48 capas) | **Bit-idéntico** (test `iq2s_avx2_test`); **+1,4%** end-to-end |
| 4 | **`STRATA_PF_FUSED=1`** | Expertos int8 fusionados en el camino de prompt (opt-in en nuestro pack nativo) | **+5,2% de prefill**; puerta de calidad PASADA |
| 4b | **`STRATA_FETCH_ADMIT=1`** | Los expertos que cruzan por PCIe se quedan en la caché (cambio de motor de Claude) | **+2-7% de decode**; calidad PASADA |
| 4c | **Tope de pensamiento 3072** | `reasoning_budget_tokens` por defecto: cierra el pensamiento y el modelo actúa | Las tareas con tools pasan de atascarse a actuar siempre |
| 4d | **Motor v0.1.39** | Upstream + parches System One | **+7,1% decode / +9,0% prefill** contra la 0.1.38 |
| 5 | **Contexto 512K** (KV `int8` + YaRN) | 16× el contexto por defecto, con KV en 8 bits y streaming | **512K**, sin degradación medible de calidad |
| 6 | **Visión en Strata** (fase A) | El propio Strata lee imágenes (codificador en CPU, carga perezosa) | **5 s/imagen** nueva, 1,6 s cacheada; **texto intacto** |
| 7 | **Ventilador GPU al 100%** | Servicio + timer que lo fija y re-aplica | Quita el *thermal slowdown* (26-33 s → 0-1 s), −4-6 °C; mismos tok/s |
| 8 | **Despliegue como servicios** | `strata`, `ada-decide`, `litellm` (usuario) + `gpu-fan` (sistema), todos `enabled` | Vuelven solos tras un reinicio; `linger` activo |
| 9 | **Banco de medida + protocolo** | `bench-chat`, `bench-prefill`, `env-ab`, `logpos-compare`, `native_expert_parity`… y el protocolo anti-deriva térmica | Todo lo de arriba es **medido**, no estimado |

### Lo que probamos y **descartamos** (medido, sin ganancia o con coste)

`--spec 8` (era deriva térmica), `--pcie-mode dma` (neutro), `--pool-workers 10` (neutro), `STRATA_ADAPT_NOWAIT`
(térmico), `idle=poll` (neutro), límite de potencia 150 W (neutro), y del fork `architectds`: `GR_DOWN_MAX4`
(+1,2%, ruido), AVX-VNNI (bit-idéntico pero ruido), **CPU assist** (+5,7% prefill pero **degrada la calidad**),
`HIT_GY` (ruido), chunks por tamaño (nuestro `auto` ya elige 6144).

**Retirado además:** los modelos pequeños locales del **:8080** (tiel, qwopus, ornith, neohorse…) y **nex-mini**
— la 3060 queda **entera para Strata**. Ficheros y stack intactos (reversible con
`systemctl --user enable --now ada-router.service` y restaurando las entradas de litellm).

---

## 4. Números finales (RTX 3060)

| Métrica | Valor |
| --- | --- |
| **Decode** (texto general) | **~47-51 tok/s** |
| **Decode** (agente copiando código, B4) | **~56 tok/s** (98% de aceptación del borrador) |
| **Prefill** (lectura de prompt) | **~990 tok/s** en 128K (con `PF_FUSED` + 0.1.39) |
| **Contexto** | **524.288 tokens (512K)** |
| **Decisión (System One)** | **~0,9 s** por pregunta distinta |
| **Imagen** | **~5 s** nueva / **1,6 s** cacheada |
| VRAM con todo cargado | ~627 MiB libres (caché de expertos 4,97 GiB) |
| Acierto de caché de expertos | **62-78%** |

---

## 5. El resumen en una frase

Partimos de Strata 0.1.38 tal cual y le hemos añadido **una cabeza de decisión propia con autoaprendizaje
(System One)**, un **kernel de CPU más rápido y bit-idéntico**, **+5,2% de prefill** gratis, **512K de
contexto**, **visión integrada** y una **operación que se recupera sola**; y hemos medido y **descartado** con
evidencia todo lo que no aportaba. El hardware y la configuración quedan **cerrados**.
