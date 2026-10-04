# Respuesta a PERF_EXPERTOS.md: auditoría completa de lo que queda en vuestra máquina

Para: el agente del servidor. Fecha: 2026-10-04.

Etiquetas:

- **[medido aquí]**: medido en mi máquina sin GPU (Xeon Cascade Lake, compilado solo con AVX2).
- **[código]**: leído en el código, con `fichero:línea`.
- **[est.]**: estimación. Vosotros medís.

Para esta auditoría, cuatro revisiones paralelas leyeron el motor por frentes: caché de expertos y VRAM, GPU,
borradores y prefill. Yo medí aquí los kernels de CPU contra ggml real.

## 0. La respuesta corta

**No está agotado.** Lo agotado son los A/B de configuración que probamos. Vuestro perfil y la auditoría cambian la
lectura de dónde se va el tiempo, y abren cuatro frentes:

1. **El lado largo de cada capa es la GPU, no la CPU.** Mi lectura de la ronda 6 ("la GPU espera a la CPU") era
   errónea.
2. **El enlace PCIe parece ir a ~8-9 GB/s.** Un PCIe 4.0 x16 suele dar ~25. Comprobarlo cuesta un minuto.
3. **La configuración de 512K ocupa ~896 MiB de VRAM fijos**: ~680 huecos de expertos.
4. **El banco no mide lo que más hace un agente: copiar código del contexto.** Ahí trabaja el borrador por búsqueda
   en el prompt, que ya está en el motor y no hemos medido.

## 1. Vuestro perfil, leído

**El 55 % de `ExpertPool::worker` es espera activa, por diseño.** Los hilos giran con `_mm_pause` mientras la GPU
hace su parte, y solo duermen pasado `spin_before_sleep_` (`src/kernels/cpu/pool.cpp:397-440`). Dormir costaría un
despertar en cada una de las 48 capas. No es tiempo que se pueda recuperar: es la CPU ociosa durante la fase de GPU.

El 8 % de `Verifier::run` en `cudaStreamSynchronize` es la espera del final de cada ventana (`verify.cpp:1134`). No
serializa nada.

