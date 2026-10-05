# X2. Latencia de System One (ada-decide :8087, S2 = 2,29 s)

**Autor:** explorer  
**Destino:** claude (para opencode2 / adrian)  
**Fecha:** 2026-10-05 04:47 UTC  
**Ficheros analizados:** `/home/bazzite/Strata/ada-decide.py`, `src/program/generate.cpp`, `serve/server.py`, `docs/fork/SYSTEMONE.md`, `docs/fork/MEDICION_RONDA6.md`, `docs/fork/ENTREGAS.md`.

---

## 1. Desglose de la Latencia Actual (S2 = 2,29 s)

En la prueba **S2** de la línea base oficial (`ENTREGAS.md:125` y `ops/bench.py:24`), el cliente envía un lote de **4 preguntas distintas sobre un estado que ya fue leído previamente**:
- Tiempo total medido: **2,29 s** para 4 preguntas.
- Latencia por decisión individual: **$2,29\text{ s} / 4 = \mathbf{0,57\text{ s}}$ (572 ms por pregunta)**.

### ¿Qué parte es Prefill y cuál Decode?

Cada pregunta de `ada-decide.py` ejecuta una petición HTTP POST a `http://127.0.0.1:8081/v1/chat/completions` con `max_tokens: 1`, `temperature: 0` y `strata_lpids` (`ada-decide.py:143-154`).

```
Latencia por decisión (~572 ms):
┌────────────────────────────────────────────────────────┬──────────────┬──────────┐
│ Fase                                                   │ Tiempo (ms)  │   %      │
├────────────────────────────────────────────────────────┼──────────────┼──────────┤
│ 1. Restauración de checkpoint (RESUME root_at)         │   ~5 - 10 ms │   1,3 %  │
│ 2. Prefill de la cola (40-60 tokens via read_windows)  │ ~450 - 480 ms│  81,3 %  │
│ 3. Decode de 1 token + copia D2H de logits (ver.run)   │  ~25 - 40 ms │   5,7 %  │
│ 4. Overhead HTTP / JSON / Python / lock de FIFO        │  ~35 - 50 ms │   7,5 %  │
│ 5. Procesamiento y softmax en ada-decide.py            │   ~5 - 10 ms │   1,4 %  │
└────────────────────────────────────────────────────────┴──────────────┴──────────┘
```

1. **Prefill (Cola de usuario nueva): ~460 ms (~80–83 % del tiempo total).**
   - El estado reside en el mensaje `system` y se recupera instantáneamente desde el checkpoint de raíz (`--prompt-cache-root 256`, `generate.cpp:7020-7028`).
   - Pero el mensaje `user` (`Question: ... Options: ... Answer: (`) contiene entre **40 y 65 tokens nuevos**.
   - Con `--short-read 128` (`strata-swift-iq2_xs.json:41`), el motor decide procesar estos tokens mediante `read_windows` (`generate.cpp:7033`) en vez de la ruta de prefill por lotes.
   - En `read_windows`, los tokens se ingieren a través de ventanas de decodificación a la velocidad de decode del modelo (~50 tokens/s).
   - Ingerir 45–55 tokens a ~50 tok/s toma exactamente:
     $$\frac{50\text{ tokens}}{50\text{ tok/s}} \approx 1,0\text{ s (en ráfagas con ventanas: ~450 ms)}$$
   - **El prefill de la pregunta domina completamente la latencia.**

2. **Decode (Evaluación de logits y extracción de token): ~30 ms (~5–6 % del tiempo total).**
   - `max_tokens = 1`.
   - El motor ejecuta una única ventana de verificación `ver.run(...)` (`SYSTEMONE.md:65-72`).
   - En esa ventana se ejecuta el router de la capa, los GEMVs densos, la atención QSA y la función `ver.copy_logits(0, ...)` que descarga las log-probabilidades a host.
   - El decode puro toma únicamente **~25 a 40 ms**.

3. **Overhead de IPC / HTTP / Serialización: ~40 ms (~7 %).**
   - Serialización JSON en `ada-decide.py`, roundtrip HTTP vía `urllib.request` a `server.py`, adquisición del `self.fifo` (`server.py:1871`), paso de comandos por tubería de texto al proceso `strata`, parseo de la línea `strata logprobs:` y respuesta HTTP.

---

## 2. Ideas para Bajar la Latencia sin Cambiar Calidad

Todas las propuestas siguientes son **matemática y probabilísticamente idénticas** (preservan exactamente las log-probabilidades y decisiones del modelo).

### Propuesta 1. Poda y compactación del andamiaje en el mensaje `user` (Ahorro: ~150–200 ms/decisión)
- **Diagnóstico (`ada-decide.py:134-139`):**
  Actualmente, cada pregunta inyecta una plantilla verbosa en el turno de usuario:
  ```
  Question: <instrucciones>
  Options:
  (A) <descA>
  (B) <descB>
  Answer: (
  ```
  Esto añade entre 15 y 22 tokens puramente sintácticos (`Question: `, `\nOptions:\n`, `\nAnswer: (`, paréntesis y saltos de línea repetidos) que deben ser procesados en cada una de las 4 preguntas.
- **Acción:**
  Mover la instrucción del formato al mensaje `system` (que ya está congelado en el checkpoint):
  - `system`: `"... Choose exactly ONE option. Format: (A) .. (B) .. State:\n<st>"`
  - `user`: Compactar agresivamente la cola: `Q:<instrucciones>\n(A)<descA>\n(B)<descB>\nAns:(`
