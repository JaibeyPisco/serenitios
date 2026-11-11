# Modern controls V3: implementation and validation

Date: 2026-11-11. Baseline: `f53d9ae764` (V2.2).
Branch: `desktop/modern-controls-v3`.
Specifications: [design system](VisualDesignSystem.md),
[architecture audit](VisualModernizationAudit.md), and
[implementation plan](VisualModernizationPlan.md).
The validated V2.2 title controls supersede the older planning text about their
position. This milestone leaves those controls and the shell unchanged.

## Scope

Default already used SerenityStylePainter before this milestone. Replacing it
with ClassicStylePainter would change Default, so its existing branches were
retained. The new painting is selected only by the existing Modern window-theme
provider used by Modern Dark and Modern Light. No new preference or color role
was introduced.

| Component | Change | Scope and compatibility |
| --- | --- | --- |
| SerenityStylePainter | Consistent normal, hover, pressed/checked, focus and disabled button surfaces; distinct focus/default boundaries; clearer tab outlines and checked/disabled checkbox marks | Modern only; existing radii 5/3 and hit rectangles retained; Default branches unchanged |
| CheckBox | Solid two-pixel keyboard focus within the widget bounds | Modern only; labels, toggle callbacks and disabled semantics retained |
| TextEditor | Visible focus border for framed single-line inputs; full border invalidation on focus gain/loss | Normal two-pixel frame gets a two-pixel ring; one-pixel/no-frame cases respect their reserved frame area; multiline editors unchanged |
| TabWidget | Solid focus indicator clipped inside the active tab | Modern only; navigation, order, close handling and layout unchanged |
| DesignMetrics / SettingsWindow | Explicit 32-pixel standard metric, used only by the Settings footer; existing compact metric remains 28 | Existing footer spacing 8 and margins 12 retained; theme changes restore 28 for Classic |

Actual production files changed:

- `Userland/Libraries/LibGfx/DesignMetrics.h`
- `Userland/Libraries/LibGfx/SerenityStylePainter.cpp`
- `Userland/Libraries/LibGUI/CheckBox.cpp`
- `Userland/Libraries/LibGUI/TextEditor.cpp`
- `Userland/Libraries/LibGUI/TabWidget.cpp`
- `Userland/Libraries/LibGUI/SettingsWindow.cpp`

TextBox.cpp and Button.cpp were audited but did not need changes: single-line
input painting belongs to TextEditor, and Button already passes the required
states to the shared painter. This avoids changing TextBox overrides/vtables or
rebuilding application-specific subclasses. No public class layout, virtual
interface, IPC definition, GML, font, existing metric value or palette file was
changed. A private footer widget implementation handles density on theme changes;
no widget instances or action callbacks are replaced.

The Settings footer was the only production form area moved to 32 pixels.
Display Settings, Mouse Settings and Keyboard Settings were checked at 1024x768.
Their existing minimum-size/layout handling can grow a Settings window by four
pixels to preserve the tab content. Returning to Default restores the 28-pixel
footer without forcibly shrinking the user's window. No page or numerous forms
had to be rewritten. Existing toolbar dimensions, list/tree row heights, compact
controls, text alignment and fonts were retained; legacy controls were not all
resized to an advertised target.

No kernel, EXT2, driver, WindowServer, WindowManager, Taskbar, application-menu,
dock, File Manager, Run, file-operation or ZIP implementation was changed.
Modern Dark, Modern Light and Default INIs are unchanged. No new animation,
blur, transparency engine, task manager or periodic repaint timer was added.
Existing cursor blinking and desktop effects remain existing behavior.

## Build

The five affected existing translation units (SerenityStylePainter, CheckBox,
TextEditor, TabWidget and SettingsWindow) compiled with the retained x86_64 GCC
16.2 cross compiler. LibGfx and LibGUI linked successfully. No application ABI
changed, so the installed applications use the updated shared libraries.

The existing compile database and Ninja-generated link commands were used.
This is an incremental target build, not a clean/full-system build. Lagom remains
absent after the earlier cleanup; it was not needed because no generated input
or build graph changed. No host packages or toolchain were reinstalled. The
cached clang-format 20.1.8 binary was extracted locally for formatting/checks.
`git diff --check` and the formatter checks passed.

`Meta/test-modern-controls.cpp` is a guest-only rendering and interaction
fixture, separately compiled/linked against the existing target libraries.
It is not a new user-facing application. Its source remains in Git, while the
binary and host orchestration scripts remain with the test artifacts.

## Disposable QEMU validation

Artifacts: `Build/modern-controls-v3-20261111/`, with `baseline/` and `candidate/`.
Both images were copied from the retained V2.2 backing image. The baseline
received only the fixture; the candidate additionally received LibGfx and LibGUI.
Installed files were extracted and SHA-256 checked. Read-only filesystem checks
passed. QEMU used one CPU, TCG, 1 GiB, 1024x768, no network, and snapshot writes.
The source and prepared backing hashes remained unchanged after QEMU quit.
Disk images, screenshots, generated binaries and logs are not committed.

