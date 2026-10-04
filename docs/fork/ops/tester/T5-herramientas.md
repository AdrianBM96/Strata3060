# T5 — herramientas (leer, buscar, editar)

- **Ejerce**: 3-5 llamadas a herramientas encadenadas.
- **Prompt exacto**: `En /tmp/tester-sbx/fib.py: 1) lee el fichero, 2) busca la línea con "return a", 3) añade al final la línea "# revisado", 4) muestra el fichero. Confirma cada paso.`
- **Sandbox**: `/tmp/tester-sbx` (se edita `fib.py`, recreado antes de cada pasada).
- **Límite**: 240 s.
- **Éxito automático**: `/tmp/tester-sbx/fib.py` termina con la línea `# revisado`.
- **Duración esperada**: 1-3 min.
