# C18. Diseño: copia PCIe en paralelo con los aciertos (estudio, sin código)

## Dónde está hoy la dependencia (`src/core/verify.cpp`, grupo de una capa)

1. `wait_flag_ge(m_flagA_, …)` (l. 1055): el plan GPU publicado por el pool del host.
2. `grouped(…)` (l. 1059): GEMV de los expertos residentes (~5,2 ms).
3. `wait_flag_ge(m_flagB_, …)` (l. 1061-1062): la parte PCIe en *staging* — **waitB, 6,5-10 ms**.
4. `fetch_blobs` + `rebase` (l. 1063-1073) y `grouped` de la parte PCIe (l. 1077).
5. `wait_flag_ge(m_flag_)` (l. 1085): la parte CPU; `moe_hit_add` combina (l. 1091).

Todo en el mismo stream `cs`: el paso 2 y el 3-4 son secuenciales aunque solo dependen del paso 1.

## Qué sync o evento hay que mover

Emitir 3-4 en un stream lateral nada más publicarse el plan (paso 1), y unirse antes del `grouped` de la parte
PCIe (l. 1077): `cudaEventRecord` tras `fetch_blobs` + `cudaStreamWaitEvent(cs, …)` antes de l. 1077. El patrón
`sh_fork` (l. 908-915, `ev_fork_`/`ev_join_`/`sh_cs_`) ya hace fork-join en este fichero y se puede reutilizar.
`moe_hit_add` (l. 1091) no se toca: sigue viendo ambas partes listas.

## Techo (con datos de la 9)

waitB 6,5-10 ms solapado con los aciertos 5,2 ms → se ahorra min(waitB, ~5 ms) ≈ **4-5 ms/ventana (~10 %)**.
No llega al ideal porque la parte CPU (`wait_flag_ge(m_flag_)`, l. 1085) sigue en serie después.
