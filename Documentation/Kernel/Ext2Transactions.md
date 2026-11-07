# Transactional storage: crash analysis and implementation contract

Status: design, independent host replay prototype and interoperability experiments; no kernel journal writer or replay implementation exists yet. The five failing crash cases remain release blockers. Do not enable a journal on a Serenity system disk with the current driver.

## Observed failures

The campaign summary is `Build/ext2-phase2-20261107-summary.json`. These are observations of persisted disk state, not claims that every possible cut has been tested:

- `ext2-phase2-20261107-b/create-21`: inode 13 was allocated and written before its directory entry reached disk. e2fsck found an unattached inode.
- `ext2-phase2-20261107-b/unlink-4`: allocation bitmaps freed inode 13 and blocks 526–558 while persisted inode/directory references still pointed at them. Group free counts also disagreed.
- `ext2-phase2-20261107-c/rename-4`: replacing a destination freed inode 14 and blocks 559–591 before removal of its persisted references completed.
- `ext2-phase2-20261107-c/truncate-1`: inode changes removed references before the allocation bitmap released blocks 527–558, leaking space.
- `ext2-phase2-20261107-c/truncate-4`: the same leaked allocation also disagreed with the persisted free-block count after superblock writes.

The sentinel survived these cases and offline repair. This does not establish preservation of arbitrary files. Changing write order can trade leaked space for dangerous references to reusable blocks; it does not make these multi-block updates atomic.

## Format decision

Implement a restricted, explicitly negotiated standard ext3/JBD2 journal rather than a private log that the existing Linux rescue medium cannot replay. This is an implementation target, not an assertion that Serenity currently supports ext3.

