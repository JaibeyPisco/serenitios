# Offline image recovery

`Meta/recover-serenity-image.py` inspects an offline Serenity filesystem image
and can publish a repaired **new copy**. It never repairs the source in place,
writes physical devices, or overwrites an existing output.

This tool is host-side recovery. A separate [bootable recovery medium](RecoveryMedia.md)
is now available for a RAM-only UEFI rescue session. Neither is an automatic boot
repair service or a guarantee that damaged files can be restored.

## Requirements and supported images

On Ubuntu/WSL, install `e2fsprogs`; VDI conversion also needs `qemu-utils`.
Shut down any VM using the source image before running the tool. A saved or
suspended VM is not a substitute for an offline disk. Hash checks detect source
changes during the operation, but cannot make a live filesystem snapshot safe.

Supported inputs are standalone ext2 images and 512-byte-sector GPT images with
one Linux/ext2 root partition, including the UEFI images built by this project.
Use explicit `--input-format vdi` for a VirtualBox VDI. Journaled/ext4 images,
ambiguous or overlapping partitions, damaged GPT tables, and unsupported GPT
geometry are rejected rather than guessed or repaired.

## Inspect without publishing an output

```sh
python3 Meta/recover-serenity-image.py --image Build/SerenityOS-System.img
```

The filesystem check runs on a temporary copy with `e2fsck -f -n`. JSON output
includes the source SHA-256, layout, filesystem state and check log. Exit status
0 means clean, 1 means repair is needed, and 2 means the inspection failed or the
input is unsupported. The tool also checks declined repair questions: e2fsck
1.47 can return zero for an unfixed superblock free-block counter mismatch.

## Recover into a new image

```sh
python3 Meta/recover-serenity-image.py \
    --image Build/SerenityOS-System.img \
    --output Build/SerenityOS-Recovered.img
```

For a powered-off VirtualBox disk, run inside WSL, adjusting the input path:

```sh
python3 Meta/recover-serenity-image.py \
    --image "/mnt/c/Users/Jaibey Pisco/VirtualBox VMs/242/SerenityOS-System-Phase1-kvyvm0_5.vdi" \
    --input-format vdi \
    --output Build/SerenityOS-Recovered.vdi \
    --output-format vdi
```

The tool verifies both GPT headers, checksums, partition identifiers, partition
table copies and bounds before selecting the root partition. Only the extracted
ext2 filesystem copy is repaired using `e2fsck -f -y`. A second read-only check
must find no unfixed errors, and the filesystem must be marked clean. EFI, GPT,
partition identities and all bytes outside the root partition are preserved.
VDI output is compared against the repaired RAW image before publication.

The output name is published using an atomic no-overwrite hard link, which
requires a filesystem supporting hard links. Original and output hashes are
included in the report. A source change or failed repair prevents publication.
The original remains available for inspection or another recovery method.

After a successful recovery, copy the new VDI to a convenient Windows folder if
needed and attach that disk to the powered-off VM in place of the old disk.
Keep the old file. Boot and inspect important files: filesystem structural
repair may discard damaged metadata or relocate files into `lost+found`.
The tool preserves the bootloader but does not install one into an image that
did not already boot.

## Validation

```sh
python3 Meta/test-recover-serenity-image.py -v
```

The 15 integration/guard tests cover real ext2 repair and file preservation,
clean-copy identity, GPT/EFI preservation, VDI round trips, malformed or
ambiguous GPT, destination collisions, failed final checks and changing inputs.

On 2026-11-07 the tool also repaired copies of all twelve valid interrupted
images from the phase-2 campaign in `Build/offline-recovery-a8t1pnug`. Their
synced sentinel survived and each copy passed a read-only filesystem check.
Further guest-remount and UEFI boot artifacts are recorded in the retained
validation directories. The running user VM was not used for corruption tests.

All twelve recovered copies subsequently passed guest mount, sentinel read,
clean unmount and a final host filesystem check in
`Build/ext2-recovery-f0a583pk`. A separate actual UEFI system image was given an
incorrect free-block count and an unclean-state marker, then recovered while
preserving GPT and EFI. The repaired copy booted through OVMF to the desktop and
shell prompt. Its report and final screenshot are retained in
`Build/uefi-recovery-test-z2_thtwh/recovery.json` and `desktop-final.png`.

## Remaining recovery work

- Validation of the bootable recovery medium on the target physical PC.
- Integration of dirty-filesystem detection with an actual recovery boot path.
- Audited crash-consistent metadata updates, not just offline repair afterward.
- Recovery beyond the tested single ext2 layout, including partition-table or
  bootloader damage, disk read failures and hardware/cache power-loss behavior.

Do not enable a new boot refusal policy until the user has a tested recovery
path. Current ext2 boot code logs an unclean-filesystem warning; this tool does
not change that mount policy or certify the system as generally stable.
