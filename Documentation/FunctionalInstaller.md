# Functional installer prototype

`Meta/install-serenity.py` installs an existing Serenity system into a **new raw
UEFI disk image**, with automatic GPT partitioning. This is the first functional
host backend. It never writes physical devices or overwrites existing output
files. The native `Installer` application can deploy the resulting image onto
an entirely empty whole disk; its initial validation target is virtual machines.

## VirtualBox and the native installer

Build a self-contained installation disk and export it to VDI:

```sh
python3 Meta/try-serenity.py --virtualbox --include-installer --repair-copy
```

This needs `qemu-utils` and `ninja-build` in addition to the host dependencies
below and a configured x86_64 build/toolchain. It builds Installer and both boot
kernels, creates `Build/x86_64/SerenityOS-Payload.img`, then embeds that image and
the native application in an enlarged **private copy** of ext2. The result is
`Build/x86_64/SerenityOS-Installer.vdi`. The original `_disk_image` is unchanged.
Embedding increases temporary disk usage: allow roughly 20 GiB of host space.
The RAW-to-VDI conversion is compared sector by sector before publishing.
Existing images are reused; existing VDI destinations are never replaced.

Copy the VDI to a convenient Windows folder using Explorer:

```text
\\wsl.localhost\Ubuntu-24.04\home\pisco\Documents\serenity\Build\x86_64
```

In VirtualBox, create an Other/Unknown 64-bit VM, skip unattended installation,
enable EFI, allocate 2 GiB RAM, disable 3D acceleration and attach the VDI as an
existing SATA disk. Use EFI without Secure Boot. This is a hard disk image,
not an ISO to attach as a DVD. VirtualBox documents VDI conversion and EFI/SATA
configuration in its [VBoxManage reference](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/vboxmanage.html).
VirtualBox execution has not been validated locally; QEMU is the tested guest.

To test native installation, add a **second, new, empty SATA disk**, at least
4 GiB, before booting. Open **Utilities → Install SerenityOS**. The source field
already points to the embedded `/home/anon/installed-uefi.img`; choose the second
disk (normally `/dev/hdb`) as the destination and confirm. The backend:

1. Validates the source GPT, backup GPT, CRCs and partition ranges.
2. Accepts only whole storage devices with 512-byte sectors, and reads the entire
   destination to verify it contains only zero bytes before writing anything.
3. Copies the image, flushes it and verifies every copied byte.
4. Moves the backup GPT to the destination's end and updates its checksums.

The window shows these phases and progress. After completion, shut down and
detach the installer disk; boot the destination with EFI. Only the original
payload's system size is installed; additional destination space is not added
to ext2 automatically. Partition GUIDs are preserved, so detach the installer
and any other copy before booting the installed disk.

The GUI is privileged and intentionally limited to blank disks. The current boot
disk and other disks with any data fail the full-disk zero check. A failure or
power loss during copying can leave a partial destination, which will then be
rejected on retry until it is explicitly recreated as an empty virtual disk.
Do not use this prototype on physical disks. There is no recovery environment.

## One-command test

On Ubuntu/WSL, install the dependencies once:

```sh
sudo apt install qemu-system-x86 qemu-utils ovmf e2fsprogs dosfstools mtools ninja-build
```

Then, from the repository root:

```sh
python3 Meta/try-serenity.py
```

The launcher automatically finds UEFI firmware, creates
`Build/x86_64/installed-uefi.img` if missing, and opens QEMU. Shut down any VM
using `_disk_image` before the first creation. Subsequent runs reuse the installed
image and do not require the formatting tools. Closing QEMU discards guest disk
changes; each run also uses a private temporary firmware variable store.
On WSL, displaying the window requires a working graphical environment such as
WSLg.

Use `--dry-run` to print the commands without creating files or opening QEMU.
Use `--image /path/to/installed.img` to select another image. The launcher does
not download dependencies, replace images, or rebuild the system.
If installation fails, it stops before launching QEMU and shows the error.

If the ext2 check reports errors, shut down any VM using the source and retry:

```sh
python3 Meta/try-serenity.py --repair-copy
```

This explicitly enables `e2fsck -f -y` on a **private temporary copy**. Repairs
can change that copy's contents; the source is never repaired or modified.
A separate read-only filesystem check must pass before installation continues.
If repair fails, nothing is published. This is a development aid, not a guarantee
of recovered file contents. The option only applies when creating a new image;
an existing installed image is reused unchanged.

## Requirements

- Linux (including WSL), Python 3, `e2fsprogs`, `dosfstools`, and `mtools`.
- A built x86_64 system with a matching `Kernel.efi`.
- A clean, **offline standalone ext2 image**, such as `Build/x86_64/_disk_image`.
  Shut down any VM using it before installing. Do not use a partitioned image
  such as `uefi_disk_image` as the source. Filesystem errors abort installation
  unless `--repair-copy` was explicitly selected and the repaired copy passes
  validation.
- Free host space for a private source copy, a 512 MiB EFI filesystem and the
  completed disk. Budget approximately twice the source size plus 1 GiB.

On Debian/Ubuntu, install the host dependencies with:

```sh
sudo apt install e2fsprogs dosfstools mtools
```

From the repository root, validate the inputs without writing anything:

```sh
python3 Meta/install-serenity.py \
    --system-image Build/x86_64/_disk_image \
    --kernel Build/x86_64/Kernel/EFIPrekernel/Kernel.efi \
    --output Build/x86_64/installed-uefi.img --dry-run
```

