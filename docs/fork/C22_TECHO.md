# C22. Techo teórico de decode y prefill en la 3060 (máx. 1 página)

## Datos

- Orden 9 (B1, ~2,3 tok/ventana, ~44 ms/ventana): espera GPU 21-22 ms, CPU expertos 14-19 ms, waitB (PCIe) 6,5-10
  ms, router+head+atención ~5-6 ms, draft 2,5 ms. Contabilidad suma (sin solape entre espera y host):
  `generate.cpp:7384-7391`.
- Expertos por tipo (del pack, `native_experts.txt`): IQ2_S (34 capas) 1,44 MB; IQ2_XXS (11) 1,25 MB; IQ1_M (3)
  1,12 MB; down q2_0 en todas (~451 KB).
- Acierto del motor 73-78 %; PCIe medida 11 GB/s; RAM: **pendiente STREAM** (provisional: `membw` 29,8 GB/s, 6 hilos).

## Modelo por ventana (decode)

- **GPU densa + aciertos** (inevitable): GEMV q8+qkv 2,4 + atención ~1 + head 1,8 + router 3,4 + shared ~1,2 +
  resto denso ≈ **12-15 ms**.
- **PCIe** = bytes_fallos_GPU / 11 GB/s. Hoy ~33 MB/ventana → **suelo 3,0 ms** (medido waitB 6,5-10: 2-3× por
  granularidad y syncs).
- **CPU** = bytes_fallos_CPU / BW_RAM. Hoy ~250 entradas/ventana × ~1,4 MB ≈ 350 MB → con 29,8 GB/s: **suelo
  ~12 ms** (medido 14-19). Si STREAM de 6 hilos da menos, el suelo sube.
- Solape de hoy: **suma** (ver contabilidad arriba). Techo con solape perfecto: **max()**.

## Tabla (ms/ventana, B1)

| | Real | Techo solape hoy (suma) | Techo solape perfecto (max) |
| --- | ---: | ---: | ---: |
| Decode | ~44 | ~30 (12 GPU + 3 PCIe + 12 CPU + 2,5 draft) | ~15-18 (manda la GPU/CPU mayor) |
| +5 pts de acierto | — | ~−2 (menos CPU+PCIe) | — |
| +10 pts de acierto | — | ~−4 | — |

## Pérdida por etapa frente a su mínimo físico

| Etapa | Real | Mínimo | Pérdida |
| --- | ---: | ---: | --- |
| waitB (PCIe) | 6,5-10 ms | 3,0 ms | ~4-7 ms (lanzamientos, syncs) |
| CPU expertos | 14-19 ms | ~12 ms | ~2-7 ms (kernels, hilos) |
| Espera GPU / host | 21-22 ms | solapable | es la suma, no un coste |

## Prefill (con lo que hay; resto pendiente C24)

- P2 128K medido: ~990 tok/s (~1 ms/token). Desglose por etapa: **pendiente C24**.
- Proyección: cada chunk stremea casi todos los expertos no residentes una vez (~33 GB en IQ2_XS a 11 GB/s ≈
  3 s fijos por chunk) + cómputo. A 32K y 86K: **pendiente C24**.
