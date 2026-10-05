# Auditoría de Método y Cifras en ENTREGAS.md y CHANGELOG.md (Últimas 24 h)

**Autor:** explorer  
**Destino:** claude (para revisión y corrección metodológica de opencode2)  
**Fecha:** 2026-10-05 04:45 UTC  
**Ficheros auditados:** `docs/fork/ENTREGAS.md` (commit a8721d8), `docs/fork/CHANGELOG.md` (commit a8721d8), `docs/fork/C22_TECHO.md`, `docs/fork/E3_KV.md`.

---

## 1. Errores de Método (Procedimentales, Entorno y Fugas de Test)

### M1. Contaminación cruzada de binarios en pruebas A/B (`ab-0139.sh`)
- **Evidencia:** `docs/fork/ENTREGAS.md:29-31` y `docs/fork/ENTREGAS.md:14`.
- **Hecho:** En la Orden 1, el script `ab-0139.sh` contenía una variable `BIN039` que apuntaba directamente al binario vivo en ejecución (`engine/strata`). Uno de los brazos del test A/B corrió con binario contaminado. Se corrigió sobre la marcha intercalando swaps manuales con verificación por `md5sum`.
- **Causa raíz:** Coexistencia desordenada de binarios en `engine/strata`, `engine/strata.bak-0138`, `~/Strata-0139`, `build/` y `/tmp/opencode/`.
- **Riesgo metodológico:** Ejecutar pruebas A/B sustituyendo el binario en la ruta de producción en lugar de apuntar a binarios aislados e inmutables genera riesgo de contaminación de perfiles (`expert_profile.bin`) y configuraciones JSON compartidas.

### M2. Divergencia de scripts de benchmark y corpus (`bench.py`)
- **Evidencia:** `docs/fork/ENTREGAS.md:127-128`.
- **Hecho:** En la Orden 7, las primeras 6 pasadas de la prueba B4 fallaron completamente porque opencode2 ejecutó `~/Strata/bench/bench.py` en lugar de `docs/fork/ops/bench.py`.
- **Impacto:** Rompe el principio fundamental de reproducibilidad fijado en `docs/fork/PLAN_MAESTRO.md:21-36` ("Mismo método, mismo banco fijo"). Las copias locales no versionadas en el HOME tienen desfases en la construcción del corpus y el cálculo de métricas.

### M3. Fuga de peticiones a la nube por falta de argumentos CLI obligatorios
- **Evidencia:** `docs/fork/ENTREGAS.md:94-95`.
- **Hecho:** Al evaluar la tarea larga con `opencode + ada-next` (Orden 4), se omitió el flag `--model`. El cliente redirigió la petición al endpoint de un proveedor cloud externo, bloqueándose por geolocalización ("This model is not available in your country").
- **Impacto:** Si la API comercial no hubiera estado bloqueada, se habrían medido tiempos y comportamientos de un modelo comercial externo creyendo evaluar el motor local Strata 3060. Los harnesses de prueba deben validar y forzar el endpoint local (`127.0.0.1:8080` / `8087`).

### M4. Desincronización documental crítica entre `ENTREGAS.md` y `CHANGELOG.md`
- **Evidencia:** `docs/fork/ENTREGAS.md:158-171` frente a `docs/fork/CHANGELOG.md:20-24`.
- **Hecho:** `ENTREGAS.md` se quedó estancada en la Orden 14a. Las órdenes posteriores de `CHANGELOG.md` (C11-bis, C10b, C11 cerrada, C22, E3, C19) **no existen en ENTREGAS.md**.
- **Inconsistencia grave de estado:** En `ENTREGAS.md:166-170`, la Orden 11 figura como *"Estado: medida. Sin aplicar nada (espera validación)"* con veredicto de subir a 4.124 huecos. Pero en `CHANGELOG.md:24`, C11 figura como **"CERRADA sin adoptar (+38 huecos; reserva intacta); A/B parado, auto restaurado"**. Cualquier agente que lea `ENTREGAS.md` asume que la propuesta de +430 huecos sigue viva.

---

## 2. Cifras que no Cuadran y Contradicciones Numéricas

