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

- [ ] **C11.** `--expert-cache 4124`. Hecho cuando: A/B 6+6 de B1, B2 y B4 contra la base de la 7, y prueba de pico
  (96K + imagen + B1) sin OOM. Si gana con IC>0 y sin OOM, pedir OK para adoptarlo. Después, el mismo contrato con
  `--kv-resident 16384`.
- [ ] **C10b.** Alcance: solo el servidor Python. Registro por petición: cliente, RESUME n, tokens de prompt, slot
  y expulsiones, y el índice del primer token distinto frente al mejor prefijo en cada relectura de más del 50 %.
  `--prompt-cache 12`. Hecho cuando: lleva 48 h de uso real y se da el reparto de causas.
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
