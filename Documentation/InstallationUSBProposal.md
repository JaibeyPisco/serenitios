# PISCO OS V8: propuesta del medio USB y la instalación

Auditoría: 2026-11-17. Base examinada: `dcd4feae31` (V7.3).
Estado: propuesta previa a implementación. No es un instalador publicado.

## Decisión recomendada

Distribuir inicialmente una **imagen de disco RAW `.img`, GPT, UEFI x86_64**,
con ESP FAT32 y arranque removible `EFI/BOOT/BOOTX64.EFI`, sin Secure Boot.
Publicar SHA-256 y, opcionalmente, un ZIP que contenga la IMG. No convertir una
imagen EXT2 sin particiones en una supuesta ISO ni distribuir QCOW2/VDI como USB.

Hay un bloqueo demostrado: el sistema actual arranca mediante GRUB UEFI desde
SATA virtual, pero falla escribiendo su raíz cuando el mismo medio es USB. Por
tanto, **no recomendar todavía un USB nativo de PISCO para instalación física**.
Cambiar IMG por ISO o cambiar Rufus por otra grabadora no resuelve ese fallo.

Para avanzar hacia la experiencia de instalación sin comandos, recomiendo como
primera implementación un **entorno live Linux mínimo, autónomo, con GRUB y una
GUI de instalación PISCO**, que despliegue un payload PISCO UEFI preparado con
los mecanismos existentes. El asistente gráfico de primer arranque de PISCO
crearía usuario/contraseña en el disco interno, sin el USB. Esta ruta evita que
el instalador dependa de las escrituras USB de Serenity. Es una propuesta, no
una ruta Linux ya construida ni validada en esta auditoría.

Si se requiere que también el entorno del USB sea Serenity, la alternativa es
resolver primero el bloqueo USB en una tarea independiente y repetir las
pruebas de lectura/escritura; después reutilizar el Installer nativo. No estimar
ese arreglo como un cambio pequeño sin localizar antes su causa.

## Qué existe realmente

### GRUB BIOS

- `CMakeLists.txt`: objetivo `grub-image`, invoca `Meta/build-image-grub.sh`.
- Genera `grub_disk_image`: disco completo, no ISO. Usa root, loop devices,
  parted, montaje, mke2fs y `grub-install --target=i386-pc`.
- La ruta por defecto es MBR: raíz EXT2 desde 1 MiB hasta el final. El espacio
  anterior permite embeber GRUB. `grub-mbr.cfg` carga `/boot/Kernel` por multiboot.
- La variante GPT reserva BIOSBOOT de 1 a 8 MiB, pero fija la raíz de 8 a
  **290 MiB**, aunque el tamaño total se calcula dinámicamente. Esa raíz no
  alcanza para el sistema moderno auditado. No reutilizar esa variante sin
  corregir el dimensionamiento en el generador.
- Las configuraciones BIOS usan `hd0` y `lun0:0:0;partN`; dependen del orden de
  discos. Para el producto: búsqueda GRUB por UUID de filesystem y selección de
  raíz del kernel por PARTUUID en GPT; no pedir al usuario editar el menú.
- EBR es una variante de prueba de particiones, no la distribución recomendada.

### GRUB UEFI

- Objetivo `grub-uefi-image`, `Meta/build-image-grub-uefi.sh`.
- Disco GPT, ESP FAT32 de unos 512 MB y raíz EXT2 en el resto.
- Instala GRUB x86_64-efi con `--removable`; busca la ESP por UUID y encadena
  `Kernel.efi` con `root=PARTUUID:...`. No requiere registrar una entrada NVRAM.
- Es la estructura más cercana al formato propuesto. Se debe reutilizar su
  contrato de arranque, no reemplazarlo por un protocolo nuevo.
- El generador tradicional reconstruye el filesystem desde Base/Root; ejecutarlo
  sobre un árbol de desarrollo desactualizado no incorpora automáticamente la
  versión validada por `run-modern.sh`.

### UEFI directo y backend de instalación

- `Meta/build-image-uefi.sh`: misma idea GPT/ESP/EXT2, con Kernel.efi directamente
  en la ruta removible y `cmdline.txt` para PARTUUID. No usa GRUB.