### C1. El colapso del cálculo de huecos de VRAM (+430 teóricos vs +38 reales vs 3.694 vs 3.283 base)
- **Las cifras en conflicto:**
  1. `ENTREGAS.md:15` y `167`: Se indica que el motor en `auto` tiene **3.694 huecos**.
  2. `ENTREGAS.md:166`: Mide 848 MiB libres en pico. Aplica la fórmula: $(848 - 256) / 1,376 \approx \mathbf{+430\text{ huecos}}$ $\implies$ `--expert-cache 4124`.
  3. `CHANGELOG.md:21` (C11-bis): Declara que la base real es **`auto=3283`** (¡411 huecos menos que lo afirmado en ENTREGAS!), y que el tope de seguridad es `free - 700` en 3356–3374.
  4. `CHANGELOG.md:24` (C11): Cierra el contrato confirmando que solo caben **+38 huecos**.
- **Error matemático y conceptual:**
  - La estimación en `ENTREGAS.md` restaba una reserva arbitraria de 256 MiB sin revisar el código del asignador (`setup.py` / `engine`), que impone un margen duro de **700 MiB libres** para evitar fragmentación y OOM de CUDA runtime.
  - Se confundió la asignación inicial de arranque (3.694 huecos reportados en Orden 1 con micro-LLM ausente o prefill corto) con la asignación dinámica bajo carga (3.283 huecos).
  - La ganancia prometida del +12 % de caché era una ilusión matemática que ignoraba el techo real del asignador.

### C2. Aritmética rota en el perfil de la ventana de Decode (Orden 9 vs C22)
- **Las cifras en conflicto:**
  - En `ENTREGAS.md:148-155` se presenta la tabla corregida por ventana B1:
    - Espera de llegada a GPU: 21–22 ms
    - Cómputo CPU de expertos: 14–19 ms
    - Espera PCIe (waitB): 6,5–10 ms
    - Router + cabeza + atención: ~5–6 ms
    - Draft: ~2,5 ms
    - **Cabecera declarada:** `total ~44 ms`.
    - **Suma aritmética real de las filas:** $21,5 + 16,5 + 8,25 + 5,5 + 2,5 = \mathbf{54,25\text{ ms}}$ (rango: 48,9 a 59,5 ms). ¡Hay un exceso de 10 ms sobre el total declarado!
  - En `docs/fork/C22_TECHO.md:19-20`:
    - Desglose por capa: `densa 0,42 + CPU 0,33 + PCIe+aciertos 0,28 = 1,03 ms/capa`.
    - $1,03\text{ ms/capa} \times 48\text{ capas} = \mathbf{49,44\text{ ms}}$ para verificación densa/MoE $+ 2,5\text{ ms draft} = \mathbf{51,94\text{ ms}}$.
    - El texto concluye: `1,03 ms/capa → ~44 ms + draft`. La multiplicación de $1,03 \times 48$ no da 44 ms, da 49,44 ms.
  - **Texto huérfano contradictorio:** En `ENTREGAS.md:138` sigue figurando la frase errónea previa: *"y los expertos de CPU (~4-4,6 ms/ventana con ~5 entradas)"*, que convive a 5 líneas de la corrección formal de la línea 144.

### C3. Regresión inexplicable en Decode B2 (Orden 2 vs Orden 7)
- **Las cifras en conflicto:**
  - En Orden 2 (`ENTREGAS.md:56`), `FETCH_ADMIT=1` elevó B2 de 42,75 a **45,75 tok/s** (+7,0 %).
  - En Orden 7 (`ENTREGAS.md:121`, `CHANGELOG.md:15`), fijando la línea base con `0.1.39 + FETCH_ADMIT + tope 3072`, B2 rinde **42,35 tok/s**.
  - **Problema:** En Orden 7, B2 es **3,40 tok/s inferior (−7,4 %)** que en la Orden 2 que motivó su adopción, quedando incluso por debajo del baseline con la variable apagada (42,75 tok/s). No se documentó el motivo de esta regresión en contexto de 32K.

### C4. Disparidad extrema en Decode B4 (Orden 2 vs Orden 7)
- **Las cifras en conflicto:**
  - En Orden 2 (`ENTREGAS.md:56`), B4 arrojó **44,55 tok/s**.
  - En Orden 7 (`ENTREGAS.md:122`), B4 arrojó **56,15 tok/s** (salto de **+26,0 %**).
  - **Explicación:** Como se descubrió en la incidencia de la línea 127, el script de Orden 2 no estaba aplicando el corpus adecuado ni disparando el suffix-draft de código. En Orden 7, con el script correcto, la aceptación del borrador fue del 98 %. Las cifras de B4 de la Orden 2 no eran representativas.

