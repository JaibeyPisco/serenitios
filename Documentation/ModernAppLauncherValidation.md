# V6: categorized application launcher

Date: 2026-11-13. Branch: `desktop/modern-app-launcher-v6`.
Parent: `b63e8e8d02` (Settings V5). Scope: the existing Taskbar launcher only.
No V7, kernel, EXT2, driver, shared GUI API or application backend changes.

## Behavior and implementation

The Serenity button and Super open a 640 x 480 maximum popup above the existing
Taskbar. Its search field, real category list and scrollable icon grid replace
the cascading menu as the primary navigation. Existing palette roles, fonts,
32-pixel app icons and IconView presentation are reused; no blur, animation,
new artwork or service was added. Default keeps its existing control painter.

Discovery still runs through `Desktop::AppFile::for_each`, respects
ExcludeFromSystemMenu and executable access, and retains the existing sorted
app/category inventory. The launcher model references that same vector; it does
not register or rediscover another set of applications. The supplied production
image discovers 56 entries in eight categories. Category names remove the menu
mnemonic ampersands for display; filtering retains the actual metadata values.

Search matches app names in the selected category, ignores surrounding spaces
and ASCII case, and refreshes immediately. All applications searches the full
inventory. Changing category clears the query; reopening starts on All with an
empty query. No results displays a message and Enter does nothing.

- Super and the Serenity button toggle the panel; Escape dismisses it.
- Ctrl+F focuses/selects the search text. Down enters the first result, arrows
  navigate the existing views and Enter opens the selected app. Tab moves
  through search, categories, results and the bottom actions.
- Double-click opens a grid item. Outside click dismisses the popup through the
  existing WindowServer close-request behavior.
- Settings, Files and Run open the real executables with the user's home as
  working directory. The retained System menu exposes the existing themes,
  About, Help, application submenus, Run and Exit actions. This is a compatibility
  fallback over the same inventory, not another registry. Exit continues to use
  ShutdownDialog and its existing shutdown/reboot commands.
- App activation uses `AppFile::spawn_with_escalation`, preserving the existing
  launch mechanism, including working directory, terminal and privilege handling.
  QuickLaunchWidget, app metadata and LibDesktop were reviewed and left intact.

An executable removed after discovery originally failed only in the spawn
child's diagnostic output. The launcher now checks executable access immediately
before activation and reports app name, path and errno. Errors returned by
AppFile are also displayed. The panel is hidden before activation, and error
dialogs have no hidden-popup parent. Failed preflight does not spawn a child or
produce a second notification. This does not eliminate the race between access
and exec, nor detect every later process failure; doing so requires changing the
existing process-launch contract and is outside this milestone.

The popup fits the screen containing the Taskbar button, reduces its sidebar on
narrow screens, and keeps scrolling inside its content. A real 640 x 480 guest
screen retained search, categories, results, count and all four bottom actions.
There is no fullscreen launchpad, global file search, recommendation feed,
recent-document database, top panel, dock or new task manager.

## Executed checks

Build: `ninja -C Build/x86_64 -j3 Taskbar`, with the existing cross toolchain.
Only affected Taskbar translation units and required link steps were built; no
full OS rebuild or installation was performed. C++ formatting, Python syntax
and `git diff --check` were checked before consolidation.

QEMU: one CPU, TCG, unchanged pinned kernel, 1024 x 768, scale 1, disposable
writes. Baseline captures used the V5 production image. Candidate testing used
an isolated image, and final integration used `run-modern.sh --test --headless`.
No personal files or preserved base/overlay contents were edited by GUI tests.

Executed:

- Before/after captures in Modern Dark, Modern Light and Default, with the same
  wallpaper, resolution and Terminal background scene. The final Dark/Light/
  Default captures show 56 production entries. Existing circular versus Classic
  title controls remain supplied by the selected theme.
- Opening/closing via Serenity, Super and Escape; ten repeated Super/Escape
  cycles; outside-click dismissal; search focus and results via keyboard;
  categories by keyboard and mouse; Utilities' real app subset.
- Mixed-case query with surrounding spaces, empty results, no-result Enter,
  clearing/reopening and category changes. Filtering never claimed a nonexistent
  executable was installed. A deliberately unavailable metadata entry was
  excluded at discovery.
- Calculator and Terminal opened from results; Settings V5, Files and Run opened
  from the bottom actions. Files opened the real home directory. Settings V5's
  existing interface was retained. Partition Editor reached the existing
  Escalator authentication dialog; cancellation/rejection used that dialog.
- A test-only executable was removed after discovery. Activation produced one
  readable error containing its name, path and `No such file or directory`.
  After dismissal, the panel reopened and real apps remained launchable.
