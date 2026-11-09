# Staged transactional storage implementation

This plan targets the five observed interrupted-write failures described in
[Ext2Transactions.md](Ext2Transactions.md). Parser and fixture work establish a
prerequisite; they do not fix those failures. Existing plain ext2 system images
remain unchanged until an opt-in journaled-image migration and rollback path is
tested. Passing these stages does not establish overall OS release readiness;
see [StabilityReleaseGates.md](../StabilityReleaseGates.md).

## Implementation checkpoint (2026-11-09)

`Kernel/FileSystem/Ext2FS/Journal.h` now provides the AK-only read-only parser.
The supported format remains checksum-v3, 32-bit targets and synchronous
commits, with optional revoke records. Input is limited to 64 MiB, descriptor
tags plus revoke entries to 16,384, and committed transactions to 4,096.
Exceeding the resource policy is rejected rather than silently truncating replay.

`Meta/test-kernel-journal-reader.py` compiles a host adapter and compares its
results with the independent Python implementation. Its 25 tests passed,
including resource-policy boundaries and all three supported block sizes.
`--kernel-build Build/x86_64` additionally checks both foundation headers using
the actual configured kernel compiler flags. This checks compilation, not guest
execution of the parser.

The transaction buffer tests passed allocation-failure, capacity, overwrite,
aliasing, move and abort checks. All 1,024 reserved blocks can be staged and read
without subsequent allocation in the header implementation. A separate fresh
host AK build in `Build/storage-foundation` passed both foundation runners.
The workflow `.github/workflows/stability-host.yml` includes these checks;
local results do not demonstrate a remote workflow run.

The C++ integration campaign in `Build/journal-contract-w_hczr4x` passed all
16 operation/scenario combinations with 20 replay invocations. Each C++ plan
matched the Python reader, its writes were applied twice to a separate image,
file contents and read-only e2fsck passed, and source hashes stayed unchanged.
Run `python3 Meta/test-kernel-journal-reader.py --integration` to repeat this
check. These remain normalized e2fsprogs fixtures, not journals captured from
a running Linux kernel. Ten host inspection regression tests also passed with
`python3 Meta/test-ext2-crash-inspection.py`.

The content-validation guest baseline is recorded in
[Ext2CrashTesting.md](Ext2CrashTesting.md). The filesystem corruption remains
reproducible. These components are not connected to mounted filesystems and no
installed image was updated. Cache isolation, transaction rollback and
integration of the writer with mounted filesystems remain implementation work.

## Commit/replay protocol checkpoint (2026-11-09)

`JournalCommit.h` now prepares checksum-v3 records from owned after-images or a
`TransactionBuffer`, reserving memory before device I/O. It serially flushes
caller-written data, persists log records and clears stale commit/end slots,
arms the journal, commits durably, checkpoints home blocks and retires the log.
Every barrier is checked. An attempted object cannot be reused, including after
failure; a failed barrier leaves durability indeterminate and requires recovery.

`JournalReplay.h` validates and owns the complete replay plan before I/O. It
writes home images, flushes them, then retires the journal and flushes again.
Recovery can be restarted by reading the persisted journal after interruption.
Both protocols require exclusive external serialization, independently trusted
journal mappings and direct device operations that reject short writes. The
filesystem needs-recovery flag must already be durable before a commit and
must not be cleared before successful recovery/checkpoint. These components do
not manage that flag or ordinary cache/inode state.

Validation:

- `python3 Meta/test-journal-commit.py`: 204 volatile-device failure/recovery
  cases across supported block sizes, including ambiguous failed writes/flushes,
  stale commits, sequence wrap, noncontiguous maps and successive transactions.
  Preparation allocation failures and allocation-free execution also passed.
- `python3 Meta/test-journal-replay.py`: 104 interrupted-recovery/persistence
  cases with noncontiguous mappings, uncommitted tails, revokes, sequence/ring
  wrap and repeated updates to the same target. Entire non-journal disk contents
  are compared after retry; moved/failed objects cannot perform more I/O.
- `Build/journal-producer-uvxvdw5a/results.json`: 20 disposable-filesystem
  scenarios and 25 Linux replay invocations passed. C++-encoded records matched
  the independent Python reader, Linux e2fsck replay needed no metadata repair,
  exact file contents passed and source hashes stayed unchanged. Includes
  escaped journal magic. These descriptors are emitted by the C++ producer
  without debugfs normalization. The test uses prepared journal snapshots;
  the separate simulated-device suite exercises execute().
- All four headers passed syntax checks using actual configured x86_64 kernel
  flags; the 25 parser tests and transaction-buffer checks still passed.

The failure model covers complete block writes and volatile device caches, not
torn sectors or physical power loss. Genuine journals captured from a running
Linux kernel remain a separate interoperability gate. No writable journal mount
is enabled, no existing system image is migrated, and the five ext2 corruption
cases remain open until filesystem operation integration passes its gates.

