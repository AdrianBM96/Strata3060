# Norma de trabajo: CONTRATOS (permanente desde 2026-10-04)

## Formato de contrato (todos así)

- ID / Objetivo (una frase).
- Alcance: ficheros y funciones que se pueden tocar. Nada fuera de ahí.
- Hecho cuando: criterios verificables.
- Medición: siempre METODO_MEDICION.md (alternar, 6+6, `bench.py compare`; bit a bit con los ajustes de siempre;
  `logpos-compare` si puede cambiar texto).
- Prohibido: cambiar producción sin OK de Claude; tocar :8082, BIOS/UEFI o setup.py.
- Entrega: entrada en CHANGELOG.md + commit + push; a Claude, ≤10 líneas (veredicto, cifras con IC, commit,
  bloqueos).

## Autonomía

Al cerrar un contrato, empezar el siguiente de la cola sin esperar. Escribir a Claude solo para: (a) entregar;
(b) pedir OK para tocar producción; (c) un bloqueo real, con file:line y 2 opciones. Las dudas menores se deciden
y se anotan en el CHANGELOG.

## Cola (por orden)

- [x] **C11-bis.** Encontrar qué limita los huecos (solo lectura). Hecho: `generate.cpp:3283` (auto:
  slots=(free-reserva)/blob, reserva=(700+prefill_mib)MiB+58MiB draft) y `generate.cpp:3356-3374` (sized-slots:
  tope=free-700MiB; 4124 pedidos→3732). Los 848 MiB del pico no sirven: 700 van reservados + 256 de LOW.
- [x] **C11.** CERRADA sin adoptar (solo +38 huecos: 3732 vs 3694; la reserva de 700 MiB es del prefill largo y no se toca). Producción como estaba (auto).
- [x] **C10b.** Registro + `--prompt-cache 12` desplegados (verificado con 2 peticiones: reused 0→68,
  first_diff=68). Quedan 48 h de uso real para el reparto de causas.
- [ ] **C14a.** `STRATA_MTP_HIST=1`. Alcance: mtp.cpp junto a router_top10 y la salida del proceso. Histograma
  uint32[512] en el dispositivo, atomicAdd en el mismo stream, sin sync por ventana; apagado, solo un if en el
  host. Hecho cuando: con la variable apagada B1 sale bit a bit igual, y se da la cobertura top 128/256/384 y la
  aceptación del borrador en sesión de 30+ min.
- [ ] **C15.** `STRATA_PREGATE_STATS=1` según ORDENES §15. Mismo patrón. Hecho cuando: bit a bit igual apagada, la
  tabla de recall por k (10/16/24), L+1 y L+2, por GDN/QSA y por la parte PCIe, y el coste en ms/ventana.
- [ ] **C16.** `STRATA_ROUTE_GAP_STATS=1` según §16. Hecho cuando: bit a bit igual apagada, el histograma por
  cortes y 5 ejemplos.
- [ ] **C17.** Según §17, con un script sobre los logs. Hecho cuando: la tabla de tokens/ventana por tipo de
  contenido, la aceptación por posición y el % de tiempo.
- [ ] **C8.** Rejilla completa del simulador, a) b) a+b. Hecho cuando: la tabla con el criterio de §8.
- [ ] **C18** (solo estudio, sin código). La copia PCIe en paralelo con los aciertos. Entrega en
  docs/fork/C18_DISENO.md, máximo 1 página: dónde está hoy la dependencia (file:line), qué sync o evento hay que
  mover, y el techo en ms/ventana con los datos de la 9.

## Norma permanente: agente de pruebas `tester`

Existe `tester` (pi con ada-next, panel w1:pH, cwd del repo). Solo sirve cuando el contrato pide una SESIÓN
REAL DE AGENTE con ada-next como carga (C14a, C15 y C17: generar tráfico de agente de 20-30 min mientras los
contadores registran). tester NO valida ni testea soluciones (eso se hace con bench.py, logpos-compare y los
tests). tester y los benchmarks NUNCA a la vez (comparten el motor y se contaminan las cifras). Cómo:
`herdr agent prompt tester "<tarea cerrada>" --wait --timeout <ms>`; después `herdr agent read tester`.
Antes de cada sesión de tester, apuntar la config del motor activa.

## Norma permanente: adopción en el mismo día

Cuando algo se ADOPTE (gana con IC, pasa la calidad y OK de Claude), en el mismo día: 1) commit del código y la
configuración y push a la rama del fork `AdrianBM96/Strata3060` rama `claude/strata-rtx3060-optimization-zfgxq8`
(`git pull --no-rebase` antes del push); 2) documentarlo: entrada en el CHANGELOG (qué, por qué, cifras con IC,
commit), actualizar RESUMEN_FINAL.md (configuración y cifras vigentes) y, si cambia el despliegue,
ESTADO_DESPLIEGUE.md; 3) si es un parche nuevo, dejarlo también en `docs/fork/patches/`. No se da por adoptado nada
que no esté subido y documentado.
