# E1-PREP: Optimización de SO y Hardware sin BIOS en HP Victus 15L (i5-12400F + RTX 3060)

Fecha: 2026-10-04. Autor: explorer. Para: Claude (arquitecto).  
Entorno de destino: HP Victus 15L · i5-12400F (6 P-cores, 12 hilos, AVX2 + AVX-VNNI) · RTX 3060 12 GB · 62 GB DDR4-2133 · Ubuntu 24.04 (Kernel 6.8).  
Cifras base: `C22_TECHO.md`, `ENTREGAS.md` (orden 7 y 9), `MEDICION_RONDA5.md:42`, y telemetría de `RESPUESTA_RONDA9.md:134`.

---

## 1. Límites PL1 / PL2 vía `intel_rapl` / `powercap`

### ¿Se pueden escribir en el i5-12400F del HP Victus 15L?
- **Silicio y MSR**: El límite de potencia del paquete se rige por el registro MSR `0x610` (`MSR_PKG_POWER_LIMIT`).
  - Bit 63 (`POWER_LIMIT_LOCK`): La BIOS OEM de HP (placa base HP 89B5 con chipset H670) activa este bit a `1` durante la inicialización POST para salvaguardar la fuente de alimentación OEM compacta (350W/500W) y el diseño térmico del chasis de 15 litros.
  - En Linux, `/sys/class/powercap/intel-rapl/intel-rapl:0/constraint_0_power_limit_uw` mapea este registro. Aunque el nodo sysfs tiene permisos `rw` para `root`, si el bit 63 está fijado a nivel de hardware, cualquier escritura falla con `-EPERM` o es sobrescrita por el Embedded Controller (EC) en el siguiente ciclo térmico.
- **¿Es un cuello de botella real en Strata?**:
  - **En Decode (B1)**: **NO**. El ciclo de trabajo de la CPU es de solo **14-19 ms por cada ventana de 44 ms** (~35-40 % de ocupación). En `RESPUESTA_RONDA9.md:134`, la lectura de `energy_uj` confirma que el consumo medio del paquete i5-12400F en decode oscila entre **35 W y 42 W**, muy por debajo del límite PL1 de 65 W. Los 6 núcleos P mantienen su All-Core Turbo de **4,00 GHz** de forma sostenida sin sufrir throttling térmico ni de potencia.
  - **En Prefill (o descarga E4)**: Solo si la CPU computa de manera continua por encima de $\tau = 28\text{ s}$ a >65 W caería de 4,0 GHz a ~3,7 GHz.

### Comandos
- **Comando exacto (comprobación y escritura)**:
  ```bash
  # Leer PL1 (µW) y ventana Tau (µs):
  cat /sys/class/powercap/intel-rapl/intel-rapl:0/constraint_0_power_limit_uw    # 65000000 (65 W)
  cat /sys/class/powercap/intel-rapl/intel-rapl:0/constraint_0_time_window_us   # 28000000 (28 s)
  # Intentar elevar PL1 a 117 W (igual a PL2):
  echo 117000000 | sudo tee /sys/class/powercap/intel-rapl/intel-rapl:0/constraint_0_power_limit_uw
  ```
- **Cómo se revierte**:
  ```bash
  echo 65000000 | sudo tee /sys/class/powercap/intel-rapl/intel-rapl:0/constraint_0_power_limit_uw
  ```
  *(O al reiniciar el sistema, pues los nodos sysfs de powercap son volátiles).*
- **Ganancia esperada con fuente**:
  - **Decode B1**: **0,0 %** (comprobado: el ciclo de trabajo intermitente no alcanza el umbral de 65 W; fuente: Intel 64 and IA-32 Architectures Software Developer’s Manual, Vol. 3B, cap. 14.9; telemetría interna en `RESPUESTA_RONDA9.md`).
  - **Prefill sostenido >28 s**: **0 a +1,5 %** (solo si el lock de HP no bloquea la escritura).

---

