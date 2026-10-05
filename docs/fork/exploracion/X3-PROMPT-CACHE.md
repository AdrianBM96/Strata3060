# X3. Prompt-Cache con Agentes y Causa de Relecturas Íntegras

**Autor:** explorer  
**Destino:** claude (para opencode2 / adrian)  
**Fecha:** 2026-10-05 04:50 UTC  
**Ficheros analizados:** `include/strata/core/conversation_cache.hpp`, `include/strata/program/conv_cache.hpp`, `serve/server.py` (C10b), `docs/fork/ENTREGAS.md` (Órdenes 6 y 10), `docs/fork/CHANGELOG.md` (C10b), `docs/fork/E3_KV.md`.

---

## 1. Los Datos de C10 / C10b (Mediciones Reales)

En la auditoría de 1.872 peticiones reales de agentes (periodo 2026-10-02 $\to$ 2026-10-04, `ENTREGAS.md:202-228`), el tiempo total invertido por Strata en prefill fue de **10.582 segundos**:
- Reúso global de prefijos: **56 %** (8,76M tokens reutilizados de 15,61M tokens solicitados).
- TTFT proxy: mediana **309 ms** cuando hay reúso ($\ge 90\,\%$) frente a **1.682 ms** sin reúso ($0\,\%$).

### Dónde se fue el tiempo de prefill (10.582 s):
1. **161 relecturas íntegras grandes:** **6.507 s (el 61,5 % de todo el gasto)**.
2. **205 arranques en frío / nuevas sesiones:** **1.989 s (el 18,8 % del gasto)**.
3. Prefill residual de colas de herramientas y turnos normales: 2.086 s (19,7 %).

**El 80,3 % de todo el tiempo de prefill del servidor estuvo concentrado en las 161 relecturas íntegras y los rearranques.**

---

## 2. ¿Qué Causa Domina las Relecturas Íntegras?

El análisis de los logs de C10b (`server.py:1637-1647`: `api`, `ua`, `prompt`, `reused`, `resume`, `evictions`, `first_diff`, `phash`) y el código del motor (`conversation_cache.hpp`) revela la causa unívoca:

### A. La Causa Dominante: Expulsión por Contención de Slots (`evictions = 164`)
- **Evidencia cuantitativa:**
  - En el periodo auditado hubo exactamente **164 expulsiones (`evictions`) registradas**, cifra que calca casi 1:1 las **161 relecturas íntegras grandes**.
- **Causa raíz en la arquitectura (`include/strata/core/conversation_cache.hpp:220-231`):**
  - La configuración desplegada tenía `--conversation-cache-slots 4` (y `--conversation-cache-mib 8192`).
  - En la máquina conviven concurrentemente **al menos 4 clientes distintos**:
    1. Claude Code
    2. pi
    3. omp / opencode (con ada-next)
    4. ada-decide (:8087)
    5. Subagentes concurrentes generados por Claude Code o opencode.
  - La cola de conversaciones aparcadas es una `std::deque<SavedConversation> entries_` con política FIFO/LRU estricta (`conversation_cache.hpp:279`):
    ```cpp
    while (!entries_.empty() && (entries_.size() >= slots_ || bytes_ > budget_ - held - incoming)) {
        bytes_ -= entries_.front().bytes();
        entries_.pop_front();
        ++evictions_;
    }
    ```
  - **La colisión:** Cuando un agente o subagente ejecuta 3 o 4 llamadas sucesivas (p. ej. herramientas o subagentes), llena los 4 slots. La conversación aparcada del cliente principal (de 20K a 32K tokens) es expulsada al frente de la cola (`pop_front()`).
  - Cuando el cliente principal vuelve en su siguiente turno tras recibir la salida de la herramienta, su estado ha sido destruido de RAM.
  - Al no encontrar checkpoint ni KV en `best()` (`conversation_cache.hpp:196-211`), el motor emite `RESUME 0` y **relee los 25.000–32.000 tokens íntegros desde el token 0**.
  - A 990 tok/s, cada relectura de 25K tokens cuesta **~25 a 30 segundos de bloqueo**. 161 relecturas $\times$ ~40 s = **6.507 segundos quemados**.

### B. Causas Secundarias y Descartadas:
- **Compactación de contexto / Truncamiento por el cliente: DESCARTADA (0 casos).**  
  Ningún cliente en producción recortó turnos intermedios ni borró la cabecera.
- **Divergencia de prefijo en system prompt (`first_diff`): MENOR.**  
  Los system prompts de los clientes son estables. El hash de prefijo (`phash`) se mantuvo idéntico en sesiones del mismo cliente.
