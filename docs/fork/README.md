# Strata3060: el fork

Este fork (`AdrianBM96/Strata3060`) parte de [Niko1221/Strata](https://github.com/Niko1221/Strata) 0.1.38
(commit `99f3dbd`) con dos objetivos:

1. **Que Strata sea lo mejor posible en una RTX 3060** (12 GB, y la de 8 GB) con un PC barato: CPU AVX2 sin AVX-512,
   DDR4, 32-64 GB de RAM y a menudo PCIe 3.0. Más velocidad y más contexto, **sin perder calidad**.
2. **Ver si puede correr también GLM-5.3-Flash**, para llevar ese modelo a hardware de consumo.

Estado (2026-10-03): **auditoría y plan**. Siete agentes revisaron el código en paralelo, cada uno con un frente:
VRAM, kernels CUDA, CPU/RAM/PCIe, decode y contexto, instalador, investigación de GLM y portabilidad. Todavía no hay
cambios en el motor ni en el instalador. Ninguna cifra está medida en una RTX 3060: las estimaciones van marcadas
como tales.

| Documento | Qué contiene |
| --- | --- |
| **[PLAN_RTX3060.md](PLAN_RTX3060.md)** | Qué limita a una 3060, qué esperar hoy, y el plan por fases (medir, configuración, CPU, VRAM, MTP, kernels) con ganancia estimada, riesgo y esfuerzo |
| **[GLM_AUDITORIA.md](GLM_AUDITORIA.md)** | Qué es GLM-5.3-Flash, cuánta memoria necesita, qué se reutiliza del motor, cuánto cuesta portarlo y qué opciones hay |
| [informes/](informes/) | Los 7 informes completos, con citas `archivo:línea` |
| [informes/prototipos-cpu/](informes/prototipos-cpu/) | Prototipos de kernels AVX2 medidos (IQ2_S por bloques, Q2_0 en planos) y sus arneses |
| [informes/simulaciones/](informes/simulaciones/) | Simulaciones de la longitud del borrador MTP y de la VRAM |
| [informes/glm-datos/](informes/glm-datos/) | `config.json`, plantilla de chat y scripts de cálculo de memoria de GLM-5.3-Flash |

## Lo más importante en cinco líneas

- **En una 3060 manda la CPU, no la GPU.** Con 12 GB, la parte de cada ronda que hace la CPU tapa a la GPU. El mayor
  margen está en los kernels AVX2: dos prototipos dan x1,2-1,3 (idéntico bit a bit) y x2,3-2,6 en el kernel
  [medido]; +10-20 % de decode entre las cuatro mejoras de CPU [est.].
- **El contexto ya lo resuelve el motor:** con KV streaming, 128K ocupan la VRAM de 32K. Falta que el instalador lo
  ofrezca por defecto en tarjetas de 12 GB.
- **El instalador no hace en una 3060 con 32 GB lo que dicen las docs:** instala Q2_0 leyendo del SSD en vez del
  Coder en RAM. Además, un fallo en la prueba de RAM deja la KV en VRAM en un Linux con 64 GB a 128K.
- **Total estimado del plan: +15-30 % de decode** y x4 de contexto por defecto, con la misma calidad. A medir en la
  Fase 0.
- **GLM-5.3-Flash tiene 320 B de parámetros** (más del doble que Qwen3.8-Flash-Next). Su arquitectura se parece mucho
  a la de Qwen, pero sus expertos (~90 GB a 2 bits) no caben en 32-64 GB de RAM. Estimado: ~1-7 tokens/s en un PC
  así, 11-26 con 128 GB. Portarlo cuesta ~51-80 semanas-persona.