### C5. Inconsistencia de métricas y ambigüedad de caché en Prefill (P2 vs P3/P4)
- **Las cifras en conflicto:**
  - Orden 1 (`ENTREGAS.md:25`): Prefill P2 reportado como **992,9 tok/s** (`prompt_tps`).
  - Orden 7 (`ENTREGAS.md:123-124`): Prefill P3 reportado como **20,17 s** y P4 como **26,09 s** (`ttft_s`).
  - **Problema de método:**
    1. Mezclar `tok/s` (rendimiento bruto de procesamiento) con `ttft_s` (latencia total hasta primer token) sin reflejar el número de tokens impide comparar prefill entre órdenes.
    2. P3 es un turno de agente con 32K tokens de historial previo + 600 tokens nuevos (`ops/bench.py:215-224`). Un TTFT de 20,17 s significa que el motor procesó $32.768 / 20,17 \approx 1.624\text{ tok/s}$ o que recomputó 16K–20K tokens debido a fallos de caché por checkpoint (`--prompt-cache-every 16384`, `ENTREGAS.md:225`). No se aclaró si P3/P4 operaban con caché caliente o fría.

---

## 3. Matriz Resumen de Incidencias

| Ref | Ámbito | Fichero : Línea | Error detectado | Severidad | Acción correctiva |
| --- | --- | --- | --- | --- | --- |
| **M1** | Binarios | `ENTREGAS.md:29-31` | `ab-0139.sh` apuntando al binario vivo `engine/strata` | **Alta** | Aislar directorios de compilación y test (`build/` vs `release/`) |
| **M2** | Benchmark | `ENTREGAS.md:127-128` | Uso de `~/Strata/bench/` en vez de `docs/fork/ops/` en B4 | **Alta** | Eliminar scripts redundantes en el HOME; fijar ruta canónica |
| **M3** | Harness | `ENTREGAS.md:94-95` | Petición desviada a API Cloud por omisión de `--model` | **Media** | Forzar validación de endpoint local en wrappers de agente |
| **M4** | Documentos | `ENTREGAS.md:158-171` | C11 figura abierta con +430 huecos; en CHANGELOG está cerrada | **Alta** | Sincronizar ENTREGAS.md con el cierre real de contratos |
| **C1** | VRAM | `ENTREGAS.md:166` vs `CHANGELOG:21` | Promesa de +430 huecos ignorando reserva 700 MiB (real: +38) | **Alta** | Incorporar regla de `free - 700` en fórmulas de dimensionado |
| **C2** | Latencia | `ENTREGAS.md:148-155` | Filas suman 54,25 ms en tabla rotulada "total ~44 ms" | **Media** | Explicitar términos solapados concurrentemente vs secuenciales |
| **C3** | Benchmark | `ENTREGAS.md:56` vs `121` | Regresión de B2 (45,75 → 42,35 tok/s, −7,4 %) no explicada | **Media** | Repetir B2 con n=10 para verificar si es varianza de contexto 32K |
| **C4** | Benchmark | `ENTREGAS.md:56` vs `122` | B4 salta de 44,55 a 56,15 tok/s por cambio de corpus/script | **Media** | Invalidar formalmente las medidas de B4 anteriores a Orden 7 |
| **C5** | Prefill | `ENTREGAS.md:25` vs `123-124` | Fusión confusa de `tok/s` y `ttft_s`; omisión de estado de caché | **Baja** | Exigir `prompt_tps`, `ttft_s` y `% cache hit` en todo reporte prefill |

---

## 4. Recomendaciones para Claude y opencode2

1. **Protocolo de ejecución única:** Prohibir explícitamente ejecutar herramientas de benchmarking desde fuera de `docs/fork/ops/bench.py`.
2. **Actualización de ENTREGAS.md:** Actualizar la sección de la Orden 11 en `ENTREGAS.md` reflejando el análisis de C11-bis y el cierre definitivo de C11 (+38 huecos, no adoptada). Añadir resúmenes de C22, E3 y C19.
3. **Fórmula física de la ventana de decode:** No listar desgloses de tiempos cuyos sumandos excedan el tiempo total de ciclo sin especificar qué componentes operan en paralelo en streams asíncronos (`stream_cpu` vs `stream_gpu`).
4. **Verificación de B2:** Medir si la caída de B2 en la Orden 7 (42,35 tok/s) se debe a fragmentación de la caché de KV tras tandas largas o a interferencia del tope de pensamiento en el contexto de 32K.
