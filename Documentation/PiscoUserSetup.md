# PISCO OS V8.3 — cuenta e inicio de sesión nativos

## Arquitectura y auditoría

Base `456b3df441`, rama `installer/user-setup-v83`. Se conservan los medios y
destinos V7.3, V8.1 y V8.2. No se modifica kernel, EXT2 ni controladores, y no se
cambia el perfil de `run-modern.sh`.

SerenityOS usa `/etc/passwd`, `/etc/group` y `/etc/shadow`. LoginServer obtiene
`Core::Account`, autentica mediante `LibCrypt` y crea la sesión con los UID/GID
del usuario. Exige pertenencia al grupo `window` y un directorio personal.
El payload de fábrica tenía cuentas bloqueadas y `--auto-login anon` en
SystemServer.ini. No existe una base de usuarios paralela en este instalador.

La auditoría de `LibCrypt` encontró que su formato histórico `$5$` calcula una
sola pasada SHA-256 de contraseña y sal, codificada en Base64: **no es el
SHA256-crypt de Linux**, pese a compartir prefijo. Generar ese formato en Linux
no cumpliría el requisito de almacenamiento seguro. Se añade un formato
PBKDF2-HMAC-SHA256 a la misma API `crypt`, conservando la lectura del histórico.
No se usa `useradd`, `passwd` ni `crypt` de Linux para generar cuentas nativas.

