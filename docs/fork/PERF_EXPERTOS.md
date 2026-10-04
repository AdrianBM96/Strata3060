# Perfil `perf` de los hilos de expertos (lo que pediste en la ronda 6)

Fecha: 2026-10-04. Máquina: i5-12400F + RTX 3060, Strata 0.1.38 con el parche (V2), Swift IQ2_XS.
Perfil tomado durante un **B1** (1.024 tokens, razonamiento alto), motor caliente:

```bash
sudo perf record -F 999 -g -p "$(pgrep -f 'engine/strata' | head -1)" -- sleep 20
# 120.791 muestras
```

## 1. Simbolos, con hijos

```
55,65%  55,46%  ExpertPool::worker(int)                        <- casi todo SELF
20,14%   0,14%  native_gu_rows(...)                            <- el gate/up de los expertos
        |-- 11,59%  ggml_vec_dot_iq2_s_q8_K
        |--  4,47%  ggml_vec_dot_iq2_xxs_q8_K
        |--  1,48%  ggml_vec_dot_iq1_m_q8_K
        |--  1,27%  gu_rows<22, 2>            <- TU kernel de bloque
        |--  0,77%  iq256_gu_rows
        |--  0,62%  row_dot<16, 2>
11,61%  11,56%  ggml_vec_dot_iq2_s_q8_K
11,34%   0,00%  0x000062c68e45ef20        <- sin simbolo (¿kernel sin depurar?)
 8,21%            q2_0_gguf_rows_multi_avx2                     <- el `down`
 8,11%            Verifier::run(...)
```

## 2. Tipos de cuantización del modelo (`swift`)

- **gate/up**: `IQ2_S` (dominante), `IQ2_XXS`, `IQ1_M`. El pack declara **155 tensores de tipo 22 (IQ2_S)**.
- **down**: `q2_0` (`q2_0_gguf_rows_multi_avx2`, 8,2 %).
- **embedding de tokens**: `IQ4_XS` (322 MiB, en memoria mapeada).

## 3. Mi lectura (para que decidas el cambio de motor)

1. **`ExpertPool::worker` es el 55 %, y es casi todo tiempo propio, no de kernel.** Los hilos del pool
   parecen pasar la mayor parte del tiempo **esperando** (durante la fase P la CPU no tiene trabajo), no
   calculando. Eso encaja con el ping-pong: 5 hilos girando mientras la GPU hace la cadena densa.
   **Antes de escribir otro kernel, merece la pena mirar si ese 55 % es espera y si se puede dormir en
   vez de girar** (o si hay contención de atómicos en la cola).
2. **Tu kernel de IQ2_S aparece poco** (`gu_rows<22,2>` 1,27 %) y el genérico
   `ggml_vec_dot_iq2_s_q8_K` se lleva el 11,6 %. O el despacho solo lo usa en condiciones concretas
   (¿NT=2?), o en este perfil la mayoría de las filas van por el genérico. **Eso explica el +1,4 %
   (dentro del ruido): el kernel casi no se ejecuta.**
3. Los kernels reales que quedan: **IQ2_S 11,6 %** (lo más gordo), IQ2_XXS 4,5 %, IQ1_M 1,5 %, y el
   `down` en q2_0 8,2 %. Si hay que tocar uno, es **IQ2_S** (y ahí ya está el tuyo — ver el punto 2).
4. `Verifier::run` 8,1 % es la ruta de verificación, no un experto.
5. Hay un símbolo **sin resolver** al 11,3 % (`0x62c68e45ef20`); conviene identificarlo (¿un kernel
   compilado sin `-g`, o el JIT de algo?).

## 4. Lo que yo no puedo decidir

Con esto no me atrevo a decir cuál es *el* cambio: el 55 % del pool no es cómputo de un kernel
concreto. **La pregunta que decide es si ese 55 % es espera o trabajo**, y eso lo sabes leer tú en
`ExpertPool::worker`. Si es espera, el cambio no es un kernel AVX2 más rápido sino el reparto/sincronía
del pool (o el acoplamiento con la fase GPU).

El fichero crudo: `perf-expertos.txt` (top 40 con hijos).
