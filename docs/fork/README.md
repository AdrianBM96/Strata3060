# Strata3060: el fork

Este fork (`AdrianBM96/Strata3060`) parte de [Niko1221/Strata](https://github.com/Niko1221/Strata) 0.1.38
(commit `99f3dbd`) con un objetivo: **que Strata sea lo mejor posible en una RTX 3060** (12 GB, y la de 8 GB) con un
PC barato: CPU AVX2 sin AVX-512, DDR4, 32-64 GB de RAM y a menudo PCIe 3.0. Más velocidad y más contexto, **sin
perder calidad**.

Estado (2026-10-03): **auditoría y plan**. Cinco agentes revisaron el código en paralelo, cada uno con un frente:
VRAM, kernels CUDA, CPU/RAM/PCIe, decode y contexto, e instalador. Esta rama incluye además una **modificación propia del motor** (System One / `--logprobs`,
ver `CONFIG_MEDIDA_3060_Y_SYSTEMONE.md` y su parche) y las **primeras cifras medidas en una RTX 3060**
reales. El resto del plan sigue sin cambios en el motor ni en el instalador; las estimaciones van marcadas
como tales.

| Documento | Qué contiene |
| --- | --- |
| **[PLAN_RTX3060.md](PLAN_RTX3060.md)** | Qué limita a una 3060, qué esperar hoy, y el plan por fases (medir, configuración, CPU, VRAM, MTP, kernels) con ganancia estimada, riesgo y esfuerzo |
| [informes/](informes/) | Los 5 informes completos, con citas `archivo:línea` |
| [informes/prototipos-cpu/](informes/prototipos-cpu/) | Prototipos de kernels AVX2 medidos (IQ2_S por bloques, Q2_0 en planos) y sus arneses |
| [informes/simulaciones/](informes/simulaciones/) | Simulaciones de la longitud del borrador MTP y de la VRAM |
| **[CONFIG_MEDIDA_3060_Y_SYSTEMONE.md](CONFIG_MEDIDA_3060_Y_SYSTEMONE.md)** | La configuración real medida en una 3060 (decode, prefill, contexto, latencias), la convivencia con llama.cpp, y la **modificación del motor para System One** (`--logprobs N` + `ada-decide`), con el parche re-aplicable de 84 líneas |
| **[REVISION_CONFIG_3060.md](REVISION_CONFIG_3060.md)** | Revisión de esa configuración: qué cede inteligencia sin decirlo (YaRN, KV `q4_0`), qué ajustes y A/B dan más velocidad, y cómo bajar la latencia y calibrar System One |
| **[AUDITORIA_REVISION_3060.md](AUDITORIA_REVISION_3060.md)** | Auditoría de la revisión anterior: veredicto de cada punto, con las mediciones que la sostienen o la refutan |
| **[MEDICION_RONDA2.md](MEDICION_RONDA2.md)** | Ronda 2: verificación de las correcciones, `draft_vocab en` medido, `--spec 8` (+5,1 %) y el aviso de que `escalate` dispara al revés en el modelo real |
| **[SYSTEMONE.md](SYSTEMONE.md)** | System One completo: el cambio del motor, la API, operación, actualización y rollback |
| **[ops/](ops/)** | Los ficheros de despliegue que faltaban (`ada-decide.py`, `apply-tuning.sh`, `free-vram.sh`, `strata-switch.sh`, `serve-strata.sh`) |

## Lo más importante en cuatro líneas

- **En una 3060 manda la CPU, no la GPU.** Con 12 GB, la parte de cada ronda que hace la CPU tapa a la GPU. El mayor
  margen está en los kernels AVX2: dos prototipos dan x1,2-1,3 (idéntico bit a bit) y x2,3-2,6 en el kernel
  [medido]; +10-20 % de decode entre las cuatro mejoras de CPU [est.].
- **El contexto ya lo resuelve el motor:** con KV streaming, 128K ocupan la VRAM de 32K. Falta que el instalador lo
  ofrezca por defecto en tarjetas de 12 GB.
- **El instalador no hace en una 3060 con 32 GB lo que dicen las docs:** instala Q2_0 leyendo del SSD en vez del
  Coder en RAM. Además, un fallo en la prueba de RAM deja la KV en VRAM en un Linux con 64 GB a 128K.
- **Total estimado del plan: +15-30 % de decode** y x4 de contexto por defecto, con la misma calidad. A medir en la
  Fase 0.
