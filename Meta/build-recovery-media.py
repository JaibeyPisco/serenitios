#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Build a NEW x86_64 UEFI recovery disk image without mounts or device writes.

Uses an installed Linux kernel/modules, static BusyBox, Python and e2fsprogs.
The result boots a separate RAM-only Linux environment, not SerenityOS itself.
"""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib

REPO = Path(__file__).resolve().parent.parent
MODULES = ("ahci", "ata_piix", "nvme", "virtio_pci", "virtio_blk", "virtio_scsi", "sd_mod",
           "usb_storage", "uas", "xhci_pci", "ehci_pci", "uhci_hcd", "hid_generic", "usbhid", "atkbd")


def run(args):
    result = subprocess.run([str(arg) for arg in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        raise RuntimeError(f"Command failed: {args[0]}\n{result.stdout}")
    return result.stdout


def copy_file(source, root, destination=None, executable=False):
    source = Path(source)
    target = root / str(destination or source).lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o755 if executable else stat.S_IMODE(source.stat().st_mode))
    return target


def copy_libraries(binary, root, copied):
    result = subprocess.run(["ldd", str(binary)], capture_output=True, text=True)
    output = result.stdout + result.stderr
    if "not found" in output:
        raise RuntimeError(f"Missing shared library for {binary}: {output}")
    for name in re.findall(r"(?:=>\s+|^\s*)(/[^\s]+)", output, re.MULTILINE):
        path = Path(name)
        if path.is_file() and str(path) not in copied:
            copied.add(str(path))
            copy_file(path, root)


def write_initramfs(root, output):
    # newc supports the initial console/null device nodes without requiring
    # root or creating host device files during the build.
    with output.open("wb") as destination, gzip.GzipFile(fileobj=destination, mode="wb", mtime=0) as archive:
        inode = 0
        def entry(name, mode, data=None, file=None, rdev=(0, 0)):
            nonlocal inode
            inode += 1
            name = name.encode() + b"\0"
            size = file.stat().st_size if file else len(data or b"")
            fields = (inode, mode, 0, 0, 2 if stat.S_ISDIR(mode) else 1, 0, size, 0, 0, *rdev, len(name), 0)
            header = b"070701" + b"".join(f"{field:08x}".encode() for field in fields)
            archive.write(header + name + bytes((-len(header) - len(name)) % 4))
            if file:
                with file.open("rb") as stream:
                    shutil.copyfileobj(stream, archive)
            elif data:
                archive.write(data)
            archive.write(bytes((-size) % 4))
        for path in sorted(root.rglob("*")):
            name = str(path.relative_to(root))
            mode = path.lstat().st_mode
            if path.is_symlink():
                entry(name, mode, data=os.readlink(path).encode())
            elif path.is_dir():
                entry(name, mode)
            elif path.is_file():
                entry(name, mode, file=path)
        entry("dev/console", stat.S_IFCHR | 0o600, rdev=(5, 1))
        entry("dev/null", stat.S_IFCHR | 0o666, rdev=(1, 3))
        entry("TRAILER!!!", 0)


def write_gpt(path, partition_sectors):
    size = path.stat().st_size
    last = size // 512 - 1
    entries = bytearray(16384)
    struct.pack_into("<16s16sQQQ72s", entries, 0, uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b").bytes_le,
                     uuid.uuid4().bytes_le, 2048, 2048 + partition_sectors - 1, 0, "Serenity Recovery".encode("utf-16le"))
    disk_id = uuid.uuid4().bytes_le
    def header(current, alternate, table):
        data = bytearray(512)
        struct.pack_into("<8sIIIIQQQQ16sQIII", data, 0, b"EFI PART", 0x10000, 92, 0, 0, current, alternate,
                         34, last - 33, disk_id, table, 128, 128, zlib.crc32(entries))
        struct.pack_into("<I", data, 16, zlib.crc32(data[:92]))
        return data
    mbr = bytearray(512)
    mbr[450] = 0xEE
    struct.pack_into("<II", mbr, 454, 1, min(last, 0xFFFFFFFF))
    mbr[510:] = b"\x55\xaa"
    with path.open("r+b") as stream:
        for offset, data in ((0, mbr), (512, header(1, last, 2)), (1024, entries),
                             ((last - 32) * 512, entries), (last * 512, header(last, 1, last - 32))):
            stream.seek(offset)
            stream.write(data)


def build(kernel, output, size_mib=128):
    kernel = Path(kernel).resolve(strict=True)
    version = kernel.name.removeprefix("vmlinuz-")
    module_root = Path("/lib/modules") / version
    if not kernel.is_file() or not module_root.is_dir():
        raise ValueError("Choose vmlinuz-VERSION with matching installed /lib/modules/VERSION")
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Output exists; it will not be overwritten")
    parent = output.parent.resolve(strict=True)
    output = parent / output.name
    if parent == Path("/dev") or Path("/dev") in parent.parents or size_mib < 96:
        raise ValueError("Choose a NEW regular image file outside /dev, at least 96 MiB")
    for program in ("busybox", "modprobe", "e2fsck", "grub-mkstandalone", "mkfs.fat", "mcopy", "mmd", "ldd"):
        if not shutil.which(program):
            raise ValueError(f"Missing {program}; see Documentation/RecoveryMedia.md")
    report = dict(kernel_version=version, media_bytes=size_mib * 1024 * 1024, modules=[], unavailable_modules=[])
    with tempfile.TemporaryDirectory(prefix=".serenity-rescue-build-", dir=parent) as directory:
        work = Path(directory)
        root = work / "root"
        root.mkdir()
        for name in ("bin", "sbin", "dev", "proc", "sys", "tmp", "rescue", "root", "etc"):
            (root / name).mkdir()
        (root / "tmp").chmod(0o1777)
        (root / "etc/serenity-recovery-media").write_text("Serenity Recovery v1\n")
        copy_file(shutil.which("busybox"), root, "/bin/busybox", executable=True)
        busybox_check = subprocess.run(["ldd", shutil.which("busybox")], capture_output=True, text=True)
        if "statically linked" not in busybox_check.stdout + busybox_check.stderr and "not a dynamic executable" not in busybox_check.stdout + busybox_check.stderr:
            raise ValueError("Install busybox-static; the recovery init requires a static BusyBox")
        (root / "bin/sh").symlink_to("busybox")
        copy_file(REPO / "Meta/recovery/init", root, "/init", executable=True)
        copy_file(REPO / "Meta/recovery/native.py", root, "/rescue/native.py")
        copy_file(REPO / "Meta/recover-serenity-image.py", root, "/rescue/image.py")
        libraries = set()
        for source, destination in ((sys.executable, "/usr/bin/python3"), (shutil.which("modprobe"), "/sbin/modprobe"),
                                    (shutil.which("e2fsck"), "/sbin/e2fsck")):
            copy_file(source, root, destination, executable=True)
            copy_libraries(source, root, libraries)
        stdlib = Path(f"/usr/lib/python{sys.version_info.major}.{sys.version_info.minor}")
        target_stdlib = root / str(stdlib).lstrip("/")
        shutil.copytree(stdlib, target_stdlib, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "test", "tests", "idlelib", "tkinter", "ensurepip", "venv"))
        for extension in target_stdlib.rglob("*.so"):
            copy_libraries(stdlib / extension.relative_to(target_stdlib), root, libraries)
        for module in MODULES:
            probe = subprocess.run(["modprobe", "-S", version, "--show-depends", module], capture_output=True, text=True)
            if probe.returncode:
                report["unavailable_modules"].append(module)
                continue
            report["modules"].append(module)
            for line in probe.stdout.splitlines():
                if line.startswith("insmod "):
                    copy_file(line.split()[1], root)
        for index in module_root.glob("modules.*"):
            if index.is_file():
                copy_file(index, root)
        for name in ("GPL-2", "GPL-3"):
            copy_file(Path("/usr/share/common-licenses") / name, root)
        for package in ("busybox-static", "e2fsprogs", "python3.12-minimal", "grub-common"):
            copyright_file = Path("/usr/share/doc") / package / "copyright"
            if copyright_file.is_file():
                copy_file(copyright_file, root)
        initrd = work / "initrd.gz"
        write_initramfs(root, initrd)
        report["initrd_bytes"] = initrd.stat().st_size
        report["kernel_sha256"] = hashlib.sha256(kernel.read_bytes()).hexdigest()
        report["initrd_sha256"] = hashlib.sha256(initrd.read_bytes()).hexdigest()
        volume = uuid.uuid4().hex[:8].upper()
        fat = work / "esp.img"
        partition_sectors = size_mib * 2048 - 2048 - 33
        with fat.open("wb") as stream:
            stream.truncate(partition_sectors * 512)
        run(["mkfs.fat", "-F", "32", "-i", volume, "-n", "SERENITYFIX", fat])
        config = work / "grub.cfg"
        config.write_text(f"""search --no-floppy --fs-uuid --set=root {volume[:4]}-{volume[4:]}