## Kernel read-only recovery checkpoint (2026-11-09)

The `journal_replay` Boolean mount option now invokes recovery inside Ext2FS
initialization and publishes an intrinsically read-only filesystem afterward.
Normal mounts still refuse journal-bearing volumes. The restricted implementation
and its lock order are documented in [JournalRecovery.md](JournalRecovery.md).

`JournalInode.h` validates the internal journal's traditional block tree, including
all indirect blocks and caller-validated allocation/metadata exclusion. Its host
suite passed 28 valid/bounds checks, malformed-tree checks and two allocation
failure cases. Kernel preparation allocations propagate errors through `TRY`.

`Build/kernel-journal-replay-dbt5_6z8/results.json` passed 28 scenarios through
40 actual QEMU guest executions. Recovery covered create, unlink, replacement
rename and truncate, with old/uncommitted, committed and partial-checkpoint
states. Corrupt records, invalid maps, read-only descriptors and valid-checksum
records changing recovery invariants were refused without changing disk hashes.
Userland verified exact files, EROFS, EBUSY with an open file and persistent
read-only behavior after remount. Second mounts, sync and unmount left complete
recovered image hashes unchanged. Kernel/test/producer hashes are recorded in
the campaign's `provenance.json`; the original root image was unchanged.

Ordinary ext2 regression checks also passed: unsupported journal/extents/checksum
features were refused unchanged in `Build/ext2-features-92o5fwzx`, and disk-full,
open-unlink-final-close reclamation, persisted contents and clean unmount/fsck
passed in `Build/ext2-capacity-8r4hojvz`.

The new last-detach path acquires its optional filesystem lifecycle mutex before
mount-table spinlocks and defers cached inode destruction until outside them.
The development follows [DevelopmentGuidelines.md](DevelopmentGuidelines.md):
no new syscall or kernel userspace paths, fallible preparation and userland
validation. Existing writable-ext2 unmount locking is not redesigned here.

This guest campaign does not interrupt the actual kernel during replay; generic
protocol interruption tests remain separate. Writable operations, cache
isolation, inode-state publication/rollback, orphan handling and image migration
are still open. The existing five plain-ext2 corruption cases are not fixed by
read-only recovery support, and no working VM disk was updated.

## Stage 1: bounded parser and independent checks

Implement a read-only C++ parser for the explicitly supported JBD2 format. It
must produce a bounded replay plan without modifying its input or any device.
Reuse the existing Python reader and host integration fixtures as independent
oracles; reject unsupported features, invalid checksums, invalid geometry,
out-of-range targets and targets overlapping any journal block.

Acceptance:

- C++ unit tests cover committed, uncommitted, corrupt and revoked records,
  sequence/ring wrap and resource bounds.
- Compare accepted/rejected inputs and replay results with the Python reader.
- Replay into disposable copies, then check file contents and read-only e2fsck.
- Include unmodified journals generated by a running Linux kernel before
  claiming interoperability. Existing normalized debugfs fixtures alone are
  insufficient.
- Continue rejecting ordinary journal-bearing mounts; the explicit read-only
  recovery path is validated separately.

The exact supported format and evidence belong with the implementation; parser
success alone must not enable writable mounting.

## Stage 2: transaction-owned buffers and reservation

The standalone foundation is `Kernel/FileSystem/Ext2FS/TransactionBuffer.h`.
It reserves storage up front for at most 1024 full blocks (at most 4 MiB of
image data plus block identifiers), replaces duplicate targets without consuming
another credit, provides read-your-staged-image views and supports terminal
abort. `python3 Meta/test-transaction-buffer.py --build-dir Build/lagom`
builds its dedicated host checks against an existing Lagom AK library. These
include reservation failure injection, capacity/geometry rejection and staging
with subsequent header-level allocations disabled. This class has no disk I/O,
journal credit calculation, ownership enforcement, filesystem rollback or
commit/checkpoint protocol. It does not yet isolate filesystem cache writes.

Introduce a bounded transaction overlay containing full metadata block images.
Reserve its memory and journal credits before mutating filesystem state. Start
with serialized transactions, explicit owner identity and a documented lock
order. Reads by an operation must see its staged writes. Unrelated operations
must not accidentally join an active transaction.

The overlay must cover every metadata path, including directory contents,
indirect block pointers, inode tables, allocation bitmaps, group descriptors and
primary/backup superblocks. Abort must restore in-memory inode state, allocation
maps, counters, block views and lookup caches, not merely discard disk buffers.

Acceptance:

- Cache pressure cannot write uncommitted metadata to home locations.
- Uncached writes and raw superblock writes cannot bypass the transaction.
- Reservation/allocation failures before commit leave the prior state intact.
- Concurrent readers/writers and flush/unmount paths obey the chosen isolation
  and lock protocol without deadlocks.