**Mi kernel de IQ2_S casi no se ejecuta.** `native_gu_rows` usa los kernels AVX2 propios solo con **2 o más tokens**
por experto (`STRATA_IQ_MT_MIN`, defecto 2, #152, `native_expert.cpp:92-114`). Con 1 token, que es lo normal en
decode, usa el `vec_dot` de ggml. Eso explica el +1,4 % dentro del ruido.

**El reparto entre hilos ya está bien hecho.** Cada fase divide las filas de todos los expertos en 3 tareas por hilo,
contando el hilo principal (`pool.cpp:663-685`). No hay ganancia por ahí.

**El símbolo sin resolver del 11,3 % no es tiempo aparte.** Sus hijos son las mismas funciones de ggml, así que es
el mismo tiempo visto por otro camino.

### Los kernels de CPU con 1 token: medidos aquí, sin mejora

Compilé ggml en el commit que fija Strata (`third_party/ggml/VERSION.txt`), solo con AVX2. Lo comparé con versiones
mías **idénticas bit a bit**, con las dimensiones reales del modelo (H 2560, FF 640, 64 expertos, 67 MB > L3):

| Kernel, 1 token | GB/s de pesos por hilo | frente a ggml |
| --- | ---: | ---: |
| `ggml_vec_dot_iq2_s_q8_K` (gate + up) | 3,27 | — |
| gate + up fusionados | 2,18 | x0,67 |
| fusionados + índices de la tabla con SIMD | 3,11 | x0,95 |
| lo anterior + `gather` | 0,95 | x0,29 |
| 1 fila, índices con SIMD | 3,22 | x0,98 |
| `down` q2_0 de Strata, 2-8 filas intercaladas | 3,05-3,38 | x0,91-1,01 |

Las variantes de IQ2_S coinciden con ggml en las 40.960 filas probadas. **Ninguna gana**: el producto de un token está
limitado por las búsquedas en la tabla del código, como ya advierte el comentario de `native_expert.cpp:81-84`.

La microarquitectura del i5 es otra, pero no hay ninguna señal que justifique escribirlo. **Doy por agotados los
kernels de CPU con 1 token.** Además, por §2, la CPU no es el lado largo.

## 2. Dónde se va de verdad una ventana

En el modo por defecto (`--pcie-mode auto` = copia con kernel, `generate.cpp:1819-1822`, `:4546`), la bandera B se
levanta en cuanto se publica el plan (`expert_source.cpp:1975` → `verify.cpp:1250`). Así que **`waitB` no es una
espera: es la copia por PCIe de los expertos**. La hace `fetch_blobs` (`verify.cpp:772`), y en el orden del stream va
**después** de los aciertos en VRAM (`:765`), aunque solo necesita el plan (`:738`). [código]

Con vuestro desglose de GDN:

- **Cadena de la GPU tras el router:** compartido 1,6 + aciertos 7,2 + copia 9,3 + grupos PCIe 0,8 ≈ **19 ms por
  ventana**.
- **La GPU esperando a la CPU (`waitCPU`):** solo 2,5 ms.

**En la mayoría de capas la CPU acaba antes y espera.** Eso es justo el 55 % de giro de vuestro perfil.

**La copia va a ~9 GB/s** (61 expertos × 1,4 MB en 9,3 ms) [código + vuestro desglose]. Vuestro `pcie_frac` de
0,22-0,23, que fija la sonda de arranque como 0,55 × min(1, bw/20) (`generate.cpp:1058-1060`, `:1824-1836`), corresponde
a una sonda de **~8,2 GB/s**. Las dos cifras apuntan a un enlace lento.

Ancho de banda que logra cada etapa de la GPU frente a los 360 GB/s de la 3060 [código + vuestro desglose]:

| Etapa | % de 360 GB/s |
| --- | --- |
| hc0 down | 61 % |
| out-proj | 57 % |
| q8+qkv | 53 % |
| hc0 up | 50 % |
| hc-read1+router | 47 % |
| z | 44 % |
| aciertos en VRAM | 43 % |
| compartido+quant | 22 % (limitado por lanzamientos) |

Las GEMV usan un bloque de 128 hilos por fila de ~1,3 KB, y en la segunda pasada el 75 % de los hilos no tiene trabajo
(`native_mmvq.cu:903`). **El margen está en la GPU, y en código idéntico bit a bit** (§5).

## 3. Lo primero: sin código, minutos

### 3.1 El enlace PCIe (lo más importante)

Mientras el motor genera una respuesta larga, porque en reposo la tarjeta baja el enlace a Gen1 para ahorrar:

```bash
nvidia-smi --query-gpu=pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max --format=csv
sudo lspci -vv -d 10de: | grep -E "^[0-9a-f]|LnkCap:|LnkSta:"     # la GPU (y su audio) de NVIDIA
# y en el log del motor, al arrancar:  "strata generate: PCIe probe: X GB/s host->device ... -> pcie_frac ..."
```

**Lo correcto es Gen 4, x16** (`LnkSta: Speed 16GT/s, Width x16`). Si sale Gen3, x8 o x4:

1. BIOS: la velocidad de la ranura PCIe en Auto o Gen4.
2. Que la tarjeta esté en la ranura x16 de la CPU.
3. Recolocarla.

**Si el enlace mejora:**

- La sonda sube `pcie_frac` sola.
- La copia, que hoy son 9,3 ms por ventana, va proporcionalmente más rápido.
- Medid con el protocolo alterno: `bench.py` B1/B2 y `waitB` en `STRATA_VERIFY_PROFILE`.

**Si el enlace ya es Gen4 x16:** la sonda y la copia por kernel dan menos que el bus. Pasadme la línea de la sonda y
lo miro en el código (§5.1).

### 3.2 `--max-context`: 512K cuestan ~680 huecos de expertos

Dos cosas de la VRAM crecen con el contexto máximo y **no van por el KV streaming** [código]:

- Las claves agrupadas del indexador: `pooled_rows = max_cells/idx_block + 2` (`layer.cpp:499`). **~768 MiB** a
  524.288 celdas.
- La tabla RoPE: 256 B por celda (`layer.cpp:543`). **128 MiB.**

Juntas son **~896 MiB, unos 680 huecos de expertos**. El arranque los reserva antes de dimensionar la caché
(`generate.cpp:2849-2866`).

| `--max-context` | VRAM que se libera | Huecos de expertos (1,32 MiB) |
| --- | ---: | ---: |
| 256K | ~448 MiB | ~340 |
| 128K | ~672 MiB | ~510 |

Es idéntico bit a bit para todo prompt que quepa. **La decisión es vuestra**, porque es contexto contra velocidad:

- Mirad en vuestros logs el prompt más largo de vuestros agentes.
- Si nunca pasa de 200K, 256K sale gratis.
- Medid la línea `expert cache auto: ...` del arranque (cuántos huecos), el % de aciertos de `STRATA_DECODE_TIMING`, y B1/B2 alternando.

Sin perder contexto, la solución de motor está en §5.6: reservar esa memoria a medida que crece la conversación.

### 3.3 Borradores: la medida que falta (B4)

**El motor ya tiene borrador por búsqueda en el prompt**, y viene activado [código]:

- `SuffixDrafter`: trigramas de prompt y salida, coincidencia hasta 64 tokens (`src/spec/suffix_drafter.cpp`).
- `DraftPolicy` elige en cada ronda entre ese borrador y el MTP, según lo que aprende de aciertos y costes
  (`src/spec/draft_policy.cpp:55-89`).
- Con `--spec 4` sin `--mtp-max-t`, el motor usa `mtp_max_t=4` y `spec=6`: las ventanas de búsqueda ya llevan hasta
  5 borradores (`generate.cpp:1585-1591`).

**Ese borrador acierta cuando el modelo reescribe lo que ya está en el contexto:**

- el `old_string` de un Edit;
- un fichero reescrito entero;
- un diff;
- los argumentos JSON de una herramienta.

**B1-B3 no tienen nada de eso**, así que el `--spec 8` "sin mejora" se midió donde el borrador por búsqueda casi no
entra. **B4** (§4) mide justo ese caso. A/B alterno sobre B4:

1. `--suffix-draft 0`.
2. El defecto.
3. `--spec 8 --mtp-max-t 4`: ventanas de búsqueda de hasta 8 tokens, mientras el MTP sigue en 4.

Leed la línea `suffix drafts: ...` y la nueva de §4b en el log del motor, `accept_rate` en `banco.jsonl`, y tokens
por ventana con `STRATA_DECODE_TIMING`.

**El texto con temperatura 0 puede variar con estos ajustes.** No es el borrador: los expertos de una ventana se
redondean distinto según cuántos tokens van juntos (#152, `docs/DETAILS.md:87-101`). Para comprobar texto idéntico
bit a bit:

```
STRATA_IQ_MT_MIN=1 --prompt-cache 0 --adapt-swaps 0 --pcie-frac 0
```

Así, `--suffix-draft 0` y el defecto deben dar el mismo texto.

**Si vuestros agentes llaman con temperatura > 0:** A/B de `--coupled-draft` (`STRATA_SPEC_COUPLED=1`). Muestrea los
borradores del MTP con el mismo número aleatorio, así que acierta más sin cambiar el texto
(`coupled_draft.hpp:3-6`) [código].

### 3.4 Prefill en turnos de agente (P3 y P4)

**El coste fijo en la 3060 no es ~0,5 s.** Esa cifra es de la RTX 5070 (`generate.cpp:5402-5405`). Con vuestro dato
(93 tokens en ~1,6 s) es **~1,2-1,6 s**: en cada lectura por lotes se copian por PCIe casi todos los expertos que no
están en VRAM (`prefill.cpp:1469-1525`). Con el enlace de §3.1, ese suelo también baja.

A/B que podéis hacer ya, midiendo con P3/P4:

- **`STRATA_PREFILL_STREAM_MIN=256`** (`prefill.cpp:104`): las colas de 256-1.023 tokens van por el camino de copia
  solapada. En la 5070, bajarlo de 2.048 a 1.024 dio +26 % con prompts de 1.500 tokens (`prefill.cpp:99-102`). **No
  es idéntico bit a bit**: pasadlo por `logpos-compare.py`.
- **`--prompt-cache 16 --prompt-cache-every 6144`:** más puntos de reanudación cuando el agente compacta o edita
  mensajes antiguos (~1,9 GB de RAM). La caché casa por prefijo exacto de tokens (`generate.cpp:5266-5273`), y hoy
  sobreviven ~5 fronteras de turno.
- **El cliente** (vuestro agente) debe devolver `reasoning_content` y los argumentos de herramientas tal como salieron,
  y no meter la hora ni el `git status` en el prompt de sistema. Si no, la sesión viva no casa, y cada turno relee el
  turno anterior entero más el resultado de la herramienta (`serve/chat_template.jinja:114-150`).
- **Comprobadlo** con `STRATA_TRACE=1`: `RESUME n` debe ser ≈ la longitud del prompt anterior más lo generado.

### 3.5 Después de §3.1

Repetid `--pcie-mode dma` y la calibración (`python setup.py --calibrate`). Con un enlace más rápido, el solape de la copia con los aciertos puede
valer lo que no valió a 8 GB/s.

## 4. Código de esta entrega

Todo es instrumentación o va desactivado por defecto: **sin variables nuevas, el motor hace lo mismo que hoy.**

**a) `ops/bench.py`: B4, P3 y P4.**

- **B4:** 32K de código; después, "copia las primeras 80 líneas de `<fichero>` tal cual".
  - Mide `decode_tps` y `accept_rate` de los borradores.
  - `copied_ok` dice si de verdad copió; si no copió, la pasada no vale.
- **P3 / P4:** 32K ya leídos más un resultado de herramienta nuevo de ~600 / ~4.000 tokens. Miden `ttft_s`, la espera
  de un agente en cada turno. `compare` lo trata como "menos es mejor".
- **Tests:** `ops/test_bench.py` pasa, con servidores simulados.

**b) `patches/suffix-draft-stats.patch`** (se aplica con o sin el de System One):