The reference protocol records block after-images, makes the journal durable, commits durably, then checkpoints home blocks. Replay ignores incomplete transactions. Block reuse needs revoke handling or a stronger reuse restriction. See the [Linux journal format documentation](https://docs.kernel.org/filesystems/ext4/journal.html).

Initially accept only a precisely enumerated format, feature set and checksum version. Reject every unimplemented journal feature before writing any filesystem metadata. Bound journal record parsing, validate checksums and block ranges, and prevent journal blocks from being replayed onto the journal itself. Replay must finish successfully before mounting writable; failure must preserve the evidence and refuse writable mount.

The present driver now refuses journal-bearing volumes, unsupported incompatible features, and unsupported read-only-compatible features before its normal dirty-superblock write. This prevents accidentally treating such volumes as plain ext2. It does not add crash atomicity to supported ext2 volumes.

## Kernel requirements

1. Introduce transaction-owned metadata buffers and credit reservation. Reserve enough log space before mutation; start with serialized transactions and explicit size limits. No dirty transaction metadata may escape through the ordinary cache or direct superblock writes before commit.
2. Include directory entries, inode tables, indirect block references, allocation bitmaps, group descriptors and superblock counts in each operation's atomic metadata set. Track every write path, including `flush_super_block()` and cache eviction.
3. For metadata-only journaling, persist newly referenced file data before committing its metadata. The host experiment below logs data as well; it does not validate this ordered-data requirement. Decide and test large-write splitting and partial-write semantics before enabling a writer.
4. Persist journal records, issue and verify a device flush, persist the commit record and flush again. Only then may the transaction be acknowledged as durable. Checkpoint home blocks and flush before reclaiming log space. Never mark the filesystem clean after a failed write or flush.
5. Propagate ENOSPC and I/O errors without silently completing a transaction. Until revoke support is correct, prohibit reuse of freed blocks until the relevant transaction is checkpointed; enforce this across transactions and recovery.
6. Make replay restartable after a crash during recovery. Test malformed records, torn records, wraparound, full journals, failed flushes, disk removal and repeated recovery. Do not infer durable storage from completion of a cached write.

## Evidence and its limits

`Meta/test-ext3-journal-contract.py` builds disposable ext3 files using e2fsprogs. It tests create, unlink, replacement rename and truncate under four conditions: uncommitted records, committed records before checkpoint, partial checkpoint, and applying identical after-images in two successive transactions. The last condition checks logical idempotence; it is not a crash injected during replay of the same transaction ID.

The initial run passed all 16 scenarios in `Build/journal-contract-hajmffu8/results.json`. Uncommitted cases retained the old state; committed cases recovered the new state, with the sentinel intact and a clean read-only e2fsck check. These are host tools exercising the reference format. They do not exercise Serenity journal code, hardware power loss, torn sectors or ordered-data mode.

`Meta/test-ext2-features.py` boots the new kernel with three disposable volumes advertising a journal, extents or metadata checksums. All mounts were refused and whole-volume hashes remained unchanged (`Build/ext2-features-vvc_xw1o/results.json`).

## Independent reader prototype

`Meta/jbd2_reader.py` independently parses extracted journals and returns a replay plan in memory. It performs no disk writes and is not linked into the kernel or rescue medium. Its deliberately narrow format is JBD2 superblock v2, CRC32C checksum v3, 32-bit targets, synchronous commits and optional revoke records. Unsupported formats are rejected. Inputs are capped at 64 MiB; filesystem geometry, UUID and the complete journal physical-block set must be independently supplied by the caller.

The reader checks superblock, descriptor, data, revoke and commit checksums, record bounds, target ranges, journal overlap, UUIDs and feature masks before returning any writes. It handles ring and sequence-number wrap, restores escaped magic, omits uncommitted tails, and suppresses images revoked by committed transactions. A corrupt later transaction rejects the entire plan. An empty plan does not establish that a filesystem is clean.

Validation commands:

```sh
python3 Meta/test-jbd2-reader.py
python3 Meta/test-jbd2-reader-integration.py
python3 Meta/test-ext3-journal-contract.py
```

The reader suite has 21 tests, including malformed records and revoked/reused targets. The integration run passed 16 operation/scenario combinations, with 20 independent replay invocations because the repeated-after-images cases contain two rounds. Each invocation applied its plan twice to a separate copy, retired the test log, then passed read-only e2fsck and file-content checks. Input images were hash-checked unchanged before the separate Linux replay. Evidence: `Build/journal-contract-n_bhp1rt`; the legacy reference regression also passed 16 cases in `Build/journal-contract-ma_wt0fj`.

Fixture limitations are explicit. The installed e2fsprogs 1.47.0 only selects checksum v3 when the filesystem enables metadata checksums. Its [debugfs journal producer](https://github.com/tytso/e2fsprogs/blob/v1.47.0/debugfs/do_journal.c) uses typed-pointer arithmetic when placing the first tag UUID, contaminating fields. For the v3 fixtures, the harness normalizes UUID/target/flag/high fields and the descriptor checksum, retaining the producer's data and commit checksums; both readers then validate the records. These are normalized e2fsprogs fixtures, not unmodified journals captured from a running Linux kernel. Legacy fixtures are separate, and the reader intentionally rejects their checksum format.

The harness now publishes an uncommitted log start explicitly, so that this scenario scans the incomplete transaction rather than returning immediately for an empty log. Test-only log retirement uses regular-file writes without production durability guarantees. Kernel integration still needs trusted journal inode mapping, filesystem feature validation, persistent replay/retirement with flushes, interruption and I/O fault testing, and unmodified Linux-kernel-generated fixtures. No VM or installed system disk was modified for this prototype.

## Next implementation gates

Implement and independently test a read-only journal parser and replay into disposable copies first, including Linux-generated journals and Linux validation of replayed results. Then implement transaction buffering and the durable commit/checkpoint protocol; integrate create, unlink, replacement rename and truncate. Require every enumerated write cut to leave a recoverable old or new state without filesystem repair masking journal errors, plus ENOSPC and injected I/O-error coverage.

Only after those gates pass should opt-in journaled images be generated and tested through both the Serenity and native rescue boot paths. Keep existing system images unchanged until migration and rollback have been tested. Physical-PC stability additionally requires a declared hardware support scope and sustained real-hardware testing; a passing virtual-machine campaign is insufficient.