set timeout=5
set default=0
menuentry 'Serenity Recovery - screen and keyboard' {{
    linux /boot/vmlinuz rdinit=/init console=tty0 quiet panic=0
    initrd /boot/initrd.gz
}}
menuentry 'Serenity Recovery - serial console' {{
    linux /boot/vmlinuz rdinit=/init console=ttyS0,115200 quiet panic=0
    initrd /boot/initrd.gz
}}
""")
        loader = work / "BOOTX64.EFI"
        run(["grub-mkstandalone", "-O", "x86_64-efi", "--locales=", "--fonts=", "-o", loader,
             "--modules=part_gpt fat normal linux search search_fs_uuid serial terminal", f"boot/grub/grub.cfg={config}"])
        run(["mmd", "-i", fat, "::/EFI", "::/EFI/BOOT", "::/boot"])
        for source, destination in ((loader, "::/EFI/BOOT/BOOTX64.EFI"), (kernel, "::/boot/vmlinuz"), (initrd, "::/boot/initrd.gz")):
            run(["mcopy", "-i", fat, source, destination])
        staged = work / "recovery.img"
        with staged.open("wb") as stream:
            stream.truncate(size_mib * 1024 * 1024)
        write_gpt(staged, partition_sectors)
        with staged.open("r+b") as dst, fat.open("rb") as src:
            dst.seek(2048 * 512)
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        staged.chmod(0o600)
        report["image_sha256"] = hashlib.sha256(staged.read_bytes()).hexdigest()
        os.link(staged, output)
    report["output"] = str(output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, required=True, help="Readable vmlinuz-VERSION with matching installed modules")
    parser.add_argument("--output", type=Path, required=True, help="NEW recovery .img file, never a device")
    parser.add_argument("--size-mib", type=int, default=128)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.kernel, args.output, args.size_mib), indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Recovery media build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