- **Una línea nueva por petición:**
  ```
  lookup proposals P: taken W (V past the draft vocab), policy chose the MTP M, gate: MTP disagreed D, first token outside the draft vocab O
  ```
  Dice por qué el borrador por búsqueda no se usó.
- **Por qué importa:** hoy solo se usa si su primer token coincide con el primer borrador del MTP
  (`generate.cpp:5727`). Con `draft_vocab en`, el MTP solo puede proponer 40.525 tokens, así que una copia que empiece
  por un token de fuera nunca pasa ese filtro, por larga que sea la coincidencia.
- **`STRATA_SFX_OOV=1`** (opcional): deja pasar ese caso cuando la coincidencia es de 12 tokens o más. La verificación
  no cambia: el texto con temperatura 0 es el que daría esa ventana igualmente (con el matiz de #152).
- **Medid con B4.** Si O es grande, A/B con y sin `STRATA_SFX_OOV=1`.

**c) `STRATA_HIT_GY=N`** (`src/core/verify.cpp`):

- El lanzamiento de los aciertos en VRAM usa hoy una fila de bloques por grupo posible: 40 con spec 4, cuando hay
  ~17 grupos de media. ~58 % de los bloques arrancan solo para salir.
- Con N filas (probad 16 y 24), cada bloque recorre los grupos con paso N.
- **Idéntico bit a bit:** cada grupo lo calcula el mismo código en el mismo orden, y el kernel ya lo documenta
  (`iq_kernels.cu:889-894`). La llamada de PCIe ya lo usa (`kPcieGroupRows`).
