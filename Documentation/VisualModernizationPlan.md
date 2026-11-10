# Visual modernization implementation plan

Date: 2026-11-10. Status: planning only; no milestone below is implemented.
Baseline: `237d48a6fd`; branch: `desktop/visual-modernization-plan`.
Read the [audit](VisualModernizationAudit.md) and
[design specification](VisualDesignSystem.md) before implementing a milestone.

## Delivery and validation rules

Implement one independently reviewable milestone per working branch/commit.
Preserve the stability checkpoint and Run/file-operation/ZIP improvements.
Do not change the kernel, EXT2, SMP, drivers, remote repositories, or file-operation
semantics. Pause for selection of the first milestone after this planning delivery.
If a shell milestone needs broad architectural work, stop at its design review
and prefer the existing Taskbar arrangement with updated styling.

For every implemented milestone:

1. Check Git, capture the baseline and record exactly which sources/resources
   change. Reuse the current cross toolchain. Build only affected targets and
   their necessary dependencies; rebuild dependent apps if public layouts/ABI
   change. Do not claim a direct link is a clean full build.
2. Install candidate resources/binaries in a new offline disposable image and
   extract/hash-check the installed files. Never update the retained base image.
   Run QEMU with one CPU and discarded writes. In-session settings persistence
   and reboot persistence require a disposable writable test overlay: a snapshot
   discarded at shutdown cannot prove persistence. Do not reopen filesystem
   crash-consistency research as part of these checks.
3. Capture before/after screenshots at 1024x768 scale 1 with the same windows and
   wallpaper. Check scale 2 before claiming scaling support. Record QEMU command,
   serial log, hashes and visual review under Build; keep generated artifacts out
   of Git. A screenshot alone does not establish correct file contents or input.
4. Test mouse and keyboard focus, shortcuts, activation, moving/resizing,
   minimize/maximize/restore/close, menus, modal dialogs and error paths relevant
   to the change. Check long and accented labels and narrow windows.
5. Recheck the three functional improvements when shared graphics/shell changes
   affect them: Run normal/error/signal/retry; copy/move/delete and truthful
   failure reporting; ZIP responsiveness, integrity and close-while-running.
   Do not mark unexecuted acceptance checks as passed.
6. Compare idle redraw/CPU use and the same interaction trace with baseline;
   reject continuous idle redraw or visible interaction stalls. No blur or new
   rendering subsystem is required. Commit locally only after the milestone's
   required validations pass; stop before the next milestone.

## Conditional prerequisite: restore host tools

Needed before GML/IPC/CMake regeneration or a normal dependency-tracked rebuild,
not before V1. The available default G++ 13 rejects C++26 mode; use the installed
GCC/G++ 14 pair for the host build. The syntax probe passed, but the full host
tool build is unverified. Do not substitute the Serenity cross compiler for a
compiler that must produce Linux host executables.

The following is a proposed recovery sequence, not an executed result:

```sh
# Run at the repository root. Use a new superbuild directory.
Toolchain/Local/cmake/bin/cmake -S Meta/CMake/Superbuild \
    -B Build/visual-superbuild -G Ninja \
    -DSERENITY_ARCH=x86_64 -DSERENITY_TOOLCHAIN=GNU \
    -DCMAKE_C_COMPILER=/usr/bin/gcc-14 \
    -DCMAKE_CXX_COMPILER=/usr/bin/g++-14 \
    -DBUILD_LAGOM=OFF -DENABLE_JAKT=OFF \
    -DENABLE_LAGOM_LADYBIRD=OFF -DENABLE_LAGOM_LIBWEB=OFF
Toolchain/Local/cmake/bin/cmake --build Build/visual-superbuild \
    --target lagom-install --parallel 4
```

The repository superbuild installs host tools to `Build/lagom-install`, including
their shared-library dependencies and exported CMake files; it uses
`Build/lagom` for host compilation. Verify these paths are still absent or contain
the intended recoverable build before running. Inspect emitted commands and do
not run the default superbuild target, which can build the whole operating
system. A smaller selected-tool build may omit targets referenced by the export;
do not hand-write a fake LagomConfig or point CMake at an incomplete prefix.

After installation, verify exported files/tools and their Linux runtime
dependencies, then configure only the Serenity target build against that prefix
while retaining all existing target options/toolchain. Use the superbuild's
`serenity-configure` step or an equivalent explicit reconfigure, review changes
to the generated graph, and dry-run the selected userland targets before building
them. Confirm the graph has no unexpected full-system or kernel rebuild.

