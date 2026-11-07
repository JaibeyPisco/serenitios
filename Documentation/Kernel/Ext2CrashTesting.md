# Testing ext2 across interrupted writes

`Meta/test-ext2-crash-consistency.py` runs an opt-in guest workload on a disposable
64 MiB ext2 disk attached as the second AHCI drive. It never boots or repairs the
input root image directly. Provide a consistent, offline, standalone ext2 root
image, not a block device or an image currently being modified by a running VM.

Build the x86_64 kernel and workload, then run on a Linux host with QEMU, Python,
GDB with Python support, and e2fsprogs installed:

```sh
ninja -C Build/x86_64 kernel_x86-64 TestExt2CrashConsistency
python3 Meta/test-ext2-crash-consistency.py \
    --root-image /path/to/offline-serenity-root.ext2 \
    --output /tmp/serenity-ext2-crash-results
```

The output directory must not exist. `--gdb` selects another debugger executable;
`--gdb-library-path` supplies its shared-library search path if needed. The runner
uses the ordinary `Kernel.debug` symbols; rebuilding with full debug types is not
necessary. This harness currently requires x86_64, one guest CPU and the QEMU
AHCI configuration selected by the runner.

## Workloads and cut points

Each workload first creates a `sentinel` containing 4096 bytes of `0x7b` and
synchronizes the baseline. The guest then reads a known sector on the test disk
so the debugger can identify its AHCI port without assuming structure layouts.

The operations are creating a file, unlinking a file, replacing a file with
`rename()`, and truncating a file from 128 KiB to 4 KiB. The larger file crosses
the direct-block boundary and exercises indirect block allocation/freeing.

A control run establishes the write count and verifies the completed operation,
a clean unmount, and `e2fsck` success. The default campaign cuts after the first,
middle and last completed AHCI write observed during the operation and its
synchronization. Use `--all-cuts` to examine every observed write boundary, or
`--operations create truncate` to select workloads. A cut that is not reached is
an infrastructure/test failure, not a successful crash test.

`--cuts 1 4 7` selects explicit boundaries. The number of writes can vary between
the control and interrupted guests. Retain and report missed cuts; do not count
them as tested boundaries. Explicit cuts must be positive and cannot be combined
with `--all-cuts`.

The debugger stops the emulated CPU after successful write-command completion
and kills only its owned QEMU process. This models guest interruption between
completed writes. It does **not** model torn sectors, host power loss, or loss of
a real controller's volatile write cache. A passing sample is not a guarantee of
crash consistency across all interleavings.

## Artifacts and interpretation

Each case retains `disk.ext2`, serial/debugger logs, the sequence of completed
writes and the original read-only `e2fsck` output. Crash images are repaired only
as separate `recovered.ext2` copies and checked again. The original and recovered
copies are both inspected for the previously synchronized sentinel. Changes to
the interrupted operation itself are allowed to be incomplete; losing the
sentinel is reported separately.

`results.json` records observed filesystem inconsistencies, dirty/clean markers,
and sentinel preservation. Exit status 1 means at least one sampled image had
metadata inconsistencies, an incorrect state marker or lost acknowledged data.
Tool failures, guest failures and missed cuts abort with their logs preserved.
The script does not automatically modify kernel code in response to findings.

The separate `summary.json` distinguishes metadata inconsistencies from loss of
the synchronized sentinel, including after repair. Metadata inconsistency alone
still fails the strict crash-consistency campaign. A successful repair must not
be reported as an unmodified image having passed the crash test.

## Capacity and recovery-remount checks

```sh
ninja -C Build/x86_64 TestExt2Capacity TestExt2CrashConsistency -j 4
python3 Meta/test-ext2-capacity.py \
    --root-image /path/to/offline-serenity-root.ext2
python3 Meta/test-ext2-recovery.py \
    --campaign /path/to/completed-crash-campaign \
    --root-image /path/to/offline-serenity-root.ext2
```

