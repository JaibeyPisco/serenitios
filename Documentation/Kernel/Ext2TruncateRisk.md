# Interrupted ext2 truncate: scope and development mitigation

This investigation is limited to the reproducible 128 KiB to 4 KiB truncate.
It does not extend journaling or investigate the separate SMP startup issue.
All guest checks used one CPU and disposable images.

## Confirmed behavior

Both the current kernel and clean baseline commit `13372c6dee` completed a clean
control and all seven reachable completed-write cuts. Each kernel produced the
same result: cuts 1–5 needed metadata repair; cuts 6–7 and the clean control
passed read-only e2fsck. Artifacts are `Build/truncate-risk/current-cuts` and
`Build/truncate-risk/base-cuts`. Exit status 1 is the expected failed strict
consistency gate, not an infrastructure failure.

At cut 1, inode 15 already describes the intended 4096-byte file and references
only block 565. Bitmap entries 566–597 remain allocated without inode references.
Inspection identified 31 former data blocks and one detached indirect-pointer
block: 131072 bytes of unavailable space. The retained 4096-byte prefix, sentinel
and both unrelated witnesses matched their expected contents in every sample.
This is an allocation leak and metadata inconsistency, not observed corruption
of retained file data. The removed tail is intentional truncate output. These
checks do not prove preservation of every possible unrelated file.

`remove_block_pointer()` stages detached blocks in `m_blocks_pending_free`.
`flush_metadata()` persists the shortened inode and edited pointer blocks before
making those blocks reusable. An interruption in between loses the in-memory
pending-free list while the disk bitmap still protects the detached allocations.
Successful `fsync()` also calls the filesystem flush; normal completed operations
do release the space. The relevant truncate/free ordering is identical in the
clean baseline; recent read-only guards do not explain this case.

## Two evaluated alternatives

1. **Local truncate ordering/flush correction:** not a safe fix for every cut.
   Flushing allocation metadata sooner does not remove the interval between
   independent writes. Freeing allocations first can instead expose blocks
   still referenced by an inode. Closing the interval needs persistent recovery
   information or a broader recovery protocol; that exceeds a bounded local
   correction. No kernel patch was made.
2. **Temporary isolated development:** recommended. Keep important sources and
   documents on the host; use a clean, offline base and QEMU temporary snapshots
   with one CPU. Guest modifications are intentionally discarded on exit.
   For a disk whose changes must be inspected after an interruption, stop the VM,
   keep the original, and use the existing offline tool to publish a repaired
   new copy. Check important contents before reusing that copy. Never run repair
   on a live disk or treat structural fsck success as a general data guarantee.

The existing `Meta/try-serenity.py` launcher uses `snapshot=on` and QEMU's default
single CPU. Use its existing image selection with a separate clean UEFI image;
do not point another VM at a concurrently modified backing image. The temporary
snapshot workflow is for testing, not storage of the only copy of a document.

Offline copy recovery uses the existing interface:

```sh
python3 Meta/recover-serenity-image.py \
    --image /path/to/offline-image.ext2 \
    --output /path/to/new-recovered-image.ext2
```

## Verification

- All 14 interrupted images across both kernels were recovered into new copies.
  Originals retained their hashes; exact victim/witness contents and final
  read-only fsck checks passed. All 32 leaked allocations in the first case
  became free in the repaired copy. Reports: `Build/truncate-risk/mitigation`.
- Seven recovered current-kernel images passed guest remount, sentinel reading,
  clean unmount and final host fsck in `Build/ext2-recovery-ayjcuuae`.
- Two one-CPU temporary-snapshot sessions completed effective-UID-100 file
  operations. Both backing images retained their full hashes, and guest-created
  files were absent after exit: `Build/truncate-risk/snapshot-check/results.json`.
- Three successive boots of the same disposable persistent disk passed create,
  read, append, rename, deletion and post-restart content checks:
  `Build/consolidation-truncate-risk-persistence-nmeat6ys`.
- Offline recovery regression tests passed 15/15; inspection tests passed 10/10;
  capacity/ENOSPC/reclamation passed in `Build/ext2-capacity-gy2e4tty`.

The scoped conclusion is to retain the conservative ordering, document the
crash limitation and continue functional development on isolated disks. No
transactional writer expansion is justified by this repairable-space-leak case
alone. Physical power loss, torn sectors and other ext2 operations are outside
this conclusion.
