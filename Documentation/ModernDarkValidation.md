# Modern Dark V1: implementation and validation

Date: 2026-11-10. Baseline: `75c24f6da5` (visual planning), including
`237d48a6fd` (ZIP), `209b1845620` (file-operation errors), and `5727103edca` (Run).
Working branch: `desktop/modern-dark-theme`.

## Implemented scope

`Base/res/themes/Modern Dark.ini` adds a selectable opaque navy UI theme using
the existing Appearance page, palette roles and Classic window-theme provider.
It explicitly defines all 100 color roles, every metric, alignment, flag and
path exposed by `LibGfx/SystemTheme.h`. No color inheritance from Default is
assumed. Menus, fields, panels, controls, title states, tooltips, overlays,
disabled text and syntax colors are covered.

All effective metrics match Default, including the loader's default inactive
title-button alpha of 255. Fonts, sizes, window-button order, layout, wallpaper,
the Default INI and default-theme selection are unchanged. Existing readable
Gruvbox-Dark title icons are reused; no bitmap or font is replaced. Shadow paths
remain as in Default. Zero-alpha disabled title effects and the existing
rubber-band selection alpha are preserved exactly from Default; no translucent
window/panel, blur or animation is added.

The palette follows `VisualDesignSystem.md` with one documented correction:
`Accent=#3f8cf5`, while FocusOutline and TextCursor retain `#63a8ff`. The current
painter uses Accent as the checked-checkbox fill with SelectionText as its mark.
The original light accent and white mark did not meet the 3:1 target. The chosen
accent gives approximately 3.34:1 for the white mark and 3.07:1 against the hover
surface, without a painter change. Selected rows retain the specified
`#1d4ed8` background and white text. Existing Base16 Dark ANSI colors are copied
explicitly for the corresponding roles and referenced as the existing terminal
scheme; terminal ANSI redesign is outside V1.

## Confirmed startup blocker and minimal correction

The baseline `WindowServer/main.cpp` migrated every theme name except Default
back to Default. The first disposable VM reproduced this after selecting Modern
Dark in Appearance and running the normal guest reboot command. Its serial log
contained `Migrating retired theme 'Modern Dark' to Serenity`, and the desktop
returned to Default.

The application-only correction keeps installed theme files and falls back to
Default only for a non-Default preference whose theme file is missing. It uses
the existing FileSystem API/dependency and leaves Default selection unchanged.
No frame painter, IPC API, kernel, driver, filesystem or service architecture
was changed. This justified deviation from the resource-only plan is necessary
to retain a user's actual theme selection across restart.

Only WindowServer's changed main translation unit was recompiled and the target
was relinked with retained dependencies. The separate guest verification fixture
was also compiled and linked. No full-system build, Lagom rebuild, toolchain
installation or package installation was performed. Logs are retained under
`Build/modern-dark-v1-final-20261110/component-build.log`.

## Reproducible checks

Resource checks:

```sh
python3 -B Meta/test-modern-dark-theme.py
```

This verifies exact role coverage against the actual enum macros, valid colors,
unchanged effective metrics, existing resource paths, title-icon availability,
preserved shadow/alpha behavior, and 36 specified text/focus/boundary contrast
pairs. It does not certify every possible application color combination.

`Meta/test-modern-dark-theme.cpp`, built as the test-only guest executable
`TestModernDark`, exercises the real loader and live WindowServer palette:

```sh
TestModernDark files
TestModernDark default
# Select/apply Modern Dark through Appearance first:
TestModernDark dark
TestModernDark idle dark1
```

The file mode verifies every declared color against `load_system_theme` and
compares all metrics with loaded Default. Live modes verify the actual selected
theme, every delivered color, IsDark and all metrics, not just a screenshot.
Legacy images can omit the preference while using Default; the fixture permits
that only for Default, while still comparing all palette colors. Idle mode reads
the existing process statistics before/after a ten-second sleep; it neither
modifies the compositor nor creates a synthetic operating-system control.

## QEMU provenance and executed results

The final run used x86_64 QEMU TCG, one CPU, 1 GiB RAM, 1024x768, no network,
and a new disposable root image with `snapshot=on`. The installed theme,
WindowServer, test fixture and existing FileOperation fixture were extracted and
SHA-256 checked. Default was extracted and matched the untouched source file.
The kernel binary remained
`7093075d2a15e2079cc85509bd498683b3f453ca876b2915940cfc19e8d189ba`.

Executed on the final candidate:

- Initial Default boot and the real loader/live Default palette checks passed.
- Appearance enumerated Modern Dark, previewed it and applied it. The live
  palette check verified all 100 colors and unchanged metrics.
- Returning to Default worked. Three additional Default/Dark cycles observed
  all six expected panel-color transitions without failure.
