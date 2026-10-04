# Ronda 7: el PCIe, B4, y lo que el perfil nos dijo mal a los dos

Fecha: 2026-10-04, sobre `RESPUESTA_RONDA7.md` (commit `9727998`). Etiquetas: **[medido]** aquí.

---

## 1. Su corrección de mi lectura del perfil: **aceptada**

Dije que el 55 % de `ExpertPool::worker` "apunta a espera". Tenía razón en el qué y me faltaba el
porqué: **es espera activa por diseño** — los hilos giran con `_mm_pause` mientras la GPU hace su
parte, y solo duermen pasado `spin_before_sleep_` (`pool.cpp:397-440`). **No es tiempo recuperable.**

Y corrige su propia lectura de la ronda 6: **el lado largo de cada capa es la GPU**, no la CPU. Con
nuestro desglose, la cadena de la GPU tras el router es **~19 ms/ventana** (compartido 1,6 + aciertos
7,2 + copia 9,3 + grupos 0,8) y `waitCPU` solo 2,5 ms. La CPU acaba antes y espera: eso es el 55 %.

También: **su kernel de IQ2_S casi no se usa** — los kernels propios solo entran con **2+ tokens por
experto** (`STRATA_IQ_MT_MIN`, `native_expert.cpp:92-114`); en decode, con 1 token, va el `vec_dot` de
ggml. Eso explica el +1,4 % (ruido). **Da por agotados los kernels de CPU con 1 token**, con una tabla
de 6 variantes medidas contra ggml: ninguna gana.

---

## 2. El enlace PCIe: **Gen 4 x16, correcto** — pero la sonda da 11 GB/s

Lo que pedía comprobar (durante una generación, no en reposo):

```
nvidia-smi: pcie.link.gen.current = 4, width.current = 16   (en reposo baja a Gen1: es ASPM)
lspci:      LnkCap: Speed 16GT/s, Width x16
            LnkSta: Speed 16GT/s, Width x16          <- Gen 4 x16, correcto
```

Y la sonda del motor, al arrancar:

```
strata generate: PCIe probe: 11.0 GB/s host->device (best of 11.0 11.0 10.9 10.7) -> pcie_frac 0.30
```

**El enlace es correcto (Gen4 x16, ~25 GB/s teóricos), pero la sonda mide 11 GB/s.** Es el caso que él
dejó abierto: *"si el enlace ya es Gen4 x16, la sonda y la copia por kernel dan menos que el bus;
pasadme la línea de la sonda y lo miro en el código"*. Aquí está. Dos hipótesis para él: la sonda mide
con memoria paginable (que rinde ~la mitad que la fijada), o `fetch_blobs` copia en trozos pequeños.
La copia son **9,3 ms por ventana**, la partida más grande del lado GPU.

---

## 3. B4: donde un agente de verdad trabaja, el motor ya vuela

Su test nuevo, reemitir 80 líneas de un fichero del contexto de 32K (lo que hace un agente al editar):

| | decode | aceptación de borradores |
| --- | ---: | ---: |
| **B4 (copiar del contexto)** | **53,0 tok/s** (47,0 / 53,0 / 53,4) | **98,4-99,1 %** |
| B1 (decodificación general) | ~40-42 tok/s | ~65 % |

**El +26 % y el 99 % de aceptación son el borrador por búsqueda en el prompt**, que B1-B3 casi no
ejercitan. Su lectura era exacta: **el `--spec 8` "sin mejora" se midió donde ese borrador no entra.**

`copied_ok: true` en las tres: copió las 80 líneas de verdad, no es una métrica vacía.

**Su A/B de B4, medido** (reinicio por medida, 3 pasadas cada uno):

| Config | decode | aceptación | Δ |
| --- | ---: | ---: | ---: |
| **defecto** (`--suffix-draft 3`, spec 4) | **53,0 tok/s** | 98,6 % | — |
| `--suffix-draft 0` | 47,7 tok/s | 95,8 % | **−10,0 %** |
| `--spec 8 --mtp-max-t 4` | 51,8 tok/s | 98,9 % | −2,3 % |

Dos conclusiones:

1. **El borrador por búsqueda vale ~10 %** en el caso real de un agente. Está activado por defecto:
   lo que había que hacer era *medirlo*, no cambiarlo.
2. **`--spec 8` empeora también aquí** (−2,3 %). El revertirlo fue correcto; su hipótesis (que en B4
   sí ayudaría) **no se cumple**. El defecto (spec 4 + búsqueda) es lo mejor de los tres.

**No hay cambio que hacer**: la configuración por defecto ya es la óptima para este caso.

---

## 4. Lo que queda por decidir

1. **`--max-context` 512K cuesta ~896 MiB de VRAM** (~680 huecos de expertos): las claves del indexador
   (~768 MiB) y la tabla RoPE (~128 MiB). A 256K se liberan ~448 MiB (~340 expertos). **Es bit a bit
   idéntico** para todo prompt que quepa. Es contexto contra velocidad, y la decisión es de Adrián:
   hay que mirar el prompt más largo real de vuestros agentes.
2. **El PCIe** (§2): la sonda y la copia dan menos que el bus. Es su frente.
3. **Los kernels de CPU con 1 token: agotados** (§1). No hay nada que rascar ahí.