The capacity test uses a new 16 MiB ext2 disk. It synchronizes a sentinel, writes
a filler until `ENOSPC`, verifies the sentinel, deletes the filler and writes a
new file using the reclaimed space. A clean guest unmount must be followed by
read-only host `e2fsck` success and exact persisted-file comparisons.

Recovery remounts use new copies of each `recovered.ext2` from the campaign.
Serenity mounts each copy, reads the existing sentinel without overwriting it,
checks its bytes and length, and unmounts. Host `e2fsck` and a second sentinel
comparison then verify the resulting copy. Original crash disks and repaired
campaign disks are not changed by the remount runner.

These runners retain logs and disks in new `Build/ext2-capacity-*` and
`Build/ext2-recovery-*` directories. They require explicit fixture device paths,
matching volume labels and sizes, and guest completion markers, so a skipped
opt-in workload cannot be mistaken for a successful test.

## Phase-2 baseline (2026-11-07)

- Campaign: `Build/ext2-phase2-20261107-a`. All four controls passed. Twelve
  first/middle/last write-boundary interruptions were examined. Seven images
  had metadata inconsistencies requiring repair. The synchronized 4096-byte
  sentinel remained intact in every original and repaired image.
- Recovery: `Build/ext2-recovery-ct8yu4nh`. All 12 repaired-image guest remounts,
  sentinel reads, clean unmounts and final host filesystem checks passed.
- Capacity: `Build/ext2-capacity-a53ib8al` passed `ENOSPC`, sentinel preservation,
  space reclamation, persisted replacement data and clean filesystem checks.

### Capacity regression and correction

A repeat in `Build/ext2-capacity-222g8k0w` exposed a real failure: after unlinking
the filler, creating the replacement still returned `ENOSPC`. The inode cache
kept the unlinked inode and its blocks alive until periodic eviction. The
last-link path now removes the cache reference immediately, with a strong local
reference ensuring the inode cannot be destroyed while its mutex is held.
Existing open descriptions retain their own references until they close.

The strengthened test opens the filler before unlinking, verifies that the
unlinked file is still readable with link count zero, then checks that closing
the final description increases free blocks before any periodic eviction.
Replacement-file persistence and read-only host `e2fsck` must still succeed.
Three post-fix runs passed:
`ext2-capacity-iysslovt`, `ext2-capacity-w59n87kd`, and
`ext2-capacity-nswsifi6` under `Build`.

### Validation after the correction

`Build/ext2-phase2-20261107-summary.json` combines the complete create/unlink
cases from campaign `b` and rename/truncate cases from campaign `c`. All four
controls passed. Of twelve valid interrupted cases, five needed metadata repair;
the synchronized sentinel was intact in all original and repaired copies, and
all repaired copies passed host `e2fsck`. Cut points differ from the initial
baseline, so the inconsistency counts are not a like-for-like performance metric.

Campaign `b` also retained a missed `rename` cut at write 8 (that guest completed
only seven writes). This infrastructure case is excluded explicitly, not counted
as a successful cut. Campaign `c` uses reachable explicit boundaries 1, 4 and 7.

Post-fix guest recovery-remount validation passed 8/8 copies from campaign `b`
(`Build/ext2-recovery-rymf08i7`) and 6/6 from campaign `c`
(`Build/ext2-recovery-b_93n67r`), including two additional rename samples.
The memory regression check also passed all four `TestMunMap`/`TestMprotect`
one/two-CPU executions in `Build/kernel-stability-efvy3327`.

The strict phase-2 crash-consistency gate remains **not passed**. The cache
correction is built and tested but has not been deployed to the working VM.

This baseline does **not** pass strict crash consistency. Host `e2fsck` recovery
on copies worked for these samples, but there is no demonstrated integrated
recovery boot path for the installed PC image. Torn-sector writes, physical
device/cache failures, injected I/O errors, other files, other cut boundaries,
and concurrent/SMP filesystem workloads remain unvalidated. The user's working
VirtualBox disk was not used or modified for these tests.
