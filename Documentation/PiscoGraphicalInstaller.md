# PISCO OS V8.4 — instalador gráfico práctico

Base `7f94e9cba3`, rama `installer/graphical-installer-v84`.
Validación: 19 de noviembre de 2026, exclusivamente QEMU y archivos virtuales
descartables. No se modifican Kernel, Userland nativo, Base ni `run-modern.sh`.
No se inicia V8.5.

## Uso

Desde Ubuntu/WSL y la raíz del repositorio:

```sh
./run-installer.sh --session Build/pisco-installer-v84/mi-instalacion
```

El directorio debe ser nuevo. Se crea un disco virtual vacío de 3 GiB y NVRAM
privada; el USB se abre read-only. El asistente arranca automáticamente: no hay
que introducir comandos en el entorno instalador. Elegir el disco, completar
la cuenta y confirmar expresamente el borrado. No hay disco preseleccionado.

Después de la verificación, el lanzador retira automáticamente el USB virtual
mediante QMP. El asistente detecta la retirada y habilita **Reiniciar**. En una
VM lanzada con otra herramienta hay que retirar el USB virtual desde ella;
en ningún caso se debe volver a arrancar desde un destino no verificado.

Para iniciar posteriormente la instalación ya existente, sin imagen USB:

```sh
./run-installer.sh --boot --session Build/pisco-installer-v84/mi-instalacion
```

Este modo sólo adjunta el destino y su NVRAM. No consulta ni necesita la IMG del
instalador y no selecciona otro disco. La cuenta automatizada de aceptación usa
una contraseña efímera no guardada: para utilizar el sistema hay que crear una
cuenta propia con el asistente, no intentar reutilizar esa contraseña de prueba.

## Cinco pantallas

1. **Bienvenida:** alcance e inicio del asistente.
2. **Disco:** dispositivo, capacidad y serie real. Sólo aparecen destinos que
   superan la identificación V8.2. USB, particiones, destinos montados o no
   autorizados se excluyen. Continuar permanece deshabilitado sin selección.
3. **Cuenta:** nombre completo, usuario y dos campos de contraseña ocultos.
   Las reglas y el hash son los de V8.3; no se crea otro autenticador.
4. **Confirmación e instalación:** resumen del dispositivo y cuenta, casilla de
   autorización y botón «Borrar e instalar». El backend revalida identidad y
   tamaño, abre exclusivamente el destino y exige que todos sus bytes sean cero.
   Durante la operación se deshabilitan la navegación y cancelación del asistente.
5. **Finalización:** sólo aparece después de verificar ambas particiones y GPT.
   Permite apagar o reiniciar tras retirar el medio.

La cancelación anterior al inicio vuelve a Bienvenida, borra los campos de cuenta
y no escribe el disco. Los errores no muestran éxito ni una opción de arrancar
la copia incompleta; permiten volver a detectar discos sin borrado automático.

El progreso muestra las operaciones reales notificadas por el escritor:
verificación del origen, examen completo de ceros, cuenta en RAM, GRUB/EFI,
copia y lectura SHA-256 de cada partición, publicación y comprobación GPT.
No hay porcentajes, temporizadores de avance ni animación simulada.

## Dependencias y arquitectura

Linux sigue siendo exclusivamente el entorno de instalación. Se reutiliza la
semilla nativa V8.3 `Build/pisco-installer-v83/payload-2`; no se copian Xorg,
Python, Tk, Linux o sus bibliotecas al sistema de destino. GRUB encadena el mismo
Kernel.efi nativo y se mantienen el esquema GPT y las verificaciones V8.2.

