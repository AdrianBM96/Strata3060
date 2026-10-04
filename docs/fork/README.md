# Strata3060: el fork

Este fork (`AdrianBM96/Strata3060`) parte de [Niko1221/Strata](https://github.com/Niko1221/Strata) 0.1.38
(commit `99f3dbd`) con un objetivo: **que Strata sea lo mejor posible en una RTX 3060** (12 GB, y la de 8 GB) con un
PC barato: CPU AVX2 sin AVX-512, DDR4, 32-64 GB de RAM y a menudo PCIe 3.0. Más velocidad y más contexto, **sin
perder calidad**.

Estado (2026-10-03): **auditoría y plan**. Cinco agentes revisaron el código en paralelo, cada uno con un frente:
VRAM, kernels CUDA, CPU/RAM/PCIe, decode y contexto, e instalador. Esta rama incluye además una **modificación propia del motor** (System One / `--logprobs`,
ver `CONFIG_MEDIDA_3060_Y_SYSTEMONE.md` y su parche) y las **primeras cifras medidas en una RTX 3060**
reales. El resto del plan sigue sin cambios en el motor ni en el instalador; las estimaciones van marcadas
como tales.

| Documento | Qué contiene |
| --- | --- |
| **[RESUMEN_FINAL.md](RESUMEN_FINAL.md)** | **El mapa completo**: la máquina, cómo funciona el sistema por dentro (puertos, flujo, config), la tabla de **avances desde el Strata original** con su medición, y los números finales |
| **[PLAN_MAESTRO.md](PLAN_MAESTRO.md)** | **El plan completo**: qué se puede garantizar y qué no, el método común (banco fijo, estadística, puerta de calidad), decode y prefill con regla de parada, y System One frente a Jev: más rápido, cabeza de decisión propia, abstención con garantía conformal y autoaprendizaje |
| **[PLAN_RTX3060.md](PLAN_RTX3060.md)** | Qué limita a una 3060, qué esperar hoy, y el plan por fases (medir, configuración, CPU, VRAM, MTP, kernels) con ganancia estimada, riesgo y esfuerzo |
| [informes/](informes/) | Los 5 informes completos, con citas `archivo:línea` |
| [informes/prototipos-cpu/](informes/prototipos-cpu/) | Prototipos de kernels AVX2 medidos (IQ2_S por bloques, Q2_0 en planos) y sus arneses |
| [informes/simulaciones/](informes/simulaciones/) | Simulaciones de la longitud del borrador MTP y de la VRAM |
| **[CONFIG_MEDIDA_3060_Y_SYSTEMONE.md](CONFIG_MEDIDA_3060_Y_SYSTEMONE.md)** | La configuración real medida en una 3060 (decode, prefill, contexto, latencias), la convivencia con llama.cpp, y la **modificación del motor para System One** (`--logprobs N` + `ada-decide`), con el parche re-aplicable de 84 líneas |
| **[REVISION_CONFIG_3060.md](REVISION_CONFIG_3060.md)** | Revisión de esa configuración: qué cede inteligencia sin decirlo (YaRN, KV `q4_0`), qué ajustes y A/B dan más velocidad, y cómo bajar la latencia y calibrar System One |
| **[AUDITORIA_REVISION_3060.md](AUDITORIA_REVISION_3060.md)** | Auditoría de la revisión anterior: veredicto de cada punto, con las mediciones que la sostienen o la refutan |
| **[RESPUESTA_A_REVISION.md](RESPUESTA_A_REVISION.md)** | Respuesta al revisor, punto por punto: sus correcciones verificadas, lo medido en la 3060 (`draft_vocab`, `--spec 8`), el aviso de `escalate` y las respuestas a su lista de siguientes pasos |
| **[MEDICION_RONDA3.md](MEDICION_RONDA3.md)** | Ronda 3: el kernel IQ2_S medido (bit-idéntico, x1,66 en el i5, **+1,4 %** end-to-end) y la prueba de que **`calibrate` empeora** las decisiones en el 125B (resta señal, no sesgo) |
| **[MEDICION_RONDA4.md](MEDICION_RONDA4.md)** | Ronda 4: la calibración corregida **funciona** en el 125B (`escalate` por acuerdo), el desglose de la ronda (CPU 37,8 %), y **YaRN sin diferencia medible** con `logpos-compare` (99,0 % top-1) |
| **[MEDICION_RONDA5.md](MEDICION_RONDA5.md)** | Ronda 5: **YaRN no degrada** (3 textos: neutro en código/documento, mejor en conversación), A/B de sistema (gobernador, relojes, SMT), desglose de P, y **la petición a Claude** (F2 del bucle de autoaprendizaje, V2 del motor, F0 del banco) |
| **[METODO_MEDICION.md](METODO_MEDICION.md)** | **Fallo de método**: la misma config dio 40,25 y 41,50 (3 % de deriva térmica). Mis A/B por bloques no son concluyentes; el protocolo correcto (alternar + reiniciar) desde aquí |
| **[MEDICION_RONDA6.md](MEDICION_RONDA6.md)** | Ronda 6: **V2 verificado** (respuestas coinciden; velocidad sin diferencia), el **bucle de autoaprendizaje desplegado y funcionando** (se etiqueta solo, y cazó un error), `bench.py` en uso, y las dos correcciones de Claude confirmadas |
| **[PERF_EXPERTOS.md](PERF_EXPERTOS.md)** | Perfil `perf` de los hilos de expertos (lo que pidió Claude): `ExpertPool::worker` 55 % (casi todo self), sus kernels AVX2 reales, los tipos de cuantización y mi lectura |
| **[MEDICION_RONDA7.md](MEDICION_RONDA7.md)** | Ronda 7: **PCIe Gen4 x16 confirmado** (pero la sonda da 11 GB/s), **B4**: el agente copiando código va a **53 tok/s con 99 % de aceptación** (el borrador por búsqueda vale ~10 %), y los kernels de CPU con 1 token quedan **agotados** |
| **[MEDICION_RONDA8.md](MEDICION_RONDA8.md)** | Ronda 8: **el PCIe es la plataforma** (Gen4 x16, CESta limpio, sin replays ni IOMMU, RAM 2133 doble canal) y el desglose de `dma` (`waitB` −12 %, neto dentro del ruido). Aviso: `idle=poll` en el cmdline |
| **[MEDICION_RONDA9.md](MEDICION_RONDA9.md)** | Ronda 9: **idle=poll no cambia nada** (A/B entre reinicios, mediana 41,80 en ambos; confundido del kernel 139->142) - restaurado; y la **RAM** de DDR4-3200 corriendo a 2133 (XMP off), membw 29,8 GB/s |
| **[RESPUESTA_RONDA9.md](RESPUESTA_RONDA9.md)** | Respuesta de Claude: XMP necesita la BIOS, pruebas solo-remoto y la herramienta membw |
| **[RESPUESTA_RONDA6.md](RESPUESTA_RONDA6.md)** | Respuesta de Claude: tres fallos del bucle corregidos (System Two cede ante peticiones, auditoría sin sesgo, cola persistente) |
| **[MEDICION_RONDA2.md](MEDICION_RONDA2.md)** | Ronda 2: verificación de las correcciones, `draft_vocab en` medido, `--spec 8` (+5,1 %) y el aviso de que `escalate` dispara al revés en el modelo real |
| **[RESPUESTA_RONDA3.md](RESPUESTA_RONDA3.md)** | Ronda 3: el `escalate` "al revés" como sesgo de letra (y la prueba que lo decide), YaRN medido token a token con `logpos-compare.py`, y el kernel AVX2 de IQ2_S ya en el motor con su test bit a bit y su protocolo de A/B |
| **[RESPUESTA_RONDA4.md](RESPUESTA_RONDA4.md)** | Ronda 4: el +1,4 % del kernel no es significativo (y qué medir antes de otro kernel), la calibración corregida para restar solo el sesgo de letra, y `escalate` por acuerdo entre permutaciones en vez de por margen |
| **[NOTA_DECODE_RONDA5.md](NOTA_DECODE_RONDA5.md)** | Qué más probar para subir el decode: lectura correcta del desglose (ping-pong GPU densa / CPU expertos, acierto de VRAM 74,6 %), A/B de sistema de minutos (relojes, gobernador, SMT, recalibrar, modo PCIe, páginas de 2 MB) y los cambios de motor candidatos (GR en 8 bits, kernels de P) |
| **[RESPUESTA_RONDA5.md](RESPUESTA_RONDA5.md)** | Ronda 5: V2 en el parche (la decisión se lee en la primera ventana), el banco fijo `ops/bench.py`, el bucle de autoaprendizaje `ops/s1_learn.py` (registro, System Two, calibración aprendida, garantía conformal) y qué medir |
| **[RESPUESTA_RONDA6.md](RESPUESTA_RONDA6.md)** | Ronda 6: tres fallos del bucle de aprendizaje corregidos (System Two **cede Strata** si otra petición espera, la garantía se mide **solo sobre la muestra auditada**, la cola sobrevive a un reinicio) y el perfil `perf` que hace falta para elegir el siguiente kernel de CPU |
| **[RESPUESTA_RONDA7.md](RESPUESTA_RONDA7.md)** | Auditoría completa tras el perfil `perf`: la GPU es el lado largo (`waitB` es la copia PCIe, a ~9 GB/s), el enlace PCIe a comprobar, los ~896 MiB de VRAM que cuestan 512K, el borrador por búsqueda sin medir (B4), los kernels de CPU de 1 token medidos sin mejora, 4 cambios opcionales y los cambios de motor siguientes en orden |
| **[RESPUESTA_RONDA8.md](RESPUESTA_RONDA8.md)** | Ronda 8: la sonda PCIe mide bien (memoria fijada, DMA), así que los 11 GB/s en un Gen4 x16 son de la máquina y cómo encontrarlo; el desglose en modo `dma` que decide el primer cambio de motor; y lo pendiente de la ronda 7 por orden |
| **[RESPUESTA_RONDA9.md](RESPUESTA_RONDA9.md)** | Ronda 9, sin acceso físico al PC: la RAM a 2133 (XMP, solo en persona) y cómo medirla ya (`ops/membw.c`), `idle=poll` y el límite de potencia de la GPU probados en remoto, lo que sigue en paralelo y la lista para final de mes |
| **[RESPUESTA_RONDA10.md](RESPUESTA_RONDA10.md)** | Ronda 10: la RAM a 2133 no frena a Strata (CPU al 54 % de la RAM, PCIe por debajo) y se cierra; `idle=poll` cerrado; lo pendiente es enfriar la GPU sin reiniciar (ventilador por NVML, límite de potencia, `hp-wmi`), medido con los contadores de frenado |
| **[MEDICION_RONDA10.md](MEDICION_RONDA10.md)** | Ronda 10 medida: el **ventilador de la GPU al 100% quita el *thermal slowdown*** (26-33 s -> 0-1 s) y baja 4-6 C **sin cambiar los tok/s** -> **adoptado fijo al 100%** (servicio de sistema + timer cada 5 min); el límite de potencia (150 W) tampoco ayuda; y **`STRATA_PF_FUSED=1` = +5,2 % de prefill** (salida emparejada identica), **adoptado** |
| **[ANALISIS_FORK_ARCHITECTDS.md](ANALISIS_FORK_ARCHITECTDS.md)** | Análisis del fork `architectds/Strata` para una sola 3060: qué aplica (PF_FUSED adoptado, CPU assist, AVX-VNNI, MTP chain, GR_DOWN_MAX4) y qué no (layer split, PDL sm_90+, chunks, images on demand) |
| **[PORT_FORK.md](PORT_FORK.md)** | **Seguimiento del port** del fork: método por port (rama, cherry-pick, build sm_86, test bit a bit, A/B 6+6) y tabla de estado (el #1 `GR_DOWN_MAX4` **ya estaba**; el #2 `bce7fbb` está entrelazado con layer-split; el #3 `f42c58f` es portable) |
| **[ESTADO_DESPLIEGUE.md](ESTADO_DESPLIEGUE.md)** | **Estado de despliegue de bazzite** para recuperación: servicios, puertos, cambios adoptados con su medición, estado del repo del motor y reconstrucción rápida tras un reinicio |
| **[MEDICION_VRAM_CONTEXTO.md](MEDICION_VRAM_CONTEXTO.md)** | **VRAM al arrancar y prompt más largo real** (decisión de `--max-context`): el contexto de 512K cuesta **RAM (6,19 GiB), no VRAM**, y el KV streaming acierta el 99,85 % en VRAM → **no hay que tocar `--max-context`** |
| **[MEDICION_VISION.md](MEDICION_VISION.md)** | **Visión en Strata (fase A: codificador en CPU)**: lee bien, 5,0 s por imagen nueva / 1,6 s cacheada, **texto intacto** (3.690 vs 3.696 huecos); `ada-next` en litellm con `supports_vision` |
| **[RESPUESTA_RONDA11.md](RESPUESTA_RONDA11.md)** | Ronda 11: hardware cerrado; la puerta de calidad de `PF_FUSED` con `logpos-compare` en dos peticiones; y el encargo de portar del fork `architectds` (`GR_DOWN_MAX4`, MTP chain, AVX-VNNI, CPU assist), en orden, desactivable y medido |
| **[RESPUESTA_RONDA12.md](RESPUESTA_RONDA12.md)** | Ronda 12: el port cerrado (solo `PF_FUSED`); el código de Claude vive en esta rama y cómo traerlo al motor desplegado; una prueba de un reinicio para `--max-context`; MTP chain aparcado |
| **[RESPUESTA_RONDA13.md](RESPUESTA_RONDA13.md)** | Ronda 13: que Strata lea las imágenes (decisión de Adrián): hoy con el codificador en la CPU y `--lazy` (cambio en `server.py`), y después `--vision-on-demand` del fork |
| **[SYSTEMONE.md](SYSTEMONE.md)** | System One completo: el cambio del motor, la API, operación, actualización y rollback |
| **[ops/](ops/)** | Los ficheros de despliegue (`ada-decide.py`, `fit-calibration.py`, `logpos-compare.py`, `apply-tuning.sh`, `free-vram.sh`, `strata-switch.sh`, `serve-strata.sh`) y sus tests |

## Lo más importante en cuatro líneas

- **En una 3060 manda la CPU, no la GPU.** Con 12 GB, la parte de cada ronda que hace la CPU tapa a la GPU. El mayor
  margen está en los kernels AVX2: dos prototipos dan x1,2-1,3 (idéntico bit a bit) y x2,3-2,6 en el kernel
  [medido]; +10-20 % de decode entre las cuatro mejoras de CPU [est.].
- **El contexto ya lo resuelve el motor:** con KV streaming, 128K ocupan la VRAM de 32K. Falta que el instalador lo
  ofrezca por defecto en tarjetas de 12 GB.
- **El instalador no hace en una 3060 con 32 GB lo que dicen las docs:** instala Q2_0 leyendo del SSD en vez del
  Coder en RAM. Además, un fallo en la prueba de RAM deja la KV en VRAM en un Linux con 64 GB a 128K.
- **Total estimado del plan: +15-30 % de decode** y x4 de contexto por defecto, con la misma calidad. A medir en la
  Fase 0.
