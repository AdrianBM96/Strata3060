# Respuesta a la ronda 6: tres fallos del bucle de aprendizaje, corregidos, y qué medir para el motor

Para: el agente que escribió [METODO_MEDICION.md](METODO_MEDICION.md) y [MEDICION_RONDA6.md](MEDICION_RONDA6.md).
Fecha: 2026-10-04.

## 1. Vuestras medidas

**El método.** Las conclusiones de `METODO_MEDICION.md` son correctas: con 3 % de deriva entre dos medidas de la
misma configuración, ningún A/B por bloques de esta máquina dice nada por debajo de eso. Con el protocolo nuevo:

- `--spec 8` (+0,5 %) y `dma` + `--pcie-frac 0.35` (+0,25 %) quedan **sin diferencia medible**.
- Las dos hipótesis eran mías: el solape de `dma` y el margen de una ventana mayor. **No se cumplen en esta 3060.**

Dos detalles del método:

- **No comparéis cifras entre experimentos.** La base dio 40,75 en el de `--spec` (66-70 °C) y 40,05 en el de `dma`
  (84-86 °C). Solo vale la diferencia dentro de cada pareja alterna.
- **`--short-read 128`:** "3,46 frente a 3,56" no dice cuántas pasadas ni el intervalo. El IC de V2 en la misma
  prueba fue [−0,50, +0,40] s, así que una diferencia de 0,10 s queda dentro. Dejadlo puesto, porque no hace daño,
  pero en la tabla va como **sin diferencia medible**, no como neutro-positivo.

**Lo que dice el banco de System One:** S2 dura ~3,6 s y su IC al 95 % con 6+6 pasadas mide ±0,5 s. No puede ver un
cambio menor del ~13 % en esa prueba. No gastéis pasadas en A/B de latencia de System One con cambios pequeños.

**Una corrección a la ronda 6, §4:** sin umbral conformal, `auto` no es 0; **no aparece** en la respuesta, y la
decisión escala solo por las reglas de antes (permutaciones en desacuerdo, `option_mass`). El motivo `conformal`
existe solo cuando la plantilla ya tiene un umbral.

## 2. Tres fallos del bucle de aprendizaje, corregidos

Revisando vuestro despliegue encontré tres problemas en mi código de la ronda 5. Están corregidos en
`ops/s1_learn.py` y `ops/ada-decide.py`, con tests.

### a) System Two bloqueaba a vuestros agentes

Miraba `GET /status` **solo antes de empezar**. Una vez lanzado, ocupaba Strata los 25-50 s de su razonamiento, y
toda petición que llegara esperaba detrás. Esto incluye las de `ada-decide`: una decisión de 0,9 s podía tardar
50 s.

Ahora:

- **Empieza solo tras 30 s seguidos** con Strata libre: ni `busy` ni `queued` (`--system2-idle`).
- **Mientras razona, mira `/status` cada segundo.** Si hay otra petición en cola, cierra su conexión. Strata lo ve
  como un cliente que se ha ido y cancela en 0,5 s (`serve/server.py`, `_watch_client`). La otra petición entra, y la
  decisión vuelve a la cola de System Two.
- `GET /v1/systemone/stats` cuenta estas cesiones en `system2.yielded`.

Comprobado con un Strata simulado por HTTP (`test_gives_way_to_a_waiting_request`):

1. Llega una petición a mitad del razonamiento.
2. System Two corta.
3. El servidor ve el cierre.
4. La decisión se resuelve después, con el servidor libre.

### b) La garantía se medía sobre una muestra sesgada

System Two etiquetaba **todas las decisiones que escalan** y un 5 % de las que no. Pero el umbral conformal y la
comparación campeón/aspirante usaban **todas** las etiquetas retenidas. Una vez hay umbral, lo que escala es sobre todo
lo de confianza baja, así que el error medido no era el de lo que se decide solo.

El sesgo va del lado prudente: sobreestima el error. Pero puede dejar el umbral alto para siempre y que nunca se decida
nada solo.

Ahora:

- **La auditoría se sortea en todas las decisiones**, escalen o no. Es una muestra uniforme, y es la única que fija el
  umbral y decide si un modelo nuevo gana.
- **El umbral se mide solo sobre lo que podría decidirse solo.** Las decisiones que escalan por otra razón
  (permutaciones, `option_mass`) no cuentan.
- Las escaladas sin sorteo se siguen resolviendo y siguen entrando en el ajuste de la calibración, que modela
  p(etiqueta | puntuaciones) y no la mezcla de casos.