Some code generators and cached Unicode/locale/resource data may still require
work even with BUILD_LAGOM off; this avoids optional host applications, not all
generator dependencies. If GCC 14 or a specific input fails, retain the exact
failure and identify the smallest missing requirement. Do not upgrade the host,
install packages speculatively, or patch unrelated libraries to force progress.
V1 remains available while a generated-input milestone is gated.

## V1 — Selectable Modern Dark theme: recommended first change

- **Impact / complexity / risk:** high visible impact; low complexity; low to
  medium visual regression risk across applications.
- **Files:** proposed `Base/res/themes/Modern Dark.ini`; existing theme loader
  `LibGfx/SystemTheme.cpp` and `DisplaySettings/ThemesSettingsWidget.cpp` remain
  unchanged. Reuse an existing readable title-icon family. No new graphics code,
  source list, GML, IPC definition, or bitmap asset is required.
- **Change:** define the complete dark palette from the design specification in
  an additional selectable theme. Keep opaque fills, current font, title/button
  dimensions and shadow behavior. Keep Default intact and do not change the
  system/user default automatically. Validate every relevant palette role; do
  not assume missing keys inherit Default. Reuse an existing terminal scheme
  through its path without promising a terminal redesign.
- **Dependencies:** installed theme enumeration/preview/application, existing
  font and icon resources, a disposable image. Lagom is not needed if resource
  deployment is performed without regenerating build inputs. Confirm that the
  resource installer includes the new INI; if not, install it explicitly into
  the test image and document that instead of changing CMake unnecessarily.
- **Expected appearance:** navy window/menu/content surfaces, clear text, blue
  focus and selection, visually distinct active/inactive titles. Existing window
  layout and command behavior stay intact.
- **Risks:** black fallback colors from missing roles, dark title icons on dark
  backgrounds, application hardcoded colors, syntax/tooltip contrast, user color
  scheme overrides hiding the intended palette, minimum-size growth after theme
  switching. Check overrides rather than declaring a theme load failure.
- **Acceptance:** preview and apply the theme using existing Appearance controls;
  revert to Default; no unreadable fields or missing title glyphs in Terminal,
  File Manager, Settings, Run and representative error/progress dialogs. Test
  normal, hover, selected, focused, disabled and inactive states. No new crash or
  change to existing operation results. Persistence checked with a disposable
  writable overlay if included in the milestone's claim.
- **Verification:** side-by-side before/after captures and screenshot review of
  the application menu, two overlapping active/inactive windows, file selection,
  an editable Run error and a file-operation error. Open/close and switch themes
  repeatedly. Check keyboard access and the applicable regression smoke checks.
- **Build scope:** resource-only; no full userland or kernel build. Stop after
  the independently validated theme commit.

## V2 — Matching light theme and window-decoration refinement

- **Impact / complexity / risk:** high / medium / medium. Split the resource-only
  light theme and any later geometry/painter change into separate commits.
- **Files:** proposed `Base/res/themes/Modern Light.ini`; optional
  `LibGfx/ClassicWindowTheme.cpp`, `SerenityStylePainter.cpp`, `WindowTheme.h`,
  and `WindowServer/WindowFrame.cpp` only when geometry/input requires them.
- **Change:** light variant using the same semantics; then title height 32 and
  button targets 28x28, consistent border/corner rendering and active-state
  distinction. Keep the existing right-side control order and actual actions.
- **Dependencies:** V1 evidence; complete palettes; targeted LibGfx/WindowServer
  builds; restore Lagom if the normal graph needs regeneration. No new IPC API.
- **Risks:** title elision, off-by-one frame geometry, dead resize corners,
  alpha clipping, stale previews or enlarged minimum sizes after theme switches.
- **Expected / acceptance:** consistent light/dark frames with readable titles;
  paint and hit rectangles agree; maximized/fullscreen windows have no stray
  border. All resize edges/corners, title dragging, double-click maximize,
  modified markers and minimize/restore/close work in both themes.
- **Tests:** active/inactive/modal/tool/notification windows, narrow/long-title
  cases, scale 1 and 2, repeated theme/size changes and comparative drag traces.
  No global menubar or left-side decorative traffic-light controls.

## V3 — Shared control density and resource consistency

