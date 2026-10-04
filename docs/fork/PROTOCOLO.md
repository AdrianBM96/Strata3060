# Protocolo de trabajo (desde 2026-10-04)

**Claude orquesta; el agente de bazzite ejecuta.** El objetivo es gastar lo mínimo de los límites de uso de Claude.

| | Claude | Agente de bazzite |
| --- | --- | --- |
| Hace | Planes y órdenes concretas, auditoría del código y de las medidas, la decisión de qué se aplica | Implementa el código, compila, prueba, mide, despliega |
| No hace | Ejecutar pruebas, implementar cambios largos | Decidir qué se adopta sin la validación de Claude |

## Cómo se comunican

**Claude escribe en `docs/fork/ORDENES.md`** una lista de órdenes numeradas. Cada orden lleva:

- **qué hacer**: ficheros y comportamiento esperado, con `fichero:línea` si hace falta;
- **cómo comprobarlo**: pruebas, medidas y criterio de éxito;
- **qué entregar**.

**El agente:**

- implementa en commits pequeños, uno por orden;
- mide con el protocolo de siempre (`METODO_MEDICION.md`: alternar, 6+6, `bench.py compare`, `logpos-compare` para la
  calidad);
- escribe el resultado en `docs/fork/ENTREGAS.md`, con el número de orden, el commit, las cifras y el veredicto
  propuesto;
- no activa en producción nada que cambie el resultado sin la validación de Claude.

**Claude** lee `ENTREGAS.md` y los diffs, valida o pide correcciones, y actualiza `ORDENES.md`.

**Entregas cortas.** Las cifras en tablas, sin repetir lo que ya está en otros documentos.

## Estado actual

**Antes de este protocolo, Claude escribió código** que el agente aún no ha compilado ni medido:

- la integración de **v0.1.39** (`2a05958`, `INTEGRACION_V0139.md`);
- **`STRATA_FETCH_ADMIT`** (`50581c2`, `RESPUESTA_FETCH_ADMIT.md`).

Son las órdenes 1 y 2 de `ORDENES.md`.
