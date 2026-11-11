# Modern window controls V2.2

Implementation: 2026-11-10; final validation: 2026-11-11.
Baseline: `e69f8e12120d5b1ac9c333e6378f7fd570e22e06` (Modern Light V2.1).
Branch: `desktop/modern-window-controls`.

## Scope and compatibility

Modern Dark and Modern Light select `WindowTheme=Modern` through the existing
window-theme provider mechanism. Default continues to select Classic through its
existing loader fallback. No global preference service was added. ThemeEditor’s
existing provider selector also represents Modern, so loading a modern theme
cannot silently display a different provider.

Modern uses a red close, yellow minimize and green maximize/restore control on
the left. The 14-pixel circles retain the existing 24x22-pixel rectangular hit
areas and 28-pixel normal title height. Controls start eight pixels inside the
title bar. Text follows the occupied control group with an eight-pixel gap and
right elision; an empty text rectangle is allowed on extremely narrow windows.
Rounded frame geometry, resizing contours, fonts, metrics and application
content layouts are retained. Hover and pressed glyphs identify the actions;
inactive colors are muted. The modified-document indicator remains on Close.
Only controls actually supported by a window are shown.

The implementation reuses ClassicWindowTheme for the bounded Modern layout and
painting branch. The existing native WindowServer Button instances, callbacks,
mouse tracking, modality, minimize/maximize/restore logic and window manager
shortcuts are retained. Only physical button placement changes: close, minimize,
maximize. Their existing logical/event order is unchanged. The title painter
uses the occupied group extent to avoid overlapping text. LibGUI’s Appearance
and ThemeEditor previews share the circular-control painter with WindowServer.

Modern omits the application icon in the title bar. The existing right-click
title-bar window menu remains available. Classic retains the application icon
and its menu, traditional right-side buttons and original layout/paint branches.
No second control group is drawn. Notifications retain their existing layout.
Full-screen and frameless windows retain their decoration bypass.

The provider is appended without changing old enum ordinals or the shared
SystemTheme structure (2756 bytes on the tested target). No new color, metric,
IPC field, virtual method, preference service, source target or CMake dependency
was introduced. No kernel, driver, EXT2, WindowManager, Taskbar, File Manager,
Settings or Run implementation was changed. No blur, animation, asset replacement
or additional translucent surface was introduced.

## Modified files

- `Base/res/themes/Modern Dark.ini`, `Modern Light.ini`: provider selection only.
- `LibGfx/SystemTheme.h`, `Palette.cpp`: recognize Modern and reuse the provider.
- `LibGfx/ClassicWindowTheme.h`, `.cpp`: left layout, title clipping and shared
  circular-control painting.
- `WindowServer/Button.h`, `.cpp`, `WindowFrame.cpp`: native button styles,
  physical placement and occupied title-control extent.
- `LibGUI/AbstractThemePreview.cpp`: matching preview controls.
- `ThemeEditor/MainWidget.cpp`: one entry in the existing provider model.
- `Meta/test-modern-dark-theme.py`: expected provider for modern resources.
- `Meta/test-modern-window-controls.cpp`: guest-only geometry and interaction
  fixture, not an installed application feature or production hook.
- This validation report.

Library/service paths above are under `Userland/Libraries`,
`Userland/Services`, or `Userland/Applications` as appropriate.

## Build and resource checks

Eight translation units were compiled with the existing x86_64 cross compiler:
LibGfx SystemTheme, ClassicWindowTheme and Palette; LibGUI AbstractThemePreview
and Variant; WindowServer Button and WindowFrame; ThemeEditor MainWidget.
LibGfx, LibGUI, WindowServer and ThemeEditor linked successfully. Variant was
refreshed because its inline formatter enumerates providers. The test fixture
was separately compiled and linked against the target libraries.

The retained compile database and Ninja-generated link commands were used
without regenerating CMake. Lagom build/install directories remain absent after
the earlier cleanup. This is an incremental build validation, not a clean build
certification; no toolchain or package was reinstalled to restore Lagom.
C++ changes were formatted with clang-format 20 during implementation.

Both commands passed after the provider change:

```sh
python3 -B Meta/test-modern-dark-theme.py --theme "Modern Dark"
python3 -B Meta/test-modern-dark-theme.py --theme "Modern Light"
```

They check explicit loader-role coverage, syntax, resources, unchanged geometry
and contrast pairs. `git diff --check` passed.

The guest command `TestWindowControls geometry` passed 64 Modern layouts across
both themes, four widths (94, 108, 160, 640), zero through three controls and
normal/render-above modes. It checks containment, nonoverlap, nonnegative title
space, rectangular hit dimensions, transparent pixels outside circles and the
three exact active colors. It also asserts the previous provider ordinals,
SystemTheme size and Default’s original right-side golden layout.

## QEMU and executed interaction checks

Artifacts are retained under `Build/modern-window-controls-v22-20261110/`.
The copied V2.1 image received the new libraries, WindowServer, ThemeEditor,
modern theme resources and test fixture. Installed files were extracted and
SHA-256 checked. Read-only image checks passed. QEMU used one CPU, TCG, 1 GiB,
1024x768, no network, and a disposable `snapshot=on` drive. The original image,
Default resource and kernel were preserved. `command.json`, `provenance.json`
and `preserved-history.json` record configuration, hashes and Git ancestry.
After QEMU quit, the source and prepared backing image hashes were unchanged;
all final installed binaries/resources were extracted again and matched their
recorded SHA-256 values (`disk-unchanged.json`, `final-installed-check.json`).

