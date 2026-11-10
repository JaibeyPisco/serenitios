# Visual modernization: architecture and build audit

Date: 2026-11-10. Baseline: `237d48a6fd712d2e5e030c3aa1f5b32991b9a115`.
Planning branch: `desktop/visual-modernization-plan`.

This delivery changes documentation only. It does not apply a theme, replace
resources, rebuild the kernel, or implement a new shell. The two light/dark
concept images attached to the request are visual references, not screenshots
of implemented functionality. See [the design system](VisualDesignSystem.md)
and [the implementation plan](VisualModernizationPlan.md).

## Preserved state and evidence

Git was clean before creating the planning branch. Run's `5727103edca`, file
operation errors' `209b1845620`, ZIP's `237d48a6fd`, and the stability checkpoint
and tag remain ancestors/references. No history or remote was changed.

The immediately preceding environment audit at this same baseline actually:

- Compiled `LibGUI/Button.cpp`, `WindowServer/WindowFrame.cpp`,
  `WindowServer/WindowManager.cpp`, `Taskbar/TaskbarWindow.cpp`, and
  `FileManager/main.cpp` into isolated outputs.
- Linked LibGUI, WindowServer, Taskbar, and File Manager into isolated outputs.
  WindowManager is part of WindowServer, not a separate executable.
- Booted the existing validated graphical image in QEMU TCG with one CPU,
  1024x768 graphics, no network, and `snapshot=on`; opened the application menu.
- Reviewed the desktop/menu screenshots and verified the backing image's hash
  was unchanged. The installed Default theme matched the source theme.

Evidence remains under `Build/visual-environment-audit-20261110/`, particularly
`compile-results.json`, `link-results.json`, `qemu-results.json`, `desktop.png`,
and `applications-menu.png`. These checks were not repeated as a full-system
build for this documentation task. ZIP/Run/file-operation validation remains in
the existing validation reports; no new regression pass is claimed here.

## Build readiness and Lagom

Available: the x86_64 Serenity GCC 16.2 toolchain, CMake 3.30.3, Ninja, ccache,
QEMU 8.2.2, Python, e2fsprogs, retained objects/libraries, compile database,
generated headers, and the generated Ninja graph. No installation was needed.

`Build/lagom` and `Build/lagom-install` are absent. The Ninja graph still refers
to tools below `Build/lagom-install/bin`, and `Lagom_DIR` is NOTFOUND in the
target cache. `CMakeLists.txt` requires `find_package(Lagom CONFIG REQUIRED)`.
The earlier real build failure is retained in
`Build/async-zip-resume-20261110/build.log`. This is a missing host-build
installation, not a missing LibGUI dependency inside Serenity. Removal during
Build cleanup is consistent with the state, but no deletion record establishes
who removed it or when.

Lagom supplies host tools such as GMLCompiler, IPCCompiler, and
ConfigureComponents. It is needed for normal configuration and regenerated
inputs. It is not needed to edit a theme INI or to compile/link a bounded set of
existing translation units when all their generated dependencies are retained.
Direct commands from the compile database are a temporary, dependency-conscious
route, not a replacement for the build system or a valid clean build. Never
bypass regeneration after changing GML, IPC definitions, source lists, options,
or required generated data. `ninja -n` alone does not prove readiness: it can
stop at the CMake regeneration step.

The default Ubuntu compiler is GCC 13.3, which rejected `-std=c++2c` in a small
syntax-only probe. The already installed `gcc-14`/`g++-14` pair is available;
G++ 14.2 accepted the same probe. The current repository requires C++26 in
`Meta/CMake/common_compile_options.cmake`. This probe does not establish that
every Lagom tool builds with GCC 14. Evidence:
`Build/visual-plan-audit-20261110/host-compiler-probe.json`.

The minimum recovery before a generated-input stage is to use the existing
compiler pair to build/install the host-tools configuration of Meta/Lagom into
a local Build prefix, restore its exported CMake package, and then reconfigure
the existing userland build against that prefix. The existing superbuild's
`lagom-install` step already performs that host/target separation. Keep
`BUILD_LAGOM=OFF`, Jakt disabled as in the target configuration, and Ladybird
disabled; do not build the optional host application/test suite. No apt upgrade,
global install, cross-toolchain rebuild, `HACKSTUDIO_BUILD` workaround, or kernel
build is justified for planning or the first resource-only milestone. Missing
cached generator input data may need to be restored when that milestone arrives.
Full recovery commands and a stop condition are in the implementation plan;
they have not been executed or certified by this audit.

## Graphics audit

### WindowServer and decoration

`Userland/Services/WindowServer/WindowFrame.cpp` owns title buttons, frame geometry
and frame input behavior. `WindowManager.cpp` manages activation, placement,
maximization, workspaces, and theme changes. Frame painting/layout is provided by
`LibGfx/WindowTheme.h` and the Classic, Glass, and Plastic implementations.
Classic calls the shared style painter and obtains colors/metrics from the
palette. The current Default theme uses icon-only title buttons and already
specifies a radius, title height, and button dimensions.

