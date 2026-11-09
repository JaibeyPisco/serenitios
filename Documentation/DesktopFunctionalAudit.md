# Auditoría funcional del escritorio — 9 de noviembre de 2026

Estado: auditoría y propuestas. No se implementaron mejoras de aplicaciones,
kernel, controladores ni apariencia. La investigación de EXT2 y del bloqueo con
cuatro CPU permanece cerrada temporalmente.

## Consolidación y recuperación

Se revisó Git sobre `main`, HEAD
`13372c6dee57a4a609a1fb76b5ae8175e79d9419`. Antes de esta auditoría había
42 rutas pendientes: 15 modificadas y 27 sin seguimiento. No había cambios
preparados en el índice. El inventario individual con estado, clasificación y
SHA-256 está en `Build/desktop-audit-20261109/inventory.json`.

- **Experimentales: 12 rutas de kernel.** Los seis archivos nuevos
  `Journal.h`, `JournalCommit.h`, `JournalInode.h`, `JournalRecovery.cpp`,
  `JournalReplay.h` y `TransactionBuffer.h`, más la integración en
  `Kernel/CMakeLists.txt`, `Ext2FS/FileSystem.cpp`, `Ext2FS/FileSystem.h`,
  `Ext2FS/Inode.cpp`, `FileSystem/FileSystem.h` y `VirtualFileSystem.cpp`.
  La recuperación es explícita y restringida; el escritorio corriente no la
  necesita. Los prototipos de transacciones no integran escrituras ordinarias
  con la caché. Algunas modificaciones de estos mismos archivos son defensas
  necesarias **para conservar correctamente esa integración**: validación de
  geometría, protección de solo lectura, rechazo de opciones ignoradas y orden
  del mutex de desmontaje. No deben separarse o retirarse automáticamente.
- **Pruebas y diagnóstico: 24 rutas.** El job de CI; los scripts/adaptadores
  `Meta/test-*` pendientes; `Meta/gdb-dump-kernel-threads.py`; el registro de
  tests y los tres archivos de pruebas EXT2 modificados/nuevos. Son evidencia y
  herramientas, no funciones del escritorio. Se conservan, incluidas las
  configuraciones SMP para reproducibilidad histórica; no se ejecutan en esta
  etapa. El listado exacto está en el inventario.
- **Documentación e investigación: seis rutas.** `Ext2CrashTesting.md`,
  `Ext2Transactions.md`, `StabilityReleaseGates.md`, `Ext2TruncateRisk.md`,
  `JournalRecovery.md` y `TransactionalStorageImplementation.md`. Necesarias
  para preservar limitaciones y decisiones. El plan transaccional anterior no
  constituye autorización para continuar implementándolo.

Se conservaron todos los archivos existentes. No se hicieron reset, checkout,
stash, limpieza, commits ni cambios en el índice. `git diff --check` pasó. El
diff binario de archivos previamente seguidos quedó idéntico al inicial.

Se guardaron `pending.patch`, `untracked.tar`, `git-status.txt`, un bundle Git
de historia completa verificado (`history.bundle`) y sus hashes
(`backup.sha256`), en `Build/desktop-audit-20261109/`. El bundle conserva commits;
el patch y el tar conservan el trabajo pendiente. Ninguno sustituye a los demás.
Las imágenes, campañas y registros anteriores permanecen en `Build/`, incluyendo
`truncate-risk`, `stability-consolidation` y las campañas `consolidation-*`.
Esos artefactos ignorados **no quedan incluidos en un commit normal ni en el
bundle**; se mantienen en el disco actual, sin alegar respaldo externo.

**Punto Git propuesto antes de nuevos componentes:** crear una rama
`checkpoint/stability-before-desktop-20261109`, registrar en un commit de
checkpoint las 42 rutas inventariadas y este informe, y etiquetar ese commit
como `stability-before-desktop-20261109`. Debe describirse como checkpoint de
investigación, no como versión estable ni lista para publicación. No se creó
ese commit ni se publicó nada: aquí se solicita proponer el punto de recuperación.
Para desarrollo posterior, partir de ese commit en una rama de escritorio; no
mezclar nuevas funciones con más trabajo de journaling.