Interfaz: Python 3.12 + Tk/Tcl 8.6, Xorg mínimo, entrada evdev y XKB, fuentes
DejaVu y X11. No se añade escritorio Linux, gestor de ventanas, navegador,
servicios de red ni compositor. Xorg usa modesetting sobre el DRM existente,
`AccelMethod=none` y cursor software; GLX está deshabilitado. Las opciones están
descritas en el [manual de modesetting distribuido por Ubuntu](https://manpages.ubuntu.com/manpages/focal/man4/modesetting.4.html).
No se modifica código de controladores. Se incluyen los módulos de entrada
existentes del kernel Linux 6.8.0-149-generic y sus dependencias, no otro kernel.

Paquetes para preparar el host de construcción:

```sh
sudo apt-get install --no-install-recommends python3-tk xserver-xorg-core \
  xserver-xorg-input-evdev x11-xkb-utils xkb-data xfonts-base fonts-dejavu-core
```

Las versiones efectivas constan en `Build/pisco-installer-v84/packages.txt`.
Las dependencias ELF se copian con sus bibliotecas, igual que en V8.2/V8.3.
Los árboles de Tcl/Tk, XKB y fuentes forman parte del initramfs autónomo.
Xorg sólo escucha conexiones locales; no se habilita TCP. El entorno es de
laboratorio, privilegiado y de un único usuario, no una caja de seguridad frente
a root o a un host QEMU malicioso.

La captura/validación de cuenta se extrajo de la interfaz de texto V8.3 a
funciones compartidas; la interfaz de texto permanece disponible. El escritor
acepta callbacks opcionales de confirmación y progreso. Sin ellos mantiene su
contrato de consola. La confirmación gráfica queda vinculada a la instantánea
de identidad que vio el usuario; no basta con aceptar un nombre de dispositivo.

La copia temporal ext2, la ESP temporal y las credenciales se mantienen en RAM.
Sólo se almacena el verificador PBKDF2 en shadow. No se pasan contraseñas en
argumentos, variables de entorno, archivos temporales persistentes ni logs.
La limpieza del staging se realiza tanto al completar como al fallar, sin esperar
a que se cierre todo el asistente. Python/Tk no garantizan borrado inmediato de
todas las copias de cadenas en RAM; el invitado no configura swap.

## Construcción y repetición

Se requieren las dependencias de V8.2/V8.3, OVMF, QEMU y el staging preservado
de V8.1. Todas las salidas deben ser nuevas, fuera de `/dev`:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 Meta/build-pisco-virtual-installer.py \
  --v81 Build/pisco-installer-v81-20261117/v81 \
  --payload Build/pisco-installer-v83/payload-2 --user-setup --graphical \
  --output Build/pisco-installer-v84/build-NUEVO
PYTHONDONTWRITEBYTECODE=1 python3 Meta/test-pisco-graphical-install.py \
  --image Build/pisco-installer-v84/build-NUEVO/pisco-installer-v84.img \
  --output Build/pisco-installer-v84/prueba-NUEVA
```

`Meta/repack-pisco-graphical-installer.py` crea otra IMG regular nueva, conservando
GPT y payload y reemplazando sólo el initramfs de una construcción gráfica.
Valida el hash del padre y nunca sobrescribe la fuente. `--without-checker`
produce exclusivamente un medio de prueba negativa sin e2fsck; queda marcado en
su manifiesto y no puede convertirse en una imagen de producción por defecto.
La semilla nativa conserva sus hashes; la IMG completa tiene UUID/metadatos por
construcción y no se presenta como bit a bit reproducible.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 Meta/repack-pisco-graphical-installer.py \
  --source Build/pisco-installer-v84/build-NUEVO \
  --output Build/pisco-installer-v84/fallo-NUEVO --without-checker
PYTHONDONTWRITEBYTECODE=1 python3 Meta/test-pisco-graphical-install.py \
  --image Build/pisco-installer-v84/fallo-NUEVO/pisco-installer-v84.img \
  --output Build/pisco-installer-v84/negativa-NUEVA --expect-failure
```

## Validación ejecutada

Imagen aceptada: `Build/pisco-installer-v84/build-5/pisco-installer-v84.img`.
Se usaron una CPU, TCG, USB virtual xHCI, teclado PS/2 y tableta USB; 2 GiB para
el instalador. El asistente se probó a 1280×800 y el escritorio nativo a 1024×768.

`test-3/complete.json` registra nueve casos aprobados:

- Arranque USB autónomo, asistente abierto y ausencia de disco preseleccionado.
- Cancelación desde la cuenta, comprobando SHA-256 completo del destino intacto.
- Contraseña inconsistente, usuario duplicado e inválido rechazados en la GUI.
- Casilla y confirmación explícitas antes de iniciar; progreso de operaciones reales.
- Instalación completa usando sólo la interfaz: **cero comandos en la consola del instalador**.
- Retirada virtual del USB, botón Reiniciar y arranque del kernel nativo/LoginServer.
- Login con la cuenta creada, archivo personal, reinicio y segundo login.
- USB y disco ajeno con hashes sin cambios; contraseñas efímeras ausentes de logs.
- Archivo persistente leído offline, GPT válida y `e2fsck -f -n` sin errores, código 0.

Después de terminar la instalación gráfica, la validación del sistema nativo
usa Terminal para crear/leer el archivo y ejecutar sync. Estos comandos no
intervienen en la instalación. La cuenta nativa es `piscogui`, con permisos y
autenticación V8.3, sin autologin y sin contraseña guardada en las evidencias.

`negative-2/complete.json` registra seis casos aprobados. Repite arranque,
cancelación, validación de cuenta y confirmación/progreso. Al faltar e2fsck,
el asistente informa error, no completa la instalación, vuelve a la selección
de discos y el destino permanece enteramente sin cambios. El USB/disco ajeno
tampoco cambian y no se registran las contraseñas. Es recuperación de la interfaz;
para instalar correctamente se necesita el medio íntegro, no reparar esa copia.

Regresión adicional: `input-regression.json` valida identidad válida, ocho nombres
rechazados, cuatro nombres completos rechazados y seis límites de contraseña.
`hash-regression/result.json` repite la comparación del LibCrypt nativo con
Python/OpenSSL, formato antiguo y entradas inválidas. No se recompila ni cambia
el sistema nativo para V8.4.

El lanzador se probó en modo host sin ventana: `launcher-smoke/result.json`
confirma GUI en el invitado, destino intacto y ausencia de retirada prematura del
USB. `launcher-native/result.json` confirma otro arranque con NVRAM nueva y sin
USB, incluso pasando una ruta de IMG inexistente a `--boot`. Se llegó a LoginServer.
No se presenta el modo GTK del host como una prueba independiente de hardware.

Capturas reales:

- [Bienvenida](../Build/pisco-installer-v84/test-3/graphical/welcome.png).
- [Disco elegido](../Build/pisco-installer-v84/test-3/graphical/selected-disk.png).
- [Validación de contraseña](../Build/pisco-installer-v84/test-3/graphical/password-error.png).
- [Confirmación](../Build/pisco-installer-v84/test-3/graphical/confirmation.png).
- [Progreso real](../Build/pisco-installer-v84/test-3/graphical/progress.png).
- [Instalación finalizada](../Build/pisco-installer-v84/test-3/graphical/complete.png).
- [Primer LoginServer sin USB](../Build/pisco-installer-v84/test-3/graphical/first-login.png).
- [Archivos después del reinicio](../Build/pisco-installer-v84/test-3/graphical/persistence.png).
- [Fallo y retorno seguro](../Build/pisco-installer-v84/negative-2/graphical/failure-recovery.png).

Se conservan comandos QEMU, serial.log, informes, manifiestos, recetas y capturas
bajo `Build/pisco-installer-v84/`. `final-audit.json` confirma coincidencia entre
fuentes y runtime probado, checksum de la IMG, dos arranques nativos y dos logins,
ausencia de pánicos en la ejecución aceptada y sintaxis de Python/shell válida.
`graphics-runtime-files.json` registra los archivos de Xorg/Tcl/Tk empaquetados.
`preserved-before.json` y `preserved-after.json` coinciden para las imágenes,
destinos y perfil protegidos de las etapas anteriores.
Los sondeos anteriores no cuentan como aprobación:
se corrigieron el nombre del HID, la supervisión de PID 1 y la selección del
controlador gráfico. Las primeras pruebas del controlador de teclado se
interrumpieron para corregir la sustitución de texto en Tk; se repitió la batería.
No fueron pruebas aprobadas ni errores de datos del sistema instalado.

## Recuperación y límites

No arrancar una copia sin verificación final. Ante error de origen/herramientas,
usar un medio íntegro y volver a seleccionar un destino vacío. Si la copia se
interrumpió después de escribir particiones, conservar esa imagen para diagnóstico
y crear otro archivo virtual vacío. No hay borrado automático de discos ocupados,
reanudación ni reparación. Se mantienen las limitaciones de atomicidad de V8.2.

No se probaron discos físicos, Rufus, BIOS, Secure Boot, otras GPU, otras
resoluciones Linux, otras distribuciones ni otros idiomas de teclado. Se conserva
la autorización estricta QEMU/serie de V8.2: este medio no es todavía un instalador
para discos reales. No hay particionado manual, dual boot ni recuperación de
contraseñas. Los errores se presentan de forma general para evitar revelar
credenciales; las instrucciones indican no usar una copia incompleta.

Persisten los avisos conocidos de SpiceAgent sin dispositivo y AudioServer sin
canal en QEMU. No se investigaron esos subsistemas. V7.3 y los medios/destinos
validados V8.1–V8.3 se conservan; el perfil de `run-modern.sh` permanece igual.
