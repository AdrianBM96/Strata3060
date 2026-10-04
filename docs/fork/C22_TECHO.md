# C22. Techo teórico de decode y prefill en la 3060 (máx. 1 página)

## Datos

- Orden 9 (B1, ~2,3 tok/ventana, ~44 ms/ventana): espera GPU 21-22 ms, CPU expertos 14-19 ms, waitB (PCIe) 6,5-10
  ms, router+head+atención ~5-6 ms, draft 2,5 ms. Contabilidad suma (sin solape entre espera y host):
  `generate.cpp:7384-7391`.
- Expertos por tipo (del pack, `native_experts.txt`): IQ2_S (34 capas) 1,44 MB; IQ2_XXS (11) 1,25 MB; IQ1_M (3)
  1,12 MB; down q2_0 en todas (~451 KB).
- Acierto del motor 73-78 %; PCIe medida 11 GB/s; RAM: **pendiente STREAM** (provisional: `membw` 29,8 GB/s, 6 hilos).

## Modelo por ventana v2 (decode): t = Σ_capas [densa_L + max(CPU_L, PCIe_L + aciertos_L)] + draft

(Corrección: el `max()` global no es físico; las capas son secuenciales. Respuesta al solape, con file:line:
el pool CPU arranca pronto por el doorbell (`layer.cpp:382-390`, publica ids+x antes de que la GPU siga), pero el
stream espera la parte CPU en `wait_flag_ge(m_flag_, …)` (`verify.cpp:1085`) antes de `moe_hit_add` (l. 1091):
solape parcial entre capas (también el plan split, `verify.cpp:540-548`), combinación en serie.)

Por capa (totales/48): densa 0,42 ms; CPU 0,33 ms; PCIe+aciertos 0,28 ms; draft 2,5 ms total.
- Real: 0,42+0,33+0,28 = 1,03 ms/capa → ~44 ms + draft (syncs y lanzamientos incluidos).
- Techo intra-capa: 0,42+max(0,33; 0,28) = 0,77 ms/capa → **~37 ms + draft**.
- Techo con precarga (C15, PCIe_L→0): 0,42+max(0,33; 0,11) ≈ 0,75 ms/capa → **~36 ms + draft**.
Conclusión honesta: el solape intra-capa vale ~7 ms; la precarga, ~1 ms más. El resto está en densa+CPU.
