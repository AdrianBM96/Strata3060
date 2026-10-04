# E3. KV persistente de prefijos para agentes (estudio, ≤1 página)

## Tamaño

- KV por token (`--kv int8`): 12 capas QSA × 2 cabezas KV × 256 dim × 2 (K+V) × 1 B = **12.288 B/token ≈ 12 MB/1K**.
- Prefijo de 20K → **~240 MB** de KV. Más estado GDN (fijo por capa, pequeño) y la historia PLE (~118 MB fijos
  por checkpoint, no por token).
- Ojo: un checkpoint solo vale mientras las celdas posicionales de debajo conservan SUS tokens
  (`generate.cpp:1092`): persistir la KV sola no basta; hace falta el estado completo.

## Velocidades medidas

- NVMe (lectura 1 GiB del GGUF, `iflag=direct`): **3,7 GB/s** → 240 MB en **~65 ms**. RAM: orden de ~10 ms.

## Prefijo común por cliente

- Pendiente de 48 h de `cachelog` (agrupar por `phash`). Indicio: system prompts + herramientas se repiten entre
  sesiones; sin datos aún.

## ¿Restaura el motor estado de fichero?

- **No**: `conversation_snapshot.cpp` captura a memoria (sin `fopen`/`mmap`). Persistir y restaurar sería código
  nuevo (formato + validez posicional + versión del modelo).

## Techo

- Sesión nueva con prefijo compartido de 20K: prefill hoy ~22 s (20K a ~900 tok/s) → con KV persistente
  **~0,1 s**. Ahorro ≈ **20 s por sesión nueva**.
- Solo si el prefijo es byte-idéntico (system prompts estables) y el motor aprende a restaurar de fichero.
