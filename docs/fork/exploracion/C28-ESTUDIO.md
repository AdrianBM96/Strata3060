# C28-paso 1. Prefill por capas (estudio, ≤1 página)

Hoy: chunk a chunk × 48 capas; cada experto no residente viaja por PCIe una vez POR CHUNK
(6 chunks a 32K × 48 capas × 72 ms ≈ 20,8 s de PCIe). Idea: capa a capa todo el prompt;
cada experto viaja UNA vez por prompt (48 × 440 × 1,8 MB / 11 GB/s ≈ 3,5 s).

## Qué estado hay que guardar entre capas (file:line)

- **R (residual hiperconectado)**: `m.R = T*D` FP32 (`prefill.cpp:852`), con `D = N*HC = 10240`
  (`prefill.cpp:90`). Por prompt: 32K×10240×4 = **1,31 GB**; 86K = **3,52 GB**. Hoy vive en VRAM por
  chunk; por capas debe aparcarse entre capas. En VRAM no cabe (848 MiB libres): va a **RAM fijada**,
  usando el mecanismo `hand_in_` que ya existe (`prefill.cpp:1627-1632`: upload `T*D*4` por chunk).
- **Filas PLE**: `m.ple_emb` T×N×4 por chunk (`:924`, `:1697`); se re-usan por capa: cachear el anillo
  en RAM (63 MB/chunk × 6 = 378 MB a 32K), no re-leer de NVMe por capa.
- **GDN**: estado recurrente `ss.gdn_state` fluye dentro de la capa chunk→chunk (`:2005-2015`); nada
  que guardar entre capas (por capa se resetea igual que hoy por prompt).
- **KV**: se acumula igual que hoy (streaming ya existe); la atención de L-chunk-c solo necesita el
  KV de L de chunks anteriores (causal, sin futuro) → **sí**, disponible por construcción.

## Bit-exacto

Mismo T, mismos kernels, mismo orden de tokens (atención causal, GDN y routing idénticos por token;
cada chunk se combina por separado igual que hoy). Verificación: puerta `logpos-compare` (N-calidad).

## Techo (PCIe a 32K)

Expertos: 20,8 s → 3,5 s (−17,3 s). PERO el hand-off R hace round-trip por PCIe por capa:
1,31 GB × 2 × 48 = 126 GB ≈ **11,5 s**. Neto ≈ **−6 s a 32K** (~−25 % del prefill).
A 86K: expertos 48 s → 3,5 s (−44,5 s); hand-off 340 GB ≈ 31 s; neto ≈ **−13 s**.
Refinamiento posible: solapar hand-off con cómputo (dúplex) o anillo R en VRAM si cupiera.

## Conclusión

Funciona y gana (~6 s @32K, ~13 s @86K), pero 2/3 del ahorro se van en el hand-off R.
Prototipo solo con OK de Claude (paso 2).