## Configuración y alcance de la comprobación

Dos sesiones gráficas independientes en QEMU TCG, q35, VGA estándar, 1 GiB,
`-smp 1`, disco raíz con `snapshot=on`, sin discos personales ni compartidos.
Se usó el kernel actual y la imagen offline existente
`Build/ext2-phase2-20261107-b/root.ext2`. No se reconstruyó toda la imagen de
usuario: los resultados corresponden a sus binarios instalados y al kernel
actual, no certifican cada fuente actual de userland. El comando completo se
conserva en `command.json` y `shutdown-command.json`.

`Meta/try-serenity.py` ya usa snapshot para el disco y copia temporal de las
variables UEFI; al omitir `-smp`, QEMU usa una CPU. En cambio, `Meta/run.py`
mantiene **dos CPU por defecto y almacenamiento persistente**. Para ese
lanzador hay que establecer `SERENITY_CPUS=1` y añadir `-snapshot` mediante
`SERENITY_EXTRA_QEMU_ARGS`, conservando los demás argumentos necesarios.
No se cambió ningún valor global. Las pruebas que verifican persistencia deben
usar copias descartables persistentes deliberadamente, no snapshot; los discos
extra del helper de capacidad no reciben snapshot automáticamente.

Las escrituras de la auditoría solo afectaron al overlay temporal. El hash del
disco base fue verificado sin cambios entre ambas sesiones. Los cambios dentro
del invitado se descartan al terminar QEMU: los documentos importantes deben
permanecer en el host. No se efectuaron pruebas con cuatro CPU ni nuevos cortes
de EXT2. La auditoría visual usó capturas del framebuffer y entradas QMP.

## Resultados funcionales y prioridad

- **Arranque gráfico: operativo en las dos sesiones iniciadas desde el host.**
  WindowServer, Taskbar, escritorio y Terminal llegaron a estar utilizables.
  Impacto: fundamental; no se identificó aquí una corrección necesaria.
- **Menú de aplicaciones: operativo para abrir el menú, acceder a Run y al
  diálogo Exit.** También se revisó el descubrimiento de aplicaciones y los
  mensajes de error de lanzamiento existentes. No se probó cada entrada.
- **Ventanas: apertura y cierre comprobados** de File Manager, Settings,
  Display Settings, Calculator, Text Editor y Browser. No se auditaron todos
  los atajos, múltiples monitores ni todas las combinaciones de foco.
- **File Manager: navegación de home, creación de una carpeta con espacios,
  renombrado y borrado con confirmación comprobados.** El documento guardado
  desde Text Editor apareció en el listado. Copia/movimiento masivo y archivos
  comprimidos se revisaron por código, no se certificaron visualmente.
- **Configuración básica: centro Settings y Display Settings abren.** Se
  observó el fondo y sus controles y se canceló sin cambiarlo. No se certifica
  aplicar/persistir cambios de resolución, teclado, red o usuarios.
- **Aplicaciones esenciales:** Terminal ejecuta comandos; Calculator mostró
  `2 + 2 = 4`; Text Editor guardó `/home/anon/audit.txt` y perdió el indicador de
  cambios; Browser abrió y renderizó su página local de bienvenida cuando se
  lanzó desde Terminal. No se certificaron Internet, TLS, audio ni impresión.
- **Apagado: pasó en la segunda sesión.** El registro confirma desmontaje
  limpio de EXT2 y `Attempting system shutdown`; QEMU terminó sin intervención
  del host. Evidencia en `shutdown-serial.log`.
