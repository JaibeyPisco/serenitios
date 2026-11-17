# Run the Modern desktop and retained cleanup results

Date: 2026-11-12. Validated userland source: V4 commit `f596ed59f2`.
The original V4 validation is retained below; the V5 profile update is documented separately. No kernel or EXT2 code changes are included.

## V5 profile update (2026-11-13)

The launcher now reads the optional `Build/desktop-modern/profile.json`.
The selected V5 pair is `modern-v5.ext2` / `desktop-v5.qcow2`; without a profile,
the original `modern.ext2` / `desktop.qcow2` pair is used. Both V4 files remain
unchanged. See [V5 validation and rollback](ModernSettingsValidation.md).
The cleanup measurements below remain the historical 2026-11-12 results.

## V6 launcher update (2026-11-13)

The active profile now selects `modern-v6.ext2` / `desktop-v6.qcow2`.
V5 and V4 remain preserved. Serenity/Super opens the categorized searchable
application panel; System retains themes and power actions. See
[V6 validation, screenshots and limitations](ModernAppLauncherValidation.md),
including the reproduced preexisting Shell failure through Run.

## V7.1 top-panel update (2026-11-13)

The active profile selects `modern-v71.ext2` / `desktop-v71.qcow2`. It includes
V6's latest cold guest state after the user saved and shut down, plus the upper
32-pixel panel. The lower Taskbar remains; there is one clock/applet area, above.
V6's base/overlay are preserved for recovery. See
[V7.1 validation and limitations](ModernTopPanelValidation.md).
The user's 1920 x 1080 preference is preserved; reference tests also exercised
1024 x 768 and 640 x 480. No dock or global application menu was added.

## Start the updated desktop

V7.2 (2026-11-16) adds a centered functional dock for Modern Dark/Light and keeps
the V7.1 classic lower Taskbar for Default. The active pair is
`modern-v72.ext2` / `desktop-v72.qcow2`; V7.1's base/overlay remain recoverable.
See [dock validation and recovery](ModernDockValidation.md) and the separate
[WM identity contract](ApplicationIdentityContract.md).

V7.3 (2026-11-17) consolidates that desktop with applet positioning after layout,
Settings fitting small screens and retained closing-window menus. The selected
pair is now `modern-v73.ext2` / `desktop-v73.qcow2`, copied from the latest cold
V7.2 guest state. V7.2's complete base/overlay pair remains available for recovery.
See [V7.3 results, evidence and limitations](ModernDesktopConsolidation.md).

From Ubuntu/WSL in this repository:

```
./run-modern.sh
```

From Windows PowerShell:

```
wsl -d Ubuntu-24.04 --cd /home/pisco/Documents/serenity ./run-modern.sh
```

The launcher opens a GTK QEMU window named **SerenityOS Modern Desktop**, using one
CPU, 1 GiB RAM, TCG, standard VGA and the prepared Modern Dark desktop. It does
not rebuild or reinstall the OS. GTK zoom-to-fit is disabled, following the
existing runner's handling of absolute mouse input.

Use Display Settings -> Appearance -> Theme to select Modern Light, Modern
Dark or Default. The guest command `DisplaySettings -t themes` opens that page.
Modern includes the existing circular title controls, V3 controls and V4 File
Manager navigation/selection; V5 adds categorized Settings and V6 adds the application launcher. V7.1 adds the top panel; V7.2 adds the Modern dock.

Normal guest changes are stored in the overlay selected by the profile
(currently `Build/desktop-modern/desktop-v73.qcow2`).
`./run-modern.sh --test` creates a disposable copy-on-write session; its guest
changes are discarded when QEMU exits. `--headless` supports QMP validation.
Temporary session directories are removed on normal launcher exit, and only
one latest serial log/command per mode is retained. The launcher prevents a
second simultaneous launch and validates the overlay's backing path.

The selected backing content is `Build/desktop-modern/modern-v73.ext2` and the
pinned, unchanged kernel is `Build/desktop-modern/Kernel`. Keep the base and
its writable overlay together: do not replace a base under an existing writable
overlay. A future userland update should use a new base/overlay pair.
This profile has no virtual network adapter; networking was not validated.

## Why the usual launch looked Classic

No existing QEMU process was active at the start. The available shell history
used `Meta/serenity.sh run`; the standard runner resolves the disk to
`Build/x86_64/_disk_image` and the kernel to `Build/x86_64/Kernel/Kernel`.
That disk contained **only Default.ini** under `/res/themes`, whereas both
Modern theme files existed in source. Its WindowServer, Run and LibGUI binary
hashes also differed from the validated image. Its LibGUI matched the older
uncorrected V4 library, not the final cursor fix.

The V1-V4 validation images had therefore not been synchronized into that
usual disk. Selecting a theme alone could not repair the incomplete installation.
The new base was copied from the verified V4 recovery image, preserving its
coherent userland/resources, and only its offline WindowServer theme
configuration was initialized to Modern Dark. Both Modern resources and Default
remain installed. Installed hash comparisons are in
`Build/desktop-modern/preparation.json`.

