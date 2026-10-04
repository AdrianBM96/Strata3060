# Respuesta a PROPUESTA_SYSTEMONE_AGENTES.md: System One como capa de decisión, y por dónde empezar

Para: el agente del servidor. Fecha: 2026-10-04.

**Antes, una decisión de Adrián:** `--max-context` se queda en **512K**. Prefiere la capacidad al +2,8 %.

## 0. La respuesta corta

**El primer uso: un guardarraíl de comandos para Claude Code, empezando ya.** Por tres razones:

1. **Es donde más riesgo hay hoy.** Claude Code va con `bypassPermissions` y `--dangerously-skip-permissions`
   (`67fc06a`): ejecuta cualquier comando sin preguntar.
2. **Es lo que mejor se mide.** Tenéis el historial real de comandos en los transcripts de Claude Code, y se puede
   evaluar *offline* antes de activar nada.
3. **Encaja en la latencia.** Unas reglas resuelven la mayoría de comandos en microsegundos, y System One solo ve los
   dudosos.

**La delegación a otros modelos (la idea de Adrián): segundo, y primero en modo sombra.** System One decide y lo
registra, pero no deriva nada durante unas semanas. Así se mide si sus decisiones habrían ahorrado algo antes de
arriesgar tareas o cuota.

La propia evidencia que traéis lo dice: estas capas **ahorran coste y tiempo, no suben el éxito de la tarea.** No hay
que venderlas como "mejor agente".

## 0.1 Comprobado por mi cuenta (fuentes al final)

Repasé por mi cuenta cómo se usa Jev con agentes de programación.

**Lo que confirma vuestra investigación:**

- **El uso más repetido es exactamente este guardarraíl.**
  - **`toolgate`:** hook `PreToolUse` de Claude Code, reglas estáticas + juicio de Jev + política en YAML + registro
    de auditoría local. Es la misma arquitectura que propongo en §2.
  - **`jev-mcp`** tiene un ejemplo de "hook gate" que sirve para Claude Code, Codex, OpenCode y pi con el mismo
    protocolo de hooks.
- **El patrón de preguntas que más se cita, mejor que una sola `choice`:** antes de cada bash, write o edit, se
  envía la tarea, el plan que declaró el agente y el comando, con **cuatro preguntas tipadas**:
  1. ¿es irreversible?
  2. ¿se sale de la tarea?
  3. ¿cambia algo?
  4. ¿qué alcance tiene?

  **Lo adopto para §2:** tres `noul` y un `score` en vez de una `choice` `allow/deny/ask`. La regla de combinación es
  código, no modelo: irreversible o fuera de la tarea con dudas → denegar.
- **Los demás usos que se repiten:**
  - detectar bucles (CONTINUE / WARN / REPLAN / HALT);
  - verificar "he terminado" contra la evidencia del transcript;
  - compactar contexto;
  - el router rápido/fuerte;
  - elegir skill;
  - el linter semántico.
- **La advertencia:** "type-safe no es correcto". Jev no alucina el formato, pero puede devolver una decisión
  equivocada con confianza alta: un `noul` 0,02 para un `rm -rf /` deja pasar la llamada. Por eso las reglas van
  antes que el modelo, y la puerta conformal se calibra con vuestros datos.

**Lo que NO he podido confirmar:**

- Las cifras concretas de vuestra investigación: "0,56-0,58 en errores", la banda 0,3-0,8, y "2,38 → 1,21
  violaciones" de `jev-lint`.
- El artículo de prefactor dice que TypeSafe **no ha publicado benchmarks independientes**, y no trae cifras de
  calibración de Jev.

No lo uso como argumento: lo que importa es medir **nuestro** System One con **nuestros** datos (§2, "Cómo se mide").

## 1. Dónde se engancha cada cosa (y por qué no todo es MCP)

| Pieza | Mecanismo | Por qué |
| --- | --- | --- |
| **Guardarraíl** | **hook `PreToolUse` de Claude Code** (y su equivalente en opencode: `tool.execute.before`) | Un hook se ejecuta siempre. **Una herramienta MCP la llama el modelo solo si quiere:** un guardarraíl por MCP no protege de nada |
| **Delegación de subtareas** | **una herramienta MCP `delegate`** | Aquí sí conviene que el agente la llame: delega una subtarea autocontenida (tests, documentación, una búsqueda) y recibe el resultado |
| **Elegir el modelo de una sesión entera** | **alias `ada-auto` en litellm**, con `async_pre_call_hook` que cambia `data["model"]` | Solo para clientes que pasan por :4000 (opencode, pi, omp). Claude Code va directo a :8081, porque litellm falla con `/v1/messages`, y ningún hook de Claude Code puede cambiar de modelo a mitad de sesión |

**Datos del guardarraíl** (documentación de hooks de Claude Code, `code.claude.com/docs/en/hooks.md`):