- Terminal, File Manager, Settings and Display Settings remained usable. Before
  and after screenshots use the same resolution, wallpaper and application
  window positions. Active/inactive title colors and minimize/maximize/close
  glyphs were visible; no missing-color black areas were observed in reviewed
  surfaces. Dark content surfaces and Terminal's intentional ANSI background
  are not fallback-color failures.
- Reviewed real normal, selected, focused, disabled, checked, hover and held
  button states, the existing tooltip, and active/inactive windows. File Manager
  keyboard location entry navigated to Documents; its title and contents changed
  accordingly. Minimize/restore, maximize/restore and title dragging worked.
- In both Default and Modern Dark, actual Run handled a missing command, SIGTERM
  and successful retry. Error/inline messages remained legible and input was
  editable; failure did not report success.
- The existing FileOperation fixture exercised the actual production progress/
  error widget in both themes with a permission error, then verified files and
  continued GUI response. Its 12 helper cases also passed; successful GUI copy,
  move and delete passed under Modern Dark. This is fixture-driven production
  widget coverage, not a claim that every native File Manager interaction was
  replayed.
- The existing small ZIP cycle passed under Modern Dark: compression/extraction,
  names/content verification and no remaining children or zombies. No ZIP
  implementation was changed or new filesystem investigation started.

The normal guest reboot test and its live-palette assertion are recorded in
`reboot-results.json` in the final artifact directory. It retains the disposable
VM's snapshot writes across the guest reset; it does not modify the original or
backing image, nor claim that discarded writes survive quitting QEMU.
Modern Dark remained selected after that normal reboot, and all 100 live colors
and metrics matched. No verification failure or coredump occurred in the final
candidate run.

## Performance observations

Two ten-second samples per theme were collected using existing WindowServer and
Taskbar process statistics. WindowServer's share of total scheduled time was:

| Sampling condition | Default | Modern Dark |
| --- | --- | --- |
| Ordinary idle desktop/apps | 4.03% | 3.79% |
| Idle with five host screenshot captures | 9.29% | 8.77% |

These are short, normalized process-time samples in one TCG VM, not a general
benchmark or a claim of a performance improvement. Capture overhead and normal
clock, cursor and applet updates remain present.

For each theme, five captured samples of the same static File Manager region
were byte-identical. Native menu and drag traces completed with no observed
freeze. The click-plus-menu-capture measurements were 0.839 seconds for Default
and 0.828 seconds for Dark, including the automation's deliberate click delay
and screenshot overhead; they are not precise input-latency measurements.
The six repeated theme-switch detections completed in 1.84–2.72 seconds, also
including capture overhead, with comparable Default/Dark timing and no stale
final palette. There was no observed increase in sustained idle activity or
continuous visible updates to static content. Individual compositor repaint
events were not exhaustively instrumented, so this is bounded observational
evidence rather than proof of zero idle repaints.

## Captures and records

Final artifacts are in `Build/modern-dark-v1-final-20261110/`:

- `provenance.json`, installation/extraction logs, command and serial log.
- `default-comparison.png` / `dark-comparison.png` for the same desktop/window
  arrangement, and `default-settings.png` / `dark-settings.png`.
- `default-appearance.png` / `modern-dark-applied.png`, plus theme-list and
  repeated-switch captures.
- `default-run-missing.png` / `dark-run-missing.png`, both signal/retry captures,
  and `default-file-error.png` / `dark-file-error.png`.
- Checked, hover/tooltip, held-button, focused-location, keyboard Documents,
  minimized/restored, maximized/restored and moved-window captures.
- `regression-results.json`, `switch-results.json`, `interaction-performance.json`,
  idle captures, `reboot-results.json`, final results and visual-review records.

The first run, before the startup fix, remains in
`Build/modern-dark-v1-20261110/`. It also retains an initial fixture link failure
(missing explicit LibIPC in the test link, subsequently fixed) and an overly
strict initial preference-name assertion in the test fixture. The latter caused
a test-only coredump on a legacy Default preference, not a theme-application
crash. Failed attempts are not counted as passing final-candidate tests.

## Remaining limits

V1 does not redesign Taskbar, File Manager, Settings or window decoration.
Existing multicolor app icons may not visually match a future redesigned icon
family. Taskbar/applets retain their inherited presentation; V1 does not claim
complete visual uniformity across all shell elements. Hardcoded application
colors and terminal ANSI combinations outside the
tested surfaces are not comprehensively certified. Scale 2, multiple screens,
other hardware and exhaustive compositor profiling were not tested. The missing
theme fallback remains in place; a separate runtime test of an arbitrary missing
or malformed third-party theme was not performed.

No Modern Light, dock, top panel, new control-center feature or next visual
milestone is implemented. Screenshots, disk images, logs and generated binaries
remain outside the commit under Build. No changes are published remotely.
