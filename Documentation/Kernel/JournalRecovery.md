# Opt-in kernel journal recovery

The `journal_replay` filesystem-specific Boolean option allows the kernel to
recover a restricted internal JBD2 journal and expose the recovered filesystem
read-only. The userland use case is reading files after interrupted journaled
operations without relying on host-side replay. This does not enable the journal
writer for ordinary filesystem operations or correct existing plain-ext2 crash
inconsistencies.

## Interface and supported scope

A privileged caller uses the existing `fsopen`, mount-option ioctl and `fsmount`
interfaces. Set `journal_replay` to Boolean true on an Ext2FS mount descriptor
with `MOUNT_IOCTL_SET_MOUNT_SPECIFIC_FLAG` before calling `fsmount`. Boolean ioctl
values use a `u64` containing zero or one. No new syscall is introduced.

The initial path accepts a whole storage device with no detected partitions,
opened for reading and writing, containing one traditional ext2 block group
and an internal journal in inode 8. It accepts the existing ext2 feature subset
plus HAS_JOURNAL/RECOVER, and the previously documented JBD2 checksum-v3 subset.
External journals, filesystem metadata checksums, extents, multiple groups,
orphan cleanup and unsupported inode layouts are refused. Root/raw device writers
must not access the device concurrently; this path is not a general device
ownership or physical-storage certification mechanism.

Fresh ordinary mounts still refuse journal-bearing filesystems before modifying them.
Recovery requests against an already mounted source return `EBUSY`, so VFS
cannot reuse another filesystem instance and silently skip recovery validation.
The recovery filesystem remains intrinsically read-only even if the mount's
read-only flag is removed. Mounting read-only does not describe the recovery
operation itself: authorized replay writes the device before publishing the
read-only filesystem. Passing a read-only source descriptor is refused.

## Validation and durability

Initialization validates geometry and directly reads the journal inode, block
tree, allocation maps and journal before constructing the normal cache or root
inode. Mapping checks reject holes, duplicate or cyclic pointers, unsupported
flags, out-of-range or unallocated blocks, and overlap with fixed metadata.
All indirect pointer blocks belong to the excluded journal block set as well.

The complete replay plan is checked before its first write. This restricted
path rejects after-images changing the journal inode, immutable superblock
geometry/features/identity, descriptor locations, reserved descriptor space or
allocation bits protecting the journal and fixed metadata. Mutable free counters
and the filesystem write timestamp may change. These restrictions preserve the
mapping required to restart interrupted recovery.

Replay writes home images, flushes the device, retires the journal and flushes
again. Only after success does the kernel reload the primary superblock, clear
RECOVER and persist that update. It then initializes cache, descriptors and root
from recovered data and marks the filesystem intrinsically read-only. Short
reads/writes and failed flushes propagate errors; preparation allocations use
`TRY` before replay writes. Failed I/O can be indeterminate and requires
another recovery attempt from persisted state.

## Locking and lifetime

Recovery initialization holds the existing sleeping filesystem-list and
filesystem mutexes. It performs no I/O under mount-table spinlocks. For the
read-only last-detach checks, VFS acquires the filesystem's optional
`unmount_mutex()` before entering mount-table spinlocks. The check performs no
allocation or disk I/O. VFS retains the filesystem until after this mutex is
released; cached inode destruction therefore happens outside mount-table
spinlocks. This scoped path does not redesign ordinary writable-ext2 unmount.

## Repeatable checks

Build and run on disposable fixtures with a consistent offline root image:

```sh
ninja -C Build/x86_64 kernel_x86-64 TestExt2JournalReplay -j 4
python3 Meta/test-kernel-journal-replay.py \
    --root-image /path/to/offline-root.ext2
python3 Meta/test-journal-inode.py
```

