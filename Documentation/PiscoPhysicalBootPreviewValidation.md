# PISCO OS V8.5A — informe de validación

Fecha: 20 de noviembre de 2026. Base validada V8.4 `6ae10be7e3`.
Rama: `installer/physical-boot-v85a`. Todas las pruebas fueron virtuales.
El commit de entrega se identifica mediante `git log -1`; `PROVENANCE.json` junto
al artefacto registra su identificador completo después de crear el commit.

## Artefacto

`Build/pisco-v85a/final/pisco-v85a-preview-x86_64-uefi.img`

RAW GPT/UEFI x86_64, 2 015 363 072 bytes. SHA-256:

```text
de422927229af52f10b71c916a03039806fa0e9105907f278875b4275feb9594
```

El directorio de entrega contiene `SHA256SUMS`, `build-manifest.json` y procedencia.
El RAW es un archivo regular autocontenido; la copia local de entrega usa un enlace
duro al build validado para ahorrar espacio. No es un enlace simbólico ni necesita
que sobreviva otra ruta para conservar sus bytes.

Arquitectura, reconstrucción, esquema GPT, dependencias, comandos de QEMU, grabación
Rufus DD, recuperación y checklist físico:
[PiscoPhysicalBootPreview.md](PiscoPhysicalBootPreview.md).

## Pruebas ejecutadas

1. **Repositorio y preservación.** Se partió de Git limpio en `6ae10be7e3` y se creó
   una rama independiente. Comparación SHA-256 de nueve elementos contra el registro
   previo: perfil, Kernel, base ext2 y overlay V7.3; imágenes oficiales V8.1, V8.2,
   V8.3 y V8.4; destino funcional de aceptación V8.4. Todos sin cambios.
   Registro: `Build/pisco-v85a/preservation-results.json`.
2. **Auditoría de V8.4 real.** SHA de la imagen oficial comprobado, extracción de
   Linux/initramfs desde su ESP y cotejo de todos los componentes originales del
   instalador con el manifiesto V8.4. Se extrajeron `root.ext2` y `Kernel.efi` de la
   partición real y se verificaron contra la semilla nativa V8.3, no sólo contra
   nombres de archivos o un manifiesto lateral.
3. **Reproducibilidad final.** `build-7` y `build-8`, construcciones completas en
   directorios nuevos con los mismos inputs y herramientas, producen el SHA-256
   del RAW indicado arriba. Coinciden Linux, initramfs y GRUB. Los manifests conservan
   versiones, hashes de componentes, scripts y configuración mke2fs.
4. **Auditoría del RAW final.** CRC de ambas cabeceras y tablas GPT, límites sin
   solapamiento, MBR protector, fallback EFI PE32+ x86_64, hashes de GRUB/Linux/initrd
   extraídos desde la imagen, sintaxis del runtime, ausencia de entradas activas de
   instalación, originales archivados sin ejecución, procedencia de paquetes,
   `e2fsck -f -n` de la partición de datos y SHA completo del payload nativo.
   Registro: `Build/pisco-v85a/audit-accepted/results.json` y `e2fsck.log`.
5. **xHCI, tamaño RAW original.** QEMU q35/TCG, OVMF UEFI, una CPU, 2 GiB y pantalla
   1280×800. Arranque del USB sin `-kernel`, `-initrd`, discos del desarrollo, red ni
   carpetas compartidas. Interfaz lista; origen excluido; discos SATA, NVMe y USB
   adicionales marcados RO por BLKROGET y sysfs. Comprobación del bloqueo (código 64),
   lectura SHA-256 completa del payload dentro del invitado y apagado por botón.
   Los tres guardas estaban adjuntados escribibles; hashes completos antes/después
   idénticos. Registro: `Build/pisco-v85a/accept-xhci/results.json`.
6. **UHCI, teclado USB como única entrada de teclado.** Misma imagen final y una CPU;
   `i8042=off` evita que el emulador dirija teclas al PS/2 que la interfaz no seleccionó.
   Arranque UEFI, GUI, texto visible, ratón, origen excluido, RO de todos los discos,
   instalación bloqueada y apagado. Todos los hashes sin cambios. Se omite la lectura
   completa del payload en UHCI; ya se ejecutó en xHCI y en la auditoría host.
   Registro: `Build/pisco-v85a/accept-uhci/results.json`.
7. **xHCI sobre USB mayor.** Copia nueva extendida a 4 GiB, sin mover/reparar GPT;
   no se modifica el RAW de distribución. Arranque UEFI, identificación correcta de
   origen de 4 GiB, teclado USB visible, ratón, bloqueo, RO y apagado. Guardas y medio
   extendido intactos. SHA de la copia extendida:
   `bc187681f021a3696b19103d82b9d11bd8513795f723b89db5e1c7da6f574a84`.
   No se repite aquí el hash del payload dentro del invitado.
   Registro: `Build/pisco-v85a/accept-xhci-larger/results.json`.
8. **Identidad ambigua, prueba negativa.** Dos USB con el mismo UUID: detención
   segura `installer medium missing or ambiguous`, sin GUI ni instalación; hashes
   de medio y guardas conservados. Una CPU, xHCI.
   Registro: `Build/pisco-v85a/accept-ambiguous/results.json`.
