# Cambiar la arquitectura antes de exprimirla

Fecha: 2026-10-04. Autor: Claude (orquestador). Para: Adrián y el agente de bazzite.

## 1. Por qué un plan nuevo

Hasta ahora casi todo ha sido **ajustar** Strata: flags, políticas de caché, kernels. Eso ha dado:

- +7 % con la 0.1.39;
- +5-7 % con `FETCH_ADMIT`;
- el resto, dentro del ruido.

Strata ganó a llama.cpp **cambiando la forma del cálculo**, no ajustándolo. Hagamos lo mismo, ahora para **nuestra**
máquina:

- **un cable lento:** PCIe a 11 GB/s, que solo se arreglaría en la BIOS;
- **una RAM lenta:** 2133;
- **una GPU que pasa horas limitada por potencia;**
- **un uso agéntico,** que copia mucho código del contexto.

## 2. Dónde se va el tiempo hoy

Medido en `MEDICION_RONDA5.md` §3: 42 ms por ventana y **1,92 tokens por ventana** en B1.

| Partida | ms/ventana | % |
| --- | ---: | ---: |
| **Esperar la copia PCIe de los fallos (`waitB`)** | **9,3** | **22 %** |
| Calcular los aciertos en VRAM | 7,2 | 17 % |
| Leer las hiperconexiones + el router | 4,5 | 11 % |
| Esperar a la CPU (`waitCPU`) | 2,5 | 6 % |
| El resto (atención, proyecciones, cabeza...) | ~18 | 44 % |

**Las dos palancas grandes no son kernels:**

1. **El 22 % es esperar al cable.** Y la copia de la capa L solo puede empezar cuando el router de la capa L ha
   decidido. **El cable está parado mientras la GPU calcula la capa anterior.**
2. **Los 1,92 tokens por ventana multiplican todo.** Cada ventana paga la parte densa entera. Con 2,5 tokens por
   ventana, el decode sube ~30 % con el mismo hardware.

## 3. Los tres cambios de arquitectura

Van ordenados por lo que se puede ganar y lo que se arriesga. Ninguno existe en Strata: lo he buscado en `src/`, y
no hay precarga entre capas ni rutas que sepan qué hay en la caché.

### A. Precarga por predicción (*pre-gating*): **mismo resultado, bit a bit**

**La idea:** al empezar la capa L, aplicar **el router de la capa L+1** al estado que ya tenemos.

- **El estado del que se parte:** el que lee la capa L. El *residual* cambia poco de una capa a la siguiente.
- **Lo que sale:** los expertos que L+1 probablemente pida. Los que no estén en VRAM y vayan a la parte PCIe **se
  empiezan a copiar ya.**
- **El solape:** la copia ocurre mientras la GPU calcula la capa L (~0,7 ms), y la de cada capa es de ~0,2 ms.
- **Cuando L+1 decide de verdad:**
  - los acertados ya están en la tarjeta;
  - los fallados siguen el camino de hoy.

**Propiedades:**

- **Mismos bytes calculados con los mismos datos:** texto idéntico.
- **El coste de fallar la predicción** es una copia inútil, en un cable que en ese momento estaba parado.
- **El coste de predecir** es un GEMV pequeño (estado × 512) por capa.

**Techo:** casi todo el 22 % de `waitB`. Si la predicción acierta el 80 % de los fallos, son ~7 ms de 42 ms: **~+20 %
de decode.**

**Lo que no sé:** cuánto acierta en este modelo. Con 512 expertos y hiperconexiones no hay dato publicado. En otros
MoE, la literatura da 80-95 % (Pre-gated MoE, ProMoE, AdapMoE). Primero se mide (orden 15).

### B. Rutas que saben qué hay en la caché (*cache-aware routing*): **cambia el texto; opcional y con puerta de calidad**

El router elige 10 de 512 expertos. Las puntuaciones del 9.º, 10.º, 11.º o 12.º suelen estar muy cerca.

**La idea:**

- Si un experto elegido **no está en VRAM**, y uno **residente** que no se eligió tiene una puntuación casi igual
  (diferencia < δ), se usa el residente.
- Su peso es el suyo, renormalizado.

**El efecto:** quita a la vez parte de `waitB` y de `waitCPU`, y sube el acierto de la caché sin copiar nada.

**El precedente:**

- Qualcomm, *Mixture of Cache-Conditional Experts* (2024-25): ~2× en móviles con pérdida mínima.
- Strata no lo hace: su ruta es exacta.

**El riesgo** es la calidad en código, donde un token mal cuesta caro. Por eso:

- va con variable, apagada por defecto;
- δ pequeño;
- y solo se adopta si `logpos-compare` queda en el ruido **y** las tareas largas de agente siguen 25/25.

Primero se mide cuántos fallos tienen un residente "casi igual" (orden 16). Si son pocos, se descarta sin escribir el
cambio.

### C. Un borrador hecho a nuestro uso: **el mismo resultado; los tokens por ventana suben**

**Lo que acepta el borrador:**

- Al copiar código (B4), el 99 %.
- En texto normal (B1), 1,92 tokens por ventana.

La cabeza MTP es genérica. Nuestro tráfico es casi todo agentes de código.

- **A medio plazo:** adaptar la cabeza MTP (una capa) a nuestras propias sesiones. Se entrena con las salidas del
  modelo grande, como EAGLE o el *online speculative decoding*. El modelo grande decide igual, así que **la calidad no
  cambia**: solo cuántos tokens se aceptan.
- **Primero hace falta saber dónde falla el borrador:**
  - en qué posición;
  - en qué tipo de contenido: código nuevo, prosa, JSON de herramientas, pensamiento.

  El parche `suffix-draft-stats` da parte de eso. Es un proyecto grande, así que no lo ordeno hasta ver los números
  (orden 17).

## 4. Qué pasa con lo ya ordenado

| Orden | Queda | Por qué |
| --- | --- | --- |
| 5, 6, 7, 10 | sí | Baratas. La 7 es la base contra la que se miden A, B y C |
| 8 (políticas de caché) | sí, pero baja de prioridad | Como mucho dará unos puntos; A y B atacan lo mismo con más techo |
| 9 (perfil de GPU) | sí, **sube**: decide A | Dice cuánto de `waitB` sigue a la vista con la 0.1.39 + `FETCH_ADMIT` |
| 11 (VRAM) | sí | Barata |
| 14a (capa MTP) | sí | Barata, y si sale da huecos |
| 14b (predicción entre capas en el simulador) | **la sustituye la 15**, que mide el predictor bueno (el router de verdad) | El simulador solo ve ids; el router ve el estado |

## 5. El orden

```
ahora:      15 (medir el pre-gating) · 16 (medir los "casi iguales") · 17 (dónde falla el borrador)
            — instrumentación opcional, apagada por defecto; no cambian nada en producción
después:    si 15 ≥ 60 % de los fallos PCIe → escribir A (bit a bit)          → A/B contra la 7
            si 16 da ≥ 30 % de fallos con un residente cercano → escribir B   → puerta de calidad
            con 17 → decidir si C merece el proyecto
```