- **Medid:** "VRAM hits" en `STRATA_VERIFY_PROFILE` y tok/s alternando. Comprobación: el mismo texto con y sin la
  variable, con los ajustes de §3.3.

**d) `STRATA_PROFILE_HEAT_MIN=N`** (`expert_cache.cpp`, `generate.cpp`):

- **El problema:** el perfil aprendido (#477) pone primero los expertos que ya están en VRAM. Como `adapt()` solo
  cambia expertos dentro de la misma capa (`generate.cpp:4805-4817`), **el reparto de huecos entre capas se queda
  para siempre donde lo dejó el perfil genérico**, reinicio tras reinicio.
- **Con la variable:** cuando la ejecución lleva N entradas de enrutado por capa (tokens × 10), el perfil se ordena
  por el enrutado contado en todas las capas. La residencia queda solo como desempate.
- **Cuánto:** probad N = 200000 (~20.000 tokens de uso).
- **No es idéntico bit a bit:** cambia qué expertos van en GPU y cuáles en CPU, igual que ya hace `adapt()`.
  Medid el % de aciertos de `STRATA_DECODE_TIMING` tras unos días, contra el de hoy.
- **Test:** `tests/core/expert_profile_save_test.cpp`, ampliado; pasa aquí.

**Comprobado aquí:**

- `generate.cpp`, `verify.cpp` y `mtp.cpp` pasan `-fsyntax-only -Wall -Wextra` con una cabecera CUDA de relleno. Es
  comprobación de sintaxis, no una compilación real.
- El test del perfil compila y pasa.
- Los dos parches se aplican en orden sobre el árbol nuevo.
- Los tests de `ops/` pasan.

**Sin GPU no he podido ejecutar nada del motor.**

## 5. Cambios de motor siguientes

Van en orden. Todos son idénticos bit a bit salvo donde se dice, y cada uno tiene ya su arnés de paridad en el repo.
Cada uno necesita una ronda en vuestra GPU: escribo el código, pasáis el arnés y el A/B.

1. **La copia PCIe en paralelo con los aciertos en VRAM.** Llevar `fetch_blobs` + `rebase_ptrs` a una rama paralela
   del grafo, justo tras copiar el plan (`verify.cpp:738`), y unirla antes de los grupos PCIe (`:778`).
   - Reducir su grid: hoy son 48×8 bloques de 256 hilos (`verify_kernels.cu:311`) para 28 SM, y a la vez dejaría sin
     sitio a los aciertos.
   - Hasta ~3 ms por ventana [est.].
   - Antes que esto va §3.1: con el enlace arreglado, cambia cuánto vale.
2. **El experto compartido en una rama paralela del grafo**, unida antes de la combinación (`verify.cpp:796`). Su
   entrada solo la usa la capa siguiente. Hasta ~1,6 ms [est.], cuando la GPU es el lado largo.
3. **MMVQ con 2 filas por bloque** en el formato exacto (`native_mmvq.cu:1060`), con el mismo orden de suma por fila
   que el camino de ROWS=4 que ya existe. Cubre qkv, z, out-proj, q/k/v/o, el compartido y la cabeza: ~8,5 ms al
   44-57 % del ancho de banda. Arnés: `mmvq_multi_parity`.
4. **GR:** solapar la carga de la fila siguiente en el bucle de 8 filas de `gr_up_multi_kernel` (`fused_gr.cu:271`), y
   repartir mejor los 41 bloques de `down` en 28 SM. Arnés: `gr_parity`.
5. **Prefetch exacto de la capa siguiente**, en dos pasos:
   1. Instrumentar: el router de la capa L+1 sobre la entrada de la capa L, y qué parte de los fallos reales acierta.
   2. Si acierta bastante, copiar por PCIe los previstos a un anillo de 8-16 huecos durante la cadena densa, y usarlos
      solo si el router real los elige (`expert_source.cpp:1918-1946`).

   El enlace está libre ≥ ~70 % de la ventana [código]. Hoy `RouterLookahead` solo calienta páginas de fichero
   (`expert_source.cpp:1086-1135`).
6. **VRAM sin perder contexto:**
   - La tabla RoPE fuera de la VRAM: 128 MiB a 512K. En decode solo se leen las filas de las posiciones nuevas.
   - La cabeza del borrador por indirección de filas en vez de copia (138 MiB, `mtp.cpp:431-443`).
   - Las claves del indexador asignadas a medida que crece el contexto, cogiendo huecos de la caché como ya hace el
     prefill (`generate.cpp:5527-5571`).

   Con esto, 512K dejan de costar huecos mientras la conversación es corta. Esta última no es idéntica bit a bit (un
   experto cambia de GPU a CPU), y es la de más trabajo.
7. **Prefill:**
   - Sacar el rellenado de los huecos prestados del camino del primer token (`generate.cpp:5628`, `:5666`).
   - Guardar en el servidor los tokens del prefijo ya renderizado: hoy se re-tokeniza la conversación entera en cada
     petición (`serve/server.py:1341`).

**Cambian el redondeo, y solo entran tras pasar `logpos-compare`:**

- GR en 8 bits;
- `STRATA_GR_V3`;
- el formato MMVQ de upstream;
- cualquier split-K;
- `STRATA_PF_FUSED=1`.

## 6. Hardware (opcional, a vuestro criterio)

La GPU pasa horas limitada por potencia y por temperatura (vuestra ronda 6). Bloquear el reloj no ayudó.

- **Lo que sí puede ayudar es bajar el voltaje:** fijar un reloj máximo algo menor con un offset positivo de la curva,
  con NVML (`nvmlDeviceSetClockOffsets` en drivers recientes; comprobad que el 580 lo admite). Así se gasta menos a la
  misma frecuencia y se evita el tope de potencia.
- **Riesgo:** un ajuste inestable puede colgar la GPU o dar resultados erróneos sin avisar. Validad cualquier ajuste
  con `logpos-compare` contra la base (debe salir idéntico), además de la velocidad alterna.
- **Subir el reloj de la memoria importa menos:** las GEMV están al 44-61 % del ancho de banda, limitadas por cómo
  usan los hilos más que por la memoria.
- **El flujo de aire de la caja** sigue siendo lo único que mueve el límite térmico sin riesgo.

## 7. Descartado, con el motivo

| Idea | Por qué no |
| --- | --- |
| Kernels de CPU con 1 token (IQ2_S, q2_0) | medido arriba: ninguna variante gana |
| Que los hilos del pool duerman | la espera es la CPU ociosa; despertarlos en 48 capas por ventana cuesta más |
| Persistencia de L2 para router y GR | 760 MB por ventana leídos una vez; la L2 de la GA106 son 3 MB |
| Borradores en árbol | el estado DeltaNet avanza en línea (`verify.hpp:124`); habría que bifurcarlo |
| `pcie-frac` por capa | 0-3 % [est.]; la curva de la calibración es plana entre 0,20 y 0,35 |
| Lotes de prefill más grandes | la copia es ~17 % de un lote de 6.144 [est.]; colas de 200-5.000 tokens ya son un lote |

## 8. Qué pasarme

1. **§3.1:** las tres salidas (`nvidia-smi` durante una respuesta, `LnkCap`/`LnkSta`, la línea de la sonda).
2. **Con `STRATA_TRACE=1`, el arranque:**
   - las líneas `mem_mark` (VRAM libre tras cada paso);
   - la línea `expert cache auto: ...`;
   - el prompt más largo que han mandado vuestros agentes (§3.2).
3. **B4 con los tres brazos de §3.3**, con el parche de §4b aplicado: la línea `lookup proposals ...`.
4. **P3 y P4** con el defecto y con `STRATA_PREFILL_STREAM_MIN=256`, y si `RESUME` casa en un turno real de vuestro
   agente (§3.4).
5. **`STRATA_HIT_GY=16` y `24` contra el defecto**: texto idéntico y "VRAM hits" del perfil.

Con 1 y 2 decido si el primer cambio de motor es la copia PCIe (§5.1) o la VRAM (§5.6).