- Tests exercise bounded transactions and explicit oversized-operation failure.

## Stage 3: durable commit, checkpoint and replay

Write journal records and verify a device flush, write the commit record and
verify another flush, then checkpoint home blocks. Flush the checkpoint before
reclaiming journal space. Newly referenced file data must become durable before
the metadata commit unless the supported mode journals that data as well.

Integrate trusted journal-inode mapping, feature validation and replay before
writable mount. Replay and log retirement must survive interruption and repeat
safely. Until revoke handling is complete, prevent freed-block reuse until the
relevant transactions are checkpointed. A failed write or flush must not produce
a clean filesystem marker or a successful durability acknowledgement.

Acceptance:

- Inject failures and interruptions at record, commit, checkpoint, replay and
  retirement boundaries on disposable disks.
- Reject corrupt recovery input before applying a partial unvalidated plan.
- Preserve recovery evidence and refuse writable mounting on recovery failure.
- Verify all supported storage paths propagate failed durability barriers.
- Cover journal exhaustion, wraparound and restart during recovery.

## Stage 4: create-file vertical slice

Integrate file creation end to end, including inode allocation, inode table,
directory data and growth, indirect pointers, allocation maps and all counters.
Choose explicit semantics for runtime failure and durability acknowledgement.
Keep this opt-in on disposable journaled images until its acceptance gate passes.

Acceptance:

- Every reachable completed-write cut yields the old or new recoverable state.
- Previously synchronized unrelated files retain exact contents and metadata.
- After journal replay, read-only e2fsck passes without a separate repair masking
  an incorrect journal implementation.
- ENOSPC, allocation failure, short writes and failed flushes return appropriate
  errors and leave a usable, accounted-for state.

## Stage 5: unlink, rename, truncate and general mutations

Extend the proven transaction path to final-link deletion, replacement rename,
truncate and directory operations. Unlinked open files need durable orphan
tracking or an equivalent tested protocol so recovery can reclaim them after a
crash. Cover final-close cleanup and error reporting. Define bounded splitting
and partial-write semantics for large writes/truncates before enabling them.
Audit chmod, chown, timestamps, hard links and every remaining metadata mutator;
supporting four test operations is not complete filesystem coverage.

Acceptance:

- All five historical failing cases pass with the new implementation, together
  with the full enumerated-cut campaigns for all supported operations.
- Test open-unlink-read-close, crash with an open orphan, replacement rename
  across directories, indirect-block boundaries and concurrent mutations.
- Exercise recovery through both Serenity and the rescue environment.
- Archive the original failures and new logs with kernel/image hashes.

## Stage 6: opt-in deployment and remaining release work

Generate a new journaled test image only after the preceding gates pass. Verify
installation, recovery, migration and return to the previous bootable image.
Account explicitly for documents created after migration; retaining the old
system disk alone does not preserve those documents when rolling back.

Then run sustained workloads in one normal-user desktop session, with resource
trends and watchdog diagnostics, on a declared VM configuration. Physical power
loss, device write-cache behavior and physical hardware support require separate
validation within the declared hardware scope. Desktop reliability, update
delivery, permissions and listening-service exposure remain release work beyond
the transactional-storage implementation.

## Source-level hazards to preserve in reviews

- `Kernel/FileSystem/BlockBasedFileSystem.cpp`: `DiskCache::ensure()` calls
  `flush_writes_impl()` when clean cache entries are exhausted. Delaying only
  `Ext2FS::flush_writes()` cannot isolate transaction metadata.
- The same file's uncached `write_block()` path flushes a specific dirty block;
  `raw_write_blocks()` bypasses the ordinary block cache.
- `Kernel/FileSystem/Ext2FS/FileSystem.cpp`: `flush_super_block()` uses raw writes
  for the primary superblock and cached writes for backups. Both need coverage.
- `Ext2FS::rename()` removes an existing destination before adding the source
  and removing its old name. These changes must share a transaction.
- `Kernel/FileSystem/Ext2FS/Inode.cpp`: `write_directory()` resizes and writes
  directory content through the ordinary file-data path. Classification by
  inode-table and bitmap writes alone misses this metadata.
- `create_inode()`, `add_child()` and block allocation perform fallible work
  after changing allocation or link state. Durable logging does not substitute
  for correct runtime rollback.
- `Ext2FSInode::~Ext2FSInode()` discards `free_inode()` errors. Final unlink
  cleanup can occur after the original operation, at final close/destruction.
- Inode operations commonly acquire inode locks before filesystem locks;
  `rename()` holds the filesystem lock while accessing inodes. Adding a global
  transaction lock without reviewing order risks new deadlocks.

These references identify current integration points, not a claim that all
write paths have already been audited.
