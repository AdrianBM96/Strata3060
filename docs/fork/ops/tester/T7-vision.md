# T7 — visión (imagen con pregunta comprobable)

- **Ejerce**: codificador de imagen + pregunta con respuesta exacta.
- **Prompt exacto** (con la imagen `/tmp/tester-sbx/imagen.png` adjunta como imagen): `What text is in this image? Answer only the text.`
- **Sandbox**: `/tmp/tester-sbx` (usa `imagen.png` de la plantilla).
- **Límite**: 300 s (el codificador va en CPU).
- **Éxito automático**: la salida contiene `MEN WALK ON MOON`.
- **Duración esperada**: 1-2 min.
- **Nota**: esta prueba necesita adjuntar la imagen; si el runner no puede adjuntarla, se salta y se anota.
