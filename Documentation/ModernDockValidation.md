# V7.2: centered application dock

Date: 2026-11-16. Branch: `desktop/modern-dock-v72`.
V7.1 checkpoint: `a226a325c6`. Identity prerequisite: `6faab556a6`.

## Behavior and scope

Modern-provider themes (including Modern Dark and Modern Light) replace the
lower classic strip with a centered 56-pixel dock, eight pixels above the
screen bottom. Its opaque palette surface has rounded corners and a one-pixel
stroke. Entries have 44-pixel targets, existing 32-pixel icons and four-pixel
gaps. There is no blur, magnification or animation timer.

The same Taskbar process retains V7.1's upper panel, V6 launcher, applets and
clock. Default keeps V7.1's classic lower Taskbar. The two presentations are
mutually exclusive. No third shell service or global application menu is added.

DockWidget reads the existing QuickLaunch entries and WindowList. Installed
applications use the conservative identity contract documented in
[ApplicationIdentityContract.md](ApplicationIdentityContract.md). Pinned direct
profiles combine with their actual windows; unknown windows remain separate.
Custom/ambiguous launch profiles are not silently associated with a different
installed profile. No process list or parallel window registry is introduced.

Clicking a pin without windows launches its real QuickLaunch entry, preserving
AppFile arguments and escalation. A single running window activates/restores,
or minimizes when already active and unblocked. Multiple windows expose an
individual selection menu. Context menus offer activation, minimize/restore,
the existing native WM window menu, new-window launch and unpin/reordering.
Drag/drop delegates to the existing QuickLaunch implementation. Running dots,
active backgrounds and progress strips derive from real WindowList state.

Entries exceeding the available width use previous/next page buttons with
tooltips and disabled endpoints; every page retains Show Desktop. Unknown
windows do not collapse into an unidentified group. Numbered Super shortcuts
retain the existing per-window ordering, independent of pinned order/pages.
Super, Super+D, workspace switching and native window operations are preserved.
Only windows on the current workspace contribute to that workspace's dock.

The dock is rebuilt after WM or pin changes, with pending refreshes coalesced
through the existing event loop. This avoids rebuilding for every notification
in a large window-creation burst. There is no polling. Window identifiers in
menu callbacks are checked against WindowList before sending WM actions.

## Geometry

The shared DesktopGeometry helper reserves 32 pixels above and either 64
below for Modern or the existing 27 below for Default. At 1024x768 the Modern
normal work area is `(0,32,1024,672)`; at 640x480 it is `(0,32,640,384)`.
Default retains `(0,32,1024,709)` and `(0,32,640,421)` respectively. These are
framed work areas, not application client-content dimensions.

WindowManager, GUI desktop constraints, Run positioning and the V6 launcher
share that reservation. Theme changes recalculate tiled/maximized windows.
Fullscreen still uses the whole screen and the existing compositor path hides
both Taskbar windows. Taskbar animation rectangles point to visible dock entries
or the dock as an overflow fallback. Off-workspace entries receive empty rects.

The desktop icon viewport keeps V7.1's existing margins and layout: dock width,
grouping and page changes never resize or recenter the desktop icon view. The
upper clock/calendar, applet popup and notification anchors remain V7.1's.

## Builds and executed checks

Targeted dependency-tracked Ninja builds of Taskbar, WindowServer and Run passed,
including required LibGUI and LibDesktop work. There was no kernel, EXT2/driver
implementation, toolchain upgrade or whole-system build. The installed binary
set is Taskbar, WindowServer, Run, libgui and libdesktop; deployment is offline
and coordinated with the new WM identity notification.

All guest tests use QEMU TCG with exactly one CPU and discarded guest writes.
The identity-only guest suite passed before the separate identity commit; see
its report for the exact scope and metadata fallback cases.

Executed dock checks and retained evidence under
`Build/modern-dock-v72-20261116/` include:

- Dark 1024x768: real pinned File Manager launch, single-window minimize and
  restore, a second File Manager instance, individual selection/activation of
  both windows, close/update, and an unpinned Calculator launched through V6.
  Browser and Text Editor pins were also launched. Native application content
  and slow Browser initialization were not treated as dock idle behavior.
- Real context menus, pin reordering, V6 filter/launch, Settings and Run opening,
  Show Desktop, Super+number and Ctrl+Alt workspace roundtrips. Run's known Shell
  limitation was not investigated or reclassified as passing execution.
- The guest geometry fixture exercised maximize, exact restoration, fullscreen,
  minimize and unminimize. Fullscreen screenshots show neither shell bar.
  Final binary checks at 1024x768 passed in Dark, Light and Default through
  explicit theme-API selection; their exact work-area results and screenshots
  are retained in `dock-release-1024/final-theme-results.json` and `final-*`.
- Light 640x480: launcher bounds, geometry, 18 unidentified windows in one
  process, three overflow pages and activation of an individual window outside
  the first page. Repeated with coalesced refreshes; window 11 was activated
  from the final page. Theme changes to Default and Dark passed geometry at
  640x480. Default displayed classic task buttons without a duplicate dock.
