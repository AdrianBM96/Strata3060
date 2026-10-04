# Auditoría de la caché de expertos en VRAM: cómo funciona, dónde puede haber margen, y cómo medirlo antes de tocarla

Para: el agente del servidor (y Adrián). Fecha: 2026-10-04.

## 0. La respuesta corta

**Puede haber margen, pero hoy nadie sabe cuánto**, y no voy a estimarlo: las veces que he estimado sin medir, me he
pasado. La caché decide en cada momento qué 3.696 de los 24.576 especialistas viven en la tarjeta. Hoy acierta el
70-78 % de las consultas.

Lo que traigo:

1. **Un simulador** (`ops/cache-sim.py`, con tests). Reproduce **el enrutado real de vuestras sesiones** y compara la
   política actual con otras. Incluye **el óptimo teórico**: la política que conoce el futuro, que ninguna real
   alcanza. Ese es **el techo**. La distancia entre la política actual y el techo es lo que se puede ganar.
2. **Un contador nuevo en el motor:** cuántos especialistas copia la caché por petición. Hoy, en modo servidor, no se
   sabe, y esas copias usan el mismo cable PCIe que el decode.
3. **Las tres palancas que ya existen como flags** (`--adapt-decay`, `--adapt-every`, `--adapt-swaps`) y que nunca hemos
   probado. Si el simulador encuentra una combinación mejor, se prueba **sin tocar el motor**.

## 1. Cómo funciona hoy (leído en el código)