Formato nuevo: `$p$600000$<sal ASCII de 16 caracteres>$<verificador Base64>`.
La sal deriva de 12 bytes aleatorios (96 bits); el verificador son los primeros
24 bytes de PBKDF2 (192 bits). Se reutilizan SHA256 y HMAC de LibCrypto, con el
bloque PBKDF2 de RFC 8018 sección 5.2; se evita una dependencia circular entre
LibCrypt, LibCrypto y LibCore. Las 600000 iteraciones siguen el
[criterio PBKDF2-HMAC-SHA256 de OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#pbkdf2).
El formato compacto conserva los 65 bytes de `crypt_data`: no cambia su ABI.
No se afirma que `$p$` sea interoperable con el `crypt` del host Linux.

`Core::Account` genera este formato para contraseñas nuevas en SerenityOS.
En Lagom/host conserva el formato soportado por su biblioteca del sistema.
La autenticación comprueba longitud antes de la comparación en tiempo constante.
No se cambian los controles de LoginServer ni se añade otro autenticador.

## Instalación y protecciones

`Meta/prepare-pisco-user-payload.py` crea una copia nueva del payload V7.3 limpio,
reemplaza únicamente las bibliotecas nativas LibCore y LibCrypt, verifica sus
hashes y ejecuta `e2fsck -f -n`. Registra procedencia, hashes y que la semilla
requiere configuración de usuario. La semilla todavía contiene la configuración
de fábrica; no es por sí sola un sistema instalado con cuenta creada.

`Meta/build-pisco-virtual-installer.py --user-setup` incorpora el configurador
obligatorio y las herramientas offline debugfs/e2fsck al initramfs. Rechaza usar
una semilla que requiere cuenta en el modo V8.2 sin configurador. El backend
también rechaza completar esa semilla si falta el módulo de cuenta.

El flujo solicita nombre completo, usuario, contraseña y repetición. El usuario
debe empezar por letra minúscula y sólo puede contener letras minúsculas,
dígitos, guion o guion bajo, hasta 31 caracteres. Se rechazan nombres presentes
en passwd/group o con directorio personal ya existente, controles y dos puntos
en el nombre completo. Las contraseñas deben tener 12–128 bytes UTF-8 y coincidir.
La pantalla de resumen permite `EDIT` o `YES`, antes de la confirmación del disco.

Se asigna el primer UID/GID libre desde 1000 y un grupo privado del mismo nombre.
Sólo se añaden los grupos `window`, `audio` y `users`. No se añaden `wheel` ni
`phys`: la cuenta no puede acceder a memoria/dispositivos físicos ni ejecutar
utilidades privilegiadas de apagado/reinicio. Las cuentas de servicio y fábrica
conservan sus entradas y permanecen bloqueadas. No hay contraseña root nueva.

Tras confirmar el disco y comprobar que esté enteramente vacío, se prepara una
copia dispersa de la raíz en `/run/pisco` (tmpfs). debugfs modifica sólo esa
copia. Se comprueban contenido, UID/GID y permisos de cada archivo de cuenta,
directorios personales y el sistema de archivos antes de copiar al destino.
Los archivos passwd/group tienen permisos 0644 y shadow 0600, todos de root.

El nuevo hogar y Desktop, Documents, Downloads, Music, Pictures, Videos y .config
tienen propietario del usuario y permisos 0700. La configuración inicial de
Terminal apunta a ese hogar y no contiene archivos de prueba. Se protege también
el hogar de fábrica anon con 0700. Se elimina el argumento de autologin, de modo
que el inicio exige usuario y contraseña mediante LoginServer.

Las protecciones de selección V8.2 permanecen: QEMU SATA, serie autorizada exacta,
rechazo del USB y sus particiones, montajes por major:minor, swap, alias,
ambigüedad, holders/slaves, 512 bytes/sector, disco enteramente cero y confirmación
`INSTALL /dev/sda PISCO_V82_TARGET 3221225472` para el destino de prueba de 3 GiB.
Todas las escrituras de disco siguen usando el único descriptor exclusivo del
destino. GPT se publica al final, después de verificar las particiones copiadas.
Un fallo de configuración de usuario impide alcanzar ese punto y el éxito.

La contraseña no se pasa como argumento, variable de entorno ni comando de shell.
getpass desactiva el eco de la TTY y se rechaza su modo alternativo con eco.
Se genera el hash en memoria; los archivos temporales en RAM sólo contienen el
verificador. No se imprimen contraseñas, hashes ni excepciones con datos de la
cuenta. Python no garantiza borrado inmediato de todas las copias de una cadena
en RAM; el invitado instalador no configura swap. No se promete resistencia a
un administrador root, captura de memoria del host o suplantación del hardware.

El instalador usa 2 GiB RAM y una CPU en estas pruebas, para alojar la copia
dispersa y la ESP en tmpfs. El sistema nativo instalado se prueba con 1 GiB y una
CPU. Linux sólo ejecuta el instalador; el destino arranca con GRUB y Kernel.efi
nativo, sin USB ni kernel Linux.

## Construcción y ejecución de pruebas

Desde Ubuntu/WSL, en la raíz del repositorio, con dependencias de V8.2 y g++:

```sh
ninja -C Build/x86_64 LibCrypt LibCore
PYTHONDONTWRITEBYTECODE=1 python3 Meta/test-pisco-password-hash.py \
  --output Build/pisco-installer-v83/hash-NUEVO
PYTHONDONTWRITEBYTECODE=1 python3 Meta/prepare-pisco-user-payload.py \
  --source Build/pisco-installer-v81-20261117/v81/payload \
  --output Build/pisco-installer-v83/payload-NUEVO
PYTHONDONTWRITEBYTECODE=1 python3 Meta/build-pisco-virtual-installer.py \
  --v81 Build/pisco-installer-v81-20261117/v81 \
  --payload Build/pisco-installer-v83/payload-NUEVO --user-setup \
  --output Build/pisco-installer-v83/build-NUEVO
PYTHONDONTWRITEBYTECODE=1 python3 Meta/test-pisco-user-install.py \
  --image Build/pisco-installer-v83/build-NUEVO/pisco-installer-v83.img \
  --output Build/pisco-installer-v83/prueba-NUEVA
PYTHONDONTWRITEBYTECODE=1 python3 Meta/test-pisco-user-install-negative.py \
  --image Build/pisco-installer-v83/build-NUEVO/pisco-installer-v83.img \
  --output Build/pisco-installer-v83/negativa-NUEVA
```

Todos los directorios de salida deben ser nuevos. Las pruebas sólo adjuntan
archivos virtuales recién creados, sin red ni discos físicos. Las contraseñas
de prueba se generan aleatoriamente en RAM y no se entregan ni se guardan.
El disco de aceptación es una evidencia de laboratorio; para una cuenta propia,
se debe ejecutar el instalador y elegir una contraseña propia en otro destino
virtual nuevo, no reutilizar la cuenta efímera de la prueba automatizada.

## Comando para una instalación personal de laboratorio

Estos comandos crean un destino virtual **nuevo**. Si el directorio ya existe,
el primer comando falla y no se debe continuar sustituyendo archivos existentes.
La consola de la terminal recibe el instalador de texto; la ventana GTK muestra
el estado de la VM. No se habilita registro serie de las respuestas.

```sh
mkdir -m 700 Build/pisco-installer-v83/personal &&
qemu-img create -f raw Build/pisco-installer-v83/personal/target.img 3G &&
cp /usr/share/OVMF/OVMF_VARS_4M.fd Build/pisco-installer-v83/personal/vars.fd &&
qemu-system-x86_64 -machine q35 -accel tcg -cpu max,-x2apic -smp 1 -m 2G \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=Build/pisco-installer-v83/personal/vars.fd \
  -device ich9-ahci,id=ahci \
  -drive if=none,id=os,format=raw,file=Build/pisco-installer-v83/personal/target.img \
  -device ide-hd,drive=os,bus=ahci.0,serial=PISCO_V82_TARGET,bootindex=2 \
  -drive if=none,id=usb,format=raw,readonly=on,file=Build/pisco-installer-v83/build-3/pisco-installer-v83.img \
  -device qemu-xhci,id=xhci \
  -device usb-storage,drive=usb,bus=xhci.0,serial=PISCO_V83_SOURCE,bootindex=1 \
  -display gtk,gl=off,zoom-to-fit=off -vga std -nic none -monitor none -serial stdio
```

En la consola del invitado ejecutar `pisco-install /dev/sda`, revisar los datos,
crear la cuenta y confirmar la ruta, serie y tamaño mostrados. Sólo después de
`V82 INSTALLED VERIFIED` apagar ese entorno con `poweroff -f`. Se conserva el
prefijo del informe V8.2 porque el escritor verificado es el mismo backend;
el informe V8.3 incluye además el nombre y UID/GID de la cuenta.

Para arrancar después **sin USB**:

```sh
qemu-system-x86_64 -machine q35 -accel tcg -cpu max,-x2apic -smp 1 -m 1G \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=Build/pisco-installer-v83/personal/vars.fd \
  -device ich9-ahci,id=ahci \
  -drive if=none,id=os,format=raw,file=Build/pisco-installer-v83/personal/target.img \
  -device ide-hd,drive=os,bus=ahci.0,serial=PISCO_V82_TARGET,bootindex=1 \
  -display gtk,gl=off,zoom-to-fit=off -vga std -nic none -monitor none
```

Introducir la cuenta y contraseña propias en LoginServer. No hay contraseña de
la cuenta automatizada disponible. Las pruebas verifican arranque y login con
QMP y pantalla capturada; no certifican una instalación física ni herramientas
de grabación USB. `./run-modern.sh` conserva su sesión anterior V7.3.

## Recuperación y límites

Si falla o se interrumpe el configurador antes de copiar, el destino permanece
vacío y no existe una instalación completa. Si el corte ocurre después de
comenzar la copia de particiones, rigen las limitaciones V8.2: la copia parcial
se conserva para diagnóstico y se usa otro archivo virtual nuevo. No hay
reanudación, borrado automático ni reparación de discos importantes.

Se mantienen pendientes el asistente gráfico propio, recuperación/restablecimiento
de contraseñas, gestión posterior de cuentas, roles administrativos y hardware
físico. El coste PBKDF2 puede retrasar la respuesta del login síncrono existente,
especialmente con emulación TCG. No se baja el coste para acelerar la prueba.
No se promete endurecimiento completo del escritorio contra usuarios hostiles.
El cambio prueba permisos de hogar/shadow y no sustituye una auditoría de todos
los servicios IPC de SerenityOS. No se comienza V8.4.

## Resultados

Validación ejecutada el 18 de noviembre de 2026. Imagen:
`Build/pisco-installer-v83/build-3/pisco-installer-v83.img`. La procedencia y su
SHA-256 constan en `build-3/build-manifest.json`; la semilla es `payload-2`.

Se compilaron LibCrypt y LibCore. `hash-test-1/result.json` registra cuatro
vectores comparados con Python/OpenSSL, con contraseña normal, vacía, larga y
UTF-8: configuración nueva, hash completo, contraseña incorrecta y compatibilidad
histórica. También se rechazaron ocho configuraciones de formato inválido.

`test-4/complete.json` registra once casos QEMU aprobados:

1. Rechazo del USB, su partición, disco ajeno no autorizado y alias de ruta.
2. Usuario duplicado (`anon`), nombre de usuario inválido y nombre completo inválido.
3. Rechazo de repetición de contraseña diferente.
4. Edición de datos y cancelación de confirmación, con destino sin cambios.
5. Configuración obligatoria de cuenta antes de completar la instalación.
6. Comprobación offline del hash, usuarios/grupos, hogar, permisos y ausencia de autologin.
7. Rechazo de contraseña incorrecta por LoginServer nativo real.
8. Login correcto de `piscotest` desde el disco instalado, sin USB ni Linux.
9. Reinicio virtual por QMP después de `sync`, nuevo login y lectura del archivo personal.
10. UID/GID y restricciones de acceso comprobados en la Shell nativa y offline.
11. Contraseñas efímeras ausentes de los registros `.log` comprobados de esa ejecución.

La identidad nativa registrada es UID/GID 1000, grupos `window`, `users` y
`audio`, sin `wheel` ni `phys`. `test -r` devuelve 1 tanto para el archivo
existente de anon como para `/etc/shadow`. El archivo personal contiene
`PISCO-V83-PERSISTED` después del reinicio. La configuración personal inicial
tiene propietario 1000:1000 y se conserva.

`negative-2/complete.json` registra cinco casos aprobados: destino montado
rechazado y sin cambios; fallo tardío de configuración al retirar e2fsck del
initramfs de prueba, sin éxito ni escrituras; interrupción al comenzar la copia
de configuración en RAM, sin éxito ni cambio del destino; disco incompleto sin
arranque nativo; USB intacto y ausencia de contraseña en sus transcripciones.

`guards-1/complete.json` añade tres casos aprobados: invocación directa del
backend V8.2 incapaz de omitir la configuración; contraseña corta rechazada sin
eco, éxito ni escrituras; USB intacto y transcripciones sin la contraseña.
Se ejecutó con `--guards-only`; el controlador entregado incluye también estas
pruebas al ejecutar la batería negativa completa.

Capturas reales de la ejecución aceptada:

- [LoginServer sin autologin](../Build/pisco-installer-v83/test-4/native/login.png).
- [Escritorio de la cuenta nueva](../Build/pisco-installer-v83/test-4/native/desktop.png).
- [Archivo conservado tras reiniciar e iniciar sesión](../Build/pisco-installer-v83/test-4/native/screen.png).
- [Destino incompleto sin arranque](../Build/pisco-installer-v83/negative-2/incomplete-native-boot/screen.png).

Cada directorio conserva comandos QEMU, logs y resultados; `test-4/installed.json`
contiene identificadores GPT, cuenta creada y hashes de particiones. Los
registros no incluyen la contraseña de aceptación. No se adjuntaron discos físicos
ni imágenes personales existentes a estas VMs.

`final-audit.json` confirma los hashes independientes de ambas particiones,
procedencia de la imagen y coincidencia de las fuentes entregadas con el runtime
probado. Verifica dos arranques nativos y dos logins, ausencia de Linux/USB en el
comando de arranque y ausencia de pánicos en el log nativo. Los archivos de cuenta
y las bibliotecas del sistema tienen los propietarios y permisos previstos.
`test-4/fsck-after-reboot.log` contiene las cinco pasadas de `e2fsck -f -n` sobre
una copia offline posterior al reinicio, sin errores, código 0 y sin reparación.
La sintaxis de los scripts pasó y no hay cambios en Kernel, Base o el lanzador
V7.3. `preserved-before.json` y `preserved-after.json` coinciden: bases, kernels,
overlay/perfil V7.3, medios V8.1/V8.2 y destino instalado V8.2 permanecen intactos.

Los intentos anteriores no se cuentan como aceptación. `test-1` se detuvo por
una espera del controlador que incluía rechazos previos. `test-2` y `negative-1`
se interrumpieron para repetir sobre la imagen con verificación explícita de
metadatos. `test-3` instaló, autenticó y conservó archivos, pero la automatización
omitió un punto y coma al introducir los comandos de permisos: no aprobó la
batería completa. Se corrigió el mapa de teclas y se hizo que el controlador
rechazara errores HMP; `test-4` repitió y aprobó todo. Las imágenes intermedias
V8.3 sustituidas se retiraron para ahorrar espacio, conservando manifiestos,
hashes y registros en `superseded-artifacts-removed.json`.

Persisten los avisos ya conocidos de SpiceAgent sin dispositivo y AudioServer
sin canal en este hardware virtual. No se investigaron ni se cambiaron esos
subsistemas. No se ejecutó una batería completa nueva de aplicaciones V7.3,
logout, cambio posterior de contraseña o hardware físico. No se empieza V8.4.
