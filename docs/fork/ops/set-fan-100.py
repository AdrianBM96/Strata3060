#!/usr/bin/env python3
"""Pone TODOS los ventiladores de la GPU al 100% y lo deja asi (salud de la tarjeta).

Necesita root (NVML devuelve NVMLError_NoPermission si no). Pensado para un servicio
de sistema oneshot al arrancar: el ajuste vive mientras el driver esta cargado, y al
reiniciar el driver vuelve a automatico, por eso hay que re-aplicarlo en cada arranque.

Uso:  sudo /home/bazzite/Strata/.venv/bin/python set-fan-100.py [porcentaje]
"""
import sys, time

try:
    import pynvml
except ImportError:
    print("falta pynvml (pip install nvidia-ml-py)", file=sys.stderr)
    sys.exit(2)

pct = int(sys.argv[1]) if len(sys.argv) > 1 else 100
pct = max(0, min(100, pct))

# El driver puede tardar un momento en estar listo tras el arranque: reintenta.
last = None
for attempt in range(20):
    try:
        pynvml.nvmlInit()
        last = None
        break
    except pynvml.NVMLError as e:
        last = e
        time.sleep(3)
if last is not None:
    print(f"NVML no responde: {last}", file=sys.stderr)
    sys.exit(1)

ok = True
for i in range(pynvml.nvmlDeviceGetCount()):
    h = pynvml.nvmlDeviceGetHandleByIndex(i)
    nf = pynvml.nvmlDeviceGetNumFans(h)
    for f in range(nf):
        try:
            pynvml.nvmlDeviceSetFanSpeed_v2(h, f, pct)
        except pynvml.NVMLError as e:
            print(f"GPU {i} fan {f}: no se pudo fijar ({e})", file=sys.stderr)
            ok = False

# Verifica leyendo de vuelta.
for i in range(pynvml.nvmlDeviceGetCount()):
    h = pynvml.nvmlDeviceGetHandleByIndex(i)
    nf = pynvml.nvmlDeviceGetNumFans(h)
    for f in range(nf):
        try:
            v = pynvml.nvmlDeviceGetFanSpeed_v2(h, f)
            print(f"GPU {i} fan {f}: {v}%")
        except pynvml.NVMLError:
            pass
sys.exit(0 if ok else 1)
