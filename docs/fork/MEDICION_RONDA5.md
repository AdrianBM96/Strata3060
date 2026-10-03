# Ronda 5: veredicto de YaRN, A/B de sistema, y desglose de P

Fecha: 2026-10-03, sobre la [nota de decode](NOTA_DECODE_RONDA5.md) y el
[plan maestro](PLAN_MAESTRO.md). Etiquetas: **[medido]** aquí · **[est.]** estimación.

Corrijo primero un error mío que la nota señala: en `MEDICION_RONDA4.md` leí
`4,41 / 16,69 / 1,28` como milisegundos y son **expertos** por capa y ventana. De ahí el
acierto de la caché de VRAM: **74,6 %** (16,69/22,38); la CPU calcula 19,7 % y el PCIe 5,7 %.

---

## 1. YaRN: **no degrada la calidad** — los tres textos

Su método (`logpos-compare`), con suelo de ruido (dos corridas del mismo perfil) por texto:

| Texto | posiciones | top-1 igual | ΔNLL (YaRN − sin YaRN) | Veredicto |
| --- | ---: | ---: | ---: | --- |
| Código (`llama-model.cpp`) | 4.933 | 99,0 % | +0,0018 ± 0,0024 | sin diferencia |
| Documento (`DETAILS.md`, prosa) | 6.855 | 92,7 % | +0,0048 ± 0,0039 sobre el ruido | dentro del ruido (top-1 −2,3 pts) |
| **Conversación (turnos reales)** | 7.155 | 93,1 % | **−0,0371 ± 0,0072** | **YaRN es MEJOR** (5σ) |

**Conclusión: el perfil diario se queda en 512K con YaRN.** En código y documento el NLL está
dentro del ruido; en la conversación YaRN **mejora** de forma significativa (PPL 6,574 → 6,335).
No hay que renunciar al contexto por calidad.

Dos avisos de método, para que no se lean de más:
- El **top-1** de documento y conversación baja 2-7 puntos, y su criterio §1.3 lo marcaría; pero
  el mismo suelo de ruido del documento ya da 95,0 % (5 % de desacuerdo entre dos corridas
  idénticas), así que ese criterio es más ruidoso de lo que parece con un solo texto. El NLL (con
  su error estándar) es el que decide, y no penaliza.
- El suelo de la conversación salió **perfecto** (100 %, ΔNLL 0,0000): ahí el motor es
  determinista, lo que hace esa comparación la más fiable de las tres.
- `swift262` queda como alternativa, no como recomendación.

---

## 2. A/B de sistema (su §1)

| # | Prueba | Resultado |
| --- | --- | --- |
| 2 | **Gobernador de CPU** | Ya está en **`performance`** (y `energy_performance_preference=performance`). No hay nada que ganar |
| 1 | **Relojes de GPU** | Durante decode: **SM 1875 MHz de 2100** (89 %), potencia 109 W de 170, **83 °C**, persistencia *disabled*. La GPU no está a tope y va caliente |
| 3 | **Hilos SMT** (`--pool-workers 10`) | 39,20 · 38,80 · 41,70 · 40,90 → mediana **40,05** frente a **40,25** con 5 workers. **Neutro**, revertido |
| 5 | **Modo PCIe** (`--pcie-mode dma`) | en medición; se añade en el commit siguiente |
| 6-8 | `--kv-resident 16384`, páginas de 2 MB, `STRATA_ADAPT_NOWAIT` | pendientes (las páginas necesitan `sudo`) |

**Pendiente de ti (no tengo `sudo`):**
```bash
sudo nvidia-smi -pm 1
sudo nvidia-smi -lgc 2100,2100     # SM 1875 -> 2100 = hasta ~11 % en la fase P
```
Y mirar la refrigeración: **83 °C** sostenidos en una Victus 15L es la razón probable de que el
reloj no llegue al máximo.

---

## 3. `STRATA_VERIFY_PROFILE=1`: dónde se van los 21 ms de P

Una respuesta de 1.024 tokens, con `STRATA_DECODE_TIMING` + `STRATA_VERIFY_PROFILE`:

```
42,17 ms/ventana (532 ventanas)
GDN layers: waitB 7,69 · VRAM hits 5,29 · hc-read1+router 3,37 · q8+qkv/q-idx gemv 2,38
            · waitCPU 2,07 · head 1,72 · z 1,54 · hc0 up 1,34 · out-proj 1,29 · shared+quant 1,21
            · hc0 down 1,09 · waitA 0,29 · PCIe grp 0,78 · copy+combine 0,50
QSA layers: VRAM hits 1,90 · waitB 1,60 · q+q-idx 1,16 · hc-read1+router 1,12 · attention 0,84
            · hc0 up 0,45 · out-proj 0,45 · waitCPU 0,45 · shared+quant 0,41
```

Lectura: en las capas GDN, lo que manda es **`waitB` (7,69) y `VRAM hits` (5,29)**, más el
`hc-read1+router` (3,37). No hay un único kernel denso que domine; el tiempo está repartido entre
**esperas** y **cómputo de los expertos que sí están en VRAM**. Va con su §2, para que él decida qué
de sus cambios de motor (D3) ataca de verdad.

---

## 4. Lo que necesitamos de Claude

1. **F2 — el bucle de autoaprendizaje (§4.5).** Es lo que pediste: que el sistema aprenda de su uso
   sin etiquetas humanas. Su diseño (registro + etiquetas de System Two + auditoría aleatoria del
   5 % + campeón/aspirante + conformal) es sólido. **Lo escribe él** (sin GPU, con tests); nosotros
   lo desplegamos y lo probamos.
2. **V2 del motor:** leer los logprobs de la última ventana que ya lee el prompt, en vez de abrir una
   ventana de decode extra (~50 ms por pregunta). Es el ahorro más limpio de System One.
3. **F0 — el banco fijo (§1.1) como script**, para medir igual que él y que las cifras sean
   comparables.

No pedimos estimaciones nuevas: las dos últimas suyas salieron optimistas, y el plan ya lo dice.
Pedimos **código y el banco**, y nosotros medimos.
