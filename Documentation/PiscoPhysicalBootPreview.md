# PISCO OS V8.5A — medio de prueba UEFI sin instalación

Preparado el 20 de noviembre de 2026. Rama `installer/physical-boot-v85a`,
base V8.4 `6ae10be7e37aec1a68466ee2debef1634dd0ebd0`.
Esta etapa prepara una primera prueba física **de arranque del entorno USB**.
No autoriza instalar en hardware ni certifica el kernel nativo en ese equipo.

## Arquitectura y límites de seguridad

RAW de disco completo, GPT con MBR protector y dos copias de GPT:

- Tamaño exacto: 2 015 363 072 bytes (1922 MiB). Utilizar USB de al menos 4 GB.
- LBA 2048: ESP FAT32 de 128 MiB, etiqueta `P85ABOOT`.
- LBA 264192: datos ext2 de 1792 MiB, etiqueta `PISCO85A_DATA`.
- Arranque extraíble `EFI/BOOT/BOOTX64.EFI`: GRUB UEFI x86_64 sin firma.
- Linux 6.8.0-149-generic e initramfs dentro de la ESP; datos montados
  exclusivamente `ro,noload`, identificados por UUID. No hay BIOS/CSM ni shim.

La interfaz muestra dispositivos y su protección, una prueba de teclado en RAM,
verificación SHA-256 del payload, comprobación del bloqueo, reinicio y apagado.
No permite seleccionar destinos, particionar, crear cuentas ni instalar.
Los componentes originales V8.4 se conservan para auditoría bajo
`/opt/pisco/v84-review`, sin permiso de ejecución. Los puntos de entrada activos
de instalación se retiran; `pisco-install` sólo devuelve código 64. No existe
opción de GRUB o parámetro que habilite la instalación.

Antes de identificar o montar el USB, se impone `BLKROSET` a todos los dispositivos
de bloque detectados (incluidas particiones), se consulta `BLKROGET` y se comprueba
también `/sys/class/block/*/ro`, sin abrirlos para escritura ni enviar escrituras.
Linux puede permitir un `open(O_WRONLY)` aun cuando rechace la escritura posterior:
por ello la comprobación correcta es el estado de sólo lectura del kernel.
La apertura utiliza además `O_NONBLOCK` para poder proteger una unidad óptica
vacía sin exigir que contenga un medio; no habilita escritura ni montajes internos.
La vigilancia se repite cada 0,5 segundos y el inventario cada dos segundos.
No hay automontaje interno, activación de swap, red configurada ni shell de usuario.
Un medio ausente o ambiguo detiene el arranque sin abrir la interfaz.

Es una protección del flujo de prueba de confianza, no un aislamiento contra root
malicioso, un kernel comprometido, firmware defectuoso o el administrador del host.
BusyBox conserva applets internos aunque se hayan retirado sus accesos directos.
La protección de un disco conectado posteriormente se aplica en el siguiente ciclo;
no hay un consumidor que lo monte o escriba durante ese intervalo. Para la primera
prueba, desconectar los discos internos cuando sea viable o usar bloqueo físico de
escritura ofrece una protección adicional independiente del software.

Linux ejecuta únicamente este entorno. El payload conserva el kernel nativo PISCO,
el escritorio V7.3 y la preparación de autenticación V8.3 utilizada por V8.4.
El kernel nativo no se ejecuta en esta prueba; no se ha instalado nada ni se han
copiado componentes Linux al destino. No se modifica `run-modern.sh`.

## Procedencia y reconstrucción

Entrada fijada: `Build/pisco-installer-v84/build-5/pisco-installer-v84.img`, SHA-256
`827beda1336c73dad224146fbf4d859500770d8af6c41ea6fcf9dea68567e5dd`.
Se extraen de esa imagen Linux, initramfs y los componentes reales V8.4; sus hashes
se cotejan con el manifiesto validado. Además se extrae y verifica el payload real
de su partición de datos contra la semilla `Build/pisco-installer-v83/payload-2`:

- `root.ext2`: `c0d13a0aa0675f6df992e5f7770a68f5017f8c1f20bc23a32c80d9db73859a95`.
- `Kernel.efi`: `9cc25d3ea5fd5d3d6b8fe09a2c6a3ca2ad3fc755e04fd7a2704df05e1edc7998`.

La semilla incluye los ajustes nativos de autenticación V8.3; no es una copia de
un disco de pruebas con cuentas o archivos personales. Su configuración de fábrica
no se usa como inicio de sesión del entorno de prueba.

Desde Ubuntu 24.04/WSL, en la raíz del repositorio, con directorios de salida nuevos:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 Meta/build-pisco-physical-preview.py \
  --v84 Build/pisco-installer-v84/build-5 \
  --payload Build/pisco-installer-v83/payload-2 \
  --output Build/pisco-v85a/mi-construccion
