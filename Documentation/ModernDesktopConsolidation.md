# V7.3: desktop consolidation

Date: 2026-11-17. Branch: `desktop/modern-desktop-v73`.
Parent: `5f3612c23c`, including identity prerequisite `6faab556a6` and V7.1
`a226a325c6`. This is a bounded consolidation of the existing shell, not a new
desktop design. The installer is a separate, subsequent task; V8 is not started.

## Corrections

Settings previously opened at 680x430 even on a 640x480 desktop. The actual
observed client rectangle was `(108,138,680,430)`: title controls were off the
right edge and content/status extended below the reserved work area. This was
reproduced on the V7.2 baseline, not introduced by V7.3.

Settings now checks its initial full frame through the existing theme geometry,
including its menu row. When the preferred frame is too large, its client size
and position fit the existing desktop work area, subject to the unchanged
420x300 content minimum. Normal preferred size/placement remain when they fit.
No Settings categories, launch behavior, forms or File Manager layout change.
In Modern 640x480, the measured Settings client is `(2,83,636,331)` and its full
frame exactly matches `(0,32,640,384)`.

A second baseline defect was found during ordinary application closure.
WindowServer crashed while painting a closing Calculator's menu bar; Taskbar
then faulted during shutdown after losing WindowServer. Repeated reproduction
without opening Settings established that the Settings adjustment was not the
trigger. Temporary userland module/backtrace diagnostics localized the primary
failure to `WindowFrame::paint_menubar`, reading a menu name after destruction.

WindowServer's Menubar stored non-owning `Menu&` entries even though a closing
window could still be composed after its client removed its menus. Menubar now
holds `NonnullRefPtr<Menu>` entries for as long as its frame uses them. This is
a local ownership correction; it adds no IPC, global registry, allocator change,
kernel/memory-management/EXT2/driver change or new rendering feature. All
temporary signal handlers and module logging were removed before final builds.

Applet positioning previously ran only in explicit updates, while subsequent
lazy layout could change the container's final geometry. Taskbar now sends the
existing applet-position request from the top widget's
completed layout callback. Size/visibility decisions remain in the existing
update path; screen, font and theme changes invalidate the cached position.
Unchanged positions do not generate additional requests. No timer, new IPC,
applet redesign or calendar behavior change is introduced.

## Preserved integration

V7.1's top panel remains the single location for identity/launcher, clock and
actual applets. V7.2's Modern dock continues to use QuickLaunch and WindowList,
with conservative application grouping and individual unknown windows. Default
retains the classic lower Taskbar. V6 categories, filtering and real shortcuts
remain unchanged. No duplicate shell controls, decorative backend-less elements,
new animation, control center or Favorites sidebar are added.

Work areas remain `(0,32,1024,672)` / `(0,32,640,384)` for Modern and
`(0,32,1024,709)` / `(0,32,640,421)` for Default, including decoration space.
Fullscreen retains the whole screen and hides both shell bars.

## Validation method and evidence

Evidence is retained under `Build/modern-desktop-v73-20261116/`. Guest tests use
QEMU TCG with exactly one CPU, copied images and discarded writes. The user's
initially active persistent V7.2 session was not controlled or closed by tests.
Only after that session had exited was its overlay read for a cold conversion.

The production build targets are Settings, WindowServer and Taskbar; Run
was included in the dependency-tracked build check. No whole-system or kernel
build was used. `Meta/build-modern-desktop-v73-tests.py` builds guest-only
theme/frame checks plus the existing geometry/observer fixtures.
`Meta/test-modern-desktop-v73.py` drives actual WM events, keyboard input,
Settings frame checks and application closure. It requires a prepared disposable
guest and records individual passing cases, not an inferred blanket pass.

The Settings probe reads the actual server-side window rectangle through the
existing API and verifies its full themed frame is inside the real work area.
The geometry probe asserts maximize, exact restore, fullscreen, minimize and
unminimize. Closure cases wait for real WM removal and reject process-crash
markers. Test binaries and synthetic AppFiles are never shipped in release.

