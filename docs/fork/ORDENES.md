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