PYTHONDONTWRITEBYTECODE=1 python3 Meta/audit-pisco-physical-preview.py \
  --build Build/pisco-v85a/mi-construccion --output Build/pisco-v85a/mi-auditoria
```

Dependencias: Python 3.12, cpio, GRUB EFI amd64, e2fsprogs, dosfstools y mtools;
para las pruebas, QEMU x86_64 y OVMF. Se reutiliza el runtime gráfico autocontenido
V8.4: Tk/Tcl 8.6, Xorg modesetting sin aceleración, evdev, XKB y fuentes DejaVu/X11.
Las bibliotecas necesarias están en el initramfs. No se instalan paquetes al arrancar.

El constructor fija UUID, fecha base del commit V8.4, marcas temporales FAT/ext2,
propietarios, orden y metadatos cpio, gzip y GRUB. La reproducibilidad binaria se
comprueba repitiendo la construcción con **los mismos inputs y versiones de
herramientas**, no se presume para cualquier distribución o versión de GRUB.
El constructor sólo escribe archivos regulares en un directorio nuevo y retira
únicamente sus propios intermediarios grandes tras generar el RAW.

`build-manifest.json` registra los hashes de imagen, Linux, initramfs, GRUB,
runtime nuevo, componentes V8.4 originales y payload, commit base, versiones de
paquetes/herramientas y configuración mke2fs. `SHA256SUMS` verifica el RAW completo.
Dentro de la partición de datos se incluyen `PREVIEW.json`, versiones/source packages
principales y avisos de licencia disponibles bajo `licenses/`.
Estos avisos no sustituyen la revisión completa de licencias y disponibilidad del
código fuente correspondiente antes de una publicación pública masiva del runtime
Linux/GRUB/BusyBox y sus dependencias. Esta entrega es una previsualización técnica
para prueba controlada, sin publicación remota.

Las rutas de construcción sólo existen en el host. GRUB usa la ESP y el runtime
identifica el USB por UUID, sin rutas WSL, carpetas compartidas, discos de desarrollo,
parámetros QEMU ni descargas en tiempo de arranque. QEMU aporta emulación y firmware
UEFI; no carga Linux/initramfs con las opciones `-kernel` o `-initrd`.

## Ejecutar y repetir las pruebas virtuales

```sh
PYTHONDONTWRITEBYTECODE=1 python3 Meta/run-pisco-physical-preview.py \
  --image Build/pisco-v85a/final/pisco-v85a-preview-x86_64-uefi.img \
  --session Build/pisco-v85a/mi-sesion
```

Se necesita pantalla GTK/WSLg. El lanzador verifica checksum y modo preview, usa
una CPU, 2 GiB, NVRAM privada, USB read-only y ningún disco de datos escribible.
`--controller uhci` permite repetir el arranque por UHCI.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 Meta/test-pisco-physical-preview.py \
  --image Build/pisco-v85a/final/pisco-v85a-preview-x86_64-uefi.img \
  --controller xhci --output Build/pisco-v85a/mi-prueba-xhci
# Repetir con --controller uhci y otro directorio de salida.
# Añadir --ambiguous-source a xhci para comprobar rechazo de dos USB idénticos.
```

Las pruebas crean archivos virtuales SATA, NVMe y USB propios de 128 MiB,
adjuntados escribibles. Comparan todos sus bytes por SHA-256 antes/después y
comprueban la protección declarada por el invitado, la exclusión del origen,
interacción de teclado/ratón, bloqueo de instalación y apagado. La lectura completa
del payload dentro del invitado se ejecuta en xHCI; UHCI verifica arranque e interfaz
sin repetir esa lectura lenta. El auditor host verifica el mismo payload completo.
Los resultados ejecutados y sus limitaciones constan en el informe de validación.

## Grabar el USB — procedimiento para una sesión física posterior

**No se ha ejecutado esta operación ni se ha conectado un disco físico durante V8.5A.**

1. Guardar los datos del USB y verificar que se trata de una memoria prescindible
   de al menos 4 GB; anotar modelo, capacidad y, cuando esté disponible, serie.
2. Verificar el archivo completo con `Get-FileHash -Algorithm SHA256` en PowerShell
   o `sha256sum -c SHA256SUMS` en Linux. Comparar con el checksum de esta entrega.
3. Abrir Rufus, identificar expresamente ese USB y seleccionar el archivo `.img`.
   Es una imagen RAW de disco completo, preparada para copia directa DD. Si aparece
   la elección ISO/DD, elegir **DD**. No construir una ISO ni convertir/recrear sus
   particiones. Revisar nuevamente el dispositivo antes de aceptar el borrado del
   **USB seleccionado**. Este procedimiento no autoriza borrar un disco interno.
