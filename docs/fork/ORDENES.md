# Órdenes para el agente de bazzite

Ver `PROTOCOLO.md`. Los resultados van en `ENTREGAS.md`, con el número de orden.

## 1. Desplegar v0.1.39

Seguid `INTEGRACION_V0139.md`, §3 y §4.

- **Entrega:** las pruebas que pasan o fallan, y la tabla `bench.py compare` de la 0.1.39 contra la 0.1.38. Añadid el
  A/B de cada variable nueva:
  - `STRATA_TOPK_OLD`
  - `STRATA_IQ_STAGE_GRID`
  - `STRATA_SH_STREAM`
  - `STRATA_RING_BYTES`
- **Criterio:** lo que no empeore se queda. Las variables que resten, apagadas en `serve-strata.sh`.

## 2. `STRATA_FETCH_ADMIT`

Sobre la 0.1.39 desplegada, seguid `RESPUESTA_FETCH_ADMIT.md` §3.

- **Primero:** la comprobación de corrección con una respuesta.
- **Después:** `logpos-compare`, B1/B2/B4 alternando, y `--adapt-every 2 --adapt-swaps 32` y `16`.
- **Entrega:** las líneas de acierto y de swaps, la tabla de velocidad y la de calidad.

## 3. Tope de pensamiento

Seguid `RESPUESTA_FETCH_ADMIT.md` §4: 3072 y 4096, con `max_tokens` 8192 y 16384, en 3-4 tareas.

- **Entrega:** la tabla y el `max_tokens` real que mandan los clientes.
- **Criterio:** el tope más alto que actúe siempre.

---

## Validación de Claude de las órdenes 1-3 (2026-10-04)

| Orden | Veredicto | Nota |
| --- | --- | --- |
| 1. v0.1.39 | **VALIDADA.** Se queda, con todo por defecto | +7,1 % decode (p 0,013) y +9,0 % prefill (p 0,008), con IC que no cruzan el 0. Bien cortado el brazo contaminado |
| 2. `STRATA_FETCH_ADMIT` | **VALIDADA.** Se queda, con las flags de hoy | Calidad dentro del ruido. Velocidad +5-7 % en B1/B2/B4, con 3+3 pasadas, pero el mismo signo en las tres pruebas |
| 3. Tope de pensamiento | **VALIDADA: 3072 por defecto** | 4096 solo tras medirlo con tarea larga y el `max_tokens` real (32K) |

## 4. Aplicar el tope 3072

- **Qué:** `reasoning_budget_tokens` 3072 por defecto en la config del servidor, para las peticiones que no traen el
  suyo.
- **Comprobar:** una tarea larga por Claude Code y otra por opencode actúan, y el log muestra el tope aplicado.
- **Entrega:** el diff de la config y las dos líneas del log.

## 5. 4096 con el `max_tokens` real (barato, opcional)

- **Qué:** la tarea larga (TASK.md) con tope 4096 y `max_tokens` 32768, 3 repeticiones.
- **Criterio:** si actúa 3/3, el tope sube a 4096. Si no, se queda en 3072.

## 6. Herramientas no declaradas

El modelo llamó 6 veces a `read_file`, que no estaba en la lista.

- **Qué:** contad en los logs de una semana de uso real cuántas llamadas a herramientas no declaradas hay, y qué hace
  cada cliente con ellas (error, ignorar, bucle).
- **Entrega:** el número y el comportamiento. No cambiéis nada todavía: con eso decido si el servidor debe filtrarlas
  o devolver un error al modelo.

## 7. Nueva línea base y resumen

- **Qué:** con todo lo adoptado (0.1.39 + `FETCH_ADMIT` + el tope), una tanda de referencia: B1, B2, B4, P3, P4 y S2,
  6 pasadas cada una.
- **Después:** actualizad `RESUMEN_FINAL.md` con la configuración y las cifras nuevas.
- **Entrega:** la tabla. Es la base contra la que se medirá todo lo siguiente.

## 8. Caché: dos políticas más en el simulador (solo simulador, sin tocar el motor)

Base: `fetch-admit + adapt` de `ops/cache-sim.py`, lo desplegado hoy. Añadid dos variantes, cada una con su test en
`test_cache_sim.py`:

**a) Dos memorias.**

- Además del contador de hoy (corto: × `decay` cada `every` ventanas), uno largo con decaimiento lento (×0,98 en cada
  revisión).