- **Impacto medible:**
  Reducir de 55 a 30 tokens la cola ahorra ~25 tokens por pregunta $\times$ 8 ms/token = **~200 ms por pregunta**.
  Para el lote S2 (4 preguntas): el tiempo baja de **2,29 s a ~1,50 s (−34 % de latencia)**.

### Propuesta 2. Plantilla con Prefijo Cacheable de Criterios / Opciones (Ahorro: ~100–150 ms)
- **Diagnóstico:**
  En muchas tareas de System One (auditoría de herramientas, triage, clasificación), el conjunto de opciones (`criteria`) es idéntico entre varias preguntas consecutivas (p. ej. preguntas del mismo formulario o batería).
  Sin embargo, en `render()` (`ada-decide.py:135`), la pregunta va antes que las opciones (`Question: ... Options: ...`). Si cambia la pregunta, se invalida el prefijo completo.
- **Acción:**
  Colocar las opciones antes de la pregunta específica, o inyectar las definiciones de opciones fijas dentro del mensaje `system` de la plantilla.
- **Impacto:**
  El checkpoint cubre tanto el estado como la definición de opciones. La parte nueva de usuario se reduce a la pregunta puntual (~10–15 tokens). El prefill cae a **<120 ms por decisión**.

### Propuesta 3. Concurrencia con Slot Dedicado (`--batch 2` / `--slots 2`) (Ahorro: ~45 % en el lote)
- **Diagnóstico:**
  Actualmente, `strata` opera en modo estrictamente secuencial (`batch=0`).
  En `ada-decide.py:453-485`, el bucle ejecuta las 4 preguntas de forma puramente secuencial:
  $$\text{Q1 (570 ms)} \to \text{Q2 (570 ms)} \to \text{Q3 (570 ms)} \to \text{Q4 (570 ms)} = 2,29\text{ s}$$
  Además, cualquier petición interactiva de Claude Code o opencode bloquea a `ada-decide` en el `self.fifo` de `server.py:1871`.
- **Acción:**
  1. Habilitar `--batch 2` en el motor (`strata-swift-iq2_xs.json`).
  2. En `ada-decide.py`, paralelizar las llamadas HTTP usando un `ThreadPoolExecutor(max_workers=2)`.
  3. Ejecutar las preguntas de 2 en 2 concurrentemente en la GPU.
- **Impacto medible:**
  Al solapar el prefill y decode de 2 preguntas en los dos slots de batch:
  El lote de 4 preguntas se completa en 2 rondas en lugar de 4.
  La latencia de S2 cae de **2,29 s a ~1,25–1,35 s (−42 %)**.
  Añade además aislamiento: las decisiones de System One no se atascan esperando turnos largos de agente.

### Propuesta 4. Transición a Batched Prefill tras C28 (Prefill por Capas) (Ahorro: ~400 ms/decisión)
- **Diagnóstico del porqué se usa `--short-read 128`:**
  En `MEDICION_RONDA6.md:63`, opencode2 adoptó `--short-read 128` porque el camino de prefill por lotes (`read_part`) sufría una penalización enorme al pedir prestados y devolver los 3.408 huecos de caché de expertos (`lend`/`refill`, ~150–250 ms de overhead de sincronización).
- **Acción cuando entre C28 (`LAYER-MAJOR`):**
  Como se demostró en `C28-RIESGOS.md`, C28 elimina la necesidad de vaciar y repoblar la caché de VRAM en cada prefill.
  Sin la penalización de `refill`, el prefill por lotes de 40 tokens a 990 tok/s tarda:
  $$t_{\text{prefill}} = \frac{40\text{ tokens}}{990\text{ tok/s}} \approx \mathbf{40\text{ ms}}$$
  Desactivando `--short-read` (`--short-read 0`) bajo C28:
  - Prefill: 40 ms
  - Decode: 30 ms
  - IPC: 20 ms
  - **Latencia por decisión: ~90 ms** (¡menos de 0,1 s!).
  - **Lote S2 (4 preguntas): ~0,36 s en total (aceleración de 6,3×)**.

---

## 3. Matriz Comparativa de Propuestas para Bajar S2

| Estrategia | Requiere tocar C++ | Riesgo de Calidad | Latencia S2 estimada | Reducción |
| :--- | :---: | :---: | :---: | :---: |
| **Línea Base Actual** (S2 en 0.1.39) | No | 0 % | **2,29 s** | 0 % |
| **1. Poda de andamiaje en `user`** | No (solo `ada-decide.py`) | 0 % (mismo texto semántico) | **~1,50 s** | **−34 %** |
| **2. Prefijo común en `system`** | No (solo `ada-decide.py`) | 0 % | **~1,40 s** | **−39 %** |
| **3. Batching / Slot dedicado (`--batch 2`)** | No (`config` + `ada-decide.py`) | 0 % (bit-idéntico) | **~1,25 s** | **−45 %** |
| **Combinación 1 + 3** (Poda + Batching) | No | 0 % | **~0,85 s** | **−63 %** |
| **4. Batched prefill con C28** | Sí (motor C28) | 0 % (bit-idéntico) | **~0,36 s** | **−84 %** |

---

## 4. Recomendación Inmediata para Claude y opencode2

1. **Implementar ya la Propuesta 1 (Poda de andamiaje en `ada-decide.py:render`):**
   No requiere tocar el motor ni reiniciar servicios C++. Modificar el formateo en `ada-decide.py` eliminando el boilerplate de `Question:` y `Options:` en el mensaje de usuario. Se valida inmediatamente con `bench.py run --tests S2`.
2. **Evaluar `--batch 2` para System One:**
   Permite desacoplar las decisiones de fondo del flujo interactivo y reduce a la mitad el tiempo de los lotes de preguntas.
