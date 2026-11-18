# PISCO OS V8.2 — instalación nativa en un disco virtual

## Alcance y arquitectura

Rama `installer/virtual-install-v82`, basada en V8.1 `b2b4d726ce`.
El entorno Linux sólo ejecuta el instalador. El destino contiene la raíz ext2
limpia V7.3 (`dcd4feae31`) y su `Kernel.efi` nativo, sin kernel Linux,
initramfs Linux ni dependencia del USB. GRUB UEFI x86_64 ocupa la ruta fallback
`/EFI/BOOT/BOOTX64.EFI` y encadena `/EFI/PISCO/Kernel.efi`.
No se modifica el kernel, EXT2 ni el escritorio. `run-modern.sh`, su perfil,
la imagen V7.3 y el medio V8.1 se conservan.

`Meta/build-pisco-virtual-installer.py` reutiliza el payload aceptado de V8.1,
comprueba sus SHA-256 y ejecuta `e2fsck -f -n`. Construye exclusivamente archivos
nuevos sin root, loop devices ni montajes del host. El initramfs añade Python
3.12, herramientas FAT/mtools y sus bibliotecas; sus hashes se registran.
Se reutiliza el kernel Linux y los módulos de V8.1. El backend es
`Meta/InstallerRuntimeV82/pisco-install`; el init de V8.1 sólo se adapta en la copia
privada del nuevo medio.

El payload ext2 se copia como imagen de sistema de archivos, conservando sus
propietarios, permisos y enlaces. No se ejecutan binarios SerenityOS en Linux
ni se modifica el payload para simular una instalación.

## Esquema de disco

Sectores lógicos de 512 bytes, GPT con ambas cabeceras y tablas CRC32, alineación
de 1 MiB. UUID de disco y PARTUUID nuevos en cada instalación confirmada.

- LBA 2048: ESP FAT32 de 128 MiB, tipo EFI System, etiqueta `PISCO_EFI`,
  identificador FAT aleatorio. Contiene GRUB, configuración y kernel nativo.
- LBA 264192: raíz ext2 nativa de 1536 MiB, tipo de datos Linux
  `0fc63daf-8483-4772-8e79-3d69d8477de4`, nombre GPT `PISCO native`.
  Este GUID de tipo no implica ejecución de Linux.
- El resto queda sin asignar antes de la GPT de respaldo. Se preserva espacio
  para decidir futuras particiones de actualización; no hay A/B, V9, expansión
  automática ni partición de usuario independiente en esta etapa.

GRUB carga su configuración externa junto al ejecutable mediante `$cmdpath`.
Ésta busca el UUID FAT específico y pasa `root=PARTUUID:<raíz>` al kernel
nativo. `/cmdline.txt` contiene el mismo argumento de respaldo y `serial_debug`.
El arranque no requiere variables UEFI instaladas en NVRAM.

## Selección y protecciones

Esta versión **rechaza hardware físico** y sólo admite SATA QEMU de 3–16 GiB,
con sectores de 512 bytes. El destino debe tener modelo exacto `QEMU HARDDISK`,
DMI QEMU, serie SCSI VPD página 0x80 exactamente `PISCO_V82_TARGET` y autorización
única `pisco_target_serial=PISCO_V82_TARGET` en el arranque del instalador.
Es una autorización explícita del entorno virtual de prueba, no una política
definitiva para un instalador físico.

Rechaza el dispositivo del USB resuelto por UUID, sus particiones, destinos
USB, nombres alternativos, particiones existentes, sólo lectura, seriales
ambiguos, holders/slaves, montajes detectados por major:minor y swap activo.
Después de mostrar ruta, serie, tamaño y operaciones exige una respuesta exacta:

```text
INSTALL /dev/sda PISCO_V82_TARGET 3221225472
```

Vuelve a comprobar la identidad, abre exclusivamente ese dispositivo con
`O_EXCL|O_NOFOLLOW`, verifica `st_rdev` y tamaño por ioctl, y examina **todos** sus
bytes para exigir un disco enteramente cero. Un disco sin firma pero con datos
también se rechaza. No formatea ni borra discos ocupados.

Todas las escrituras de destino usan ese único descriptor abierto y offsets
acotados al dispositivo, sin ejecutar herramientas contra nombres de partición.
FAT/mtools sólo trabajan sobre `/run/pisco/install-*/esp.fat` en RAM. Se copian ESP
y raíz, se hace fsync y lectura SHA-256 completa de ambas. Sólo entonces se
publican las tablas GPT y el MBR protector, verificando todos sus bytes.
El informe queda en `/run/pisco/installed.json` y en la salida serie.

No es una caja de seguridad contra root: la consola privilegiada de laboratorio
conserva herramientas genéricas. No conectar discos importantes a esta VM.
No se certifica hotplug, DMA hostil, suplantación de QEMU ni concurrencia con
otro administrador root. El host sólo adjunta archivos virtuales recién creados.

