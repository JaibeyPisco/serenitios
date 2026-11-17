# PISCO OS V8.1 — prototipo USB autónomo

Fecha: 2026-11-17. Rama: `installer/usb-prototype-v81`.
PISCO de referencia: V7.3, `dcd4feae31a1aee6748a342a7f92d996993e94d6`.
Decisión previa: [auditoría y propuesta USB](InstallationUSBProposal.md).

## Alcance y arquitectura

V8.1 implementa el **arranque y la identificación/selección del destino**, no
la instalación. El flujo probado es firmware UEFI → GRUB x86_64 → Linux mínimo
en initramfs → volumen de datos del USB montado en sólo lectura → inventario y
plan explícitamente confirmado. No hay una GUI compleja ni un botón de instalar
que simule trabajo no implementado.

La imagen es RAW GPT de 2015363072 bytes (1922 MiB): una ESP FAT32 de 128 MiB,
una partición EXT2 de datos de 1792 MiB y las estructuras GPT. GRUB se instala en
la ruta removible `EFI/BOOT/BOOTX64.EFI`; no depende de una entrada NVRAM guardada.
Se reutiliza el ensamblado de GPT/CRC de `Meta/install-serenity.py`.

El Linux de referencia es Ubuntu `6.8.0-149-generic`, con BusyBox estático y
utilidades `blkid`/`lsblk`, sus bibliotecas y los módulos seleccionados. No necesita
red, host compartido, compiladores ni el workspace durante el arranque. El kernel
Linux, sus módulos y su initramfs están exclusivamente en el entorno instalador.
El payload de destino contiene los binarios y el kernel **nativos de SerenityOS**;
no incorpora módulos Linux ni necesita un kernel Linux para ejecutarse.

El kernel Linux, módulos, herramientas y scripts quedan registrados por hash en
`build-manifest.json`; la imagen y el initramfs también tienen SHA-256. Se debe
conservar ese manifiesto con los artefactos para fijar los inputs de una reconstrucción.
La interacción de diagnóstico es por consola serie; VGA muestra el estado y el
inventario. No se ha validado todavía un flujo local con teclado/mouse USB ni
una interfaz gráfica para el usuario final.
La investigación de los fallos USB del kernel Serenity permanece aislada: no se
modificó ese kernel, sus controladores, memoria ni EXT2.

## Payload limpio de V7.3

`Meta/build-pisco-installer.py` usa una instalación CMake **nueva** y rutas runtime
explícitas de Base. No extrae ni clona la imagen/overlay personal de V7.3. El Root
antiguo no se usó como plantilla: se comprobó que tenía bibliotecas anteriores.
Se comprueban Taskbar, WindowServer, Settings, LibGUI y LibGfx contra los binarios
actuales del build.

La plantilla crea un home de fábrica vacío, los accesos/configuración mínimos,
los permisos/propietarios necesarios y bloquea las contraseñas de fábrica. No
copia home personales, claves, historiales, Source, SDK, headers ni tests del
desarrollador. Los recursos Modern Dark/Light y Default se conservan. El kernel
EFI se copia por separado y su hash queda registrado.

La raíz usa `fakeroot` y mke2fs sobre archivos regulares, sin montar dispositivos
del host. UUID, geometría, semilla de directorios y tiempos se fijan. Se normalizan
los tiempos de los inodos **sólo en las nuevas imágenes privadas**; no se repara
ni modifica ningún filesystem de V7.3. Dos ensamblados independientes de la
plantilla corregida produjeron exactamente:

```
root.ext2 SHA-256:
40cfd9b148ae10317885b83fb13adcb8a2c577e2dcea2dc67c5c32f2e05a512f
root-manifest.json SHA-256:
7159c9a0ac38bfcb98676157878f742410a9716b4ecb1692fc61a58232697cdd
```

Esto prueba reproducibilidad del **payload EXT2**, con los mismos inputs. No
afirma reproducibilidad bit a bit de toda la IMG USB: las herramientas FAT y el
volumen externo todavía pueden introducir metadatos variables.

El payload sigue siendo de **demostración de fábrica**, con auto-login `anon`
para comprobar el escritorio. Aunque los hashes de contraseña están bloqueados,
el auto-login no equivale a un sistema instalado con cuentas seguras. OOBE,
usuario/contraseña personalizados y retirada del auto-login están pendientes.
No distribuir este prototipo como sistema instalado listo para producción.

## Protección e identificación

- El medio se localiza por UUID del volumen y un marcador; ausencia o duplicación
  detienen el flujo. Se deriva su disco padre desde sysfs y se excluye completo.