The applet fixture creates a real Applet window and queries its actual
server-side rectangle while changing its width from 16 to 32, to the full screen
width, and back to 16 pixels. It checks the top-panel bounds, accessible horizontal
position and off-screen overflow handling. Calendar opening is exercised during
the probe; dismissal uses an outside click because Escape alone does not dismiss
the existing calendar popup. Screenshots complement these rectangle assertions.

A later screenshot with applets at `(0,0)` was traced to the test observer's
scheduled exit: the existing `WMConnectionFromClient` destructor resets the
shared applet position when any WM client disconnects. It coincided with the
observer's logged expiration, not a WindowServer/Taskbar crash. No fix to this
separate multi-WM teardown behavior is included. Accepted interaction captures
keep the observer alive; the integrated production boot contains no observer or
test service. This distinction prevents mislabeling harness interference as a
remaining theme/layout defect.

Important evidence includes:

- `baseline-640/settings-before-640.png` and `candidate/settings-after-640.png`:
  actual before/after Settings capture with the measured frame result.
- `candidate/serial-crash-calc-close.log`, `serial-diagnostic-first.log` and
  `serial-menu-lifetime-backtrace.log`: failed baseline closure and localization.
- `candidate/close-cycle-results.json`: twenty completed Calculator launch/close
  cycles after the menu ownership fix, where earlier runs failed within one or
  two cycles. The original WindowServer and Taskbar remained alive through these
  fixed cycles; no diagnostic handler was present in that passing binary.
- `cold-640/` and `cold-1024/`: separate cold reference sessions and per-case
  screenshots/results for Dark, Light and Default before the final applet fix.
- `final-640/` and `final-1024/`: final binaries, nine completed matrix cases per
  resolution (18 total): geometry transitions, Settings frame/filter/keyboard,
  and V6 keyboard launch plus confirmed application removal, in all three themes.
  `matrix-complete.json` and individual screenshots accompany each serial log.
- `final-640/applet-results.json`: all three themes and twelve actual applet
  rectangle assertions, including calendar and overflow/return to normal size.
- `final-1024/final-check-complete.json` and `final-check-results.json`: three
  further applet/theme cases (twelve rectangle assertions), all twelve existing
  FileOperation CLI checks, six GUI file cases (copy, move, delete, denied,
  invalid destination and controlled partial failure), and asynchronous ZIP
  round-trip/invalid-archive cases. File results/content are checked by the
  existing fixtures; ZIP confirms no remaining children or zombies. Error
  dialogs were captured and acknowledged, and the parent windows remained
  responsive. These file/ZIP regressions ran in Modern Dark at 1024x768.
- `final-1024/smoke-results.json` and the restarted `serial.log`: five accepted
  clean-boot interaction cases: real dock mouse minimize/restore, pinned File
  Manager launch/navigation to `/tmp`/maximize, workspace 0,1 and back to 0,0,
  Show Desktop toggle, and multiple Terminal windows. The original long-session
  matrix/file logs are preserved as `serial-validation.log`.
- `final-1024/run-result.json` and `final-run-launcher.png`: Run opened from the
  actual V6 footer and closed with Escape. The earlier shell-based Run attempt
  followed closure of its parent Terminal, was invalid as an input test and is
  not counted as a pass. Executing commands through Run was not re-certified.

Failed attempts remain separate from accepted results. The first image harness
capture collided with another QMP connection. A first closure test recorded a
premature close pass before waiting for removal; its subsequent server crash
invalidated that closure result, and the driver now waits for removal. An
in-session resolution-control fixture left Terminal input unusable after its
response; that control was removed and reference resolutions are tested through
separate cold boots. Those attempts are not counted as passing desktop checks.
Additional pointer-minimize attempts timed out and are not counted as passes.
The calendar remaining open after Escape was subsequently identified as a source
of input interception; the accepted calendar runs dismiss it with an outside
click. This milestone does not change its dismissal policy.

The later classic pointer test passed with a click in the visible part of its
button. Earlier center-target and long-session Modern pointer attempts timed
out; those attempts are not passes. The clean-boot Modern pointer test then
passed both minimize and restore. Geometry-probe transitions and recorded
keyboard transitions are separate checks. V7.2's broader dock functional
evidence remains historical evidence, not a claimed rerun of every case in V7.3.

## Known limits