The original kernel remains byte-identical:
`7093075d2a15e2079cc85509bd498683b3f453ca876b2915940cfc19e8d189ba`.
The original `_disk_image` was not modified or redirected. Continuing to use an
old direct disk command can still show Classic; use the new launcher above.

## Executed validation after cleanup

- Prepared image passed read-only `e2fsck -fn`; no kernel/filesystem development
  or crash-consistency research was performed.
- QEMU booted with one CPU at 1024 x 768. Real Dark and Light Appearance/File
  Manager captures demonstrate the circular controls, shared Modern controls,
  V4 compact navigation, resizable tree and selection cards.
- The strict cursor regression passed on the installed corrected V4 library.
  V4's earlier native six-move reproduction/fix evidence remains retained; it
  is distinguished from the cursor regression rerun in this update.
- Run's missing-command, signal termination and successful retry cases passed.
- FileOperation's 12 CLI cases and six GUI scenarios passed: copy, move,
  delete, denied destination, invalid destination and controlled partial failure.
  The GUI stayed responsive and the fixtures checked resulting contents/state.
- Production asynchronous ZIP compression/extraction and invalid ZIP error
  scenarios passed, including names/content checks and no remaining children
  or zombies. The V4 64 MiB owner-close evidence remains retained; that large
  scenario was not repeated in this operational update.
- LibGUI and FileManager's Ninja targets remained buildable after cleanup and
  reported no work. Cross toolchains, Lagom, Root, generated dependencies and
  useful caches were preserved. No packages were installed.
- The actual normal GTK launch was also opened with its persistent overlay;
  duplicate-start rejection was verified. No whole-OS stability certification
  or new crash-safety guarantee is implied.

Screenshots, serial logs and results are in `Build/desktop-modern/evidence/`.
At the end of that historical update, the normal GTK window was left running. Test sessions were closed
and their temporary disks removed. Guest reboot persistence was not separately
certified during this update.

## Cleanup actually performed

Repository allocated storage measured **38.92 GiB before** and
**13.21 GiB after** the cleanup and new image setup: approximately
**25.71 GiB net recovered inside WSL**. This includes the new Modern image,
so it is lower than the gross allocation of deleted files.

Deleted 695 files: 18 obsolete disposable raw disk images,
647 PPM captures with pixel-identical PNGs retained, 14 temporary diagnostic
object files and 16 duplicate binary extractions. The exact file-by-file list,
reasons, allocation sizes and disk hashes are retained in
`Build/desktop-modern/cleanup.json`. Only repository Build artifacts were
selected. Tracked paths were excluded and running process descriptors/mappings
were checked before removal. No `git clean`, broad Build deletion or deletion
outside the repository was used.

The removed raw images were:

- `Build/async-zip-native-final-20261110/root.ext2`
- `Build/modern-dark-v1-final-20261110/root.ext2`
- `Build/async-zip-run-regression-20261110/root.ext2`
- `Build/modern-controls-v3-20261111/candidate/root.ext2`
- `Build/modern-controls-v3-20261111/baseline/root.ext2`
- `Build/async-zip-native-resume-20261110/root.ext2`
- `Build/modern-dark-v1-20261110/root.ext2`
- `Build/modern-file-manager-v4-20261112/candidate/root.ext2`
- `Build/modern-file-manager-v4-20261112/diagnostic-v3/root.ext2`
- `Build/modern-file-manager-v4-20261112/diagnostic-v4/root.ext2`
- `Build/modern-file-manager-v4-20261112/baseline/root.ext2`
- `Build/async-zip-resume-suite-v2-20261110/root.ext2`
- `Build/async-zip-run-regression-v2-20261110/root.ext2`
- `Build/async-zip-file-regression-20261110/root.ext2`
- `Build/async-zip-resume-suite-20261110/root.ext2`
- `Build/async-zip-validated-20261110/root.ext2`
- `Build/modern-light-v21-20261110/root.ext2`
- `Build/modern-window-controls-v22-20261110/root.ext2`

Small unique logs, PNG captures, backtraces, manifests, recipes and final
regression results remain. Historical PPM hashes can refer to deleted PPMs;
the matching lossless PNG and cleanup record preserve the visual evidence.
Some historical artifact recipes reference retired disks and need a freshly
prepared disposable image to rerun.

Preserved deliberately:

- `Build/x86_64/_disk_image`: original usual disk, unchanged, as a recovery copy.
- `Build/modern-file-manager-v4-20261112/final/root.ext2`: validated V4 recovery.
- `Build/modern-file-manager-v4-20261112/candidate/blocked-runtime.ext2`: unique,
  self-contained diagnostic snapshot.
- `Build/async-zip-resume-20261110/base.ext2`: unique earlier repaired base;
  retained because its archival necessity is uncertain.
- New Modern base/overlay, pinned kernel, source code, Git/checkpoints, all
  tracked tests/documentation, final evidence, build trees/toolchains, Lagom,
  useful caches and the cached clang-format executable.

This is real filesystem space reclaimed within Ubuntu/WSL. Windows VHDX
compaction was not performed: it lies outside the repository and would require
interrupting WSL. No claim is made that the Windows VHDX file shrank by the same
amount. Standard test sessions now use automatically removed overlays to avoid
accumulating another full root image per launch.