- **Reinicio: no completó en la primera sesión.** Tras elegir Reboot desde
  Exit, el registro dejó de avanzar durante más de 30 segundos en el desmontaje
  de `/dev`; no volvió al escritorio. Se terminó esa VM desde QMP y se abrió
  una nueva sesión para separar el ensayo de apagado. Impacto **alto**,
  complejidad y riesgo **desconocidos**; causa pendiente, sin atribuirla a EXT2
  ni al problema SMP. No se abrió investigación adicional del kernel.
- **Run informa éxito tras una terminación anormal: confirmado.** El intento
  de ejecutar `Browser` desde Run produjo un Shell hijo con señal 11 y Crash
  Reporter, mientras Run registró `Child shell exited with code 0` y `Ran via
  command shell`. El código usa `WEXITSTATUS` sin verificar `WIFEXITED`.
  Impacto **alto** para diagnóstico y confianza; corregir el informe tiene
  complejidad y riesgo **bajos**. La causa del crash del Shell es desconocida
  y no se resolvería con esa corrección.
- **Errores de archivos poco útiles: confirmado por código, no reproducido
  visualmente en esta sesión.** `TRY` puede devolver errores del helper sin
  emitir un registro `ERROR`; la GUI sustituye EOF por `Read from pipe returned
  null.`. Impacto **medio-alto**, complejidad **baja-media**, riesgo **bajo-medio**.
- **Compresión/extracción bloquean la acción de File Manager: por código.**
  `do_create_archive` y `do_unzip_archive` hacen `waitpid(..., 0)` dentro de la
  acción GUI. Impacto **medio-alto** con archivos grandes, complejidad **media**,
  riesgo **medio** por vida útil de ventanas y procesos. No se midió un cuelgue
  de escritorio completo; el bloqueo identificado corresponde al proceso GUI.
- **Hallazgos secundarios, sin propuesta de implementación ahora:** el texto
  de progreso toma `parts[7]` y pierde el resto de nombres con espacios
  (impacto bajo, complejidad/riesgo bajos); utmpupdate informa JSON inválido en
  esta imagen (impacto bajo en el uso observado, causa pendiente). Los avisos
  de AudioServer y SpiceAgent no prueban defectos del sistema: esta VM no
  proporciona esos dispositivos. La ventana de Browser apareció parcialmente
  fuera de pantalla; no se determinó si proviene de preferencias de la imagen.

## Tres mejoras propuestas, sin implementar

### 1. Run debe reconocer y mostrar una terminación anormal

**Problema:** confunde el estado de salida de un Shell muerto por señal con
éxito y cierra su interfaz. Es el candidato con evidencia dinámica más fuerte.

**Esperado:** distinguir fallo de `waitpid`, salida normal y terminación por
señal; no registrar éxito ni ocultar el fallo. Mostrar un mensaje comprensible
y permitir corregir/reintentar el comando. Conservar la semántica existente
para comandos válidos y errores 126/127; no convertir todo código no cero en
fallo de lanzamiento sin analizar ese contrato.

**Componentes:** `Userland/Applications/Run/RunWindow.cpp`, especialmente
`run_as_command` y `do_run`; prueba focalizada del estado del hijo.

**Complejidad:** baja, aproximadamente 0,5–1 jornada incluyendo comprobación.
**Riesgos:** mensaje duplicado con Crash Reporter, pérdida de detalles del
error, cambios accidentales en la interpretación de comandos. No arregla la
causa del crash del Shell ni añade supervisión a todas las aplicaciones.

**Aceptación:** comando válido sigue funcionando; comando inexistente conserva
error visible; hijo terminado por señal produce fallo explícito y nunca éxito;
el usuario puede reintentar y el escritorio sigue utilizable.
**Verificación visual:** capturar Run antes y después de un comando válido,
uno inexistente y un hijo terminado deliberadamente en una imagen temporal;
comparar el cuadro de error y el registro. No depender de reproducir el crash
intermitente observado para probar el manejo de señales.

### 2. File Manager debe explicar por qué falló una operación