- Puntuación para elegir víctima y candidato: `corto + w × largo`.
- Rejilla: `w` ∈ {0,5; 1; 2; 4} y decaimiento largo ∈ {0,95; 0,98; 0,995}.
- **Idea:** no echar especialistas que se usan siempre solo porque no salieron en las últimas palabras.

**b) Coste por tipo.**

- Un fallo de un IQ2_S cuesta más CPU que uno de IQ1_M. Medid el coste por experto y tipo en el i5, por ejemplo con
  `native_expert_bench` o `native_expert_parity`: µs por experto y token de IQ2_S, IQ2_XXS e IQ1_M en gate/up, más el
  `down` q2_0.
- Sacad el tipo de cada capa del log de carga.
- El uso de cada experto se multiplica por el coste de su capa al elegir víctima.
- Columna nueva **`cpu ms/w`**: los fallos que calcula la CPU (sin la parte PCIe), multiplicados por su coste.

**Entrega:** la tabla del simulador con vuestra traza larga, con `fetch-admit + adapt` como referencia y lo mejor de
a), b) y a+b, mirando `eng.hit`, `cpu ms/w`, `swaps/w` y `free/w`.

**Criterio:** solo propongo cambio de motor si alguna gana ≥ 2 puntos de `eng.hit`, o ≥ 5 % de `cpu ms/w`, sin más
swaps. Si ninguna, la caché se da por cerrada.

## 9. Perfil de la GPU en la configuración nueva

Con todo lo adoptado (0.1.39 + `FETCH_ADMIT` + el tope), una respuesta de B1 y otra de B2 con
`STRATA_VERIFY_PROFILE=1 STRATA_DECODE_TIMING=1`, y `STRATA_VERIFY_NODES=1` una vez.

**Entrega:**

- el desglose por etapa en ms por ventana, como en `MEDICION_RONDA5.md` §3;
- tokens por ventana;
- el número de nodos del grafo.

**Opcional, si tenéis `ncu`:** el ancho de banda conseguido (DRAM throughput) de los 3 kernels más largos.

Con esto elijo el siguiente cambio de motor que no altere ningún bit. Los candidatos: MMVQ con 2 filas por bloque, la
copia PCIe en paralelo con los aciertos, el *pipelining* del GR y la rejilla de aciertos.

## Validación de la orden 4

**VALIDADA.** El tope 3072 se queda. La verificación por efecto (~12K caracteres de pensamiento, frente a 31K+) y las
dos tareas largas a 25/25 bastan.

**Nota aparte, sin orden:** opencode sin `--model` va a un modelo en la nube bloqueado por país. Si Adrián quiere,
poned `ada-next` como modelo por defecto de opencode, para que no falle al arrancar sin argumentos.

---

## Órdenes 10-14: lo que queda sin medir (2026-10-04)

Repaso de todo `docs/fork/`. Quedan palancas sin medir. Ninguna cambia el texto que sale.

**Orden de ejecución:**

- La 10 se puede hacer ya: solo lee.
- La 11, después de la 7, contra la línea base nueva.

## 10. Reutilización de prefijos en agentes (R5 de `PLAN_MAESTRO.md`, nunca medida)

Un agente reenvía casi todo el contexto en cada turno. El prefill que no se hace ahorra más que cualquier kernel.

- **Qué:** de los logs de una semana real (puede ir junto a la 6), por petición:
  - cliente;
  - tokens del prompt;
  - `RESUME n` (`generate.cpp:6701`);
  - tokens leídos de nuevo;
  - TTFT.
- **Para cada turno con < 90 % reutilizado,** la causa:
  - compactación;
  - un cambio pronto en el prompt (fecha, `git status`, lista de herramientas, orden de los mensajes);
  - expulsión de la caché porque otro cliente (System One, un subagente) ocupó el hueco;
  - u otra.
- **Entrega:**
  - % de tokens de prompt reutilizados por cliente;
  - segundos de prefill que se gastaron en releer;
  - el reparto de causas;
  - los `--prompt-cache`, `--prompt-cache-every` y `--prompt-cache-root` actuales.
- **Después decido:** más huecos, `--prompt-cache-every` menor, o un arreglo en el servidor si la causa es un prefijo
  que cambia.

## 11. VRAM: quién la usa y si caben más expertos (después de la 7)

Hoy quedan 627 MiB libres, y 450 MiB más dieron +318 huecos y +2,8 % de decode (`MEDICION_VRAM_CONTEXTO.md`).

