# Prototipos de kernels de CPU (AVX2)

Código de investigación del [informe 03](../03-host-cpu-ram-pcie.md), **no** forma parte del motor. Se midió en una
VM KVM con un Xeon Cascade Lake (~2,6 GHz, 4 vCPU sin SMT, ruido de ±10-20 %); en Zen 2/3 o Alder Lake puede dar
otra cosa.

| Archivo | Qué mide | Resultado [medido en esa VM] |
| --- | --- | --- |
| `iq2s_proto.cpp` | IQ2_S con índices, escalas y signos vectorizados por bloque, frente al `row_dot` actual de `src/kernels/cpu/iq_avx2.cpp` (incluido como referencia) | x1,21-1,34, 0 floats distintos |
| `iq2s_proto_g.cpp` | la misma idea con `vpgatherdq` | x0,39 (más lento) |
| `iq2s_seed.cpp` | la comprobación bit a bit con varias semillas | 0 diferencias en 8 semillas x nt 1-4 |
| `q2plane_proto.cpp`, `q2plane_proto2.cpp` | Q2_0 con la activación desentrelazada "en planos" | x2,3-2,6 en L2, x1,6 en streaming con 4 hilos; dif. relativa media 3,5e-7 |
| `q2plane_thp.cpp` | el anterior con páginas de 2 MB (THP) | sin diferencia medible |
| `bench_cpu.cpp`, `bench_cpu2.cpp` | los kernels AVX2 del repo sin modificar, GB/s por núcleo | tabla en el informe 03, §5 |
| `iq4nl_bench.cpp`, `aq_bench.cpp`, `memread.cpp`, `ghz.cpp` | IQ4_NL, cuantización de activaciones, lectura de DRAM, reloj | idem |

Compilar desde la raíz del repo (ejemplo):

```sh
g++ -O3 -mavx2 -mfma -mf16c -std=c++20 -Iinclude -Ithird_party/ggml \
    docs/fork/informes/prototipos-cpu/iq2s_proto.cpp -o iq2s_proto
```

`bench_cpu*.cpp` y `q2plane_proto*.cpp` enlazan además los `.cpp` del repo que usan (`src/kernels/cpu/q2_avx2.cpp`,
`src/kernels/cpu/iq_avx2.cpp`); ver sus `#include`.