## Construcción y pruebas reproducibles

Desde Ubuntu/WSL, en la raíz del repositorio:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 Meta/build-pisco-virtual-installer.py \
  --v81 Build/pisco-installer-v81-20261117/v81 \
  --output Build/pisco-installer-v82/build-NUEVO
PYTHONDONTWRITEBYTECODE=1 python3 Meta/test-pisco-virtual-install.py \
  --image Build/pisco-installer-v82/build-NUEVO/pisco-installer-v82.img \
  --output Build/pisco-installer-v82/prueba-NUEVA
PYTHONDONTWRITEBYTECODE=1 python3 Meta/test-pisco-virtual-install-negative.py \
  --image Build/pisco-installer-v82/build-NUEVO/pisco-installer-v82.img \
  --output Build/pisco-installer-v82/negativas-NUEVAS
```

Los directorios de salida deben ser nuevos. Se necesitan las dependencias de
V8.1, Python 3.12 del host, mtools, dosfstools, QEMU y OVMF. La construcción no es
todavía un SDK portable: reutiliza el staging retenido de V8.1 y la distribución
Ubuntu validada. El payload es reproducible y no cambia; UUID y metadatos del
medio/instalación se generan por ejecución, así que la IMG completa no es
bit a bit reproducible.

Cada prueba usa QEMU q35, TCG, **una CPU**, 1 GiB RAM, sin red. El USB se adjunta
read-only. La instalación escribe un archivo RAW virtual vacío de 3 GiB. Se
adjunta además un disco ajeno con un testigo; se comparan sus hashes completos
y los del USB. Los comandos QEMU exactos, salida serie, informes y capturas
quedan en los directorios de evidencia. El arranque posterior usa NVRAM nueva y
únicamente el disco instalado, sin USB ni `-kernel` del host.

Para abrir posteriormente el disco validado (con todas las VMs de prueba ya
apagadas), puede reutilizarse su NVRAM privada desde la raíz del repositorio:

```sh
qemu-system-x86_64 -machine q35 -accel tcg -cpu max,-x2apic -smp 1 -m 1G \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=Build/pisco-installer-v82/test-3/native-first/vars.fd \
  -device ich9-ahci,id=ahci \
  -drive if=none,id=os,format=raw,file=Build/pisco-installer-v82/test-3/target.img \
  -device ide-hd,drive=os,bus=ahci.0,serial=PISCO_V82_TARGET,bootindex=1 \
  -display gtk,gl=off,zoom-to-fit=off -vga std -nic none
