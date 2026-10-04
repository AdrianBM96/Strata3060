# T3 — código nuevo (función + test)

- **Ejerce**: escritura de código nuevo con su test (contenido tipo "código nuevo" de §17).
- **Prompt exacto**: `En /tmp/tester-sbx escribe mcd.py con una función mcd(a, b) (algoritmo de Euclides) y test_mcd.py con 3 tests unittest. Ejecuta los tests y dime si pasan.`
- **Sandbox**: `/tmp/tester-sbx` (se crean `mcd.py` y `test_mcd.py`).
- **Límite**: 240 s.
- **Éxito automático**: existen `/tmp/tester-sbx/mcd.py` y `/tmp/tester-sbx/test_mcd.py`, y `python3 -m pytest -q` sobre el test pasa.
- **Duración esperada**: 2-3 min.