- Sólo se enumeran discos completos con dispositivo real en sysfs. Se excluyen
  particiones, medios de sólo lectura, discos montados/swap reconocidos, discos
  con particiones y firmas de filesystem detectadas.
- Se muestran ruta, tamaño total en bytes, transporte, modelo e identidad/WWID.
  En la prueba: `/dev/sdc` era el USB, `/dev/sda` el destino de 8 GiB con identidad
  `PISCO_V81_TARGET`, y `/dev/sdb` el señuelo con datos `PISCO_V81_PROTECTED`.
  Los nombres de dispositivo no están fijados en el programa.
- Un disco sin firmas se etiqueta `candidate-unverified`: **no** se declara que
  todo su contenido esté a cero. Una futura escritura requerirá verificación
  completa y comprobación más fuerte de montajes, aliases y cambios de identidad.
- `pisco-plan /dev/DISCO` exige selección explícita y escribir exactamente
  `PLAN /dev/DISCO`, vuelve a comprobar elegibilidad e identidad/tamaño, y sólo
  imprime un plan con `writer=unavailable`. No ejecuta mkfs, particionado ni copia.
- No existe backend de instalación PISCO en este initramfs. La consola root de
  diagnóstico incluye BusyBox y **no es un límite de seguridad contra un operador
  root**; no debe exponerse como producto para discos físicos. Las herramientas
  genéricas no forman un instalador autorizado ni quedan certificadas por estas
  comprobaciones. Los tests sólo adjuntan archivos virtuales nuevos.

## Construcción de referencia

En Ubuntu/WSL se necesitan CMake y el build x86_64 ya compilado, Python 3,
fakeroot, e2fsprogs, dosfstools, mtools, GRUB EFI (`grub-mkstandalone` y módulos),
BusyBox **estático**, util-linux, cpio, libzstd, el kernel Linux fijado y sus módulos.
QEMU/OVMF se necesitan para validar. V8.1 no instala paquetes automáticamente.

Crear una instalación runtime nueva, sin actualizar el Root o la imagen activa:

```sh
cmake --install Build/x86_64 --prefix "$PWD/Build/pisco-installer-v81-20261117/runtime"
```

El builder necesita una copia legible del kernel. En esta ejecución se usó una
copia regular `Build/pisco-installer-v81-20261117/linux-6.8.0-149-generic`; el único
uso de root fue leer/copiar `/boot/vmlinuz-6.8.0-149-generic` a ese archivo y darle
propietario 1000:1000 y modo 0644. No se cambió el kernel del host ni sus permisos.

Construir en un directorio **nuevo** (los destinos existentes se rechazan):

```sh
python3 Meta/build-pisco-installer.py \
  --runtime Build/pisco-installer-v81-20261117/runtime \
  --linux-kernel Build/pisco-installer-v81-20261117/linux-6.8.0-149-generic \
  --linux-version 6.8.0-149-generic \
  --output Build/pisco-installer-v81-20261117/rebuild
```

El prototipo validado está en:
`Build/pisco-installer-v81-20261117/v81/pisco-installer-v81.img`.
Su checksum de distribución se conserva en `v81/SHA256SUMS`.
No reemplaza la base, el overlay, el perfil ni `run-modern.sh` de V7.3.

## Ejecución y pruebas

La prueba crea un destino nuevo de 8 GiB y un señuelo de 128 MiB con una tabla
de particiones y un marcador de datos. Ambos se adjuntan con snapshots; el USB
se adjunta en sólo lectura. No se adjuntan `/dev` del host ni imágenes de trabajo.
QEMU usa q35, TCG, **una CPU**, 1 GiB y variables OVMF privadas, sin `-kernel`.

```sh
python3 Meta/test-pisco-installer.py \
  --image Build/pisco-installer-v81-20261117/v81/pisco-installer-v81.img \
  --output Build/pisco-installer-v81-20261117/test-new-xhci \
  --verify-payload

python3 Meta/test-pisco-installer.py \
  --image Build/pisco-installer-v81-20261117/v81/pisco-installer-v81.img \
  --output Build/pisco-installer-v81-20261117/test-new-uhci \
  --usb uhci
```

Los directorios de evidencia deben ser nuevos. La prueba arranca el medio USB,
comprueba identidad y exclusiones, prueba cancelación/confirmación sin escritura,
captura la pantalla, apaga el invitado y compara los hashes de los tres discos.
La consola serie se consume continuamente para evitar que el socket frene el
arranque del invitado. El checksum completo del payload se ejecutó en xHCI.

Resultados ejecutados:

