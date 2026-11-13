# V7.1: top panel and desktop work area

Date: 2026-11-13. Branch: `desktop/modern-top-panel-v71`.
Parent: `745fa543d7` (V6). No dock, global application menu, control center,
kernel, EXT2 or driver implementation is included.

## Implemented behavior

The existing Taskbar process now owns two Taskbar-type windows. The upper
opaque 32-pixel panel contains SerenityOS and the V6 launcher entry at the left,
a flexible empty center, and the existing applet area plus one clock at the
right. The lower 27-pixel Taskbar retains Quick Launch, real window buttons and
Show Desktop. The identity, applet area and clock are not duplicated below.
WindowList, task-button activation, workspaces and existing launch metadata are
reused. Menus remain in application windows.

The clock prefixes its existing configured time format with the current date
on screens at least 800 pixels wide. On narrower screens it omits that prefix;
the calendar and ISO-date tooltip remain available. It reuses the existing
clock timer, not another polling loop. If the complete applet group cannot fit
beside identity/clock, that group is hidden as a unit and its server window is
moved outside the screen. It returns when space becomes available. This avoids
partially clipped applet controls. The tested default applet set fits at 640.

The applets are the actual existing programs and reflect their existing service
state; no Wi-Fi, Bluetooth, battery or connected-device tile was invented.
Audio's existing slider was anchored below the moved applet and constrained
horizontally. The test verifies its real popup, not physical sound playback.
The QEMU profile still has no NIC and the existing audio channel warning;
network connectivity and hardware audio were not certified or repaired.

## Geometry and integration

`LibGfx/DesktopGeometry.h` supplies the shared 32/27-pixel reservation. This
follows the existing fixed Taskbar reservation architecture; it does not add
struts, IPC messages, a service or global preferences. WindowManager applies it
to the main screen's normal work area. Fullscreen retains the complete screen.

Reference work areas, including window decoration space:

- 1024 x 768 screen: origin (0,32), size 1024 x 709, lower boundary y=741.
- 640 x 480 screen: origin (0,32), size 640 x 421, lower boundary y=453.

Coordinates use the existing exclusive right/bottom rectangle convention. The
bottom Taskbar retains its original position and height. Maximized normal
windows use this work area; the existing hidden outer frame border is cropped
at screen edges. A Modern window with an application menu has a maximized
client rectangle of (0,81,1024,660), not 709 pixels of client content.

Normal drag positions, bottom corner tiling comparisons and cascade wrapping
account for the nonzero work-area top. Fullscreen invalidation visits both
Taskbar windows. Saved-window/dialog desktop constraints use the same main-screen
work area. The desktop icon viewport reserves both strips without changing
files or adding special icons. Notifications start below the top panel.

V6's layout and discovery were preserved; only its screen anchor/height budget
changed so it opens below the identity and fits between the two panels. The
calendar also opens below the clock. No new window-control layout was added.

`LibGUI/Desktop.h` no longer imports the whole Taskbar implementation merely to
obtain a constant. Explicit MimeData includes repair the two consumers that
previously received that type indirectly. No public LibGUI object layout or
kernel/IPC ABI was changed. The updated shared LibGUI and affected executables
were installed together and extracted/hash-checked from the image.

## Executed validation

Used the existing cross toolchain and targeted Ninja builds of Taskbar,
WindowServer, NotificationServer, FileManager and Audio.Applet, with required
library compilation/link steps. No whole OS rebuild, installation of tools,
resource generator replacement or kernel build was needed.

QEMU tests used one CPU per guest, disposable writes, standard VGA and TCG.
The user's initially running normal V6 session was not controlled or closed by
the tests. An independent V6 baseline was used for before captures. The user
subsequently saved and shut down V6, permitting a cold disk conversion.

Checks actually exercised:

- Dark, Light and Default before/after captures at 1024 x 768; the upper bar,
  lower Taskbar, launcher and app windows retain their palette/control styles.
- V6 opening from identity and Super, Escape dismissal, name search and real
  application launch; Settings V5 opens and its Mouse query works. Run's window
  opens and can be cancelled. No claim of successful Shell command execution
  is made: the V6-documented Shell failure was not reopened in this milestone.
- A guest geometry fixture with a local application menu asserted maximize,
  exact restoration, real fullscreen, minimize and unminimize at 1024 x 768 in
  all three themes, and at 640 x 480. It also checks a nonzero-origin work-area
  input. Fullscreen client rectangles matched the complete screen and both
  panels disappeared visually.
- Real File Manager mouse controls were traced: maximize yielded client
  (0,81,1024,660) and frame (-2,30,1028,713), with the title controls visible
  below the top panel. Minimize, task-button restore, restore and close were
  exercised. The early apparent overlap was not accepted as a pass; later
  correctly targeted input and traced rectangles/captures established the
  real maximize result. Diagnostic logs were removed from production code.
- Ctrl+Alt workspace navigation, Super/launcher operation, Show Desktop and
  existing task-button interactions were exercised. The bars remain stationary
  while workspace window buttons follow their existing filtering.
- Calendar navigation/popup and a real notification below the panel were
  captured. The existing Audio applet popup was captured below its moved icon
  in the final installed image. No audio backend change was made.
- Twelve native FileOperation CLI cases passed. Six GUI cases passed: copy,
  move, delete, denied destination, invalid destination and partial failure.
  Fixtures verified resulting files/content, singular errors and responsive UI.