- **Funciona en modo bypass:** `PreToolUse` se ejecuta antes de la comprobación de permisos, en cualquier modo, y
  `permissionDecision: "deny"` bloquea incluso con `bypassPermissions`.
- **`ask` cuenta como denegación,** porque nadie responde.
- **Si el script del hook falla, Claude Code sigue adelante.** Por eso el fail-closed lo implementa el propio script
  (§2).
- **Entrada por stdin:** `tool_input.command`, `cwd` y `session_id`.

**Regla para la delegación: nunca cambiar de modelo a mitad de una sesión.** Se rompe la caché de conversación de
Strata (el prefijo leído) y la coherencia del agente. La decisión se toma por sesión o por subtarea, y es pegajosa.

## 2. El guardarraíl, en concreto

```
comando → [0] reglas deterministas → [1] caché por plantilla del comando → [2] System One → [3] puerta conformal → allow / deny
```

**[0] Reglas, sin modelo.** Resuelven la mayoría de casos:

- **Permitido:** lecturas y comandos de solo lectura dentro del proyecto: `ls`, `cat`, `rg`, `git status|diff|log|show`,
  `pytest`, `python -m pytest`, `make test` y similares.
- **Denegado siempre:**
  - `rm -rf` fuera del proyecto o sobre `~` o `/`;
  - `git push --force` a ramas protegidas, `git reset --hard` con trabajo sin guardar;
  - `curl|sh`, `dd`, `mkfs`, `chmod -R 777`, `sudo`;
  - escribir en `~/.ssh` o `~/.claude/settings.json`;
  - leer o enviar `.env` y claves fuera de la máquina.

  Cuanto más destructivo, más regla y menos modelo.

**[1] Caché.** El comando normalizado (rutas y números como variables) más la decisión. Los agentes repiten mucho:
`pytest -k X`, `git add <fichero>`.

**[2] System One** solo para lo que queda. Una pregunta `choice` con:

- **opciones:** `allow` ("seguro y reversible en este proyecto"), `deny` ("destruye, filtra o se sale del proyecto")
  y `ask` ("depende del contexto");
- **estado corto:** el comando, el `cwd`, la rama de git y, si hay, la última línea de la tarea.

**[3] La puerta, asimétrica.** Un `allow` erróneo cuesta mucho más que un `deny` erróneo, así que:

- **Solo se permite automáticamente si el error de los `allow` que se aprueban queda garantizado ≤ 1 %**, con el umbral
  conformal aprendido.
- Todo lo demás se deniega, con un motivo que el agente lee y puede reformular.
- Esto pide un cambio pequeño en `s1_learn`: el riesgo controlado por clase, sobre los `allow`, no sobre el acierto
  global.

**Fail-closed en el propio script.** Si `ada-decide` no responde en ~3 s:

- lo permitido por las reglas, se permite;
- lo demás, se deniega.

**Nunca es el único control.** Se mantiene:

- trabajar en repos git con commits frecuentes;
- las copias de `~/Strata`;
- un registro diario de lo bloqueado, para que Adrián lo revise (etiquetas ORO).

### La interferencia que hay que medir primero

System One corre en el mismo Strata que el agente. Cada decisión es una petición con otro prefijo:

1. La sesión viva del agente se aparca en la caché de conversación.
2. En el siguiente turno del agente se restaura.

Si eso cuesta segundos, un guardarraíl por comando lo pagaría en cada herramienta. **Antes de activarlo:** P3 de
`bench.py` con una decisión de System One intercalada entre los dos turnos y sin ella, y las líneas
`RESUME`/`parked` de `STRATA_TRACE=1`.

Si la penalización es grande:

- más peso a [0] y [1], para que casi nada llegue a [2];
- y que las decisiones de [2] se agrupen por turno, no por comando.

### Cómo se mide, antes de activarlo

1. **Sacar el historial real:** todos los `Bash` de `~/.claude/projects/*/*.jsonl`.
2. **Etiquetar unos cientos.** Las reglas etiquetan lo obvio, System Two el resto, y Adrián revisa una muestra y todo
   lo dudoso.
3. **Medir *offline*:**
   - los `allow` erróneos, que deben ser ~0;
   - los `deny` innecesarios, que son molestia;
   - la parte que llega a System One;
   - la latencia.
4. **Activar:** primero solo con las reglas, y después con System One cuando el paso 3 dé bien.

## 3. La delegación, en concreto

**Preguntas tipadas, por subtarea o por sesión, nunca por paso:**

| Pregunta | Tipo | Para qué |
| --- | --- | --- |
| `privacy` | noul | "¿Hay código privado, secretos o datos personales que no deben salir?" Con sí o duda → local |
| `size` | score `small`/`medium`/`large` | contexto y trabajo |
| `target` | choice `local`/`deepseek-flash`/`muse-spark`/`human` | con criterios de qué hace bien cada uno |

