# Respuesta a MEDICION_RONDA10.md y ANALISIS_FORK_ARCHITECTDS.md: portad los cambios del fork, en este orden

Para: el agente del servidor. Fecha: 2026-10-04.

## 1. Lo que cierra la ronda 10

- **Enfriar la GPU no da tok/s.** El ventilador al 100 % quita el frenado térmico (26-33 s → 0-1 s por tanda) sin
  mover la mediana. El límite de potencia tampoco. Con esto, **el hardware y la configuración del PC quedan
  cerrados**.
- **`STRATA_PF_FUSED=1`: bien adoptado.** +5,2 % en dos A/B independientes, con los brazos `off` antes y después.

## 2. La puerta de calidad que pedís para `PF_FUSED` ya existe

Es `ops/logpos-compare.py`, la de YaRN: teacher-forcing, con acuerdo de top-1, KL, diferencia de NLL con su error
estándar, y un suelo de ruido medido con una segunda pasada.

Un detalle: `PF_FUSED` solo actúa en el **camino por lotes** del prompt (colas > 1.024 tokens), y `logpos` escribe los
tokens que pasan por las **ventanas**. Así que la prueba se monta en dos peticiones:

1. **Arrancad con `STRATA_LOGPOS=<fichero> STRATA_LOGPOS_TOPK=20 --short-read 4096 --adapt-every 100000`**, con
   `STRATA_PF_FUSED=0` (A) o `1` (B).
2. **Petición 1:** un contexto largo (8-12K tokens de código vuestro), con `max_tokens=1`. Va por lotes, que es donde
   actúa el flag.
3. **Petición 2:** el mismo contexto más una continuación fija de ~2.000 tokens (código y texto), con
   `max_tokens=1`. La continuación (< 4.096) va por ventanas y queda en el fichero: cada logprob depende del estado
   que dejó la lectura por lotes.
4. Repetid A para el suelo de ruido, y comparad:
   ```bash
   python3 logpos-compare.py A.tsv B.tsv --floor A2.tsv
   ```

**Criterio:** la diferencia de NLL de A-B dentro de la de A-A2. Si sale fuera, decídmelo antes de dejarlo puesto.

## 3. El trabajo: portar del fork `architectds/Strata`, de menor a mayor riesgo

Vuestro análisis es bueno: comparte base (`99f3dbd`) y casi todo lo útil para una 3060 está identificado. Portadlo en
este orden, **cada cambio en su propio commit y desactivable**, para poder medirlo y quitarlo solo:

1. **`STRATA_GR_DOWN_MAX4=1`** (`4c3f599`, #443).
   - Según el fork, **idéntico bit a bit** y +2,1/+2,4 % de decode en Blackwell.
   - Se activa con variable: si el cherry-pick entra limpio, no cambia nada sin ella.
2. **MTP chain / early** (`bce7fbb`).
   - Bit-idéntico según el fork: una espera por ronda de borrador en vez de por paso.
   - Si no trae variable para apagarlo, añadidle una (`STRATA_MTP_CHAIN=0`) antes de medir.
3. **Kernels AVX-VNNI + gather para i-quants** (`f42c58f`).
   - Choca con mi `row_dot_iq2s` en `iq_avx2.cpp`. Al resolverlo, quedaos con los dos y que mi interruptor
     `STRATA_IQ2S_BLOCK` siga funcionando.
   - Pasad `tests/core/iq2s_avx2_test.cpp`: tiene que seguir dando bit a bit.
   - Esperad poco: con 1 token por experto manda ggml (mi ronda 7).
4. **CPU assist en prefill** (`STRATA_PREFILL_CPU`): **el último, y solo si 1-3 salen bien.**
   - No es bit-exacto: 1,3-1,9 % L2 por experto, según vuestro análisis.
   - Pasa por la puerta de §2 además del A/B de velocidad.
   - Medidlo con P3 y P4 de `bench.py`: colas de 600 y 4.000 tokens tras 32K, la espera real de un agente.

**No portéis** lo multi-GPU (layer split, peer, pipeline), PDL (sm_90+), la KV Q4_0 en el prompt, ni los chunks por
tamaño de prompt: vuestra propia tabla dice que no aplican.

**Para cada cambio:**

- **Compilad** con `-DCMAKE_CUDA_ARCHITECTURES=86` y pasad sus tests y los arneses de paridad que traiga:
  `ctest -R "parity|iq2s|profile"`, más los que añada el commit.
- **Bit a bit:** con `STRATA_IQ_MT_MIN=1 --prompt-cache 0 --adapt-swaps 0 --pcie-frac 0`, el texto de B1 con temperatura
  0 debe ser idéntico con el cambio activado y sin él. Esto no aplica al 4, que va por §2.
- **Velocidad:** A/B alternando 6+6. B1, B2 y B4 para decode; P3, P4 y `bench-prefill.py` para prefill.
- **Si no se distingue del ruido,** se queda apagado. Si gana, se activa en `serve-strata.sh`, como `PF_FUSED`.
- **Documentad** de qué commit del fork sale cada cambio y cómo se resolvió cada conflicto.

**Ojo con `generate.cpp`:** el parche de System One y el de contadores del borrador también lo tocan. Después de cada
cherry-pick, comprobad que los dos se siguen aplicando (`git apply --check`), o regeneradlos.

## 4. Lo que sigue pendiente

1. **La VRAM al arrancar y el prompt más largo real**, para la decisión de `--max-context`.
2. **`STRATA_HIT_GY=16` y `24`.**
3. **`STRATA_PROFILE_HEAT_MIN=200000`**, dejado unos días.

**De mi lado:** reviso cada port que subáis: el diff contra el commit del fork, los conflictos resueltos y que nada
cambie con la variable apagada.
