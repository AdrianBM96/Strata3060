# Tope de pensamiento medido: el umbral está entre 3072 y 4096

Fecha: 2026-10-04. Tarea compleja con una herramienta (`write_file`), mismo prompt, `max_tokens=8192`.
18 llamadas (se cerraron las 3 de 6144 por innecesarias: están por encima del umbral donde ya falla todo).

## Tabla

| Tope | reps | stop | thinking | ¿actúa? | args válidos |
| --- | --- | --- | ---: | --- | --- |
| sin tope | 3 | max_tokens ×3 | ~31K c | **no (0/3)** | — |
| 1024 | 3 | tool_use ×3 | ~4,2K c | **sí (3/3)** | sí |
| 1536 | 3 | tool_use ×3 | ~6,3K c | **sí (3/3)** | sí |
| 2048 | 3 | tool_use ×3 | ~7,7K c | **sí (3/3)** | sí |
| 3072 | 3 | tool_use ×3 | ~12,5K c | **sí (3/3)** | sí |
| 4096 | 3 | max_tokens ×3 | ~17K c | **no (0/3 completan)** | — |

## Lectura

- **Umbral nítido entre 3072 y 4096**: por debajo, el modelo actúa siempre (12/12); por encima, nunca completa
  (0/6). A 4096 el modelo incluso emite 1 tool call pero **se come el resto del presupuesto pensando** y muere por
  `max_tokens` sin terminar.
- El tope no recorta el razonamiento útil: a 2048 el modelo piensa ~7,7K caracteres (~2K tokens) y actúa. Sin tope
  piensa ~31K y **no hace nada**.
- **Recomendación: `reasoning_budget_tokens=2048`** por defecto en llamadas agénticas (con `max_tokens=8192` deja
  ~6K para actuar). Es el centro del rango que funciona y coincide con la primera medición.

## Para el diseño (Claude)

Esto valida "tope generoso + transición forzada": el tope no es un compromiso de inteligencia, es lo que **permite**
que haya acción. La reserva de acción sale gratis del mismo número: con tope 2048 de 8192, la acción tiene sitio
garantizado. La extensión por progreso queda como mejora opcional, no como necesidad.