Executed with Modern Dark and Modern Light:

- Green maximizes and restores to the recorded original rectangle. Double-click
  on the title bar maximizes/restores. Yellow hides the window, and selecting
  its Taskbar entry restores it. Red sends the normal close request.
- Title dragging changes the native client rectangle. Right-border resizing
  was verified under Dark; bottom-right resizing was verified under Light
  (500x260 to 550x310). Rounded transparent corner pixels are outside the hit
  region; the visible border inside the corner remains resizable.
- A 108-pixel-wide window retains all three controls and elides its long title
  without overlapping them. Returning to normal width restores usable content.
- Active/inactive title bars and controls are distinguishable. Hover/held mouse
  states and cancellation by moving outside a held control were exercised.
  Clicking the close hit area outside the visible circle also closes correctly.
- Native modal dialogs have their own Close control. Clicking parent controls
  while blocked does not close, maximize or minimize the parent. Closing the
  dialog restores normal parent interaction. Dark used its red control; Light
  used keyboard Enter. Light’s Tab/Space navigation activated the narrow-window
  button and returned to the original size.
- F11 enters actual full screen, removes title bar/controls and Taskbar, then
  restores normal decoration and placement. No duplicate control group appears.
- The native title-bar context menu was opened under Dark and dismissed with
  Escape. Default separately passed right-side close, minimize/Taskbar restore,
  maximize/restore, title double-click, dragging and its application-icon menu.

Native application compatibility and regression checks under Light passed:

- Terminal, Settings and Appearance opened and were usable. File Manager
  navigated to Documents via its keyboard location field and back to home;
  its title/content and File menu were visually checked.
- Run: missing command (127), deliberate SIGTERM (15), editable retry and a
  successful `exit 0`. Error messages remained legible.
- FileOperation: 12 helper cases; six graphical cases (copy, move, delete,
  permission denied, invalid destination and controlled partial failure).
  Error cases emitted one failure and no success, and the fixture verified
  responsiveness and resulting files after dismissal.
- ZIP: valid compress/extract cycle and invalid archive error. Contents/names
  were checked by the existing fixture; both cases reported no remaining
  children or zombies.

These are compatibility regressions; no ZIP or file-operation implementation
was reopened or modified.

## Comparable captures and idle observations

The same 1024x768 wallpaper, Terminal, File Manager and Display Settings layout
was captured under Default, Dark and Light. V2.1 captures are copied byte-for-byte
as the pre-change reference, not presented as newly executed V2.2 tests:

- [Dark before](../Build/modern-window-controls-v22-20261110/dark-comparison-before-v21.png)
  and [Dark after](../Build/modern-window-controls-v22-20261110/dark-comparison-after.png).
- [Light before](../Build/modern-window-controls-v22-20261110/light-comparison-before-v21.png)
  and [Light after](../Build/modern-window-controls-v22-20261110/light-comparison-after.png).
- [Default after](../Build/modern-window-controls-v22-20261110/default-comparison-after.png)
  retains Classic. Separate captures cover narrow windows, modals, full screen,
  hover/pressed states, Settings, File Manager navigation and error dialogs.

A further Default → Dark → Light selection cycle passed live checks of all 100
colors and metrics. Three consecutive idle title-area pixel samples per theme
were identical. Existing process-statistics samples over ten-second intervals
reported WindowServer shares of approximately 10.9% of scheduled time for Default,
9.5% for Dark and 9.7% for Light in this TCG run. These observations showed no
new busy loop or visible interaction stall; they are not compositor repaint
instrumentation or a general performance benchmark. Taskbar graphs and clocks
continue their existing updates.

ThemeEditor loaded Modern Light with the correct Modern provider, switched its
preview to Classic and back, and displayed the expected controls in each case.
The unsaved changes were discarded; no theme was overwritten. A normal guest
reboot then retained Modern Light; its live palette and native Modern close
control passed again after startup. See `reboot-result.json` and
`light-controls-after-reboot.png`.

## Evidence and limitations

`dark-interactions.json`, `light-interactions.json`, `classic-interactions.json`,
`regression-results.json`, serial logs and individual PNG/PPM captures retain
executed results. Logs from earlier sessions are archived as
`serial.log.previous-*` / `debug.log.previous-*` rather than overwritten.

The initial fixture iterations had incorrect exclusive rectangle-edge
expectations and incorrectly queried a non-checkable fullscreen action. Those
fixture-only assertions were corrected and rerun successfully; their crash
records were retained. Some host input checks also required corrected hit
coordinates or window activation. Those attempts were not accepted as passes.
The interrupted earlier QEMU session was restarted and the remaining Light and
Classic checks were executed again. No production architectural fix resulted
from these harness corrections.

This validates the tested one-CPU x86_64 configuration and applications, not all
apps, themes, accessibility tools, screen scales or multi-monitor arrangements.
Render-above geometry is asserted offscreen; its complete UI workflow was not
separately exercised. Modern has no separate global layout toggle: selecting
Default gives Classic, and the existing ThemeEditor provider field permits
custom themes. Notifications and unrelated existing theme providers were not
redesigned. The previous stability checkpoint and functional commits remain
ancestors. No remote publishing is part of this change.
