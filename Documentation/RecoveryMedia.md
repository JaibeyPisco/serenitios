# Bootable UEFI recovery medium

`Meta/build-recovery-media.py` creates a GPT/FAT disk image that boots a separate
RAM-only Linux recovery environment. It contains GRUB, a matching Linux kernel
and storage modules, static BusyBox, Python and e2fsck. This makes recovery
available without successfully booting the installed Serenity system.

The current medium supports manual diagnosis and **clone recovery to a second,
entirely zero-filled disk**. It does not repair the original disk in place or
automatically redirect a dirty Serenity boot into recovery.

## Build on an x86_64 Linux host

Ubuntu/WSL dependencies:

```sh
sudo apt install --no-install-recommends linux-image-virtual busybox-static \
    grub-efi-amd64-bin grub-common e2fsprogs dosfstools mtools kmod python3
```

Choose an installed `vmlinuz-VERSION` with matching `/lib/modules/VERSION`.
Ubuntu may make `/boot/vmlinuz-*` readable only by root. In that case make a
readable workspace copy, retaining the `vmlinuz-VERSION` filename. For the
validated build:

```sh
mkdir -p Build/recovery-media-kernels
sudo install -m 0644 /boot/vmlinuz-6.8.0-149-generic \
    Build/recovery-media-kernels/vmlinuz-6.8.0-149-generic
python3 Meta/build-recovery-media.py \
    --kernel Build/recovery-media-kernels/vmlinuz-6.8.0-149-generic \
    --output Build/Serenity-Recovery.img > Build/Serenity-Recovery.manifest.json
```

The output must not already exist. The builder runs without mounts, host device
writes or root privileges once the kernel is readable. It creates a 128 MiB
image by default, validates dependencies and records kernel/initramfs/image
hashes and included storage modules. Common license texts and packaged
copyright notices are included. Keep the manifest with the image.

## Boot and operate

Write the `.img` file to a spare USB using a raw-image writer, selecting the USB
carefully: writing a raw image replaces that USB's previous contents. The builder
itself never writes a USB or physical disk. Use the PC's UEFI boot menu to select
the USB; Secure Boot must be off for this unsigned prototype.

GRUB offers a screen/keyboard entry and a serial-console entry. The environment
loads into RAM, mounts only its virtual filesystems, and opens `RESCUE>`. It does
not mount storage filesystems automatically or configure a network.

1. Run `list` to identify disks by name, byte size and available model/serial.
2. Run `check DISK`, for example `check sdb`. It validates GPT and root partition
   geometry, marks the source disk/partition read-only, then runs read-only
   e2fsck. No source repair occurs.
3. To recover, attach a second empty disk at least as large as the whole source.
   Use `recover SOURCE TARGET`, for example `recover sdb nvme0n1`.
4. Review the selected identities and type the exact displayed phrase, such as
   `RECOVER sdb TO nvme0n1`. Any other response cancels before writes.
5. The tool revalidates device identity and mount state, makes the source
   read-only, exclusively opens the devices, and scans the entire destination
   for zero bytes **before** writing. Disks with any existing content are refused.
6. It copies and hashes the source, verifies the clone against both source and
   destination, relocates backup GPT if needed, and repairs only the clone's
   ext2 root. A second read-only check, clean state and durability flush are
   required before `RECOVERY_COMPLETE`.
7. Run `poweroff`. Disconnect the original disk before booting the recovered
   copy: GPT/root partition identities are intentionally preserved, so attaching
   both during boot can select the wrong root partition. Extra destination
   capacity remains unused; filesystems are not resized.

The last successful report is available at `/tmp/last-recovery.json` while the
rescue session runs. RAM logs disappear after poweroff. Mounted disks,
read-only destinations, aliases, unsupported/ambiguous GPT layouts and sector
sizes other than 512 bytes are rejected. The native menu refuses device
operations when executed directly on the development host.

## Automated validation

```sh
python3 Meta/test-recovery-media.py -v
python3 Meta/test-recover-serenity-image.py -v
python3 Meta/test-recovery-boot.py --media Build/Serenity-Recovery.img
python3 Meta/test-recovery-boot.py --media Build/Serenity-Recovery.img --nvme-target
python3 Meta/test-recovery-uefi.py --media Build/Serenity-Recovery.img
```

The menu tests extract the exact kernel/initramfs from the built FAT image and
boot them against private disk fixtures. They drive the normal interactive
console, including cancellation and occupied-destination rejection; there is no
unattended destructive test mode in the recovery medium. Source and occupied
disk hashes must remain unchanged. Larger-destination GPT, filesystem state,
sentinel data and host e2fsck are checked afterward.

The UEFI test separately boots the actual image through OVMF and an emulated USB
controller. A readiness marker and screenshot verify that GRUB, Linux, the RAM
environment and recovery menu all started.

### Validated build (2026-11-07)

The final 128 MiB artifact is `Build/Serenity-Recovery-20261107-v4.img`, with
package/kernel/module hashes in the adjacent `.manifest.json` file.

- Eight native clone/guard tests and fifteen offline image-recovery tests passed.
- A SATA fixture completed the interactive cancellation, occupied-destination
  refusal, read-only source check, blank clone, repair and larger-disk GPT
  relocation flow: `Build/native-recovery-boot-q5of5j5m`.
- The actual final medium booted as an emulated UEFI USB and displayed its menu:
  `Build/recovery-uefi-boot-ipmhczi8`, including `menu.png`.
- An approximately 2 GiB actual Serenity UEFI image with an unclean state and
  damaged free-block counter was recovered to a larger emulated NVMe disk:
  `Build/native-recovery-boot-32p5siy5`. The source and occupied-destination hashes
  stayed unchanged; host e2fsck found the clone clean afterward.
- With the original disconnected, that recovered copy booted through OVMF from
  NVMe to the Serenity desktop and shell prompt. Screenshot and command:
  `Build/recovered-nvme-boot-mm0smnrv`.

Only disposable files/emulated devices were written during these checks. The
working VirtualBox VM and physical disks were not used as recovery targets.

## Limits

This is an experimental recovery prototype validated in QEMU, not hardware
certification. Actual PC testing, 4Kn disks, RAID/controllers requiring firmware,
unreadable source sectors, encryption, GPT repair and root layouts with multiple
Linux partitions remain unsupported or unvalidated. Recovery can salvage
filesystem structure but cannot guarantee recovery of every damaged file.

The medium does not remove the crash-consistency failures found in phase 2.
Automatic recovery selection at boot and crash-consistent filesystem metadata
updates remain separate milestones. See [offline recovery](Recovery.md) and
[the interruption tests](Kernel/Ext2CrashTesting.md).