- **Impact / complexity / risk:** high / medium / medium to high because LibGUI
  and LibGfx serve many applications. Migrate selected forms before any global
  font or size change.
- **Files:** `LibGfx/DesignMetrics.h`, `SerenityStylePainter.cpp`, affected
  `LibGUI/Button.cpp`, `CheckBox.cpp`, `TextBox.cpp`, `TabWidget.cpp` as needed,
  `SettingsWindow.cpp`, and only the GML forms actually migrated. Existing icon
  directories and font resources are reused first.
- **Change:** apply the specified control states and 32-pixel standard density
  in selected forms, retain 28-pixel compact density, and remove conflicting
  local padding. Do not replace functioning checkboxes with unbacked switches.
- **Dependencies:** V1/V2 palette decisions; host GML tools for changed forms;
  rebuild affected libraries and dependent binaries when needed.
- **Risks:** clipped fixed-height fields, focus/default-state ambiguity, tiny
  dialogs becoming too large, bitmap-icon scaling and hardcoded app colors.
- **Expected / acceptance:** consistent button/input/tab spacing and visible
  keyboard focus; no clipped text, changing hit target on hover, or loss of
  shortcuts. All state combinations in the design specification remain usable.
- **Tests:** representative settings forms, Run, file dialogs, MessageBox,
  lists/trees, disabled and checked controls at both scales; run the three prior
  functional suites for shared-library changes, not only screenshot comparisons.

## V4 — Desktop and File Manager layout

- **Impact / complexity / risk:** high / medium / medium. Desktop density and
  File Manager layout should be independently reviewable changes.
- **Files:** `FileManager/main.cpp`, `DesktopWidget.cpp`, `DirectoryView.cpp`,
  `FileManagerWindow.gml`; `LibGUI/IconView` only if shared behavior truly needs
  changing; existing wallpaper/icons under `Base/res`.
- **Change:** clearer icon-cell spacing and labels; simplify toolbar grouping,
  keep editable breadcrumbs, a resizable real directory sidebar, and current
  icon/list/column modes. A favorites section can be a follow-up with links to
  verified directories. Do not move/delete the user's desktop contents, create
  phantom folders, or imply recoverable trash semantics.
- **Dependencies:** V3 metrics and working GML generation; existing models,
  launch actions, FileOperation and ArchiveOperations remain the data/behavior
  providers. No filesystem or helper-protocol redesign.
- **Risks:** shared desktop/windowed view assumptions, hidden actions, long-name
  truncation, drag/drop and clipboard regressions, losing ZIP progress ownership.
- **Expected / acceptance:** readable 32-pixel icons, stable labels, a less
  crowded toolbar and clear location/selection. All paths and modes remain
  accessible. No search field without an implemented, accurately labeled query.
- **Tests:** 1024x768 and narrow windows; names with spaces/UTF-8, history, editable
  paths, selection, launch, drag/drop, clipboard and file-operation/ZIP suites.
  Check desktop and regular windows separately in light and dark themes.

## V5 — Settings category launcher

- **Impact / complexity / risk:** medium to high / medium / medium.
- **Files:** `Applications/Settings/main.cpp`, existing `.af` application
  metadata in `Base/res/apps`, and an optional local layout GML. Keep individual
  settings applications and `LibGUI/SettingsWindow` behavior unchanged first.
- **Change:** categorize real installed settings apps and optionally filter their
  names; show descriptions and route activation to the existing executable.
  Preserve root-required metadata and Escalator. A category view is not an
  embedded settings page and must be presented as launching a real app.
- **Dependencies:** V3 styles, complete app model, host generators if GML changes.
  Embedded panels and a shared settings state model are separate decisions.
- **Risks:** hidden metadata entries, dropped privileged launch behavior, broken
  tab arguments, unsupported sections falsely implying configurable features.
- **Expected / acceptance:** a readable category navigator containing only real
  settings destinations. Display/appearance, input, users, clock and network
  destinations open the correct apps; existing Apply/Cancel/Defaults still work.
- **Tests:** every listed destination, installed/missing app handling, keyboard
  selection/filter reset, privileged flow and unsaved-settings close behavior.
  Preserve the previous launcher as a recoverable baseline.

## V6 — Application launcher before shell separation

- **Impact / complexity / risk:** high / medium / medium to high.
- **Files:** `Services/Taskbar/main.cpp`, `TaskbarWindow.cpp`, `QuickLaunchWidget`,
  `LibDesktop/AppFile` consumers and a local launcher widget if needed.
