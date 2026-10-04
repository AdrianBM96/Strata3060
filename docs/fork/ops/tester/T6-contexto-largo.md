# T6 — contexto largo (pregunta sobre fichero grande)

- **Ejerce**: lectura de contexto largo (~48K tokens) + pregunta comprobable.
- **Prompt exacto**: `El fichero /tmp/tester-sbx/grande.py define 2000 funciones f0000 a f1999. Dime: ¿cuántas hay y cómo se llaman la primera y la última? Responde en una línea.`
- **Sandbox**: `/tmp/tester-sbx` (lee `grande.py` de la plantilla, ~168 KB).
- **Límite**: 300 s (el prefill largo tarda).
- **Éxito automático**: la salida contiene `2000`, `f0000` y `f1999`.
- **Duración esperada**: 2-4 min.