- **Arranque en frío:** se auditan **todas** las primeras **200** decisiones de cada plantilla (`--audit-warmup`), y
  después el 5 %. La probabilidad depende solo de cuántas van, no de la decisión, así que la muestra sigue siendo
  uniforme.

**Por qué 200.** Con `--delta 0.1` y la rejilla de 19 umbrales, certificar un error ≤ alpha sin ningún fallo requiere
como mínimo **103 decisiones retenidas para alpha 0,05**, y **50 para alpha 0,1**. Es la cola binomial, no una
estimación. Se retiene la mitad de las auditadas, así que 200 dejan ~100: el mínimo justo para alpha 0,05, si casi no
hay errores.

**Coste:** a vuestros 25-50 s por decisión, 200 decisiones son **1,4-2,8 h de GPU libre por plantilla**, una sola
vez. Si es mucho, `--audit-warmup 100` con `--alpha 0.1`.

Comprobado:

- `test_escalated_labels_do_not_bias_the_guarantee`: 400 escaladas con la etiqueta **equivocada** no cambian el
  umbral. Con la selección anterior, el test falla (lo he comprobado).
- `test_only_decisions_that_could_be_automatic_set_the_threshold`: las decisiones que escalan por permutaciones no
  cuentan para el umbral.

### c) La cola vivía en memoria

Un reinicio de `ada-decide` perdía lo pendiente. Ahora la cola **se deduce de la base**: decisiones auditadas o
escaladas, con estado y sin etiqueta. Se retoma al arrancar.

Una respuesta de System Two que no se puede leer queda marcada como `failed` y no se reintenta en bucle. Se ve en
`stats`, en `labels.failed`.

Comprobado: `test_the_queue_survives_a_restart`.

### Desplegarlo

1. Copiar `s1_learn.py` y `ada-decide.py`, y reiniciar la unidad con los mismos argumentos.
2. La base existente se abre sin tocar nada: se añaden dos columnas (`audit`, `reasons`). Comprobado:
   `test_a_database_from_before_opens_and_its_rows_are_not_audited`.
3. Vuestras 2 decisiones de prueba quedan como **no auditadas**, porque no se sabe cómo se eligieron, y no cuentan
   para la garantía.

Tests: `ops/test_s1_learn.py` pasa de 11 a **16**. `test_ada_decide.py` (14) y `test_bench.py` siguen pasando, y el
CI del fork ya los ejecuta.

## 3. Decode y prefill: lo que queda es el motor, y para elegir el cambio necesito un perfil

Estoy de acuerdo con vuestra conclusión: la configuración está agotada. Vuestro desglose de la ronda 5 dice dónde
mirar:

- En las capas GDN, **`waitB` (7,69 ms) es la partida más grande**: la GPU esperando a que la CPU acabe sus expertos,
  más la copia por PCIe.
- Le sigue `VRAM hits` (5,29 ms).

La GPU espera a la CPU, así que el siguiente cambio de motor tiene que ser **de CPU**. El kernel de IQ2_S no bastó
(+1,4 %, dentro del ruido). Antes de escribir otro necesito saber **qué función se lleva el tiempo de los hilos de
expertos**, y no voy a suponerlo:

```bash
# durante un B1 (1.024 tokens), con el servidor ya caliente
sudo perf record -F 999 -g -p "$(pgrep -f -- ' --serve' | head -1)" -- sleep 20
sudo perf report --no-children --sort symbol --stdio | head -40 > perf-expertos.txt
```

Junto con eso, la línea del log de carga que dice **qué tipos de cuantización** tienen los expertos del modelo que
usáis (`swift`).

Con esos dos ficheros:

- Si domina un `row_dot<TY, NT>` concreto, escribo su versión por bloques, con test bit a bit, como la de IQ2_S.
- Si dominan el reparto o la sincronización de los hilos (`pool`, esperas, `memcpy`), el cambio es otro.

Lo decidís vosotros con el `compare`.

**Más VRAM o mejor flujo de aire en la caja:** los contadores dicen que la GPU pasa horas limitada por potencia y por
temperatura, y bloquear el reloj no ayudó. Si cambiáis la ventilación, medid antes y después con el mismo protocolo
alterno.

## 4. Qué pasarme de vuelta

1. `perf-expertos.txt` y la línea de los tipos de los expertos (§3).
2. Tras desplegar §2, `GET /v1/systemone/stats` pasados unos días: `audited`, `labels`, `system2.yielded` y si alguna
   plantilla ya tiene umbral.
3. Si notáis que vuestros agentes esperan a System Two pese a §2a, el `GET /status` de ese momento.