- Async ZIP cycle and invalid-ZIP scenarios passed. Fixtures verified names and
  contents, existing error behavior, continued event processing and no remaining
  children/zombies. The earlier 64 MiB close-while-running suite was not rerun.
- The 640 x 480 mode survived its resolution-confirmation rollback timeout;
  the launcher fit between the bars and search/launch remained accessible. The
  geometry fixture then asserted usable (0,32,640,421) and fullscreen 640 x 480.
  Existing large settings forms can exceed that screen; they were not redesigned.
- Final integration used the real `run-modern.sh --test --headless` profile.
  The user's preserved 1920 x 1080 preference also booted correctly, providing
  an additional large-screen check. Tests did not change that production default.

These are scoped functional/visual checks, not an overall OS stability or
instrumented compositor certification. Existing clock/graph applets update
periodically. Static panel layout introduces no timer, animation or continuous
paint request; interaction and idle observations are retained with the evidence. In final
integration, the static top region changed zero pixels over five seconds;
this measures image stability, not the number of internal paint calls.

### Failed prototypes and test-input corrections

The first prototype asserted while reading an unconstrained identity-button
width before assigning its fixed dimensions. Initialization now sets those
dimensions before layout. The next prototype attempted SetAppletAreaPosition
before registering the manager window; requests are now gated and ordered after
SetManagerWindow on the same existing connection. Neither assertion nor IPC
validation was removed; signal/pledge permissions were not broadened.

Some UI attempts had the Terminal minimized or changed focus after task-button
clicks; another resolution attempt automatically reverted before later inputs.
Those attempts were not counted as passes. An initial large-screen pixel
check also caught a workspace transition before its animation had finished;
the completed roundtrip was checked after the transition settled. Explicit focus, traced input and a
confirmed mode lasting beyond its timeout were used for the completed checks.
The final geometry fixture includes a menu and fills its test background;
earlier black test canvases were not missing product palette roles.

## Files and evidence

Product changes are limited to the shared geometry header, LibGUI desktop
constraints/includes, WindowManager, Taskbar/ClockWidget, V6 anchoring,
NotificationServer positioning, desktop viewport margins and Audio popup
anchoring. WindowList, QuickLaunchWidget, Settings, archive/file-operation code,
theme files, kernel and drivers were not redesigned or modified.

`Meta/test-top-panel-v71.cpp` and `Meta/build-top-panel-v71-test.py` provide the
guest-only geometry/notification fixture. To build into a new directory:

```
python3 Meta/build-top-panel-v71-test.py --output Build/new-v71-fixture
```

Install that fixture only in a disposable guest. No new diagnostic executable or
new application metadata is shipped in the production image.

Evidence remains under `Build/modern-top-panel-v71-20261113/`: build/fsck logs,
serial traces (including failed prototypes), image/binary provenance, input
recipes, regression JSON and lossless PNGs. Key files include:

- `evidence/dark-before.png`, `light-before.png`, `default-before.png` and
  their after counterparts at the reference resolution.
- `pointer-diagnostic.png`, `dark-maximized-final.png`,
  `dark-fullscreen-final.png`, `notification-below-panel.png`.
- `dark-640-confirmed.png`, `launcher-640-confirmed.png` and the serial
  `TOP PANEL GEOMETRY PASS` result for that confirmed mode.
- `release-desktop.png`, `release-launcher.png`, `release-calendar.png`,
  `release-audio-popup.png` and final integration captures.

Some scenes contain test commands, a test notification or the Display Settings
screen-number overlay. Final captures are real QEMU pixels, not mockups.

## Preserved image and launch

After the user shut down V6, `desktop-v6.qcow2` was verified unused and converted
read-only into a new standalone `modern-v71.ext2`. This carries forward the
latest guest files and configuration, including 1920 x 1080. Only six installed
files were replaced: Taskbar, WindowServer, FileManager, NotificationServer,
Audio.Applet and libgui.so.serenity. Installed hashes and read-only fsck results
were checked. The unchanged kernel is retained. No filesystem repair ran.

V6's base and persistent overlay remain together, unchanged; older recovery
images and all V1-V6 commits remain. The generated profile now selects:

```
{"base": "modern-v71.ext2", "overlay": "desktop-v71.qcow2"}
```

The existing runner needs no new flags or source changes. From Ubuntu/WSL:

```
./run-modern.sh
```

Or from PowerShell:

```
wsl -d Ubuntu-24.04 --cd /home/pisco/Documents/serenity ./run-modern.sh
```

Normal launch creates the new persistent overlay on first use. `--test` discards
its session writes. Rollback requires stopping QEMU and selecting
`modern-v6.ext2` / `desktop-v6.qcow2` in profile.json; never replace an existing
overlay's backing image. Subsequent writes in V6 and V7.1 remain independent.
The temporary test disks were retired after QEMU exited. Duplicate PPMs were
removed only after verifying pixel-identical lossless PNGs; logs, recipes and
PNGs remain. No images or large logs are committed, and no remote changes are
published.

## Remaining limits

One main screen, scale 1 and the supplied applet set were exercised. Multi-screen
fullscreen/work-area behavior, every application form, scale 2, physical audio,
network hardware, extreme custom clock formats/applet sets and all keyboard
shortcut combinations are not certified. The existing Shell limitation remains.
Fixed reservations also remain if Taskbar is stopped, as with the prior bottom
reservation; this is not a dynamic shell-strut implementation.

V7.1 stops here. No V7.2 dock work was started.
