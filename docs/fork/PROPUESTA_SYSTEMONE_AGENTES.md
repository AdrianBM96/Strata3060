# Propuesta a Claude: System One como capa de decisión para agentes (MCP + delegación)

Para: Claude. Fecha: 2026-10-04. **Petición: investiga esto y propón lo mejor.**

## 1. De dónde viene

Adrián pidió una **auditoría profunda** (más de 100 fuentes) de cómo usa la gente **Jev** (TypeSafe) con
**agentes de programación**, para sacar ideas. Resumen de lo encontrado:

**Patrones que se repiten** (con la evidencia que traen):
- **Guardarraíl de permisos** (¿comando destructivo? allow/ask/deny): `toolgate`, `jevvy`, `jev-pi`, OpenRouter
  cookbook, Greenwash (fail-closed).
- **"¿Está hecha la tarea?"** (cortar el bucle): flaviocopes, construct.computer, Microsoft, shipwithjev.
- **Router de modelo** (barato/caro por paso): LangChain `ModelRouterMiddleware`, Tetrate, MS blog.
- **Router de esfuerzo** (low/high): awesome-typesafe-jev, "keel".
- **Selección de herramienta** (choice sobre tools + "none").
- **Compactación de contexto** (keep/summarize/drop por trozo): LiteLLM, jev-pi.
- **Detección de bucles** (¿se repite sin avanzar?).
- **Linter semántico**: `jev-lint` midió **2,38 → 1,21 violaciones/tarea** en 96 runs.
- **Búsqueda semántica de código**: Oko (**21-23% más rápido, 13-44% menos tokens**; MRR 0,39 vs 0,24), JevGrep
  (**40% más barato en SWE-bench**).
- **Revisión/ranking de PR** (P0/P1/P2), **gate de commit**, **memoria** (jevmem), **seguridad de runtime**.

**La integración estándar es un servidor MCP** (`jev-mcp`, Oko, jev-review, jev-code, toolgate) → funciona en
Claude Code, Codex, Cursor y **OpenCode** sin integración a medida. Existe **OpenJev** (27B local, 4B/9B) → la
idea local es viable.

**La evidencia incómoda (y nuestra oportunidad):**
- Jev está **"confiadamente equivocado"**: en errores da 0,56-0,58 (a veces 0,99); bien calibrado en los
  extremos, **mal en la banda 0,3-0,8** (alphaxiv, lmspedia, prefactor, maximumeffort, alexmolas).
- **A/B de agente completo: recorta coste/contexto, NO sube el éxito de la tarea** (jevwiki, community tier).
- **El gate nunca debe ser el único control**; los umbrales hay que **calibrarlos con tus datos**.
- Un guardarraíl falló por no ver la verificación local → alguien quitó el plugin a la semana.

**Lectura para nosotros:** el punto débil de Jev (calibración en la banda media) es **exactamente lo que
nuestro bucle de autoaprendizaje arregla** (se calibra con *nuestras* decisiones), y nuestro **512K** permite
estados que Jev no lee. Pero **no prometamos "mejor agente"**: la evidencia dice que el ahorro es en coste y
latencia.

## 2. La propuesta de Adrián (lo nuevo)

**Que nuestro System One decida también si la tarea se delega a OTRO modelo** — en particular a los que
tenemos por licencia de **OpenCode Go**: **DeepSeek V4.1 Flash** y **Muse Spark 1.3** — en vez de gestionarla
el 125B local.

Es decir: System One no solo decide *dentro* del flujo, sino **qué cerebro** atiende cada tarea/paso.

- **Criterios**: complejidad, longitud de contexto, sensibilidad/privacidad, coste, latencia, y **tu historial**
  (el bucle aprende qué tareas salen bien en local y cuáles conviene delegar).
- **Salida**: `local` (125B) | `deepseek-v4.1-flash` | `muse-spark-1.3` | `gemini`/`glm` (ya en litellm) | `human`.
- **Con confianza**: si no está claro, se queda en local (fail-safe) o escala.

## 3. Lo que te pedimos que investigues y propongas

1. **Arquitectura**: ¿System One como **servidor MCP** (patrón confirmado) o integrado en litellm/opencode? ¿Cómo
   encaja con el bucle actual (`ada-decide` :8087, `s1_learn.py`)?
2. **El router de delegación**: qué preguntas tipadas (choice/score/noul), qué criterios, y **cómo se combinan
   en código**. Cómo se entera System One de qué modelos hay y qué cuestan.
3. **Qué decisiones priorizar** dado nuestro límite de **~0,9 s/decisión**: las per-turn (esfuerzo, "¿hecha?",
   router) encajan; las per-tool (guardarraíl por comando) son caras → ¿merecen un modo rápido?
4. **La calibración**: cómo usamos el bucle para **batir a Jev donde falla** (banda 0,3-0,8), con umbrales
   aprendidos y garantía conformal.
5. **Seguridad**: fail-closed, nunca control único, y qué decisiones **no** deben delegarse a un modelo.
6. **La latencia del propio System One**: los speedups V1-V5 del plan (objetivo ≤200 ms) pasan a ser
   importantes si va a decidir en cada turno.

## 4. Restricciones

- El **125B y System One comparten la 3060** → las decisiones deben caber en los huecos del bucle.
- **512K** de contexto disponible (estado largo).
- Los modelos cloud ya están en **litellm** (MiniMax, Gemini, GLM, DeepSeek v4 vision vía B.AI). **OpenCode Go**
  (DeepSeek V4.1 Flash, Muse Spark 1.3) habría que añadirlo.
- Nuestro System One es **local, gratis, y aprende**; Jev es API de pago.

**Pregunta final:** ¿cuál es, en tu criterio, el **primer uso** que más aporta y mejor se puede medir, y cómo lo
montarías?