- `Meta/install-serenity.py`: ensamblador host sin root, loop ni montajes. Crea
  un archivo RAW nuevo, GPT primaria/secundaria con CRC, ESP de 512 MiB y copia
  de una raíz EXT2 offline. Rechaza destinos existentes y `/dev`.
- Puede incluir el binario Installer y un payload de instalación completo en
  una copia ampliada del filesystem. Ya existe empaquetado mediante
  `Meta/try-serenity.py`. No depende de compilar dentro del equipo del usuario.
- Su boot directo es una alternativa de control útil, pero no elimina el
  problema USB del kernel de PISCO.

### Installer nativo

`Userland/Applications/Installer/main.cpp` implementa una GUI real privilegiada
y un backend: valida GPT, acepta sólo dispositivos completos de sectores de
512 bytes, comprueba que **todo** el destino esté a cero, copia, sincroniza,
verifica todos los bytes y reubica la GPT secundaria si el disco es mayor.
`Base/res/apps/Installer.af` solicita privilegios de administrador.

No implementa selección amigable por modelo/serie/capacidad, exclusión explícita
de todo dispositivo fuente/montado, usuarios, contraseña, aprovechamiento de
todo el espacio, reanudación ni borrado autorizado de un disco usado. Conserva
los GUID del payload. El escaneo a cero evita muchas escrituras peligrosas,
pero no equivale a comprobar propiedad del disco ni sirve como flujo normal
para un disco físico usado. La GUI y las operaciones de disco siguen juntas.

La raíz del payload conserva su tamaño: un disco destino grande no se transforma
automáticamente en un filesystem grande. Esto debe mostrarse honestamente o
dimensionarse con herramientas existentes en la etapa live; no anunciar espacio
que no se pueda utilizar.

### Cuentas e inicio de sesión

- `useradd`, `passwd`, `Core::Account::set_password` y `sync` ya existen.
- Account usa `SecretString`, salt aleatorio y `crypt` con prefijo `$5$`.
  Reutilizar el mecanismo actual; no inventar almacenamiento de contraseñas.
- `sync` escribe rutas absolutas `/etc/passwd`, `/etc/group`, `/etc/shadow`:
  llamarlo desde el USB no configura por sí solo la cuenta del disco destino.
  Sus cambios a los tres archivos no constituyen una transacción única.
- LoginServer ya tiene login gráfico real, comprueba contraseña, grupo `window`
  y directorio home. Base configura `--auto-login anon`: debe eliminarse del
  sistema instalado, junto con las credenciales predeterminadas utilizables.
- Recomiendo un asistente de primer arranque en el destino. Reutiliza esas APIs
  con el `/etc` correcto y evita persistir secretos durante el traslado del USB.
  Debe crear home/permisos, grupos necesarios y configuración moderna limpia;
  ser reintentable ante interrupciones; retirar su marcador sólo tras verificar
  cuenta y login. No habilitar una sesión de escritorio normal antes de terminar.
- Si se exige una única pantalla de cuentas antes de copiar, hará falta un
  backend de configuración offline del destino, con pruebas de hashes/permisos
  y recuperación. Linux no puede ejecutar los ELF de Serenity mediante un simple
  chroot. Nunca pasar la contraseña como argumento de proceso, registrarla o
  guardarla en texto plano en USB, payload o configuración pendiente.

## Diseño de distribución e instalación

El medio externo y el payload instalado deben ser distintos:

1. La IMG USB contiene GRUB, su entorno live, recursos de la GUI y el payload
   verificado. La ruta del payload es interna; el usuario no escribe rutas.
2. La GUI presenta bienvenida, discos por identidad y capacidad, resumen de la
   operación, confirmación, progreso real, verificación y reinicio.
3. Un backend privilegiado admite sólo las operaciones necesarias. Excluye el
   USB fuente, discos montados, discos ambiguos y destinos insuficientes; vuelve
   a validar la identidad antes de escribir y no depende de `/dev/hda` fijo.
4. En la primera implementación se aceptan únicamente **discos virtuales nuevos
   y vacíos**. Borrado de discos usados, particionado manual, coexistencia,
   redimensionado y dual boot son trabajo posterior, no funciones simuladas.
5. El disco instalado lleva su ESP, bootloader/kernel y raíz propios. Sus UUID,
   PARTUUID y configuración deben ser coherentes y distintos del medio fuente.
   No clonar sin más todos los identificadores de una imagen pública repetida.