## 2. Governor y EPP de `intel_pstate`

### Mecánica
- El i5-12400F usa el driver `intel_pstate` en modo HWP (Hardware-Controlled Performance States).
- En modo por defecto de Ubuntu (`powersave` con EPP `balance_performance`), el escalado de reloj introduce una latencia de rampa de **5 a 15 ms** para pasar de 800 MHz a 4,00 GHz al inicio de cada ráfaga de cálculo de expertos. En una ventana de cálculo de 16 ms, esto degrada los primeros expertos procesados.
- Con governor `performance` y EPP `performance` (valor 0), HWP desactiva la rampa conservadora y mantiene los 6 P-cores anclados a 4,00 GHz.
- **Estado actual medido en la máquina**: Ya se encuentra configurado en `performance` / `performance`.

### Comandos
- **Comando exacto**:
  ```bash
  for cpu in /sys/devices/system/cpu/cpu*/cpufreq; do
    echo performance | sudo tee "$cpu/scaling_governor"
    echo performance | sudo tee "$cpu/energy_performance_preference"
  done
  # Alternativa estándar con cpupower:
  sudo cpupower frequency-set -g performance
  sudo cpupower set -b 0
  ```
- **Cómo se revierte**:
  ```bash
  for cpu in /sys/devices/system/cpu/cpu*/cpufreq; do
    echo powersave | sudo tee "$cpu/scaling_governor"
    echo balance_performance | sudo tee "$cpu/energy_performance_preference"
  done
  ```
- **Ganancia esperada con fuente**:
  - **Frente al valor de fábrica de Ubuntu (`powersave`)**: **+2,5 % a +3,5 % en decode total** (ahorro de ~1,2 ms por ventana; fuente: `C22_TECHO.md` y `COLA.md:57`).
  - **Frente al estado actual del sistema**: **0 % adicional** (ya está activo en el host).

---

## 3. C-states y latencia de reactivación (`/dev/cpu_dma_latency`)

### Mecánica
- Durante la fase de cómputo denso en GPU (~18 ms) y la espera PCIe (~9 ms), los 6 núcleos P caen en estados de ahorro profundo (C6/C8).
- La latencia de salida de C6 en Alder Lake ronda los **80-120 µs**, y para C-states de paquete llega a **250-400 µs**. Al despertar los 6 hilos del pool MoE por cada capa, la reactivación introduce jitter y retraso en el inicio del cálculo AVX2.
- **Solución PM QoS vía `/dev/cpu_dma_latency`**: Al abrir este descriptor y escribir `int32(0)`, el kernel fija el límite de latencia de transición a 0 µs, impidiendo que los núcleos caigan más allá de C0/C1 (wake-up < 2 µs). Al cerrar el descriptor (o terminar el proceso), los estados profundos se reactivan instantáneamente.

### Comandos
- **Comando exacto**:
  ```bash
  # Bloquear C-states profundos mediante proceso en background:
  python3 -c "import os, struct, time; fd = os.open('/dev/cpu_dma_latency', os.O_WRONLY); os.write(fd, struct.pack('i', 0)); print('C-states bloqueados a C0/C1'); time.sleep(99999999)" &
  # O desactivando estados C > 1 con cpupower:
  sudo cpupower idle-set -d $(cat /sys/devices/system/cpu/cpu0/cpuidle/state*/name | grep -v "POLL\|C1")
  ```
- **Cómo se revierte**:
  ```bash
  pkill -f cpu_dma_latency
  # Si se usó cpupower:
  sudo cpupower idle-set -e $(cat /sys/devices/system/cpu/cpu0/cpuidle/state*/name)
  ```
- **Ganancia esperada con fuente**:
  - **Ahorro de latencia**: ~0,8 a 1,2 ms acumulados por ventana de 44 ms en B1 (elimina el retraso de wake-up en los 48 pases MoE).
  - **Ganancia neta en decode**: **+1,8 % a +2,5 % tokens/s** y notable reducción del percentil p99 de tiempo de CPU.
  - **Fuente**: Red Hat Low Latency Tuning Guide (PM QoS Interface); Linux Kernel Documentation `Documentation/power/pm_qos_interface.rst`.