- The guest model fixture passed discovered entries, Calculator identity,
  scoped/case-insensitive filtering, whitespace reset and invalid index bounds.
  It ran with 57 entries while the executable fixture existed, and with 56
  after removal. Test metadata/binaries are absent from the production image.
- Final integration also exercised mouse double-click on Calculator, Taskbar
  window minimize/restore, the existing clock/calendar popup, empty-result Enter
  and Settings V5 through the actual run-modern profile.
- A real 640 x 480 display mode was applied through Display Settings. The panel
  stayed within the usable area and search remained functional. The screen-ID
  overlay visible in that capture belongs to Display Settings.
- The existing Exit dialog was shown and cancelled. Reboot was then actually
  executed, returned to the desktop and reopened the launcher. Power off was
  actually executed afterwards and QEMU exited normally. These are functional
  action tests, not filesystem recovery or crash-safety certification.
- A guest fixture sampled Taskbar statistics across ten guest seconds including
  popup opening: 711 syscalls and 4,202 socket-write bytes in the final interval.
  Existing clock work and initial paint are included. No new timer or polling
  loop was introduced. Normal interaction remained responsive; these samples
  are not a steady-state compositor benchmark or proof of zero repaints.

### Existing Shell failure, explicitly not a pass

Running `exit 0` through Run caused Shell to terminate with signal 11 in
`Core::ThreadEventQueue::process`/`Vector<QueuedEvent>::clear`, reading
`0x2020202020202028`. The same command was reproduced through the original menu
on the untouched V5 image, with the same address and backtrace. `/bin/true`
through Run also encountered the Shell failure in the candidate. Run displayed
its existing signal error and permitted editing; Taskbar continued responding.
No Shell/kernel fix was attempted. Successful Run command completion is **not**
certified by V6. This preexisting failure remains a limitation for development.

Early diagnostic attempts included a missing first typed character and an
Escape that did not cancel Escalator. Those input attempts were not counted as
passes; explicit focus/Cancel and a later completed fixture run were used.
The first removed-executable test exposed the missing diagnostic and was rerun
after the local preflight correction. Prototype logs are retained.

## Artifacts and reproduction

Local evidence is retained in `Build/modern-launcher-v6-20261113/`: build and
serial logs, install recipes, read-only fsck results, image/binary hashes, model
and timing output, and `evidence/*.png`. Important pairs are `dark-before.png`
and `dark-after.png`, plus the Light and Default equivalents. Other captures
include `small-launcher.png`, `missing-executable-error-final.png`,
`settings-v5-opened.png`, `final-files-home.png`, `reboot-desktop.png`,
`power-off-selected.png` and the baseline/candidate Shell error evidence.

Build the diagnostic guest fixture into a new directory with:

```
python3 Meta/build-taskbar-launcher-v6-test.py --output Build/new-v6-fixture
```

It only builds; install it in a disposable guest to run. It expects the supplied
Calculator metadata and samples Taskbar without changing process state. It is
not installed in the production image.

Start the updated desktop from Ubuntu/WSL:

```
./run-modern.sh
```

Or from PowerShell:

```
wsl -d Ubuntu-24.04 --cd /home/pisco/Documents/serenity ./run-modern.sh
```

Click Serenity or press Super. Try Calculator, Utilities, Settings, Files and
System -> Exit. Switch themes through System -> Themes or Display Settings.
`--test` discards guest writes on exit; normal runs use a persistent overlay.

V6 selects `Build/desktop-modern/modern-v6.ext2` and `desktop-v6.qcow2` via the
existing generated profile. The new overlay is created on first normal launch.
V4's base/overlay, V5's base, the unchanged kernel and all prior commits remain.
No V5 persistent overlay existed at preparation/publication. Only `/bin/Taskbar`
was updated in the new production base; installed Settings V5 was hash-checked.
No test metadata, binary or credential was included. The disposable raw test
copy was retired after QEMU stopped; logs, recipes and final PNGs remain.
Duplicate PPMs were removed only after verifying pixel-identical lossless PNGs.
Production image checks
used read-only `e2fsck -fn`; original images were not repaired or overwritten.

Rollback: stop QEMU, then set the profile to `modern-v5.ext2` /
`desktop-v5.qcow2` (or V4's original pair). Never replace an overlay's backing
image. Subsequent guest changes in different profiles are independent.

## Limits and stopping point

The list is discovered at Taskbar startup; newly installed metadata needs a
Taskbar restart/login. Search does not implement Unicode case folding or search
file contents. Very long names use the existing IconView wrapping; selected
items expose their full name/description through the status label and tooltip.
Multiple screens, scale 2, every application backend, all Quick Launch shortcuts
and general OS stability were not exhaustively tested. The existing Shell
failure and launch-time access/exec race remain as described above.

V6 ends here. No V7 top bar, dock or unrelated subsystem work was started.