- Twelve FileOperation CLI cases and six GUI cases (copy, move, delete, denied,
  invalid and partial failure) passed with the existing fixtures verifying
  resulting files and responsive input. Async ZIP cycle and invalid-ZIP cases
  passed, including their no-remaining-children-or-zombies assertions. This does
  not certify every desktop process or rerun the older large close-while-ZIP suite.
- Clock/calendar, actual Audio applet popup and a real notification were
  captured. The Audio popup appeared below its upper applet at `(814,31,50,125)`.
  Physical audio/network operation is not certified by this no-NIC VM profile.
- After application startup settled, the dark dock changed zero pixel bytes
  over five seconds. This is image stability, not an internal paint-call count.
  A final isolated comparison against the identity-only classic baseline used
  26.2% versus 27.6% of one host CPU at idle. Four identical Super+1 toggles on
  Terminal took 3.01 versus 3.03 seconds, with whole-QEMU CPU samples of 80.1%
  versus 87.1%. Both static strips changed zero bytes. These short samples
  include the guest and updating applets; they are not Taskbar-only profiling
  or a statistically controlled performance benchmark.

`dock-dark/` retains the detailed input/regression serial trace and screenshots;
`dock-light-640/` retains the small-screen/theme/overflow traces, including the
pre-coalescing run. `dock-release-1024/` holds the final binary test image and
subsequent theme/geometry captures. Identity tests remain in `identity-dark-2/`
and `identity-default/`. Before/after at the same 1024x768 resolution and scene
are `identity-dark-2/identity-pass.png` and `dock-prototype/identity-pass.png`.
`dock-dark/dark-desktop.png` is the uncluttered running dock, not a mockup.

The dock-specific guest fixtures can be rebuilt with:

```
python3 Meta/build-modern-dock-tests.py --output Build/new-dock-fixtures
```

`TestModernDockGeometry` checks geometry/fullscreen; its `dark`, `light` and
`default` modes call the same existing theme API as Taskbar, without adding any
production theme interface. `notification` displays a real test notification.
`TestModernDockObserver` logs real WM state/rect/identity/workspace events;
`overflow` creates eighteen unknown normal windows. These are test-only binaries.
The identity image runner's optional `--desktop-fixtures`, `--width`, `--height`
and `--linger` support preparing copied disks for these interactive checks;
that mode records provenance, not an automatic blanket desktop pass.

Failed input attempts are retained: an early second click arrived during app
startup; one error-dialog Enter preceded the dialog; an overflow foreground
process prevented subsequent Shell commands until interrupted. Those attempts
were not counted as passes. An initial idle sample overlapped Browser startup;
only the later settled sample was accepted. The high window-burst cost in the
first dock prototype led to coalescing, then a repeated overflow check.
Rapid menu-driven theme attempts in the final reference guest were ambiguous
and were superseded by the explicit API fixture checks; the failed input trace
is kept in `dock-release-1024/serial-menu-input-attempts.log`.

## Remaining limits

Main screen and scale 1 only. Multi-screen placement, scale 2, exotic custom
themes/applet sets, every shortcut combination, physical audio and networking
are not certified. Existing oversized restored application windows/forms at
small resolutions were not redesigned; maximize uses the tested reserved area.
The fixed reservation remains if Taskbar is stopped, as in V7.1. No launch-token
protocol for argument-specific profiles or arbitrary process ancestry is added.
No V7.3/control center or remote publication is included.

## Recovery and release image

The new standalone `Build/desktop-modern/modern-v72.ext2` is converted read-only
from the retained V7.1 persistent overlay, carrying forward the user's cold
guest state. Only Taskbar, WindowServer, Run, libgui and libdesktop are replaced.
Installed files are extracted and hash-checked; read-only fsck runs before and
after installation. Kernel, V7.1 base and V7.1 overlay hashes are preserved in
`release-provenance.json`. No test executable, synthetic AppFile or fixture
autostart is installed in this release image.

The selected V7.2 pair is `modern-v72.ext2` / `desktop-v72.qcow2`. The previous
profile is retained in
`Build/modern-dock-v72-20261116/profile-recovery-v71.json`, and both V7.1 files
remain together. Roll back only with QEMU stopped: select `modern-v71.ext2` /
`desktop-v71.qcow2` in `Build/desktop-modern/profile.json`. Never replace the
backing image underneath an existing overlay. V1-V7.1 commits, older images and
their earlier evidence remain intact.

Run from Ubuntu/WSL with `./run-modern.sh`, or from PowerShell:

```
wsl -d Ubuntu-24.04 --cd /home/pisco/Documents/serenity ./run-modern.sh
```

`--test --headless` uses the normal runner with discarded writes and QMP. Normal
launch creates the new persistent V7.2 overlay on first use. The preserved guest
resolution is 1920x1080; the reference validation also exercises 1024x768 and
640x480. No user preference is changed to force the test resolution in release.

The real `./run-modern.sh --test --headless` profile was cold-booted after
selection. Its 1920x1080 desktop and File Manager pin launch were captured in
`release-run-modern-desktop.png` and `release-run-modern-file-manager.png`,
with the command, serial log and result record alongside them. The test guest
was closed and its writes discarded; the persistent V7.2 overlay remains for
the first normal user launch to create.
