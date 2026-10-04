# T4 — código copiado (refactor que reutiliza)

- **Ejerce**: copiar código existente del contexto con cambios mínimos (contenido tipo "código copiado" de §17;
  es donde el borrador por búsqueda acierta).
- **Prompt exacto**: `En /tmp/tester-sbx/fib.py hay una función fib. Crea /tmp/tester-sbx/fib_par.py con la misma función renombrada a fib_par, idéntica por lo demás. No cambies nada más.`
- **Sandbox**: `/tmp/tester-sbx` (lee `fib.py`, crea `fib_par.py`).
- **Límite**: 180 s.
- **Éxito automático**: existe `/tmp/tester-sbx/fib_par.py` y contiene `def fib_par(n):` y `a, b = b, a + b`.
- **Duración esperada**: 1-2 min.