9. **Verificación visual de teclado.** Capturas reales inspeccionadas en UHCI y en
   xHCI/4 GiB. La comparación de la zona del texto excluye caret y ratón: 972 y 972
   píxeles cambiados respectivamente. El mismo comprobador rechaza la captura inicial
   vacía, evitando aprobar únicamente porque se enviaron eventos QMP.
   Registro: `Build/pisco-v85a/keyboard-accepted.json`.

El arranque adicional del lanzador de entrega usa sólo el RAW del directorio
`final`, USB read-only, NVRAM privada y ningún disco de datos; su resultado se
registra en `Build/pisco-v85a/final-smoke/results.json`. Se termina por SIGTERM
controlado del host después de comprobar GUI y protección, sin confundirlo con
otra prueba de apagado por botón.

## Capturas reales

- [UHCI: teclado USB e inventario protegido](../Build/pisco-v85a/accept-uhci/keyboard.png).
- [xHCI: USB de 4 GiB y discos protegidos](../Build/pisco-v85a/accept-xhci-larger/keyboard.png).
- [xHCI: payload verificado](../Build/pisco-v85a/accept-xhci/final.png).
- [Rechazo de medio ambiguo](../Build/pisco-v85a/accept-ambiguous/fail-closed.png).

Comandos efectivos, series virtuales, logs seriales, PNG/PPM y resultados JSON
permanecen en los directorios de cada prueba. No son mockups.

## Problemas encontrados y corregidos

- Etiqueta ESP inicial demasiado larga para FAT: se corrigió a `P85ABOOT` antes de
  generar las imágenes aprobadas.
- La comprobación inicial suponía que un disco RO debía rechazar `open(O_WRONLY)`.
  Linux puede permitir abrirlo y bloquear las escrituras después. Se reemplazó por
  verificación BLKROGET + sysfs, abriendo exclusivamente O_RDONLY y sin emitir
  escrituras de prueba. Las versiones iniciales se detuvieron de forma segura.
- Se añadió espera para la enumeración USB y diagnóstico de fallo sin shell ni
  salida de PID1. El caso de UUID ausente o múltiple continúa bloqueado.
- El arranque con sólo USB detectó una detención al inspeccionar una unidad óptica
  vacía. La apertura RO usa ahora `O_NONBLOCK`; se conservan ambas comprobaciones
  de protección y se incluye explícitamente una unidad óptica vacía en las pruebas.
- La prueba automatizada intentaba leer JSON serial antes de recibir la línea
  completa. Ahora espera un registro completo válido.
- El fixture con PS/2 y USB podía dirigir eventos de teclado al dispositivo distinto
  del elegido por Xorg. Se verificó nuevamente con sólo teclado USB y texto visible;
  las capturas vacías anteriores no se cuentan como prueba de teclado aprobada.

No se tocaron kernel nativo, memoria, EXT2, controladores ni código de V7.3/V8.4.
No se investigaron errores históricos ajenos a esta preparación.

## Limitaciones y recuperación

- **Ninguna prueba física, grabación real con Rufus, instalación física ni arranque
  nativo en hardware se ejecutó.** El resultado habilita preparar una primera prueba
  controlada del entorno USB; no certifica SATA/AHCI, NVMe o USB del kernel nativo.
- UEFI x86_64 con Secure Boot desactivado; sin soporte BIOS/CSM, UEFI de 32 bits o ARM.
- Validación gráfica en 1280×800. Resoluciones menores, GPU/firmware particulares,
  VMD/RAID, dispositivos 4Kn, suspensión, hotplug físico y periféricos especiales
  permanecen pendientes. No se extrapola QEMU a compatibilidad física.
- Distribución de teclado US; se selecciona un teclado y un puntero detectados por
  capacidades. No se han certificado varios teclados simultáneos ni Bluetooth.
- Una unidad óptica vacía puede mostrar la capacidad provisional publicada por
  Linux (1 GiB en QEMU); no representa un medio insertado ni un destino instalable.
- La protección es del flujo previsto, no una barrera frente a un operador root
  malicioso. No se enviaron escrituras deliberadas a discos internos para probar RO;
  sí se verificaron sus flags y todos los bytes de los guardas virtuales.
- El modo preview no permite instalar, crear usuario ni arrancar el kernel nativo.
  La capacidad de instalación V8.4 se conserva en sus imágenes originales y código.
- Los inputs fijados y las versiones de construcción deben preservarse para repetir
  exactamente el binario. La revisión completa de licencias/fuentes correspondientes
  para una publicación pública masiva queda pendiente.

Ante un fallo: apagar, retirar el USB, conservar logs/fotos y volver al sistema
existente. No reparar GPT, formatear discos ni cambiar el modo SATA como reacción
automática. El medio se puede reconstruir en otro directorio; las imágenes V7.3 y
V8.1–V8.4 y `run-modern.sh` permanecen disponibles para recuperación. No hubo push.
No se inicia otra etapa automáticamente.