The guest suite checks create, unlink, replacement rename and truncate under
uncommitted, committed and partial-checkpoint states. It also checks default
mount refusal, corrupted checksums/maps, read-only source descriptors and
valid-checksum records that would destroy journal recovery invariants.
Validation-refused images must retain their full hashes. Successful recovery
must preserve exact
file contents, pass host read-only e2fsck and survive a second read-only mount,
sync, remount and unmount without changing the recovered image. An open file
must prevent final unmount with `EBUSY`.

The guest test is opt-in through environment variables and otherwise skips.
Kernel paths contain no hardcoded userspace mount or file paths. See
[DevelopmentGuidelines.md](DevelopmentGuidelines.md) for the development rules
and [TransactionalStorageImplementation.md](TransactionalStorageImplementation.md)
for remaining writer/cache integration work.

## Validation evidence (2026-11-09)

- `Build/kernel-journal-replay-dbt5_6z8`: 28 scenarios passed through 40 guests
  with one CPU; exact files, host e2fsck, EROFS, open-file EBUSY and unchanged
  second-mount hashes passed. Provenance identifies the tested binaries.
- `Build/kernel-journal-replay-nug5i99l`: the subsequent already-mounted-source
  guard passed committed recovery and its second mount with the updated kernel
  and test binaries (two guests).
- Ordinary ext2 feature rejection and ENOSPC/reclamation passed in
  `Build/ext2-features-92o5fwzx` and `Build/ext2-capacity-8r4hojvz`.
- The four-CPU campaign `Build/kernel-journal-replay-7zt11l_2` was incomplete:
  three scenarios passed before a guest stalled during startup, before the
  recovery workload began. Keep this failure; it is not a passed SMP gate.
  Follow-up partial-checkpoint and three-scenario campaigns in
  `Build/kernel-journal-replay-joswvh_4` and
  `Build/kernel-journal-replay-tgbh_y8x` passed eight guests with all four CPUs
  active, but do not explain the original stall. The runner now captures GDB
  traces on SMP guest failure when GDB is available.

Actual guest interruption during replay and a resolved SMP startup baseline
remain open. Simulated-device protocol interruption tests are separate evidence.

### Stability consolidation (2026-11-09)

The bounded consolidation run reproduced the startup stall with both the current
kernel and an independently compiled clean `13372c6dee` baseline, using the same
root, userland test and four-CPU QEMU configuration. Each initial batch passed
19 of 20 guests. In both failures all four CPUs were halted in the scheduler's
idle loop, before the filesystem workload began. This demonstrates that the
symptom predates the working-tree recovery changes; its internal cause remains
unconfirmed. No speculative kernel patch was applied.

A further 30 current-kernel filesystem-workload guests and 40 original-workload
boot-only guests passed. They do not resolve or erase the observed failures.
The deliberately short diagnostic-tool timeout is excluded from natural failure
counts. The new helper can inspect saved thread state using separately compiled
matching debug types without calling guest functions or changing guest memory.

The complete incremental build passed. Three successive boots of the same data
disk verified effective-UID-100 create/read/append/rename/unlink and persistence
across two restarts, with clean host e2fsck after each boot. The 28 recovery
scenarios were repeated and passed through 40 guests. The six selected
memory/thread regression guests passed with one/four CPUs; parser, commit,
replay, map, buffer, inspection, feature-rejection and capacity suites passed.

The strict ordinary-ext2 crash campaign did **not** pass: one of four interrupted
operations left a truncate bitmap inconsistency covering blocks 566–597. All
four clean controls passed, and the selected file witnesses survived. Ordinary
ext2 writes remain outside the journal; recovery must not be advertised as
complete protection for those writes. Kernel sources and all prior changes were
preserved; this consolidation added tests and diagnostics only.

Raw results, commands, binary hashes, failure stacks and snapshot-preservation
checks are indexed in `Build/stability-consolidation/results.json`. The complete
recovery rerun is `Build/kernel-journal-replay-uicldx2b`, and the strict cut
campaign is `Build/ext2-consolidation-20261109`.