### Painter and Default preservation

`TestModernControls render-baseline` recorded the previous six-state button,
checkbox and top-tab render matrix. `TestModernControls render` checked the new
Modern colors, focus/disabled boundaries and rounded-corner containment.
The Default RGBA hash was identical in both images:
`8658526850182066648` (FNV-1a). Both Modern matrices changed as expected.
This is exact preservation for this matrix, not certification of every widget.
See `candidate/render-comparison.json`.

Before/after captures use the same fixture, fonts, window dimensions, resolution,
wallpaper, labels and themes:

- [Dark before](../Build/modern-controls-v3-20261111/baseline/dark-controls-before.png)
  / [Dark after](../Build/modern-controls-v3-20261111/candidate/dark-controls-after.png).
- [Light before](../Build/modern-controls-v3-20261111/baseline/light-controls-before.png)
  / [Light after](../Build/modern-controls-v3-20261111/candidate/light-controls-after.png).
- Default before/after and input-focus/Settings images are retained in the same
  directories. Use `*-settings-settled.png` for final themed form review;
  earlier transition captures can contain pending repaint states.

### Executed input and layout checks

All three themes passed the following fixture checks:

- Disabled button activation was blocked. Normal mouse activation, keyboard
  Tab/Space activation and activation after enabling the disabled action
  produced the expected counters. Disabling it again prevented activation.
- Hover and held-button pressed images were captured. Checkbox toggles enabled
  and disabled a real action. Disabled checked state and text stayed visible.
- Framed inputs showed focus, accepted editing and copied/pasted exact text.
  Initial labels/input/list items included Café, configuración and spaces.
- List selection worked with mouse and keyboard. Long rows retained the existing
  horizontal scrolling behavior rather than forcing larger row heights.
- Tab focus was reached with Shift+Tab; Left/Right and Ctrl+Tab in both directions
  switched real pages. Long tab labels remained bounded.
- At 320-pixel client width, single-line editing and list selection still worked;
  fields/buttons remained usable and long titles were elided. No font increase
  or per-application repair was needed.

`controls-results.json` and `tabs-narrow-results.json` retain input results.
`footer-density.json` records the measured footer borders in settled captures:
Default 28 pixels; Dark 32; Light 32. Default was selected after Modern to check
that density is reversible, not merely correct on initial construction.

Display Settings' six pages, Mouse Settings' three pages, Keyboard Settings'
input test and Cancel, Settings launcher, Terminal, and File Manager were used.
File Manager's editable location field and tree navigated Desktop/Documents;
its title, contents, selection and File menu were reviewed. The existing V2.2
`TestWindowControls geometry` regression also passed.
The test image has no configured Keyboard Settings keymap list; input works with
the existing map. Adding/removing/applying keymaps was not part of this check.

### Functional regressions

Under Modern Light, the retained production fixtures passed:

- Actual Run: missing command (127), deliberate SIGTERM (15), correction/retry
  and successful `exit 0`; both error presentations remained readable.
- FileOperation: 12 helper cases and six GUI cases (copy, move, delete, denied,
  invalid destination, partial failure). Files/results were verified; failure
  cases did not report success or duplicate the error, and UI remained responsive.
- ZIP: valid compress/extract cycle and invalid ZIP error, with filenames,
  contents and child cleanup checked.
- A 64 MiB ZIP remained interactive and movable. Its owner window was closed
  during compression; the resulting archive verified, with 447 live event-loop
  ticks and no remaining children or zombies.

See `regression-results.json`, `zip-close-result.json`, serial logs and error/
progress screenshots. No filesystem crash-consistency or SMP research was done.

### Idle observations and limitations

With Terminal and Appearance left open, ten-second process-statistics samples
reported WindowServer at about 9.25% of scheduled time for the baseline and
6.52% for the candidate. Three consecutive title-area pixel samples were stable.
No added busy loop or visible interaction stall was observed. These samples do
not establish a speedup or count compositor repaints; existing clocks/graphs and
cursor updates are expected. See `idle-comparison.json` and `light-idle-*.png`.

Early host-input attempts used a Classic close coordinate incorrectly or sent
arrows without putting the tab bar in keyboard focus. They were corrected and
rerun; those attempts were not counted as passing checks. Theme screenshots were
reviewed after pending repaint settled. Logs/captures are retained.

Validation is limited to the tested one-CPU scale-1 configuration and forms.
Scale 2, vertical tab arrangements, all applications and every state combination
are not certified. Lists/trees, multiline editing, radios and toolbar geometry
were deliberately not redesigned. Unusually dense or custom SettingsWindow
pages may need individual compact-density review; no broad form migration is
claimed. The bounded incremental build does not replace restoring Lagom before
future generated-resource work. No remote publishing or V4 work is included.