4. Esperar la finalización y verificación de la herramienta; expulsar de forma segura.
   Si Windows ofrece inicializar/formatear una partición desconocida, cancelar.

En un USB mayor, queda espacio al final y la copia secundaria GPT continúa en el
final de la imagen original. No expandir ni «reparar» el particionado para esta
prueba. Se valida también el arranque virtual sobre un medio mayor cuando conste
expresamente en el informe; no se presupone que todo firmware lo gestione igual.

La [FAQ oficial de Rufus](https://github.com/pbatard/rufus/wiki/FAQ) documenta las
imágenes planas DD y la distinción con ISO. La estructura GPT, MBR `55 aa` y ESP
del archivo se verifican automáticamente. No se ha ensayado Rufus contra un USB real.
Como alternativa, [Raspberry Pi Imager, opción Use Custom](https://www.raspberrypi.com/documentation/computers/getting-started.html)
permite grabar una imagen local: omitir personalización de cuentas y sistema.
Usarlo como grabador no convierte esta imagen x86_64 en un sistema para Raspberry Pi.

## Lista de primera prueba física controlada

- Registrar equipo/placa, CPU x86_64, RAM, versión UEFI, GPU, puerto USB y memoria.
- Guardar la configuración de firmware previa; arrancar mediante el menú temporal
  **UEFI: USB**, sin cambiar el disco de arranque permanente ni usar BIOS/CSM.
- Secure Boot debe estar desactivado: GRUB no está firmado. No borrar claves UEFI.
  Tener disponible cualquier clave de recuperación de cifrado exigida por el equipo.
- No cambiar ciegamente RAID/VMD/AHCI: puede impedir arrancar el sistema existente.
  Preferir discos internos desconectados para la primera comprobación.
- Confirmar pantalla legible, resolución, ausencia de recortes y mensaje
  **SIN INSTALACIÓN**. El modo gráfico necesita un dispositivo DRM compatible,
  inicialmente el framebuffer UEFI/simpledrm; no se incluyen todos los controladores
  y firmware de GPU propietarios. Una pantalla negra se reporta, no se repara aquí.
  La interfaz de esta etapa requiere al menos 1024×768 para todos sus controles;
  resoluciones inferiores quedan pendientes.
- Probar teclado USB/PS2 y ratón; la detección usa capacidades, no nombres QEMU.
  La distribución inicial es US; probar letras y números, sin introducir contraseñas.
  Bluetooth, táctiles y diseños alternativos no están certificados.
- Confirmar que el USB figura como origen excluido y los demás discos como
  **Sólo lectura · no autorizado**; no confundir falta de detección con disco vacío.
- Ejecutar «Comprobar bloqueo» y, con tiempo suficiente, «Verificar payload».
  No abrir shell, montar discos, instalar ni alterar particiones.
- Registrar detección SATA/NVMe/USB, modelo, capacidad, puerto y mensajes; fotografiar
  resultados. Ante un error de protección, apagar y no continuar.
- Apagar con el botón, retirar USB y comprobar que el sistema existente arranca
  normalmente. Restaurar sólo los ajustes UEFI cambiados deliberadamente.

Reportar esa prueba en otro informe con modelo exacto y resultados positivos y
negativos. Pasar QEMU no certifica hardware físico ni autoriza una instalación.

## Limitaciones del kernel nativo (auditoría de código, no prueba física)

- SATA/AHCI: existe controlador nativo; ATAPI permanece sin implementar y hay
  trabajo pendiente en hotplug y sincronización DMA (`Kernel/Devices/Storage/AHCI`).
  El arranque AHCI virtual validado anteriormente no prueba una controladora física,
  RAID/VMD, reanudación, desconexión o todos los tamaños de sector.
- NVMe: existe controlador nativo, con transferencias limitadas a PAGE_SIZE por
  petición en `NVMeNameSpace.cpp` y pendientes de sincronización DMA en el controlador.
  No se han validado aquí arranque nativo NVMe físico, 4Kn, múltiples namespaces o VMD.
- USB: existen controladores nativos xHCI/UHCI. xHCI rechaza versiones fuera de
  0.9–1.2 y controladores sin páginas de 4 KiB; hay rutas incompletas de protocolo/
  transferencias y pendientes de timeout en UHCI (`Kernel/Bus/USB`). No se investigan
  ni corrigen errores históricos. No se ha demostrado root nativo USB físico.
- El escritor V8.2, archivado e inactivo aquí, exige sectores lógicos de 512 bytes
  y mantiene restricciones conservadoras de destino. No se amplía para hardware
  físico, 4Kn, dual boot, discos ocupados o instalación automática.

Que **Linux del preview** detecte un dispositivo SATA/NVMe/USB no demuestra que
el kernel nativo pueda arrancar, leerlo o escribirlo de forma fiable. Esta etapa
termina en el arranque del medio protegido y no inicia otra fase automáticamente.
