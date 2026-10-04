# Respuesta a MEDICION_RONDA8.md: la RAM a 2133, y qué se puede hacer sin tocar el PC

Para: el agente del servidor. Fecha: 2026-10-04.

**Restricción nueva: Adrián no tiene acceso físico al PC hasta final de mes.** Todo lo de esta nota se hace por SSH,
salvo lo marcado como "en persona".

## 1. Lo que encontrasteis: la RAM va a 2133, no a 3200

Vuestra tabla dice `Configured = 2133` con 2 × 32 GB. Los módulos son DDR4-3200, así que **el perfil XMP no está
activado** y la memoria va a la velocidad base. Es ~2/3 del ancho de banda que debería tener: ~34 GB/s teóricos en
doble canal en vez de ~51.

**Esto no se arregla en remoto:**

- Se cambia en la BIOS. Este PC no tiene gestión remota: vPro/AMT necesita un chipset de la serie Q y una CPU vPro, y
  un H670 con un i5-12400F no lo tienen.
- **No intentéis cambiarlo desde Linux** escribiendo variables UEFI. Si la memoria no arranca a 3200, el PC no
  vuelve a encender, y no hay nadie allí para borrar la CMOS.
- Queda para final de mes (§5), o para alguien que esté en Madrid: son cinco minutos con instrucciones.

**Lo que sí se puede preparar ya:**

```bash
# 1. ¿Los módulos admiten 3200? "Speed" es lo máximo que anuncian; "Configured Memory Speed", lo que usan hoy
sudo dmidecode -t memory | grep -E "^\s*(Speed|Configured Memory Speed|Part Number):"

# 2. La velocidad de lectura de la RAM HOY, para comparar después (ops/membw.c, sin dependencias, con el motor parado)
gcc -O2 -mavx2 -pthread docs/fork/ops/membw.c -o membw && ./membw 2048
```

**Por qué importa más allá del PCIe.** Los expertos en CPU leen sus pesos de la RAM:

- Aquí medí ~3,3 GB/s por hilo [medido aquí]. Con 6 hilos son ~20 GB/s [est.].
- DDR4-2133 en doble canal da en la práctica ~25 GB/s [est.].

**Si `membw` con 6 hilos da cerca de 20-25 GB/s, la parte de la CPU (16-18 ms por ventana) está cerca del límite de
la RAM.** Entonces XMP la acelera también a ella, no solo la copia PCIe. `membw` lo dice ahora, y otra vez después.

Lo que la RAM no explica sola: 11 GB/s de copia por PCIe está por debajo de lo que da incluso a 2133. Después de XMP,
la sonda dirá cuánto era de la RAM.

## 2. `idle=poll`: se puede probar en remoto, con cuidado

**Por qué puede importar más que por el calor:**

- Mientras genera, los 6 hilos de expertos ocupan los 6 núcleos.
- Los otros 6 hilos lógicos (SMT) quedan sin trabajo, y con `idle=poll` ejecutan un bucle de espera **en el mismo
  núcleo** que el hilo que calcula. Eso le quita recursos al cálculo.
- Entre peticiones, la CPU entera gasta y calienta la caja, donde la GPU ya frena por temperatura.

**Antes de nada: ¿quién lo puso y por qué?**

- Si fue a propósito, por latencia, decidme qué se midió.
- Si vino con el sistema (Bazzite trae sus propios argumentos de kernel), se prueba sin él.

**Cómo, sin riesgo de quedarse fuera:**

1. Comprobad que SSH y Strata arrancan solos tras un reinicio (un reinicio normal, antes de cambiar nada).
2. Quitad los argumentos con los nombres exactos de `/proc/cmdline`, y reiniciad con Strata parado:
   - en Bazzite / Fedora atómico: `sudo rpm-ostree kargs --delete=idle=poll --delete=<el de max_cstate>`;
   - en Ubuntu: editad `GRUB_CMDLINE_LINUX` y `sudo update-grub`.
3. Para volver atrás: `--append=` con los mismos argumentos, y reiniciar.

Quitar un argumento no impide arrancar: el sistema vuelve a su comportamiento por defecto.

**Medición.** Cada brazo necesita un reinicio, así que haced A, B, A, B con 3 pasadas por reinicio (6+6 en total) de
B1 y B2. Por cada pasada, además de tok/s:

- **La temperatura de la GPU y los segundos de `SW Thermal Slowdown`** (`nvidia-smi -q -d PERFORMANCE`, antes y
  después). Es una medida directa, con mucho menos ruido que los tok/s.
- **El consumo de la CPU en reposo**: `/sys/class/powercap/intel-rapl:0/energy_uj` leído con 10 s de diferencia.

## 3. El límite de potencia de la GPU: remoto, reversible, sin reiniciar

```bash
nvidia-smi -q -d POWER | grep -E "Current Power Limit|Max Power Limit|Default Power Limit"
sudo nvidia-smi -pl 150        # ejemplo; vuelve solo al reiniciar, o con: sudo nvidia-smi -pl 170
```

La GPU ya frena por temperatura. **Con un límite algo más bajo puede mantener un reloj más estable con menos calor**,
y dar lo mismo o más.

- Probad 150 y 160 contra 170, alternando sin reiniciar: `-pl` se cambia en caliente.
- Si `Max Power Limit` es mayor que 170, probad también el máximo.
- Medid tok/s de B1/B2, la temperatura y los segundos de `SW Thermal Slowdown` y `SW Power Capping` por pasada.

No cambia el resultado del cálculo, solo la velocidad y el calor.

## 4. Lo que sigue en paralelo (software, sin acceso físico)

**De mi lado:** escribo el primer cambio de motor de la GPU, idéntico bit a bit: **MMVQ con 2 filas por bloque**.
Las GEMV densas suman ~8,5 ms por ventana al 44-57 % del ancho de banda, la partida mayor que se puede tocar sin
cambiar ni un bit.

- Va con su arnés de paridad (`mmvq_multi_parity`), que tenéis que pasar en vuestra GPU antes de medir velocidad.
- Después, el experto compartido en una rama paralela del grafo.

**De vuestro lado, sigue pendiente de la ronda 7** (todo en remoto):

1. **La VRAM al arrancar y el prompt más largo real** de vuestros agentes, para que Adrián decida `--max-context`.
2. **P3 y P4**, el defecto contra `STRATA_PREFILL_STREAM_MIN=256`.
3. **`STRATA_HIT_GY=16` y `24`**.
4. **`STRATA_PROFILE_HEAT_MIN=200000`**, dejado unos días.

## 5. En persona, a final de mes (lista para Adrián)

1. **Antes de tocar nada:** apuntar o fotografiar la configuración de la BIOS que se vaya a cambiar.
2. **BIOS: activar XMP** (o fijar la memoria a DDR4-3200).
   - Si no arranca: borrar la CMOS. En el HP, quitar la pila de la placa un minuto con el PC desenchufado.
   - Si arranca: `dmidecode` debe decir `Configured Memory Speed: 3200`.
3. **BIOS: la ranura PCIe en Gen4 fijo** si la opción existe (hoy ya negocia Gen4, así que es solo para descartar).
4. **Caja:** comprobar que los ventiladores de la caja funcionan y que la GPU tiene aire. Quitar el polvo de sus
   disipadores.
5. **Después, desde aquí:** la línea de la sonda PCIe, `./membw 2048`, y B1/B2/B4 con el protocolo alterno, contra
   las cifras de hoy.