**Arranque:** el perfil aprendido (#477), con el orden por enrutado de `STRATA_PROFILE_HEAT_MIN` que activasteis,
llena la caché (`generate.cpp:2849-2866`, `:3071-3118`).

**En marcha, la función `adapt` del modo servidor** (`generate.cpp:4795-4867`):

- **Cuenta el uso.** Cada vez que una palabra usa un especialista, su contador sube 1 (`expert_source.cpp:1888`).
- **Revisa cada 4 ventanas** (`--adapt-every 4`). En cada capa compara los no residentes más usados con los residentes
  menos usados, y cambia uno por otro si `nuevo ≥ 2` y `nuevo ≥ viejo + 1,5`.
- **Como mucho 96 cambios por revisión** (`--adapt-swaps 96`), los de más ganancia.
- **Después, todos los contadores × 0,7** (`--adapt-decay 0.7`).

Cuatro rasgos de diseño que pueden costar aciertos o tiempo. **Ninguno está medido aún:**

| # | Rasgo | Por qué puede importar |
| --- | --- | --- |
| 1 | **Memoria muy corta.** × 0,7 cada 4 ventanas: un uso pesa la mitad tras ~2 revisiones (~8 ventanas, ~16 palabras) | Sigue bien un cambio de tema, pero puede expulsar especialistas que se usan siempre, solo porque no salieron en las últimas palabras, y volver a traerlos poco después: copias que no ganan nada |
| 2 | **Solo cambia dentro de la misma capa** | El reparto de huecos entre capas queda fijo durante toda la sesión. `STRATA_PROFILE_HEAT_MIN` lo corrige solo al reiniciar |
| 3 | **Hasta 96 copias de 1,38 MB por revisión** = hasta 133 MB, ~12 ms del cable a 11 GB/s, cada 4 ventanas | Van por el mismo PCIe que la copia de cada ventana (9,3 ms, la partida mayor de la GPU). Si hay muchas, compiten. No se sabía cuántas hay: §3 |
| 4 | **Todos los fallos cuentan igual** | Fallar un IQ2_S cuesta más CPU que fallar un IQ1_M (vuestro perfil: IQ2_S 11,6 %, IQ2_XXS 4,5 %, IQ1_M 1,5 %). Una política podría preferir guardar los caros |

Y uno fuera del decode: **un prompt largo toma prestados hasta ~3.020 huecos y luego los rellena**
(`generate.cpp:5527-5571`). Es el frente de prefill, ya apuntado en la ronda 7.

## 2. El simulador: `ops/cache-sim.py`

```bash
# 1. una traza de uso REAL, unas horas de vuestros agentes (~4 KB por palabra generada: 200.000 palabras ≈ 800 MB)
#    en strata-swift-iq2_xs.json, "args": [..., "--dump-routing", "/home/bazzite/Strata/bench/routing.bin"]
#    (fuera de /tmp; quitadlo después)
# 2. la comparación, con el perfil del que arranca el motor y sus huecos
python3 docs/fork/ops/cache-sim.py ~/Strata/bench/routing.bin --slots 3696 --profile <perfil aprendido> --per-layer
python3 docs/fork/ops/cache-sim.py ~/Strata/bench/routing.bin --slots 3696 --profile <perfil> --grid   # + la rejilla
```

**Políticas que compara:**

| Política | Qué es |
| --- | --- |
| `static` | El conjunto de arranque, sin cambiar nunca |
| `adapt` | La regla del motor, reproducida tal cual: cada 4 ventanas, umbral 2 y +1,5, máximo 96, decaimiento 0,7, y el nuevo sirve desde la ventana siguiente |
| `adapt cross` | Igual, pero quitando huecos de cualquier capa |
| `lru` | Referencia: admite cada fallo |
| `belady (per layer)` / `belady (global)` | **El techo**: el óptimo con el futuro conocido, con el reparto de capas de hoy o libre |

**Qué mide:**

- **Acierto por uso:** es el que imprime el motor.
- **Acierto por especialista distinto:** lo que de verdad calcula la CPU.
- **Copias por ventana:** en MB y en ms de PCIe.

**Primero, validarlo.** El `adapt` simulado, con vuestras flags, tiene que dar un acierto parecido al de la línea
`decode expert cache hit rate` del motor en la misma sesión. Si no se parece, el simulador está mal: decídmelo y no
sigáis.

**Cómo leer el resultado, y qué haría yo en cada caso:**

| Lo que sale | Qué significa | Lo siguiente |
| --- | --- | --- |
| `adapt` ≈ `belady (per layer)` (a 1-2 puntos) | La política actual está casi en el techo de lo que puede hacer dentro de cada capa | Nada en la política. Si `belady (global)` sale bastante más alto, el margen está en mover huecos entre capas: `adapt cross` dirá si una regla simple lo consigue |
| Alguna combinación de `--grid` gana claramente a la de hoy | Las flags están mal puestas para vuestro uso | **A/B en el motor solo cambiando esas flags**, alternando, B1/B2/B4. Es el cambio más barato posible |
| `adapt` lejos del techo y la rejilla no lo cierra | El margen existe y requiere otra política | Yo escribo una en el motor (por ejemplo, con dos memorias, larga y corta) y la pruebo primero en el simulador |

**Ojo:** el techo no cuenta lo que cuesta copiar, y suele copiar mucho. Mirad también las copias por ventana de cada
fila: una política que gana 2 puntos pero copia el triple puede salir perdiendo en el motor real.

## 3. El contador nuevo en el motor

En esta rama, `src/program/generate.cpp`: la línea de cada petición dice ahora

```
strata serve: decode expert cache hit rate: 74.6% (… hits / … lookups), N experts swapped in over W windows
```

- Son 3 líneas: un contador y la línea ampliada.
- No cambia ningún cálculo.
- Se aplica con o sin vuestros parches de System One y de borradores (comprobado).
- Traedlo como los anteriores:
  ```bash
  git show <commit> -- src/program/generate.cpp | git apply --3way
  ```

**Con eso sabremos si el rasgo 3 importa:**

- **N/W × 1,38 MB** = los MB por ventana que la caché mete en el cable.
- **Si son pocos MB por ventana** (≪ 9 MB): no compiten con la copia del decode.
- **Si son decenas:** sí, y el remedio es barato. Por ejemplo, `--adapt-swaps 32` o `--adapt-every 8`, probado en el
  simulador primero.

## 4. Qué os pido, en orden

1. Traer el contador (§3) y la traza (§2) de **unas horas de uso real**: las líneas de acierto y copias de esas mismas
   peticiones, y el fichero del perfil del que arrancó.
2. Pasar el simulador: la tabla normal, `--per-layer` y `--grid`. Pasadme la salida tal cual.
3. Validar §2: ¿el `adapt` simulado se parece al acierto del motor?

Con eso decido, con números vuestros, si hay que cambiar flags, la política, o nada.
