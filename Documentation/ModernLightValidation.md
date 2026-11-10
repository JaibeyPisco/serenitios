# Modern Light V2.1: implementation and validation

Date: 2026-11-10. Baseline: `99081e4dba`.
Working branch: `desktop/modern-light-theme`.

## Scope

`Base/res/themes/Modern Light.ini` adds the light counterpart specified in
`VisualDesignSystem.md`: white content/control surfaces, blue-gray panels,
dark primary/secondary text, blue selection, visible focus and contrasting
active/inactive title bars. The existing Light title-icon family is reused.
No image, font, control or application feature was introduced.

The theme explicitly defines all 100 colors and all existing metric, alignment,
window-provider, flag and path roles. There is no reliance on Default color
inheritance. All effective metrics match Default and Modern Dark. Title-button
positions, fonts, dimensions, window behavior, shadow paths and the Classic
provider remain unchanged. The preexisting disabled-title-effect and rubber-band
alpha values are preserved exactly; no translucent surfaces, blur or animations
are added. The existing Serenity terminal/ANSI scheme remains separate from the
light UI; a dark terminal background is intentional, not a missing-color area.

Default already uses related light surfaces. Modern Light deliberately differs
through its stronger structural strokes, darker focus accent, selected blue,
secondary text and more blue-tinted active title. It does not use geometry changes
to manufacture a larger visual difference.

The only production change is the new INI. Default, Modern Dark, WindowServer,
WindowManager, Taskbar, File Manager, Settings, Run, kernel and driver sources
are unchanged. The V1 installed-theme startup fix is retained and reused, not
modified. No production binary, full operating system, Lagom or toolchain was
rebuilt. Only the test-only guest verifier was compiled and linked using retained
target libraries and existing generated build commands.

## Tests and reproducibility

The V1 resource and guest tests were extended without changing their default
Modern Dark behavior. Resource checks now accept a theme argument:

```sh
python3 -B Meta/test-modern-dark-theme.py
python3 -B Meta/test-modern-dark-theme.py --theme "Modern Light"
```

Both passed: exact role coverage, color syntax, referenced resources, unchanged
metrics/shadows, and 36 text/focus/boundary contrast pairs per modern theme.
ANSI colors are preserved from the existing schemes; this does not certify every
terminal ANSI foreground/background combination.

The guest executable built from `Meta/test-modern-dark-theme.cpp` retains the
name `TestModernDark` for compatibility and adds the modes `files-light` and
`light`:

```sh
TestModernDark files
TestModernDark files-light
TestModernDark default
TestModernDark dark
TestModernDark light
TestModernDark idle light1
```

Run live modes only after selecting the corresponding theme in Appearance.
File modes validate every explicit color using the real theme loader and compare
all metrics with Default. Live modes assert the actual WindowServer theme name,
all 100 delivered colors, metrics and IsDark. Idle mode reads existing process
statistics during a ten-second sleep. These are test utilities, not new visible
operating-system controls.

## QEMU setup and executed results

Artifacts are retained under `Build/modern-light-v21-20261110/`.
The source was the validated V1 backing image. A new disposable copy received
only Modern Light and the updated test executable; the resource/executable were
extracted and SHA-256 checked. Read-only filesystem checks passed. QEMU used one
CPU, TCG, 1 GiB RAM, 1024x768, no network, and `snapshot=on`. Default remained the
initial selection; no configuration was changed to select Light automatically.

Executed checks:

- Appearance listed Default, Modern Dark and Modern Light. Two full cycles of
  Default → Dark → Light succeeded, with live-palette assertions at each of the
  six selections. Both modern file-loader checks and initial Default checks
  passed as well.
- Comparable captures of the same Terminal, Settings, File Manager and Display
  Settings arrangement were made for all three themes with identical resolution,
  wallpaper and window positions. Titles, title-control glyphs, menus, panels,
  fields, selected text, inactive windows and disabled actions were readable in
  reviewed surfaces. No missing-role black areas were observed; real loader and
  live checks provide stronger evidence than screenshots alone.
- Modern Light's existing checkbox, tooltip, hover, held-button and keyboard
  focus states were exercised. Keyboard location entry navigated File Manager
  to Documents; its title and directory contents changed accordingly. Minimize,
  restore, maximize, unmaximize and native title dragging worked without changing
  the implementation or metrics.
- Actual Run handled a missing command, SIGTERM and a successful retry under
  Light. The dialog/inline messages remained legible, the command remained
  editable, and the retry exited successfully.
- The existing FileOperation fixture passed its 12 helper cases and six GUI
  cases under Light: copy, move, delete, denied destination, invalid destination
  and partial copy. The actual production progress/error widget was used; files
  and continued GUI response were checked after each result. Errors were not
  duplicated or reported as success. This is fixture-driven production-widget
  coverage, not a claim to have replayed every native File Manager workflow.
- The existing ZIP fixtures passed the small compress/extract cycle with all
  names/content verified, and the invalid-ZIP error case. Both ended without
  remaining children or zombies. ZIP code was not modified or investigated.

The normal guest reboot test is recorded in `reboot-results.json`; the selected
theme is checked again using the live verifier after boot. Snapshot writes are
retained across that guest reset, not across quitting and discarding the VM.
This does not make the source/backing image's default selection Light.
The normal reboot passed: Modern Light remained selected, and the post-boot live
check matched its name, all 100 colors, metrics and IsDark. No verification
failure or coredump occurred during the run.

## Performance observations

One ten-second scheduled-process-time sample per theme was taken with the same
applications and five host screenshot captures. WindowServer's normalized share
of total scheduled time was 10.11% for Default, 8.68% for Modern Dark and 8.87%
for Modern Light. These are short TCG observations including capture and normal
clock/cursor/applet activity, not a benchmark or a performance-improvement claim.

For each theme, five samples of the same static File Manager region were
byte-identical. Native menu and window-drag traces completed without an observed
freeze. The Light menu click-plus-capture measurement was 0.759 seconds, including
the automation's deliberate delay and capture overhead; it is not a precise
input-latency measurement. There was no observed added sustained activity or
continuous visible change in static content. Individual compositor repaints were
not exhaustively instrumented, so zero idle repaints is not claimed.

## Evidence and limits

Primary records:

- `three-themes-list.png`; `{default,dark,light}-comparison-cycle-1.png` and
  matching Settings captures for comparable views.
- `light-run-missing.png`, `light-run-signal.png`, `light-file-denied.png`,
  `light-file-invalid.png`, `light-file-partial.png`, `light-zip-invalid-error.png`.
- Focus, checkbox, tooltip, pressed, keyboard-directory, minimized/restored,
  maximized/restored and moved-window captures.
- `light-resource-check.json`, `dark-resource-check.json`, `provenance.json`,
  `fixture-build.log`, `switch-results.json`, `performance-results.json`,
  `idle-percentages.json`, `regression-results.json`, `reboot-results.json`,
  serial log, QEMU command, final results and visual-review records.

The contrast calculations cover specified opaque UI combinations; they are not
a certification of every application's hardcoded colors or antialiased pixels.
Existing icon artwork and inherited shell/applet presentation remain. Scale 2,
multiple monitors, physical hardware, exhaustive repaint profiling and every
possible input/error path were not tested. No general system stability claim is
made. Generated binaries, disk images, logs and screenshots stay outside Git in
Build; no remote publication occurs. V2.2 is not started by this delivery.
