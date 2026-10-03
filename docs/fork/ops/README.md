# Ficheros de operación (los que faltaban para poder revisarlos)

Estos ficheros viven en la máquina de despliegue, no en el repo del motor. Se incluyen aquí para que
la configuración sea **reproducible y revisable**.

| Fichero | Qué es |
| --- | --- |
| `ada-decide.py` | El servicio System One (`POST /v1/systemone`, :8087). Contrato Jev: `choice`/`score`/`noul`. |
| `apply-tuning.sh` | Reaplica el tuneo a los `strata-*.json` (setup los reescribe y borra las claves nuestras). |
| `free-vram.sh` | Aparta el modelo de llama.cpp antes de que Strata cargue (`before_load`). |
| `strata-switch.sh` | Cambia de perfil Strata: `swift` (512K, diario), `swift262` (262K sin YaRN), `coder`, `qwen` (visión). |
| `serve-strata.sh` | El arranque del servidor (carga perezosa; sin `--lazy` si hay visión). |

Ver también: [`../SYSTEMONE.md`](../SYSTEMONE.md) (motor + API), [`../README_ADA.md`](../README_ADA.md)
(operación), [`../AUDITORIA_REVISION_3060.md`](../AUDITORIA_REVISION_3060.md) (esta auditoría).