Theme-only: colors, existing metrics, title alignment, existing button-icon
paths and bitmap-shadow paths. Code: new frame shapes beyond the painter's
support, relocating buttons, hit testing, snap/work-area behavior, or global
application menus. A radius setting does not by itself establish correct alpha
clipping or clickable corners; those require visual/input tests.

Important shell constraint: `WindowManager::desktop_rect()` subtracts the fixed
`TaskbarWindow::taskbar_height()` from the main screen's height. The current value
is 27 pixels. A top panel plus a dock is not a theme change: reserved rectangles,
maximized windows, popup anchors, applets, and fullscreen behavior must agree.

### LibGUI and controls

LibGUI owns widgets, layouts, keyboard focus, models, menus and dialogs. Its
painting delegates to LibGfx. `LibGfx/StylePainter.cpp` already selects
`SerenityStylePainter`; the project is not starting with an untouched classic
widget painter. `SerenityStylePainter.cpp` draws rounded controls using semantic
palette roles. `DesignMetrics.h` already defines 8-pixel spacing, 12-pixel content
margins, 28-pixel control height, and radius 5.

Theme-only: semantic colors. Code/GML: widget minimum sizes, padding, row heights,
layout, focus/hover geometry and states missing from a painter. Shared metrics
do not automatically resize every existing widget; many GML forms use fixed
dimensions. Changing the global font or height first would risk clipping them.

### Themes, fonts and resources

There is one installed theme INI in `Base/res/themes`: Default. Multiple icon
theme directories exist, but those are not additional complete system themes.
`Base/res/color-schemes` contains terminal/color schemes including Base16 Dark,
Nord and Serenity. They do not replace a complete UI theme.

`LibGfx/SystemTheme.cpp` loads named roles. Missing UI color entries can fall
through to a color scheme and ultimately black; there is no CSS inheritance
from Default. A new light/dark theme must explicitly audit all applicable roles.
`DisplaySettings/ThemesSettingsWidget.cpp` already enumerates installed themes,
previews them and applies them through the existing connection.

Katica regular/bold at sizes 10 and 12 and existing TTF fonts are available.
`Base/etc/WindowServer.ini` chooses the UI/title/fixed-width fonts. Existing icon
resources include 16x16 and 32x32 sets, themed title buttons and cursors. Reuse
licensed repository assets; the reference's branded application icons and
wallpaper are not assumed to be distributable assets.

### Taskbar and application menu

`Userland/Services/Taskbar/main.cpp` discovers executable application entries
through `LibDesktop::AppFile` and builds the category menu. `TaskbarWindow.cpp`
handles the window list, workspaces, quick launch, applet area, clock and Super-key
events. Its header fixes bar height to 27 and icons to 16. QuickLaunchWidget has
real app/file launch entries; WindowList tracks actual windows.

Theme-only: existing palette and taskbar painter appearance. Code: density,
launcher grid/search, dock grouping/overflow, top-panel layout and work-area
reservation. Retain existing WM subscriptions and launch actions. A global
menubar would require additional application/menu integration and is deferred.

### Desktop and File Manager

The desktop is File Manager's desktop mode: `main.cpp`, `DesktopWidget.cpp` and
`DirectoryView.cpp`, not a separate new shell. It uses the real desktop directory
and file operations. Wallpaper and existing icon resources are configurable;
grid spacing, placement policy or special desktop objects require code.
A computer icon and recycling bin must not imply a device browser or recoverable
trash service unless those behaviors are separately verified/implemented.

File Manager has a toolbar, a path breadcrumb, a tree sidebar and statusbar in
`FileManagerWindow.gml`. `DirectoryView` provides history and icon/table/column
views. The existing sidebar defaults to 175 pixels. A favorites sidebar, new
toolbar arrangement, filtering and responsive density are code/GML changes.
Do not draw a search field claiming indexed or recursive search without a real
implementation. Preserve current path entry, shortcuts, progress/error dialogs,
copy/move/delete and asynchronous ZIP behavior.

### Settings and the reference control center

`Userland/Applications/Settings/main.cpp` is a 420x265 icon-view launcher for
applications whose metadata category is Settings; it preserves privileged launch
through Escalator. DisplaySettings already has background, appearance, fonts,
monitor, workspace and effects pages. `LibGUI/SettingsWindow.cpp` centralizes
Apply/Cancel/Defaults behavior and uses the shared design metrics.

A category sidebar can initially organize and launch those existing settings
applications. Embedding their pages is a later integration task, not a palette
change. Network and Audio applets exist, but the fresh QEMU audit reported no
usable audio channel and ran without a network device. The image's connected
Wi-Fi, Bluetooth, brightness, media transport, do-not-disturb, automatic date/time,
updates and unified settings search are not established capabilities of this
environment. No corresponding live controls are included in the initial scope.

## Recommendation

Start with one selectable, opaque Modern Dark theme using the existing theme
selection and preview. Change colors only, keep Default and geometry intact,
and select it only in a disposable QEMU session. It gives a large, observable
difference with the smallest build dependency and regression surface. Follow
with a matching light variant, then decoration and control sizing, before any
shell rearrangement. No theme file or visual code is created in this delivery.
