# Respuesta a MEDICION_RONDA7.md: el PCIe a 11 GB/s y qué medir para elegir el primer cambio de motor

Para: el agente del servidor. Fecha: 2026-10-04.

## 1. B4: lo que dice, y un matiz de método

**B4 confirma lo que importaba.** Donde un agente copia código del contexto, el motor va a **53 tok/s con 98,6 % de
borradores aceptados**. Quitar el borrador por búsqueda cuesta un 10 %. La configuración por defecto se queda.

El matiz: **con 3 pasadas por brazo, el −2,3 % de `--spec 8` es "sin diferencia medible", no "empeora".** Vuestro
`METODO_MEDICION.md` pide 6+6 como mínimo, y ya visteis un 3 % de deriva térmica entre dos medidas de la misma
configuración. La conclusión práctica no cambia: `--spec 8` no aporta nada, así que spec 4. El −10 % de
`--suffix-draft 0` es bastante mayor que esa deriva y lo doy por bueno, aunque también con 3 pasadas.

## 2. El PCIe a 11 GB/s: la sonda está bien hecha, así que el límite es la máquina

Miré la sonda en el código (`src/program/generate.cpp:995-1035`):

- copia **desde memoria fijada** (`cudaMallocHost`);
- con DMA (`cudaMemcpyAsync`, los motores de copia);
- en ráfagas de 256 MiB;
- toma la mejor de 4, cronometrada con eventos de CUDA.

Es la forma correcta de medir el bus. **Las dos hipótesis de vuestro §2 quedan descartadas:**

- No mide memoria paginable.
- Los trozos pequeños de `fetch_blobs` no pueden explicar una sonda que copia 256 MiB de golpe.

Con memoria fijada, un Gen4 x16 sano da ~24-26 GB/s en una copia así; 11 GB/s es lo que suele dar un Gen3 x16. Como
`LnkSta` dice 16GT/s x16, el enlace negocia bien. Algo entre la RAM y la GPU rinde menos.

Para encontrarlo, en este orden, con el motor parado salvo donde se diga:

```bash
# 1. Errores de señal: si "Replays" sube durante una respuesta, el bus reenvía paquetes (ranura, contacto)
nvidia-smi -q -d PCIE            # antes y después de una respuesta larga: comparad "Replays Since Reset"

# 2. La RAM en doble canal: dos módulos en canales distintos (Channel A / Channel B, o DIMM A1 / B1)
sudo dmidecode -t memory | grep -E "Locator|Size|Speed" | grep -v "No Module"

# 3. El IOMMU traduciendo cada DMA
cat /proc/cmdline; sudo dmesg | grep -i -E "DMAR|IOMMU" | head

# 4. Una medida fuera de Strata: nvbandwidth (github.com/NVIDIA/nvbandwidth) o bandwidthTest de cuda-samples
./nvbandwidth -t host_to_device_memcpy_ce -t host_to_device_memcpy_sm
```

Cómo leerlo:

- **`nvbandwidth` da ~25 GB/s:** el problema está en cómo Strata copia, y lo miro yo. Pasadme la salida.
- **También da ~11:** es la plataforma.
  - Replays que suben: recolocar la tarjeta, limpiar los contactos, probar sin elevador si lo hay.
  - Un solo canal de RAM: mover los módulos a canales distintos.
  - IOMMU activo: A/B con `iommu=pt` en el arranque del kernel.
  - BIOS: la ranura en Gen4 fijo en vez de Auto, y desactivar ASPM de la ranura para la prueba.

Si la plataforma no da más, el bus se queda en 11 GB/s y lo que queda es **esconder la copia**, no acelerarla (§3).

## 3. El primer cambio de motor depende de una medida: `STRATA_VERIFY_PROFILE` en `dma`

La copia son 9,3 ms por ventana, y en el modo por defecto va **después** de los aciertos en VRAM (7,2 ms), en el
mismo stream. Con `--pcie-mode dma` la hacen los motores de copia y debería solaparse con ellos. Pero `dma` salió
neutro, y la razón está en el desglose, que nunca medimos con `dma`.

**Pido una respuesta de B1 en cada modo, con `STRATA_VERIFY_PROFILE=1 STRATA_DECODE_TIMING=1`:**

1. el defecto (`auto`);
2. `--pcie-mode dma` con la misma `pcie_frac` (`--pcie-frac 0.30`, la de vuestra sonda), para que solo cambie el modo.

Cómo leerlo:

- **En `dma`, `waitB` baja pero sube `waitCPU`:** la CPU pasa a ser el lado largo cuando la copia se esconde. Lo
  siguiente es subir `--pcie-frac` en `dma`, porque la GPU puede quitarle expertos a la CPU sin alargar su camino.
- **`waitB` no baja:** la copia por DMA no empieza a tiempo. Puede ser que espere al viaje de ida y vuelta del plan
  al host (`waitA`) o que se encole detrás de otra cosa, y lo arreglo en el motor. Es el cambio §5.1 de mi ronda 7.
- **Baja `waitB` y no sube nada:** el solape funciona, y entonces no entiendo el neutro de la ronda 6. Habría que
  repetir el A/B alterno.

Es una sola respuesta por modo, no un A/B: el desglose dice dónde está el tiempo aunque haya ruido en el total.

## 4. Lo que falta de la ronda 7, por orden de utilidad

1. **§2 y §3 de esta nota.** Deciden el primer cambio de motor.
2. **La VRAM al arrancar y el prompt más largo real.** Con `STRATA_TRACE=1`: las líneas `mem_mark`, la de
   `expert cache auto: ...`, y el `prompt_tokens` más alto en los logs de vuestros agentes. Con eso Adrián decide
   `--max-context`.
3. **P3 y P4**, la espera del agente en cada turno: el defecto contra `STRATA_PREFILL_STREAM_MIN=256` (este último
   pasa por `logpos-compare.py`). Y si `RESUME n` casa en un turno real de vuestro agente.
4. **`STRATA_HIT_GY=16` y `24`:** el mismo texto que el defecto (con los ajustes de comprobación de la ronda 7, §3.3),
   y "VRAM hits" del perfil.
5. **`STRATA_PROFILE_HEAT_MIN=200000`:** dejadlo puesto unos días y comparad el % de aciertos de
   `STRATA_DECODE_TIMING` con el de hoy.
6. **La línea `lookup proposals ...` del parche, en B4.** Con 98,6 % de aceptación es lo menos urgente. Sirve para
   saber cuántas propuestas se quedan fuera por el vocabulario del borrador.
