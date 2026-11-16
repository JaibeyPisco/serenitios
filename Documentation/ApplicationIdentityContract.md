# WM application identity for V7.2

The identity change is independent of the dock. Classic Taskbar still creates,
activates, minimizes and restores one button per real window. Default retains
its existing presentation. No kernel, filesystem implementation, driver or
application launch semantics change.

## Existing model and missing connection

WindowServer owns windows through GUI client connections. Their client IDs are
connection IDs, not process IDs. WM state notifications carry client/window IDs,
workspace, title, geometry, progress and active/minimized/blocked state. Existing
activation and window-menu requests already address individual windows.

Installed `/res/apps/*.af` files identify launch profiles by their full filename
and specify an executable, optional arguments, terminal/escalation behavior and
icons. QuickLaunchWidget persists those paths (or executable/document paths),
then launches through AppFile, Core::Process or Desktop::Launcher. It does not
receive the window connection created by a launch. AddToQuickLaunch's PID is
only an explicit user request; it cannot identify all existing or future windows.
The current WM interfaces cannot map an arbitrary client ID to its process.

WindowServer can already read the authenticated socket peer PID and the
kernel's process statistics. The same facilities are used for window title
usernames. This requires no extra pledge, unveil, process registry or kernel
interface. Taskbar cannot obtain the socket peer credentials itself.

## Minimal extension

One asynchronous `window_application_identity` notification is appended to the
existing WM client endpoint. Existing message ordinals and signatures remain
unchanged. It carries manager/client/window IDs and the connection owner's
kernel-reported full executable path. It follows the corresponding window state
notification, including the initial snapshot when a manager registers.

WindowServer reads the peer PID and executable once when the GUI connection is
accepted, while its owner is alive. The result belongs to that connection and
is destroyed with it. There is no deferred PID lookup, PID-reuse cache, parent
walking or inference from client-provided titles/icons. Failure produces an
empty identity and never rejects a window. This is shell grouping information,
not an authentication or authorization credential.

Taskbar matches the full executable path against all installed valid AppFiles,
including profiles hidden from the launcher. A unique direct profile without
arguments or RunInTerminal yields its full `.af` filename as the group key.
Different executable paths with equal basenames never match. Multiple matching
profiles, unknown executables, terminal wrappers and argument-specific profiles
remain unidentified. Auxiliary processes use their own executable; they never
inherit a parent's group. Symlink/path aliases are conservatively left unmatched.

The key is stored on the existing WindowList window. Its application iterator
returns all matching real windows; an empty key returns only that particular
client/window pair. Activation still uses each window's original identifiers,
so minimized windows, multiple windows and workspace filtering retain their
existing mechanisms. This commit does not replace classic buttons with groups.

## Compatibility and limits

Updated WindowServer and LibGUI must be deployed together: an old LibGUI does
not understand the appended notification. No public GUI Window object layout or
old WM event layout changes. Taskbar's private WindowList layout changes only
inside Taskbar. There is no supported mixed-version deployment claim.

This deliberately does not identify launch profiles sharing one executable or
attribute arbitrary child processes to a parent application. Supporting those
would require an explicit launch token/application registration contract. The
installed AppFile catalogue is read at Taskbar startup, consistent with the
existing launcher; restart Taskbar after installing/removing launch profiles.

## Guest validation

Build the independent guest probe with:

```
python3 Meta/build-application-identity-test.py --output Build/identity-fixture
```

Run it in a copied V7.1 disk with discarded guest writes:

```
PYTHONDONTWRITEBYTECODE=1 python3 Meta/run-application-identity-test.py \
  --source Build/desktop-modern/modern-v71.ext2 \
  --fixture Build/identity-fixture/TestApplicationIdentity \
  --output Build/identity-test --theme 'Modern Dark'
```

The probe uses real WM notifications and the production identity resolver and
WindowList iterator. It checks a group with two windows in one process, a group
spanning two processes, a different installed application, an unknown auxiliary
executable with an identical window title, minimized restoration, activation of
each window, and closing/recreating a window. It also rejects missing paths,
equal basenames in another directory, ambiguous metadata, arguments and terminal
profiles. The host drives classic Super+2 activation/minimization/restoration
and asserts the resulting real WM states.

The runner preserves config ownership, verifies extracted installed hashes,
performs read-only fsck, records the unchanged source-image hash, QEMU command,
serial logs and real screenshots. QEMU uses TCG and exactly one CPU. Fixture
binaries, synthetic metadata and autostart entries exist only in the disposable
test disk, never in the retained desktop image.

Executed on 2026-11-16 from base `a226a325c6`: targeted Ninja builds of Taskbar,
WindowServer and the required LibGUI/LibDesktop dependencies passed. The real
guest contract probe passed at 1024x768 in Modern Dark and Default. Default also
passed the host-driven classic Super+2 trace, including a minimized notification
followed by active/unminimized notifications for the same client/window pair.
Screenshots were visually inspected in both themes. Evidence is retained under
`Build/modern-dock-v72-20261116/identity-dark-2/` and `identity-default/`, with
build logs in the parent directory. The retained V7.1 image SHA-256 remained
`c61644cbb204e369468458d43f625b23a6ba25489bc2b78f5de5da98d5b5893e`.

The first disposable-image prototype replaced a WindowServer configuration
without preserving its owner and failed before desktop startup. The installer
now preserves existing uid/gid; that failed attempt is retained in
`identity-dark/` and is not counted as a passing run. No production image or
configuration was modified. Dock behavior, Light, small resolutions and the
broader desktop regression suite are not certified by this identity-only test.