6. Retirar el USB y arrancar el destino, con variables UEFI nuevas, inicia la
   configuración gráfica de cuenta y luego LoginServer. Archivo creado en home,
   cierre y nuevo arranque deben conservarse. El USB no es una dependencia.

La imagen de distribución se construirá desde un conjunto coherente de binarios
y recursos de V7.3 y una plantilla limpia, **no desde el home o el overlay
personal del desarrollador**. Excluir archivos personales, claves, credenciales,
historiales, probes, compiladores y rutas del workspace. El uso de V7.3 real en
la prueba de esta auditoría es privado; esa IMG no es publicable.

Para UEFI inicial: GPT + ESP FAT32 + entorno live/payload; Secure Boot fuera de
alcance. BIOS se entrega después como IMG separada, o se añade BIOSBOOT y GRUB
i386-pc a una IMG dual cuando ambas rutas hayan pasado. No prometer una imagen
dual por tener solamente una ESP. Aun con un live Linux, el PISCO instalado
seguirá necesitando controladores compatibles para el disco interno y la GPU.

Costos relativos: empaquetado UEFI existente, bajo; completar Installer nativo y
cuentas, medio; live Linux con GUI/backend y empaquetado autónomo, medio y con
dependencia de construcción adicional; reparar transporte USB nativo, incierto;
BIOS/UEFI dual, pruebas y ensamblado adicionales; ISO híbrida, complejidad extra
sin beneficio demostrado en esta etapa.

## Compatibilidad con las grabadoras

Rufus documenta DD-Image y comprueba la firma `55 AA` en 0x1fe. La IMG de prueba
tiene esa firma, GPT completa y ESP; no es un EXT2 desnudo. Etcher declara `.img`
y `.raw` en su lista de formatos soportados. Por tanto, el formato de disco RAW
propuesto está admitido por ambas herramientas; se debe grabar el disco completo
en modo imagen/DD, conservando la tabla, ESP y filesystem, sin extraer archivos.
La opción de escritura tipo ISO no forma parte de esta ruta.

Fuentes primarias consultadas el 2026-11-17:

