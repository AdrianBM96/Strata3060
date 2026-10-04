# El modelo se atasca pensando y no actúa — propuesta para que decidas el plan

Para: Claude. Fecha: 2026-10-04. **Petición: decide tú el plan** (arquitectura, orden, qué medir). Aquí van
los hechos medidos, la evidencia externa y unas ideas, pero la decisión es tuya.

## 1. El problema, medido aquí

Con una tarea de código compleja (`minisql.py`: parser CSV + evaluador de `where` + tests), el **125B
(ada-next / Strata) entra en un bloque de "pensamiento" que no cierra** y **nunca emite la acción** (tool call
o texto). El motor lo reporta como `stop=length`, 0 tool calls. Resultado por harness:

| Harness | Thinking | Resultado | Evidencia |
| --- | --- | --- | --- |
| **pi** | medium | **OK, 25/25**, 17 min | 34 req, 78K in, 37K out |
| **omp** | high | **NO actúa** | 1 respuesta de **32.768 tokens** (todo pensar) en 645 s |
| **omp** | medium | **NO actúa** | 8.192 tokens (todo pensar) |
| **omp** | low | **NO actúa** | 8.192 tokens (todo pensar) |
| **omp** | off | 23/25, **atascado** | 168 req, **587.863 tokens de entrada**, sin cerrar |
| **ada-cli** | (defecto) | **atascado** | una respuesta de **21.204 tokens** en 431 s; ficheros sin tocar 13 min |

Log del motor, la petición de `omp` (high):
`prompt 19763 tokens ... 32768 generated in 645658 ms (50.8 tok/s)` → el techo entero, sin una sola tool call.

`omp` **sí** llama herramientas con `thinking off`/`low` en una tarea **trivial** (creó un fichero). El fallo
aparece con la tarea **compleja** + cualquier nivel de thinking.

## 2. Evidencia externa: es un problema conocido de la familia Qwen

- **arXiv (LLM agents / tool calls):** *"Reason-then-Act only partially helps... the model narrates its intent
  to call tools but never produces a valid invocation, resulting in near-zero tool calls."*
- **Qwen3-32B-AWQ + vLLM (QwenLM/Qwen3#1817):** *"Thinking mode plans tool calls but fails to execute them ~60%
  of the time"* — razona sobre llamar a la herramienta y **no la emite**.
- **Qwen 3/3.5/3.6:** *"long agentic loops die"*, *"tool-calling is broken"*, *"raw tool-call markup delivered
  as the final answer"*.
- **NVIDIA / hermes-agent (#129440):** *"reasoning-only clean stop promotes a tool call stranded in reasoning"*.

### Cómo lo resuelven (para inspirar el plan)

| Solución | Dónde |
| --- | --- |
| **Presupuesto de pensamiento** (`thinking_token_budget`, `max_thinking_tokens`, `think: N`) | vLLM, NVIDIA NIM, propuesta de Ollama |
| **Parser de reasoning/tool-call correcto** (`qwen3`, `qwen3_coder`, `qwen3_xml`) | vLLM, LM Studio |
| **Dos llamadas**: pensar hasta el tope, luego forzar la respuesta | vLLM forums |
| **Límites duros / detección de bucles** | Strands, smartMaxTurns, OpenClaw |
| **Desactivar thinking** (`enable_thinking: false`) | común |
| **Thinking adaptativo + effort + presupuesto de tarea** (sin budgets manuales) | Claude (modelos nuevos) |

### El dato que cambia el marco

Tabla medida de la propuesta de **Ollama** (acotar el pensamiento):

| Presupuesto | Aciertos | Tiempo medio |
| --- | ---: | ---: |
| **16.384** | **23/30** | 201 s |
| 4.096 | 20/30 | 99 s |

Y casos donde el **pensamiento ilimitado fallaba** (162K tokens, "hit the window", incorrecto) y **con tope
acertaba**. Es decir: **acotar el pensamiento no solo evita el atasco — a menudo mejora el acierto.**

**Aviso:** vLLM tiene un **bug abierto** (#44676): `thinking_token_budget` mete el tag de fin **en medio de los
argumentos de una tool call** (Qwen3.5+). Si se activa un tope, hay que verificar que no corrompe las tool calls.

## 3. Lo que ya tiene Strata

- **`reasoning_budget_tokens`** (#123, `serve/server.py`): *"el máximo de tokens que esta petición puede pensar"*;
  al alcanzarlo, **el motor cierra el pensamiento** e inyecta el fin. **Está a 0 (apagado) en nuestra config.**
- El servidor **ya detecta** el patrón (`stop=length`, 0 content, 0 tool calls) en su resumen del request.
- `--logprobs N` (System One) y `--reasoning_effort` (none/low/medium/high) en el motor.

## 4. Ideas (para que las juzgues, no para que las ejecutes sin más)

1. **Reserva de acción**: que el pensamiento no pueda comerse todo `max_tokens`; garantizar siempre espacio
   para emitir (patrón "allocating space for reasoning" de OpenAI). Hace **imposible** "pensó y no actuó".
2. **Tope generoso + transición forzada** con `reasoning_budget_tokens`, calibrado (2K/4K/8K/16K).
3. **Extensión guiada por progreso**: alargar el tope solo si el pensamiento no se repite (novedad como señal).
4. **Guardia en el servidor**: ante `length + 0 acción`, **reintentar** con menos tope + empujón, o devolver una
   señal limpia al harness. El harness nunca ve el fallo.
5. **System One como router del presupuesto** (nuestro Jev): decidir cuánto pensar por paso (el "router de
   esfuerzo" resolviendo un problema real).
6. **Auditar el parser de tool-calls** de Strata contra el template del modelo (un parser incorrecto pierde
   llamadas **en silencio** — es el síntoma exacto).

## 5. Lo que te pedimos que decidas

- **Arquitectura**: ¿dónde va cada cosa (motor / servidor / config / harness)? ¿`reasoning_budget_tokens` basta
  o hace falta el guardia con reintento?
- **Orden**: qué primero, qué depende de qué, y qué es medible ya.
- **La tensión calidad/atasco**: cómo lo resolvemos **sin perder razonamiento** — ¿tope fijo generoso, extensión
  por progreso, reserva de acción, o combinación?
- **Verificación**: qué medimos (acierto con el corrector `bench3/grade_minisql.py`, tokens, tiempo), con qué
  protocolo, y cómo comprobamos el bug de vLLM (#44676) si tocamos el tope.
- **Si el parser** hay que tocarlo, y cómo confirmarlo.

**Contexto de la máquina**: RTX 3060 12 GB, i5-12400F, 62 GB; `ada-next` = Swift 1.5 125B IQ2_XS; el banco de
la tarea está en `/tmp/opencode/bench3/` (spec + corrector); los harnesses son pi, omp y ada-cli (este último es
**nuestro producto**).
