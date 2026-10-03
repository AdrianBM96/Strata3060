# Ficheros de operación (los que faltaban para poder revisarlos)

Estos ficheros viven en la máquina de despliegue, no en el repo del motor. Se incluyen aquí para que
la configuración sea **reproducible y revisable**.

| Fichero | Qué es |
| --- | --- |
| `ada-decide.py` | El servicio System One (`POST /v1/systemone`, :8087). Contrato Jev: `choice`/`score`/`noul`. Permutaciones, calibración contextual, temperatura, señales para escalar (`margin`, `option_mass`, `escalate`) y log JSONL. |
| `fit-calibration.py` | Ajusta la temperatura (y decide si conviene la calibración contextual) con decisiones etiquetadas del log de `ada-decide.py --log`. |
| `test_ada_decide.py` | Tests sin GPU de los dos anteriores (`python3 test_ada_decide.py`). |
| `apply-tuning.sh` | Reaplica el tuneo a los `strata-*.json` (setup los reescribe y borra las claves nuestras), incluidos `--kv int8`, `--prompt-cache-root 256`, `--logprobs 32` y `"draft_vocab": "en"`. |
| `free-vram.sh` | Aparta el modelo de llama.cpp antes de que Strata cargue (`before_load`). |
| `strata-switch.sh` | Cambia de perfil Strata: `swift` (512K, diario), `swift262` (262K sin YaRN), `coder`, `qwen` (visión). |
| `serve-strata.sh` | El arranque del servidor (carga perezosa; sin `--lazy` si hay visión). Copia el subconjunto de borradores del config antes de arrancar. |

Ver también: [`../SYSTEMONE.md`](../SYSTEMONE.md) (motor + API), [`../README_ADA.md`](../README_ADA.md)
(operación), [`../AUDITORIA_REVISION_3060.md`](../AUDITORIA_REVISION_3060.md) (la auditoría) y
[`../REVISION_CONFIG_3060.md`](../REVISION_CONFIG_3060.md#ronda-2-tras-la-auditoría-medida) (la ronda 2).