- [Rufus FAQ: DD e Ignore Boot Marker](https://github.com/pbatard/rufus/wiki/FAQ)
- [Rufus: escritura de imágenes](https://github.com/pbatard/rufus/blob/master/src/format.c)
- [Etcher: extensiones admitidas](https://github.com/balena-io/etcher/blob/master/lib/shared/supported-formats.ts)
- [GRUB: instalación removible](https://www.gnu.org/software/grub/manual/grub/html_node/Installing-GRUB-using-grub_002dinstall.html)

Esto verifica soporte documental/de código y estructura del artefacto, **no una
grabación GUI ejecutada con Rufus/Etcher**. No se conectó ni grabó un USB real.
Antes de publicar: fijar versión de la grabadora, seleccionar la IMG en una VM
Windows y grabar exclusivamente un USB virtual descartable; verificar los bytes
grabados y arrancar ese resultado. Una copia RAW hecha por QEMU no se presentará
como una prueba de Rufus.

## Pruebas ejecutadas en esta auditoría

Evidencia: `Build/v8-media-audit-20261117/`; suite host:
`Build/v8-media-audit-tests.log`.

- Git estaba limpio en V7.3 al iniciar. No se modificó código de producto,
  kernel, particiones existentes ni el perfil de `run-modern.sh`.
- `Meta/test-install-serenity.py -v`: **13 pruebas, OK**, incluidas pruebas reales
  de filesystem y manejo de fallos. No prueban por sí solas un arranque ni un
  asistente de cuentas. Algunas fixtures de la suite ejercitan reparación de
  copias temporales; no se reparó la imagen V7.3.
- Se ensambló un medio privado con el backend existente y se puso GRUB
  `2.12-1ubuntu7.3` en la ESP mediante `grub-mkstandalone`. Replica la ruta
  GRUB → Kernel.efi → raíz por PARTUUID, sin root, loop ni montajes del host.
  Es una sonda de viabilidad, no la implementación del medio de instalación.
- QEMU q35, TCG, una CPU, 1 GiB, OVMF sin Secure Boot, sin `-kernel` ni acceso a
  dispositivos físicos. Medio con snapshot descartable y segundo disco virtual
  vacío de 4 GiB. Cada arranque usa variables UEFI privadas.
- Control AHCI: GRUB/EFI/kernel/raíz e inicio de WindowServer y servicios de
  sesión pasaron. Capturas y logs en `ahci/` y repetición `ahci-final/`.
- USB xHCI + almacenamiento BOT: la raíz se monta, aparecen errores de flush
  `errno=74`, y `keymap` falla en `LibCore/ConfigFile.cpp:100`. No se llega al
  escritorio. Repetición xHCI: mismo fallo. UHCI + USB BOT: mismo patrón y
  aserción de keymap. Son fallos de acceso/escritura y de un proceso de usuario;
  no se afirma haber localizado su causa ni que sean un kernel panic.
- La etiqueta genérica «kernel crash/assertion» del primer JSON del harness es
  imprecisa: el serial identifica `keymap` en userspace. Las repeticiones usan
  una etiqueta genérica corregida. El resultado no pasa en ningún caso USB.
- No se ejecutó la generación tradicional con `grub-install`: falta esa utilidad
  y el conjunto de módulos BIOS i386-pc en este entorno. BIOS/SeaBIOS queda sin
  probar. No se instalaron paquetes para ocultar esa dependencia.
- No se ejecutó aquí una instalación completa del payload a otro disco, creación
  de cuenta, login nuevo, ni un live Linux. Los resultados históricos de
  `Documentation/FunctionalInstaller.md` no se cuentan como pruebas de esta fase.

## Puertas de validación antes de uso físico

1. Reproducibilidad: construir desde una plantilla limpia, manifiesto de hashes,
   versiones fijas, licencias y paquetes; sin SDK, red ni workspace en runtime.
2. Integridad: validar ambos GPT/CRC, ESP, bootloader, kernel, raíz y payload;
   rechazar imágenes truncadas, hashes erróneos y discos pequeños/ambiguos.
3. Firmware: UEFI con variables nuevas y arranque removible; BIOS/SeaBIOS por
   separado si se ofrece; probar Secure Boot activado para documentar su rechazo,
   no prometer soporte sin firmas e infraestructura correspondientes.
4. USB realista en QEMU: arranque desde `usb-storage` en xHCI y UHCI, además del
   control AHCI. Lectura/escritura sostenida sin errores, periféricos utilizables,
   escritorio/asistente visible y respuesta a pérdida del medio. Para nativo,
   los fallos observados son una puerta cerrada, no una advertencia cosmética.
5. Grabación: Rufus o Etcher sobre USB exclusivamente virtual, comparación de
   contenido y boot de lo grabado. Probar una capacidad mayor y GPT secundaria.
6. Destino independiente: instalar sobre virtuales nuevos SATA y después NVMe,
   cambiar orden de puertos; excluir fuente, particiones montadas y un disco
   señuelo con datos. Verificar que el señuelo y el USB permanezcan intactos.
7. Flujo gráfico: teclado y mouse, 1024x768 y 640x480, progreso real, cancelación
   antes de escribir, errores comprensibles, fallo de lectura/escritura, pérdida
   de energía e instalación incompleta que nunca se anuncie como correcta.
8. Primer arranque sin USB ni discos de desarrollo: OOBE, cuenta única, home y
   grupos correctos, contraseña válida/inválida, credenciales predeterminadas
   bloqueadas y auto-login desactivado; no secretos en logs/argv/archivos planos.
9. Persistencia: crear archivo, cerrar sesión, apagar, arrancar otra vez sólo
   desde destino y verificar contenido. Repetir con variables OVMF nuevas para
   demostrar que no depende de una entrada temporal NVRAM o del USB.
10. Sólo tras pasar todo lo anterior, definir una lista concreta de hardware
    físico y una fase posterior expresamente autorizada. No certificar «cualquier
    PC» a partir de QEMU. No usar discos físicos durante esta propuesta.

Conclusión: IMG/GRUB es la base adecuada. El formato de grabación no es el
bloqueo actual; lo es el funcionamiento del entorno nativo sobre USB. Antes de
implementar V8 hay que escoger el live autónomo propuesto o abrir la tarea de
habilitación USB nativa, sin confundir el escritorio SATA validado con un
instalador USB validado.
