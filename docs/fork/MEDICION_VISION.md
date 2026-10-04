# Visión en Strata (fase A: codificador en CPU) — medido

Fecha: 2026-10-04. **Decisión de Adrián** (ronda 13): las imágenes las lee **Strata**, no nex-mini.
Fase A = codificador en la **CPU** + carga perezosa, sin tocar la VRAM. **[medido]**.

## Qué se hizo

1. **Parche de `server.py`** (commit `883937e` de la rama de Claude): `--lazy` solo se rechaza si el
   codificador va en la **GPU**. Con `gpu: false`, el servidor arranca perezoso y el encoder (CPU) también.
   Aplicado con `git apply --3way` (limpio); `serve/test_server.py`: **119 tests OK** (incluye `LazyVision`).
2. **Config `strata-swift-iq2_xs.json`**: bloque `vision` con `exe: engine/strata-vision`,
   `mmproj: mmproj-Qwen3.8-Flash-Next-BF16.gguf`, `model: <swift shard 1>`, **`gpu: false`**, `max_tokens: 300`;
   y `--vision` + `--vram-reserve-mib 700` en los args.
3. **`serve-strata.sh`**: `HAS_VISION` ahora mira `vision.gpu` → con CPU, camino perezoso normal (no
   `free-vram.sh`).
4. **litellm**: `ada-next` con `supports_vision: true`.

## Resultados

- **Arranque**: el servidor va en **carga perezosa**; el encoder arranca en CPU (`strata-vision ... --max-tokens
  300`, sin `--gpu`); `health` → `"images": true`.
- **Lee de verdad** (por :8081 directo y por :4000 litellm):
  - "MEN WALK ON MOON" → correcto (26,9 s la 1ª, con carga del modelo).
  - "HELLO WORLD 2026" → correcto, **5,0 s** (modelo cargado).
  - la misma imagen otra vez → **1,6 s** (caché por hash, `cache_n=183`).
  - captura de terminal con un `TypeError` → lee el error exacto y la causa.
- **El texto NO se resiente**: huecos de expertos **3.690 vs 3.696** sin visión (−6), y B1 **42-43 tok/s**
  (igual). El encoder en CPU no toca la VRAM (solo ~0,9 GB de RAM).
- **Frente a nex-mini**: en la captura del `TypeError`, **ambos aciertan** (misma respuesta); Strata **4,8 s**
  vs nex-mini **2,5 s** (más lento, pero de sobra).

## Pendiente

- **Retirar `ada-praxis` → nex-mini** del router de litellm (Claude: "se retira cuando §2 dé bien"; §2 ya da
  bien). Decisión de Adrián: quitarlo o dejarlo como respaldo.
- **Fase C** (`--vision-on-demand`, codificador en GPU cogiendo VRAM del caché solo mientras hay imagen) si
  5 s por imagen se hace lento. No parece necesario hoy.