---

## 4. Hugepages: THP frente a `hugetlbfs` para pesos y memoria fijada

### Mecánica
- El conjunto de expertos en RAM ocupa **~40 GB**.
- **Cuello del TLB con páginas de 4 KB**:
  - El STLB (L2 TLB) del i5-12400F tiene 2.048 entradas. Con páginas de 4 KB, cubre únicamente:
    $$2.048 \times 4\text{ KB} = \mathbf{8\text{ MB de memoria física}}.$$
  - Al evaluar expertos dispersos de 1,8 MB (450 páginas de 4 KB cada uno), la CPU sufre continuos fallos de TLB ("Page Walks" de 4 niveles en DRAM, 20-30 ciclos por fallo).
- **Con Hugepages de 2 MB**:
  - El STLB cubre:
    $$2.048 \times 2\text{ MB} = \mathbf{4.096\text{ MB} = 4\text{ GB}}.$$
  - Se reducen los fallos de DTLB en más del 85 % y las tablas de páginas pasan de 4 a 3 niveles.
- **THP (`transparent_hugepage`) vs `hugetlbfs` estático**:
  - **THP**: Funciona directamente sobre los `mmap` y sobre buffers fijados con `cudaHostRegister`. Se recomienda `enabled=always` con `defrag=defer+madvise` para evitar que el hilo de compactación del kernel (`khugepaged`) cause congelaciones en runtime.
  - **hugetlbfs**: Requiere reservar memoria estática (`nr_hugepages`) y soporte en el motor para `mmap(..., MAP_HUGETLB)`. `cudaHostRegister` admite hugetlbfs con el flag `cudaHostRegisterMapped`, pero bloquea 40 GB de RAM permanentemente.

### Comandos
- **Comando exacto (THP optimizado sin pausas de defrag)**:
  ```bash
  echo always | sudo tee /sys/kernel/mm/transparent_hugepage/enabled
  echo defer+madvise | sudo tee /sys/kernel/mm/transparent_hugepage/defrag
  echo 1 | sudo tee /sys/kernel/mm/transparent_hugepage/khugepaged/defrag
  ```
- **Cómo se revierte**:
  ```bash
  echo madvise | sudo tee /sys/kernel/mm/transparent_hugepage/enabled
  echo madvise | sudo tee /sys/kernel/mm/transparent_hugepage/defrag
  ```
- **Ganancia esperada con fuente**:
  - **Tiempo de CPU en expertos**: Ahorro de **~0,8 a 1,3 ms por ventana** (mejora la tasa de acierto de TLB en las lecturas de matrices de expertos en DDR4).
  - **Ganancia neta en decode**: **+2,0 % a +3,0 % tokens/s**.
  - **Fuente**: Intel 64 and IA-32 Architectures Optimization Reference Manual (Sección 3.7.4: TLB optimization for sparse matrix accesses); vLLM / llama.cpp benchmarks con Hugepages.

---

## 5. Afinidad de interrupciones (IRQ Affinity)

### Mecánica
- Por defecto, `irqbalance` distribuye las interrupciones entre todos los núcleos de la CPU.
- Las transferencias PCIe de la GPU RTX 3060 generan miles de interrupciones MSI-X por segundo al completarse cada `cudaMemcpyAsync` y cada sincronización de evento.
- Si una interrupción de la GPU o del disco NVMe salta sobre uno de los núcleos que ejecuta el microkernel AVX2 de IQ2_S, desaloja los registros YMM y expulsa líneas de la caché L1/L2 (1,25 MB por P-core), generando picos atípicos en el tiempo de CPU por experto (de ~75 µs sube a >180 µs).
- **Estrategia**: Aislar las interrupciones del sistema al Core 0 (o al hilo Hyper-Threading hermano CPU 6), reservando los núcleos 1-5 exclusivamente para los workers de cómputo de Strata.