**Problema:** el protocolo entre helper y GUI puede perder el error real y
mostrar un fallo de pipe que no ayuda a resolver permisos, ruta o falta de
espacio. No implica que una operación parcial haya revertido sus cambios.

**Esperado:** propagar una sola respuesta terminal de error con operación,
ruta y causa disponible; diferenciar EOF inesperado de un error ya informado.
No añadir reintentos automáticos ni afirmar que se deshicieron cambios.

**Componentes:** `Userland/Services/FileOperation/main.cpp`,
`Userland/Applications/FileManager/FileOperationProgressWidget.cpp` y su
cabecera solo si hace falta guardar estado. Usar el protocolo existente.

**Complejidad:** baja-media, aproximadamente 1–2 jornadas con regresiones.
**Riesgos:** doble notificación, terminar antes de consumir el mensaje,
diagnóstico ambiguo de operaciones parcialmente realizadas.

**Aceptación:** copiar/mover/borrar siguen funcionando en los casos normales;
destino sin permiso o inexistente muestra operación/ruta/causa; un fallo
intermedio no informa éxito y no se describe como rollback. Probar falta de
espacio únicamente sobre disco pequeño descartable.
**Verificación visual:** comparar diálogo actual y nuevo ante permiso denegado
y destino inválido; comprobar que la ventana se recupera y que archivos
afectados/restantes coinciden con lo informado.

### 3. Evitar la espera bloqueante al crear o extraer ZIP

**Problema:** las acciones de archivo comprimido esperan sincrónicamente al
proceso hijo; File Manager no puede procesar normalmente sus eventos mientras
esa espera continúa.

**Esperado:** lanzar el helper y observar su terminación de forma asíncrona con
los mecanismos existentes de userland; mantener la ventana sensible, impedir
acciones duplicadas sobre la misma tarea e informar éxito/error al terminar.
No introducir un nuevo gestor global de trabajos ni añadir cancelación de
procesos como parte de este cambio.

**Componentes:** `Userland/Applications/FileManager/main.cpp`, en
`do_create_archive` y `do_unzip_archive`; pequeña clase local o cabecera solo
si lo exige la vida útil del trabajo. No cambiar kernel, formato ZIP o APIs
generales de procesos.

**Complejidad:** media, aproximadamente 2–3 jornadas incluyendo ciclo de vida.
**Riesgos:** callbacks hacia ventanas destruidas, procesos sin recoger,
notificación duplicada y cambio involuntario de argumentos/ruta del helper.
Si el mecanismo existente no permite una solución local, volver con propuesta
antes de ampliar el alcance.

**Aceptación:** durante un ZIP grande File Manager repinta y se puede mover;
el resultado conserva nombres y contenido; ZIP inválido muestra error;
cerrar la ventana durante el trabajo no causa crash ni deja zombies.
**Verificación visual:** grabar o capturar una extracción suficientemente larga
mientras se mueve la ventana y se usa otra aplicación; repetir con archivo
inválido y cierre de ventana. Todas las entradas y salidas sobre discos
temporales.

## Recomendación y cierre

Orden recomendado: **Run → errores de archivos → ZIP asíncrono**. Las dos
primeras tienen alcance pequeño y mejoran resultados que el usuario ve; la
tercera mejora respuesta sin rediseñar la interfaz. Son estimaciones de alcance,
no compromisos de calendario. El reinicio queda visible como limitación de
alto impacto, sin alegar una solución acotada ni desviar este trabajo al kernel.
Mientras tanto, usar apagado y un nuevo lanzamiento de QEMU; si una VM no sale,
la terminación desde el host se limita a imágenes descartables.

Las capturas PNG, registros serie, comandos, inventario y respaldos están en
`Build/desktop-audit-20261109/`. El crash del Shell quedó en el registro y en
`browser.png`; el coredump temporal del invitado no fue exportado. Esta auditoría
no declara al escritorio libre de fallos ni sustituye una campaña completa de
aplicaciones. Se detiene aquí, antes de implementar las tres mejoras.