- **xHCI: 7 casos aprobados**, incluidos GPT/CRC, checksums del payload y hashes intactos.
- **UHCI: 6 casos aprobados**, sin repetir el checksum completo del payload.
- Fuente USB, su partición y señuelo con particiones rechazados.
- Tamaño e identidad del destino distinto de la fuente comprobados realmente.
- Cancelación y confirmación de plan comprobadas; ninguna instalación simulada.
- Reproducibilidad de raíz/manifiesto del payload aprobada en construcciones
  independientes, con comprobaciones EXT2 de sólo lectura.
- Control separado: el backend host existente ensambló un disco UEFI desde ese
  payload limpio y arrancó su kernel nativo y WindowServer sin USB ni Linux.
  **No es un disco instalado por V8.1**, por lo que no demuestra instalación
  desde el asistente ni persistencia de un destino instalado.
- Sintaxis Python/shell y `git diff --check` comprobados.
- Guardas adicionales: se rechazaron una imagen de salida existente y un enlace
  simbólico sin cambiar sus bytes; también se rechazó una GPT con CRC alterado.
  Registro: `safety-checks.log`.

Los primeros intentos no se cuentan como aprobados: hubo timeouts por no consumir
la salida del socket serie, un permiso incorrecto del loader Linux en el initramfs,
y una variable compartida del inventario que mostraba tamaños incorrectos. Se
corrigieron en el empaquetado/harness y el runtime. Un control de la plantilla
nativa también detectó un alias `[` ausente y pines duplicados causados por el
grupo QuickLaunch antiguo; se corrigieron únicamente en la plantilla de fábrica
y se repitieron los ensamblados y pruebas finales. La instalación CMake fresca
requiere además los enlaces `test → test-bin` e `install → install-bin`; se
añadieron y se excluyeron los ejecutables auxiliares de tests. El primer control nativo no
tenía `serial_debug` y no pudo confirmar su marcador; se repitió con ese argumento.
Los logs de esos intentos se conservan separados de las ejecuciones aceptadas.
Una repetición concurrente agotó los cinco segundos del helper QMP durante la
negociación inicial. El timeout se amplió localmente a treinta segundos y la
aceptación se repitió secuencialmente; no se cuenta ese timeout como un pase ni
se atribuye por sí solo a un fallo de Linux o del hardware virtual.

Evidencia bajo `Build/pisco-installer-v81-20261117/`:

- `validation-xhci/complete.json`, `results.json`, `serial.log`, `command.json`,
  [captura xHCI](../Build/pisco-installer-v81-20261117/validation-xhci/screen.png).
- `validation-uhci/` con los mismos registros y
  [captura UHCI](../Build/pisco-installer-v81-20261117/validation-uhci/screen.png).
- `factory-repro-final/result.json`, `v81/build-manifest.json`, manifiesto de
  archivos nativos y `final-verification.json`.
- `native-control-v81/`: control del payload corregido; los controles
  anteriores y sus recetas también se conservan separados de la aceptación.

La verificación final compara hashes de la base V7.3, su perfil y los kernels
con la procedencia conservada. Permanecen intactos. El overlay funcional no se
abrió ni se reemplazó; `run-modern.sh` mantiene su perfil V7.3. Los invitados de
prueba quedaron apagados. V8.1 cumple exclusivamente el criterio de arranque
USB autónomo e identificación/plan de un disco distinto del instalador.

## Pendiente y límites de aceptación

**V8.1 sólo aprueba el medio autónomo y la identificación/plan del segundo disco.**
No implementa escritura, particionado del destino, expansión, GUID propios por
instalación, backend privilegiado definitivo, GUI de instalación, OOBE, cuentas,
contraseñas nuevas, retirada del auto-login, reinstalación, recuperación ni dual
boot. No se probaron NVMe, 4Kn, hotplug, aliases de montajes, RAID/dm/LVM, varios
USB iguales ni un instalador sobre disco ocupado. No se certifica resistencia a
pérdida de energía o a un operador root.

BIOS/SeaBIOS y Secure Boot no están implementados/probados en este medio. No se
ejecutó Rufus o Etcher sobre un USB real o virtual; la compatibilidad documental
del formato se describe en la auditoría anterior. Faltan la revisión de licencias
y la provisión de fuentes correspondientes antes de una distribución pública.

No se probó hardware físico. El éxito con USB o SATA virtual no certifica un PC,
sus controladores ni su disco interno. Antes de afirmar que la instalación
funciona, una fase posterior deberá escribir exclusivamente un destino virtual
seleccionado y confirmado, retirar el USB, arrancar sólo ese destino con NVRAM
nueva y verificar login y persistencia. El siguiente trabajo requiere su propio
alcance; esta entrega no lo ejecuta automáticamente.