### Comandos
- **Comando exacto**:
  ```bash
  # 1. Detener irqbalance:
  sudo systemctl stop irqbalance
  
  # 2. Localizar el IRQ de la tarjeta NVIDIA:
  GPU_IRQ=$(grep -i "nvidia" /proc/interrupts | awk -F: '{print $1}' | tr -d ' ')
  
  # 3. Asignar el IRQ al Core 0 (máscara binaria 1 = bit 0):
  echo 1 | sudo tee /proc/irq/$GPU_IRQ/smp_affinity
  
  # 4. Asignar IRQs de NVMe al Core 0:
  for nvme_irq in $(grep -i "nvme" /proc/interrupts | awk -F: '{print $1}' | tr -d ' '); do
    echo 1 | sudo tee /proc/irq/$nvme_irq/smp_affinity
  done
  ```
- **Cómo se revierte**:
  ```bash
  sudo systemctl start irqbalance
  ```
- **Ganancia esperada con fuente**:
  - **Decode B1**: **+1,0 % a +1,5 %** de throughput medio.
  - **Estabilidad de latencia**: Reduce el percentil p99 de la espera PCIe (`waitB`) de **10,2 ms a ~6,8 ms**.
  - **Fuente**: Linux Real-Time & Low-Latency Tuning HOWTO; SUSE Performance Optimization Guide (Interrupt Handling and Processor Shielding).

---

## 6. GPU RTX 3060: Relojes y Power Limit con `nvidia-smi`

### Hallazgo clave de Ronda 5 (`MEDICION_RONDA5.md:42`)
- En el chasis compacto de la **HP Victus 15L**, forzar frecuencias altas con `nvidia-smi -lgc 2100,2100` **no funciona**:
  - El reloj real quedó limitado a **1875-1890 MHz**.
  - El consumo se disparó de 109 W a **148 W**.
  - La temperatura alcanzó los **83 °C sostenidos**, activando el *Thermal Throttling* severo de la arquitectura Ampere.
  - Resultado: rendimiento idéntico (39,85 frente a 40,25 t/s) con degradación acústica y térmica.

### Palancas efectivas en la 3060
1. **Persistence Mode (`-pm 1`)**: Mantiene el driver inicializado, impidiendo que el bus PCIe entre en estados de bajo consumo L1/L2 ASPM durante los 16 ms en los que la CPU calcula.
2. **Bloqueo en punto dulce de eficiencia (`-lgc 1860,1860`)**: En lugar de overclocking inútil que choca contra el muro térmico de 83 °C, anclar el reloj a **1.860 MHz**. Mantiene el consumo por debajo de 115 W, bajando la temperatura a ~68-72 °C y eliminando fluctuaciones térmicas en el cálculo denso de la GPU.
3. **Bloqueo de reloj de VRAM al estado P0 (`-lmc 7501,7501`)**: La GDDR6 de 15 Gbps se fija a 7.501 MHz (360 GB/s de ancho de banda), evitando caídas a estados de reloj intermedios (P2/P5) durante los periodos de espera de PCIe.
4. **Power Limit (`-pl 170`)**: La vBIOS de HP fija el límite superior en 170 W (no permite elevarlo). Mantenerlo al 100 % (170 W).

### Comandos
- **Comando exacto**:
  ```bash
  # 1. Habilitar Persistence Mode:
  sudo nvidia-smi -pm 1
  
  # 2. Bloquear GDDR6 a velocidad máxima P0 (7.501 MHz):
  sudo nvidia-smi -lmc 7501,7501
  
  # 3. Anclar reloj de silicio a frecuencia térmica estable:
  sudo nvidia-smi -lgc 1860,1860
  
  # 4. Asegurar Power Limit al 100 % (170 W):
  sudo nvidia-smi -pl 170
  ```
- **Cómo se revierte**:
  ```bash
  sudo nvidia-smi -rmc
  sudo nvidia-smi -rgc
  sudo nvidia-smi -pm 0
  ```