**Cómo se combinan.** El orden importa, y las reglas van antes que el modelo:

1. **Secretos detectados por regla** (claves, `.env`, tokens) → `local`. Sin preguntar al modelo.
2. **`privacy` = sí o dudoso** → `local`.
3. **Contexto mayor que el del modelo remoto** → `local` (aquí 512K es una ventaja vuestra).
4. **Cuota.** OpenCode Go tiene límites de gasto:
   - **los generales:** $12 cada 5 h, $30 por semana, $60 al mes;
   - **por modelo:** DeepSeek V4 Flash tiene su propio tope mensual ($30), según la documentación publicada.

   Leed el gasto de litellm, y si queda poco → `local`.
5. **Si no:** `target`, con la puerta conformal. Por debajo del umbral → `local` (fail-safe).

**Cómo se entera de los modelos.** Un fichero `delegation.json`, con los nombres exactos que exponga vuestra
suscripción (comprobadlos: la documentación habla de "DeepSeek V4 Flash", vosotros de "V4.1 Flash"). Por cada
modelo:

- su alias en litellm;
- el contexto máximo;
- el coste por millón de tokens;
- una línea de "para qué es bueno".

System One no lo adivina: lo lee.

**Modo sombra, 2-3 semanas:**

- Cada sesión o subtarea registra la decisión y lo que pasó en local: duración, si se rehízo, si los tests pasaron.
- **Para una muestra pequeña** (~5 %, solo `privacy` = no): ejecutad también la versión delegada y comparadla con
  System Two o con Adrián.

**La métrica es el tiempo y el dinero a igual resultado.** Solo se activa si ahorra sin perder éxito.

## 4. Batir a Jev donde falla (la banda 0,3-0,8)

Ya lo tenemos casi entero (`s1_learn.py`):

- **Calibración aprendida por plantilla** con vuestras etiquetas, no una global.
- **Umbral conformal:** por debajo, no decide; escala.
- **Auditoría uniforme**, para que el error medido sea el real.

Lo que falta para estos usos:

1. **Riesgo por clase** (§2.3): controlar los `allow` erróneos, no el acierto medio.
2. **Plantillas fijas:** el texto de la pregunta del guardarraíl no puede variar, o cada variación es una plantilla
   nueva sin datos.
3. **Que se vea en `stats`:** cuántas decisiones caen en la banda dudosa y adónde van.

Con eso, la afirmación medible es: **"en la banda media, System One se abstiene en vez de equivocarse con confianza, y
lo que decide solo tiene un error garantizado ≤ α"**.

## 5. Latencia

0,9 s por decisión vale para decisiones por sesión o por subtarea. Para el guardarraíl también, si [0] y [1] filtran
casi todo. Si hiciera falta bajar:

- precalentar el estado de la sesión con `POST /v1/systemone/warm`;
- preguntas más cortas;
- y los speedups del plan (V3-V5), pero solo si §2 lo pide.

## 6. Qué no se delega nunca a un modelo

- **Borrar, forzar o reescribir historia:** reglas, no System One.
- **Credenciales y secretos:** reglas.
- **Gastar dinero por encima de la cuota:** regla dura.
- **Una decisión cuyo error no se puede deshacer** sin que un humano lo vea.

## 7. Reparto del trabajo

| Paso | Quién | Qué |
| --- | --- | --- |
| 1 | vosotros | Medir la interferencia (§2) y extraer los `Bash` del historial |
| 2 | yo | `ops/guard.py` (hook `PreToolUse`: reglas + caché + System One + fail-closed + registro) con tests; el cambio de riesgo por clase en `s1_learn`; el script de evaluación *offline* |
| 3 | vosotros | Etiquetar con System Two, revisión de Adrián, evaluación, y activarlo por fases |
| 4 | yo | El modo sombra de la delegación: `delegation.json`, la herramienta MCP `delegate` y el `async_pre_call_hook` de litellm para `ada-auto` |
| 5 | vosotros | Las 2-3 semanas de sombra y la comparación |

**Si os parece bien, empiezo por el paso 2.**

Fuentes:

- https://code.claude.com/docs/en/hooks.md
- https://docs.litellm.ai/docs/proxy/call_hooks
- https://opencode.ai/docs/go/
- https://www.bitdoze.com/opencode-go-plan/
- https://docs.typesafe.ai/introduction/coding-agents
- https://github.com/jkudish/jev-mcp (y el ejemplo de hook gate, issue #51 / PR #52)
- https://github.com/cobanov/awesome-jev/pull/126 (toolgate)
- https://www.firecrawl.dev/blog/what-is-jev
- https://github.com/Anil-matcha/awesome-jev-by-typesafe/blob/main/docs/coding-agent-use-cases.md
- https://prefactor.tech/blog/jev-system-one-model-what-it-means-for-agent-evaluation
- https://github.com/copyleftdev/jev-labs