1. `nvidia-smi --query-compute-apps=pid,name,used_memory --format=csv` y `nvidia-smi`: ¿hay algún proceso de escritorio
   (Xorg, gnome-shell, kwin, Steam) que use la 3060? Si lo hay, decidme cuánto ocupa antes de quitar nada.
2. **El pico real:** muestreo de `nvidia-smi --query-gpu=memory.used -lms 100` durante:
   - el prefill más largo que tengáis (86K, o `bench-prefill.py` a 128K);
   - una imagen;
   - B1.

   **Entrega:** el mínimo de VRAM libre.
3. **Si el mínimo libre es > 400 MiB:** `--expert-cache <huecos de hoy + (mínimo libre − 256 MiB) / tamaño de hueco>`.
   A/B contra la 7 con B1, B2 y B4, y la prueba de pico otra vez: ningún OOM.
4. **`--kv-resident 16384`** (R4, pendiente desde la ronda 5):
   - la VRAM que libera;
   - los huecos que gana;
   - B1/B2;
   - el acierto de bloques KV y el decode con 32K y 86K de contexto (por si el streaming del KV pierde).
- **Criterio:** se queda lo que gane sin OOM en el pico y sin bajar el decode a 86K.

## 12 y 13: RETIRADAS

Adrián tiene razón, ya estaban vistas:

- **12 (PCIe):** `RESPUESTA_RONDA8.md` §2 ya comprobó que la sonda usa memoria fijada y DMA. Los 11 GB/s son de la
  máquina, y lo que falte está en la BIOS, que no se puede tocar en remoto. El trabajo sigue siendo **esconder la
  copia**, no acelerarla (orden 9).
- **13 (reloj de la VRAM):** se podría poner por software, pero `RESPUESTA_RONDA7.md` §6 ya concluyó que importa poco:
  las GEMV usan el 44-61 % del ancho de banda y las limita el kernel, no la memoria. El reinicio remoto no es problema
  (ya comprobado), así que **solo se reabre si el perfil de la orden 9 muestra kernels pegados al ancho de banda**. Lo
  que hay ahí se busca con la orden 9.

## 14. Caché de expertos: lo que queda por medir

La caché **no está cerrada**:

- el motor acierta 73-78 %;
- el techo teórico del simulador (Belady) está en ~85 %.

La orden 8 prueba dos políticas. Esta mira las otras dos vías. Solo hay que medir, sin cambiar nada en producción.

**a) La capa del borrador (MTP): 836 MiB con sus 512 expertos siempre en VRAM.**

1. Mirad en el código si el motor ya cuenta el uso de cada experto de esa capa (solo leer; `generate.cpp:3011` es
   donde se monta).
2. Si no lo cuenta, añadid un contador opcional:
   - `STRATA_MTP_HIST=1`;
   - un `uint32` por experto, sumado en el host donde se enruta el borrador;
   - al salir, se vuelca a un fichero.

   Con la variable apagada no debe haber ningún coste.
3. Una sesión real de agente de ≥ 30 minutos.

**Entrega:** qué fracción de los usos cubren los 128, 256 y 384 expertos más usados, y la aceptación del borrador de
esa sesión.

**Criterio:**

- **Si 256 cubren ≥ 95 %,** escribo el cambio: dejar residentes solo los más usados del borrador. Liberaría ~400 MiB,
  unos 300 huecos para el modelo principal. Con +318 huecos medimos +2,8 % de decode.
- **Si no, se descarta.**

**b) ¿Se puede adivinar qué expertos pedirá la capa siguiente?** (solo simulador, con vuestra traza larga)

- En `cache-sim.py`, un modo `--predict`:
  - con la primera mitad de la traza, una tabla de coincidencias "experto e en la capa L → experto f en la capa L+1";
  - con la segunda mitad, predecir los k más probables de L+1 a partir de los 10 de L, con k = 10 y 20;
  - medir la precisión y la cobertura **solo sobre los fallos** de la caché.

  La referencia: "los mismos que el token anterior en esa capa".
- Con su test en `test_cache_sim.py`.

**Entrega:** la tabla de precisión y cobertura de los dos predictores, con k 10 y 20.

**Criterio:** si la tabla acierta ≥ 60 % de los fallos con k = 10 **y** la orden 9 muestra que la espera de la copia
PCIe (`waitB`) está a la vista, diseño la precarga:

- los bytes son los mismos;
- la copia empieza antes, mientras la GPU calcula la parte densa de la capa anterior.

Si no se cumplen las dos condiciones, se descarta.