- **Change:** organize installed executable apps into a readable grid/list with
  application-name filtering. Retain categories, real quick-launch entries, Run,
  Settings, power actions and keyboard dismissal. No recent-document database,
  recommendation service or indexed filesystem search.
- **Dependencies:** V3 controls; the existing application discovery/launch model;
  Lagom for new sources/layouts. Keep the launcher on the existing Taskbar first.
- **Risks:** stale app lists, bad popup focus/anchors, accidental duplicate
  launches, filter results hiding essential system actions.
- **Expected / acceptance:** a clearer launcher inspired by the reference, with
  every displayed item backed by a real action. Super toggles it, Escape closes
  it, arrows/Enter operate it, and missing executables produce truthful errors.
- **Tests:** executable discovery, empty filter result behavior, duplicate names,
  long labels, outside click, repeated open/close, multi-window applications and
  shutdown/restart through the existing mechanisms on disposable images.

## V7 — Top status panel and centered dock

- **Impact / complexity / risk:** very high / high / high. Requires a bounded
  userland geometry review before implementation; not a theme milestone.
- **Files:** `Services/Taskbar/TaskbarWindow.*`, `QuickLaunchWidget.*`,
  `TaskbarButton.*`, `WindowList.*`, `ClockWidget.*`,
  `WindowServer/WindowManager.cpp`, and applet placement consumers. Consider two
  windows in the existing Taskbar process before adding another service.
- **Change:** separate available status/clock/launcher controls from app launch
  and running-window entries. Replace the hardcoded bottom-only work-area
  assumption consistently for the new layout. Keep per-application menus in
  their windows. Do not introduce a global task manager or kernel interfaces.
- **Dependencies:** V6 launcher and existing WM events; explicit work-area,
  popup, applet and fullscreen geometry contract; real application identity and
  window grouping policy. Confirm existing protocols are sufficient; if not,
  stop and propose a smaller styled-Taskbar alternative before extending IPC.
- **Risks:** maximized windows behind panels, popup misplacement, applets on the
  wrong screen, inaccessible windows, privilege/lifecycle changes and excessive
  repainting. A centered floating rectangle alone is not a working dock.
- **Expected / acceptance:** 32-pixel top panel, 56-pixel dock with overflow and
  32-pixel icons, correct 672-pixel work-area height at the baseline resolution.
  Running indicators reflect actual windows; minimize/restore/multi-window
  activation, Super+digit, workspaces and show-desktop remain functional.
- **Tests:** maximize/restore/snap/fullscreen, scale changes, screen reconfiguration,
  popup anchors, overflowing app lists, multiple windows per app, dock click and
  keyboard activation, restart/exit cleanup, idle CPU and comparative drag trace.
  Do not claim multi-screen support based on a single-screen test.

## V8 — Optional control-center popup with verified actions

- **Impact / complexity / risk:** medium / medium / medium, conditional on actual
  backing services. This is not required to complete V1–V7.
- **Files:** existing Taskbar/applet consumers, DisplaySettings launch actions,
  `Applets/Audio` and its existing LibAudio connection only if usable.
- **Change:** group verified theme/display/settings and clock actions; expose
  volume/mute only after the real Audio service/device succeeds in the test VM.
  Reuse current APIs and state notifications. Omit unsupported tiles entirely.
- **Dependencies:** V7 placement/focus contract and separately demonstrated
  service/device behavior. No Wi-Fi/Bluetooth/brightness/media backend work is
  authorized by a visual milestone.
- **Risks:** decorative toggles with no state, stale values, unusable devices,
  focus loss and duplicated applet ownership.
- **Expected / acceptance:** a compact themed popup whose displayed actions and
  values are real. Theme changes use the current mechanism; unavailable hardware
  never appears connected or adjustable. Every visible control has a tested
  effect or opens the documented existing application.
- **Tests:** service unavailable/available states where testable, opening and
  dismissing via keyboard/mouse, external state updates, one source of truth for
  applets, and absence of periodic repaints while idle.

## Selection requested

Choose V1 first: one additional selectable dark theme, resource-only and fully
reversible through the existing Appearance page. Its first acceptance checkpoint
is an actual QEMU before/after comparison plus input/error-dialog smoke checks.
This planning delivery provides specifications, not passing results for future
visual changes. Do not begin V1 or another milestone until it is selected.