Remove `--dry-run` to perform the installation. It does not require root.
The dry run checks input headers and the output path; dependency checks,
available storage and the complete ext2 check are resolved during installation.

## Installed layout and behavior

- 512-byte sectors, protective MBR, primary and backup GPT with CRC32 checksums.
- 1 MiB alignment; a 512 MiB FAT32 EFI system partition.
- A root partition containing the source ext2 filesystem, rounded up to 1 MiB.
  The installer does not resize the filesystem or claim unused disk capacity.
- `EFI/BOOT/BOOTX64.EFI` contains the supplied kernel. `cmdline.txt` identifies
  the root partition by its new GPT PARTUUID. This follows the existing direct
  UEFI boot path in `Meta/build-image-uefi.sh` and needs no GRUB or firmware entry.

Accounts, permissions and settings are retained from the source filesystem. The
caller must supply matching kernel and system builds. UUIDs inside the copied
ext2 filesystem are retained; do not attach the source and installation together
to tools that identify filesystems solely by filesystem UUID.

All writes happen inside a private temporary directory beside the destination.
Normal failures clean up intermediates. The output name is published only after
the image has been flushed, using an atomic no-overwrite hard link. This requires
a destination filesystem supporting hard links. An uncatchable interruption can
leave a `.serenity-install-*` directory; remove it only after the process exits.
The tool does not promise resumable installation or power-loss recovery.

## QEMU smoke test

With QEMU and OVMF installed, copy the OVMF variable store to a private writable
file (the paths below are typical on Ubuntu), then boot the new image:

```sh
cp /usr/share/OVMF/OVMF_VARS_4M.fd /tmp/serenity-OVMF-VARS.fd
qemu-system-x86_64 -machine q35 -m 1G \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
    -drive if=pflash,format=raw,file=/tmp/serenity-OVMF-VARS.fd \
    -drive if=none,id=os,format=raw,file=Build/x86_64/installed-uefi.img,snapshot=on \
    -device ich9-ahci,id=ahci \
    -device ide-hd,drive=os,bus=ahci.0 \
    -serial stdio
```

Snapshot mode keeps the installed image unchanged during this test. Secure Boot
is outside this prototype's scope.

## Automated checks

```sh
python3 Meta/test-install-serenity.py -v
```

The suite validates both GPT copies and checksums, partition alignment, rejected
inputs, existing destinations, failure cleanup, and a competing output created
at publication time. With the host filesystem tools available, it also builds a
real ext2/FAT installation and checks the copied kernel, boot arguments and root
contents. This filesystem integration test does not prove guest bootability.

### Initial validation (2026-11-05)

All seven tests passed with the filesystem tools available. A disk created from
the local x86_64 build was also booted with QEMU q35, OVMF and an AHCI disk, in
snapshot mode. The EFI prekernel read the generated PARTUUID from `cmdline.txt`;
the kernel mounted the root filesystem and started SystemServer, WindowServer's
main loop, LoginServer and user-session services. This verifies the installation
through graphical service startup, not every application or physical hardware.

The original build's ext2 image had block-bitmap inconsistencies and was correctly
rejected. Only a separate temporary copy was repaired with `e2fsck -f -y` for the
boot test. The installer itself never repairs the source or suppresses check
failures. Guest logs also reported network, audio and Spice service errors; the
smoke test does not establish those devices as working.

### VirtualBox validation (2026-11-07)

The user completed a native installation to a new 8 GiB SATA VDI. The installer
reported installation and verification complete. After detaching the installer
VDI, the installed disk booted to the graphical desktop with EFI, Secure Boot
off, one CPU and 32 MiB video RAM. The user also created a file, shut down and
booted again, and confirmed that the file persisted. Network and audio remain
unverified; these results do not establish physical-PC compatibility.

DisplaySettings crashed while opening the active theme configuration. Its theme
selection now falls back to the first installed theme when the active theme is
absent, and returns an error if there are no themes. The updated binary compiles;
it still needs graphical regression testing and inclusion in a new image.
Existing VDI images are not updated by rebuilding this application.

Before physical-PC installation, identify the target motherboard, storage
controller, graphics and network devices, then test a separate boot medium on
that exact hardware. USB boot and internal-disk access must both be validated.
Use a separate blank destination disk for the first installation; the prototype
does not support installing alongside existing partitions or recovering an
interrupted installation.

## Remaining milestones

1. Broader VirtualBox device testing and physical hardware/driver validation.
2. An installation USB workflow and stronger device/mount ownership checks.
3. A separate privileged service and an unprivileged installer GUI.
4. An independent recovery environment for boot repair and reinstall.
5. Account creation, existing partitions, resizing, encryption and dual boot.

The prototype does not implement recovery, partition shrinking, data preservation
during reinstall or encryption. Only blank virtual disks have been used for
native backend validation.

Run the native backend smoke test with:

```sh
ninja -C Build/x86_64 Installer kernel_x86-64 generate_kernel_efi_image -j 4
python3 Meta/test-native-installer.py
```

It writes only newly allocated fixtures and a copy of the build's root image.
The source fixture is a small GPT disk used for copying tests, not a bootable OS.
It checks boot-disk rejection, empty-disk copying, byte verification, rejection
after installation, and GPT relocation on a larger destination. The backend is
run as PID 1 for isolation; its exit deliberately causes an init-exit panic.
Only the expected exit code is accepted, not unrelated kernel panics.

This test exposed an ATA capacity off-by-one: IDENTIFY returns a sector count,
while StorageDevice expects the last LBA. The AHCI creation path now subtracts
one; without this correction, a full-device scan reads beyond the real disk.