One main screen and scale 1 are the validation target. Multi-screen, scale 2,
physical audio/networking, all privileged settings forms, every shortcut and
all service-failure recovery paths are not certified. Individual large settings
forms and oversized restored application windows are not redesigned by this
change. The fixed shell reservation remains if Taskbar is stopped.

Default's lower controls can be vertically clipped after changing from Modern:
the observed lower window retains a 56-pixel client height at the classic
27-pixel reservation. This is also visible in `cold-1024/1024-default-desktop.png`
with the V7.2 Taskbar, before this applet change. A mouse click in the visible
button area and keyboard activation still work. It is retained as a known
preexisting layout limitation under the final applet-only correction scope;
Default's visual compatibility is not certified as free of clipping.

Some Modern captures show the identity button temporarily unpainted, although
its mouse area and the Super launcher remain functional; a later interaction
repaints it. The same visual state is present in the pre-change V7.2 Taskbar
capture `cold-1024/1024-dark-desktop.png`. It is documented, not redesigned or
claimed fixed by this applet-positioning change.

Run's preexisting Shell execution limitation remains distinct from opening,
placing and closing Run. No old journaling, allocator or unrelated filesystem
failure was investigated or repaired. A secondary Taskbar shutdown fault was
seen after the original WindowServer crash; this milestone addresses the
reproduced primary menu-lifetime trigger rather than redesigning global failure
recovery. Short successful tests are not a certification of overall OS integrity.

## Release and recovery

The V7.3 base is `Build/desktop-modern/modern-v73.ext2`, with a separate
`desktop-v73.qcow2` overlay selected through the existing profile mechanism.
Settings, WindowServer and Taskbar are installed and compared byte-for-byte with
the final builds. Read-only `e2fsck -f -n` passes after installation. No test
binary, observer or test service is installed in this base.

The first candidate had become stale when the user subsequently saved a V7.2
session. It is retained as `modern-v73-pre-final.ext2`. The final candidate was
regenerated from the latest cold V7.2 overlay; `release-provenance.json` records
its source hashes, the unchanged pinned kernel and all installed binary hashes.
The old V7.2 base and overlay are retained together. V1 through V7.1 recovery
artifacts and the original direct-run image are not modified.

After closing QEMU, recovery selects the complete old pair by copying
`Build/desktop-modern/profile-v72-before-v73.json` to
`Build/desktop-modern/profile.json`. Do not replace a backing base under an
existing overlay. The normal command remains `./run-modern.sh`; from PowerShell:

```
wsl -d Ubuntu-24.04 --cd /home/pisco/Documents/serenity ./run-modern.sh
```

No remote push is performed. V8 is not started.

## Integrated boot result

The actual `./run-modern.sh --test --headless` command booted the selected V7.3
base at the preserved 1024x768 preference with one CPU. This was a discarded
session, not a claimed GTK/persistent-reboot test. The production V6 launcher
opened, the clock calendar opened and dismissed with an outside click, and
Display Settings applied Modern Light from Modern Dark. Both calendar captures
show the actual applets correctly placed beside the clock and the centered
dock. No observer/test service was present. The test guest was closed afterward.

Real final captures:

- [Integrated Dark/calendar](../Build/modern-desktop-v73-20261116/integrated/calendar-final.png)
- [Integrated Light/calendar](../Build/modern-desktop-v73-20261116/integrated/calendar-light-final.png)
- [Integrated Light desktop](../Build/modern-desktop-v73-20261116/integrated/desktop-light-final.png)
- [Final 640x480 Settings](../Build/modern-desktop-v73-20261116/final-640/640-dark-settings.png)
- [Settings before correction](../Build/modern-desktop-v73-20261116/baseline-640/settings-before-640.png)

`integrated/results.json`, its serial log/command, `promotion.json` and
`final-verification.json` retain the deployment evidence. Final serial logs for
both resolutions and the integrated boot contain no CPU-crash, verification,
assertion or kernel-panic marker. The V7.2 base/overlay and pinned kernel hashes
remain unchanged; the V7.3 base hash is unchanged after the discarded boot, and
the new overlay passes `qemu-img check`. This is finite regression evidence,
not a claim of exhaustive stability, leak detection or repaint/CPU profiling.

V7.3 is closed with the explicitly retained limitations above. V8 remains a
separate task and is not begun.
