# T8 — humo de configuración (arranca y responde)

- **Ejerce**: 1 llamada a herramienta + 1 respuesta. Sirve para "¿arranca y responde bien con la config nueva?".
- **Prompt exacto**: `Lee el fichero /tmp/tester-sbx/fib.py y dime en una línea qué devuelve fib(10).`
- **Sandbox**: `/tmp/tester-sbx` (lee `fib.py` de la plantilla).
- **Límite**: 120 s.
- **Éxito automático**: la salida contiene `55`.
- **Duración esperada**: <1 min.