- **Granularidad de checkpoints intermedios (`--prompt-cache-every 16384`): AGRAVANTE.**  
  Al estar ausente el flag en la config original, Strata usó el defecto de 16.384 tokens (`generate.cpp:627`). Si una sesión de agente tiene 12.000 tokens y bifurca o retoma, al no haber alcanzado los 16.384 tokens, solo existía el checkpoint de raíz (`root_at` en ~256 tokens), obligando a recalcular 11.700 tokens.

---

## 3. Propuesta Concreta

Para eliminar el 61 % del gasto de prefill sin comprometer la estabilidad del sistema:

### 1. Ampliar los Slots de Conversación a 16 y la Cuota de RAM a 16 GiB
- **Factibilidad física en el hardware de bazzite:**
  - El equipo dispone de **62 GB de RAM DDR4-2133 física**.
  - Con el modelo Swift IQ2_XS y el arena cargados, quedan **~26 GB de RAM libres sin uso**.
- **Consumo real por conversación aparcada (`E3_KV.md:5-7`, `conversation_cache.hpp:32-64`):**
  - Una conversación de 32K tokens en INT8 ocupa:
    - KV: $12\text{ capas} \times 2\text{ heads} \times 256\text{ dim} \times 2\text{ (K+V)} \times 1\text{ B} = 12.288\text{ B/token} \approx \mathbf{384\text{ MB}}$.
    - Checkpoints de estado (GDN recurrente + PLE + colas QSA): **~118 MB fijos**.
    - Total por conversación completa de 32K tokens: **~502 MB en RAM**.
- **Ajuste propuesto en `strata-swift-iq2_xs.json`:**
  ```json
  "--conversation-cache-slots": "16",
  "--conversation-cache-mib": "16384",
  "--prompt-cache": "16"
  ```
  - 16 slots $\times$ 502 MB = **~8,0 GB de RAM** (queda margen de seguridad de >18 GB libres en el sistema).
  - Permite mantener en memoria simultáneamente:
    - 4 clientes principales (Claude Code, opencode, pi, ada-cli).
    - Hasta 3 subagentes por cliente sin expulsión mutua.
  - **Impacto estimado:** Reduce las 164 evictions a prácticamente **0**, recuperando **~5.500 a 6.000 segundos** de prefill (ahorro de más del 50 % del tiempo total de procesamiento de prompts).

### 2. Acortar el Intervalo de Checkpointing a 4.096 Tokens (`--prompt-cache-every 4096`)
- Añadir a `strata-swift-iq2_xs.json`:
  ```json
  "--prompt-cache-every": "4096"
  ```
- **Razón:** En sesiones largas de agentes que van creciendo paso a paso (4K $\to$ 8K $\to$ 12K $\to$ 16K $\to$ 20K), guardar un checkpoint cada 4.096 tokens garantiza que cualquier bifurcación o reintento de herramienta nunca tenga que recalcular más de 4.096 tokens (~4 s a 990 tok/s) en lugar de los 16.384 tokens (~16 s) actuales.

### 3. Política de Desalojo con Cuota Mínima por Cliente (Anti-Monopolio de Subagentes)
- **Problema de `conversation_cache.hpp:225`:**
  `make_room` desaloja con `entries_.pop_front()` (el más antiguo global). Si un subagente entra en un bucle de 5 llamadas de herramientas, desaloja toda la cola.
- **Mejora en C++ (`eviction_victim` / `make_room`):**
  Incorporar en `ConversationCache` una política de cuota mínima:
  - Ningún cliente puede ocupar más de $N_{\text{slots}} / 2$ slots si hay otros clientes esperando.
  - Al buscar víctima de expulsión, desalojar preferentemente la conversación más antigua del **cliente que más slots acumule**, preservando al menos 1 slot por `phash`/cliente activo.

### 4. Supresión de Rearranques en Días de Agente (Vínculo con X1)
- Como se documentó en X1, retirar `--lazy` de `serve-strata.sh` para que el motor no sufra reinicios innecesarios.
- Los 205 arranques en frío que costaron 1.989 s se reducirán drásticamente, consolidando la persistencia de los prefijos en RAM.

---

## 4. Resumen de la Respuesta para Claude

1. **¿Qué causa domina las relecturas íntegras?**  
   La **expulsión por contención de slots** (`evictions = 164`, que explica las 161 relecturas íntegras de 6.507 s). Con solo 4 slots para $\ge 4$ clientes y subagentes solapados, la cola FIFO desaloja los contextos de 25K–32K tokens entre turnos de herramientas.
2. **Propuesta concreta:**  
   - Subir `--conversation-cache-slots` a **16** y `--conversation-cache-mib` a **16384** (coste: ~8 GB de RAM en un sistema con 62 GB, dejando >18 GB libres).
   - Fijar `--prompt-cache-every 4096` (evita recálculos de hasta 16K en turnos intermedios).
   - Desalojo proporcional por cliente para evitar que un subagente monopolice la caché.