```

Este comando permite escrituras persistentes sólo en el archivo de destino
virtual y su NVRAM privada. `./run-modern.sh` sigue ejecutando la versión V7.3
anterior, como exige su conservación; no se lo redirige al disco de ensayo.

## Recuperación y límites

Ante cualquier error no se muestra `V82 INSTALLED VERIFIED`. No arrancar ni
promover ese destino. Si la copia se interrumpe antes de publicar GPT, el disco
parcial queda sin tablas válidas y la siguiente instalación lo rechaza por no
estar vacío. En estas pruebas la recuperación consiste en conservar el archivo
para diagnóstico y crear **otro archivo virtual nuevo**. No se ofrece un borrado
automático ni reanudación de destinos parciales.

Publicar GPT al final reduce la posibilidad de arrancar una copia incompleta;
no promete una transacción atómica resistente a todos los cortes de energía.
Un corte durante la publicación de GPT puede dejar una sola copia válida,
aunque las particiones ya se verificaron antes. No se implementa reparación.

Pendientes: hardware físico, NVMe, 4Kn, BIOS, Secure Boot, discos existentes,
dual boot, selección física definitiva, GUI, cuentas/contraseñas nuevas,
retirada del autologin anon, redimensionamiento y actualizaciones. El éxito SATA
virtual no certifica compatibilidad física. La investigación USB histórica
permanece aislada. La imagen mantiene las limitaciones conocidas de V7.3;
esta fase no repara errores históricos de memoria, journal o EXT2.

## Resultados de esta ejecución

Validación del 18 de noviembre de 2026. Imagen aceptada:
`Build/pisco-installer-v82/build-3/pisco-installer-v82.img`, RAW GPT/UEFI,
2015363072 bytes. El SHA-256 y las entradas de construcción están en
`build-3/build-manifest.json`.

La prueba `test-3` completó nueve casos:

1. Arranque USB autónomo e identificación de USB, destino y disco ajeno.
2. Rechazo del USB, su partición, un serial no autorizado y un alias de ruta.
3. Cancelación de confirmación, con hash completo del destino sin cambios.
4. Instalación confirmada y lectura completa de ambas particiones en Linux.
5. Verificación independiente de ambas GPT/CRC y hashes del USB/disco ajeno.
6. Hashes de particiones leídos desde el host, archivos GRUB/cmdline con PARTUUID
   correcto y hash del Kernel.efi instalado igual al payload nativo aceptado.
7. Arranque exclusivamente desde el destino y NVRAM nueva, sin USB ni `-kernel`:
   `SerenityOS EFI Prekernel`, kernel nativo y escritorio V7.3.
8. Reinicio solicitado desde la Shell nativa y regreso al escritorio.
9. Archivo `/home/anon/v82-persistence.txt` creado por el sistema nativo, leído
   después del reinicio y comprobado offline con debugfs: `PISCO-V82-PERSISTED`.

`negative-2` completó cuatro casos adicionales:

- Destino ext2 montado read-only rechazado por major:minor.
- Hash completo de ese destino sin cambios después de desmontarlo.
- Interrupción de QEMU después de comenzar realmente a copiar la ESP: sin
  mensaje de éxito y con ambas zonas GPT todavía enteramente cero.
- Reintento sobre la copia parcial rechazado por el examen completo de ceros;
  ningún byte adicional cambia y el USB conserva su hash.

`final-audit.json` confirma dos arranques EFI nativos en el log del reinicio,
ausencia de Linux/pánicos en ese log, espacio sin asignar todavía cero, sintaxis
Python válida, backend de la imagen igual al archivo fuente entregado y ausencia
de cambios en Kernel/Userland/Base o el lanzador V7.3. No se recompilan esos
componentes: se instala el payload binario validado y se comprueba su integridad.

Como comprobación adicional, `native-confirmation/result.json` registra un
tercer arranque con NVRAM nueva y USB ausente, seguido por `sync` y `shutdown`
solicitados en la Shell nativa, con salida QEMU normal. Tras ese apagado se
extrajo una copia nueva de la raíz: `fsck-after-shutdown.log` contiene las cinco
pasadas de `e2fsck -f -n`, sin errores y con código 0. El archivo persistente
también se leyó correctamente en esa copia. La receta se conserva en
`confirm_shutdown.py`; el controlador entregado incorpora asimismo apagado
nativo y comprobación read-only del sistema de archivos para futuras repeticiones.

La raíz instalada antes de su primer arranque tiene SHA-256
`40cfd9b148ae10317885b83fb13adcb8a2c577e2dcea2dc67c5c32f2e05a512f`,
exactamente el de la raíz limpia V8.1. Kernel.efi conserva SHA-256
`9cc25d3ea5fd5d3d6b8fe09a2c6a3ca2ad3fc755e04fd7a2704df05e1edc7998`.
El PARTUUID de esta instalación es `e211a830-f46f-4414-9b09-a7ae9dff46eb`;
el UUID GPT es `e1905812-d4b6-4730-a26a-732ff4706920`.
`preserved-images.json` confirma por hash las imágenes V7.3/V8.1, kernels,
payload y perfil protegidos. No se promociona ni reemplaza la imagen V7.3.

Evidencia conservada bajo `Build/pisco-installer-v82/`:

- `test-3/complete.json`, `results.json`, `installed.json`, comandos QEMU y logs.
- [Escritorio instalado sin USB](../Build/pisco-installer-v82/test-3/native-first/desktop-first.png).
- [Archivo conservado después del reinicio](../Build/pisco-installer-v82/test-3/native-first/screen.png).
- [Entorno USB e inventario de discos](../Build/pisco-installer-v82/test-3/installation/screen.png).
- `negative-2/complete.json`, resultados y transcripciones de montaje,
  interrupción y reintento. `final-audit.json` y `preserved-images.json`.
- `native-confirmation/`, `confirm_shutdown.py`, `root-after-shutdown.ext2`
  y `fsck-after-shutdown.log`, comprobación adicional de apagado e integridad.
- Recetas y archivos RAW virtuales del destino, disco ajeno y copia interrumpida.

Los intentos anteriores no se cuentan como aprobados. `test-1` detectó que
`lsblk SERIAL` no ofrece la serie SATA sin udev: se sustituyó por lectura exacta
de SCSI VPD 0x80. `test-2` falló al crear directorios FAT por falta de CP850:
se incorporaron los módulos glibc gconv y sus hashes. Ambos fallaron antes de
escribir el destino. `negative-1` se interrumpió para corregir una espera CRLF
del controlador de pruebas; `negative-2` repitió y completó las comprobaciones.
Sus registros se conservan separados. No fueron fallos de integridad del destino.

Los avisos de SpiceAgent sin dispositivo y AudioServer sin canal ya estaban en
el control V8.1; no se modifican ni se cuentan como regresiones del instalador.
No se ejecutó una batería completa nueva del escritorio V7.3 ni se certificó
resistencia a todos los cortes de energía. La aprobación se limita a la
instalación y al arranque independiente con persistencia en los discos QEMU
descritos. No se inicia V8.3 ni se implementa el asistente gráfico.