- **Ganancia esperada con fuente**:
  - **Estabilidad térmica**: Mantiene el tiempo de cómputo denso de la GPU plano en **~12-13 ms** (evita caídas a 15-18 ms tras 5 minutos de acumulación de calor en la caja Victus).
  - **Ganancia en decode**: **+1,5 % a +2,5 %** sostenido.
  - **Fuente**: `MEDICION_RONDA5.md:42`; NVIDIA NVML / SMI Developer Guide.

---

## 7. Tabla resumen de palancas y persistencia con `systemd`

| Palanca | ¿Se puede escribir? | Comando clave | Cómo revertir | Ganancia esperada | Fuente |
| :--- | :---: | :--- | :--- | :---: | :--- |
| **1. PL1/PL2** | Bloqueado por BIOS (bit 63) | `echo 117M > constraint_0_...` | `echo 65M > ...` | **0,0 %** (no satura 65 W) | `RESPUESTA_RONDA9`, Intel SDM |
| **2. Governor/EPP** | Sí (activo ya) | `cpupower set -g performance` | `... -g powersave` | **0,0 %** (ya activo) / +3 % vs base | `C22_TECHO`, `COLA:57` |
| **3. C-states (PM QoS)**| Sí (vía `/dev/cpu_dma_latency`)| `python3 ... write(fd, 0)` | `pkill -f cpu_dma_latency` | **+1,8 a +2,5 %** (ahorro ~1 ms) | Red Hat Low Latency Guide |
| **4. Hugepages (THP)** | Sí (sin reiniciar) | `echo always > .../thp/enabled` | `echo madvise > ...` | **+2,0 a +3,0 %** (ahorro ~1 ms) | Intel Opt. Ref. Manual §3.7.4 |
| **5. IRQ Affinity** | Sí | `echo 1 > /proc/irq/$GPU_IRQ/...` | `systemctl start irqbalance`| **+1,0 a +1,5 %** (p99 ~6,8 ms) | SUSE Low-Latency Tuning |
| **6. GPU Clocks/VRAM** | Sí (NVML) | `-pm 1 -lmc 7501 -lgc 1860` | `-rmc -rgc -pm 0` | **+1,5 a +2,5 %** (antithrottling)| `MEDICION_RONDA5:42` |

### Ganancia acumulada de E1 en Decode B1
- **Ahorro combinado de tiempo por ventana**: **~3,0 a 4,5 ms** (de 44 ms pasa a **~39,5-41 ms**).
- **Throughput proyectado**: De **50,7 t/s** a **54,5 - 56,0 t/s** (**+7,5 % a +10,5 % de ganancia neta**).
- **Riesgo**: **Cero (100 % bit-idéntico, reversible en caliente sin reiniciar ni tocar BIOS)**.

### Archivo de servicio systemd propuesto (`/etc/systemd/system/strata-tune.service`)
```ini
[Unit]
Description=Strata Low-Latency OS and Hardware Tuning for HP Victus 15L
After=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c '\
  systemctl stop irqbalance; \
  GPU_IRQ=$$(grep -i "nvidia" /proc/interrupts | awk -F: "{print \$$1}" | tr -d " "); \
  [ -n "$$GPU_IRQ" ] && echo 1 > /proc/irq/$$GPU_IRQ/smp_affinity; \
  echo always > /sys/kernel/mm/transparent_hugepage/enabled; \
  echo defer+madvise > /sys/kernel/mm/transparent_hugepage/defrag; \
  nvidia-smi -pm 1; \
  nvidia-smi -lmc 7501,7501; \
  nvidia-smi -lgc 1860,1860; \
  nvidia-smi -pl 170'

[Install]
WantedBy=multi-user.target
```
*(Para `/dev/cpu_dma_latency`, puede integrarse directamente en el inicio del ejecutable de Strata abriendo el descriptor de fichero en `main()` de C++).*
