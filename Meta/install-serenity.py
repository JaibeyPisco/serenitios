#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Assemble an offline x86_64 UEFI installation in a NEW raw disk image.

No root, loop devices, mounts, physical disks or GRUB are needed. The source
must be a clean, standalone ext2 filesystem image, not a partitioned disk.
"""

import argparse
import filecmp
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib

SECTOR = 512
MIB = 1024 * 1024
ESP_START = 2048
ESP_SECTORS = 512 * MIB // SECTOR
ROOT_START = ESP_START + ESP_SECTORS
ENTRY_COUNT = 128
ENTRY_SIZE = 128
ESP_TYPE = uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b")
LINUX_TYPE = uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4")


def regular_file(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Not a regular file: {path}")
    return path


def make_plan(system_image, kernel, output):
    system_image = regular_file(system_image)
    kernel = regular_file(kernel)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(f"Output already exists (will not overwrite): {output}")
    parent = output.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("Output parent must be a directory")
    output = parent / output.name
    # A fixed disk image deliberately cannot select /dev devices as its target.
    if output == Path('/dev') or Path('/dev') in output.parents:
        raise ValueError("Physical devices are not supported; choose a new image file")
    with system_image.open("rb") as source:
        source.seek(1024)
        superblock = source.read(1024)
    if len(superblock) != 1024 or struct.unpack_from("<H", superblock, 56)[0] != 0xEF53:
        raise ValueError("System image must contain a standalone ext2 filesystem")
    blocks, = struct.unpack_from("<I", superblock, 4)
    log_block_size, = struct.unpack_from("<I", superblock, 24)
    compat, incompat = struct.unpack_from("<II", superblock, 92)
    if compat & 4 or incompat & ~2:
        raise ValueError("Journaled/ext4 or unsupported ext2 features in system image")
    source_size = system_image.stat().st_size
    if log_block_size > 2 or blocks == 0 or blocks * (1024 << log_block_size) > source_size:
        raise ValueError("Invalid or truncated ext2 image")
    with kernel.open("rb") as source:
        header = source.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError("Kernel must be an x86_64 EFI executable")
        source.seek(struct.unpack_from("<I", header, 60)[0])
        pe = source.read(96)
    if (len(pe) != 96 or pe[:4] != b"PE\0\0"
            or struct.unpack_from("<H", pe, 4)[0] != 0x8664
            or struct.unpack_from("<H", pe, 24)[0] != 0x20B
            or struct.unpack_from("<H", pe, 92)[0] != 10):
        raise ValueError("Kernel must be an x86_64 EFI application (PE32+)")
    if kernel.stat().st_size > 480 * MIB:
        raise ValueError("Kernel is too large for the EFI partition")
    root_sectors = (source_size + MIB - 1) // MIB * (MIB // SECTOR)
    # Leave room for the backup partition entries/header, rounded to 1 MiB.
    sectors = ((ROOT_START + root_sectors + 33 + 2047) // 2048) * 2048
    return dict(system_image=str(system_image), kernel=str(kernel), output=str(output),
                source_bytes=source_size, disk_bytes=sectors * SECTOR,
                esp_start=ESP_START, esp_sectors=ESP_SECTORS,
                root_start=ROOT_START, root_sectors=root_sectors,
                root_uuid=str(uuid.uuid4()), disk_uuid=str(uuid.uuid4()))


def gpt_header(current, backup, entries_lba, last_lba, disk_uuid, entries_crc):
    header = bytearray(SECTOR)
    struct.pack_into("<8sIIIIQQQQ16sQIII", header, 0,
                     b"EFI PART", 0x10000, 92, 0, 0, current, backup, 34,
                     last_lba - 33, uuid.UUID(disk_uuid).bytes_le,
                     entries_lba, ENTRY_COUNT, ENTRY_SIZE, entries_crc)
    struct.pack_into("<I", header, 16, zlib.crc32(header[:92]))
    return header


def write_partition_table(disk, plan):
    last = plan["disk_bytes"] // SECTOR - 1
    mbr = bytearray(SECTOR)
    struct.pack_into("<B3sB3sII", mbr, 446, 0, b"\0\x02\0", 0xEE,
                     b"\xff\xff\xff", 1, min(last, 0xFFFFFFFF))
    mbr[510:] = b"\x55\xaa"
    entries = bytearray(ENTRY_COUNT * ENTRY_SIZE)
    partitions = (
        (ESP_TYPE, uuid.uuid4(), ESP_START, ESP_SECTORS, "EFI System"),
        (LINUX_TYPE, uuid.UUID(plan["root_uuid"]), ROOT_START, plan["root_sectors"], "SerenityOS"),
    )
    for index, (kind, unique, start, count, name) in enumerate(partitions):
        struct.pack_into("<16s16sQQQ72s", entries, index * ENTRY_SIZE,
                         kind.bytes_le, unique.bytes_le, start, start + count - 1,
                         0, name.encode("utf-16le"))
    crc = zlib.crc32(entries)
    for offset, data in (
        (0, mbr),
        (SECTOR, gpt_header(1, last, 2, last, plan["disk_uuid"], crc)),
        (2 * SECTOR, entries),
        ((last - 32) * SECTOR, entries),
        (last * SECTOR, gpt_header(last, 1, last - 32, last, plan["disk_uuid"], crc)),
    ):
        disk.seek(offset)
        disk.write(data)


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def copy_partition(source, disk, start_sector):
    disk.seek(start_sector * SECTOR)
    with Path(source).open("rb") as stream:
        shutil.copyfileobj(stream, disk, length=MIB)


def install(plan, repair_copy=False, installer_binary=None, payload_image=None):
    required = ("e2fsck", "mkfs.fat", "mmd", "mcopy")
    missing = [tool for tool in required if not shutil.which(tool)]
    if installer_binary and not shutil.which("debugfs"):
        missing.append("debugfs")
    if payload_image and not shutil.which("resize2fs"):
        missing.append("resize2fs")
    if payload_image and not installer_binary:
        raise ValueError("--payload-image requires --installer-binary")
    if missing:
        raise ValueError("Missing tools: " + ", ".join(missing)
                         + ". On Debian/Ubuntu install e2fsprogs, dosfstools and mtools.")
    output = Path(plan["output"])
    # Keep every intermediate on the destination filesystem. Publish with link(),
    # which atomically refuses an existing name, including a racing symlink.
    with tempfile.TemporaryDirectory(prefix=".serenity-install-", dir=output.parent) as directory:
        work = Path(directory)
        root = work / "root.ext2"
        print("[1/5] Copying and checking the system filesystem", flush=True)
        shutil.copyfile(plan["system_image"], root)
        kernel = work / "Kernel.efi"
        shutil.copyfile(plan["kernel"], kernel)
        snapshot = make_plan(root, kernel, output)
        if snapshot["source_bytes"] != plan["source_bytes"]:
            raise ValueError("System image size changed during installation")
        if repair_copy:
            print("Repairing the TEMPORARY COPY only; the source image is unchanged.", flush=True)
            repair = subprocess.run(["e2fsck", "-f", "-y", str(root)])
            if repair.returncode not in (0, 1):
                raise subprocess.CalledProcessError(repair.returncode, repair.args)
        try:
            run("e2fsck", "-f", "-n", root)
        except subprocess.CalledProcessError as error:
            if not repair_copy and error.returncode == 4:
                raise ValueError("The ext2 copy has errors. Shut down any VM using the source, then retry "
                                 "with --repair-copy to repair ONLY the temporary copy.") from error
            raise
        if installer_binary:
            binary = regular_file(installer_binary)
            app_file = Path(__file__).resolve().parent.parent / "Base/res/apps/Installer.af"
            embedded = [(binary, "/bin/Installer"), (app_file, "/res/apps/Installer.af")]
            if payload_image:
                payload = regular_file(payload_image)
                with payload.open("rb") as stream:
                    stream.seek(512)
                    if stream.read(8) != b"EFI PART":
                        raise ValueError("Payload must be a prepared raw GPT image")
                # Grow only the private ext2 copy to hold the installation payload.
                new_size = ((root.stat().st_size + payload.stat().st_size + 256 * MIB + MIB - 1) // MIB) * MIB
                with root.open("r+b") as stream:
                    stream.truncate(new_size)
                run("resize2fs", "-f", root, f"{new_size // 1024}K")
                resized = make_plan(root, kernel, output)
                plan = {**plan, **{key: resized[key] for key in ("source_bytes", "root_sectors", "disk_bytes")}}
                embedded.append((payload, "/home/anon/installed-uefi.img"))
            # debugfs has its own command parser; quote paths for that parser,
            # never interpolate them into a shell command.
            def quoted(path):
                value = str(path)
                if "\n" in value or "\r" in value:
                    raise ValueError("Newlines are not supported in installer paths")
                return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

            for source, destination in embedded:
                run("debugfs", "-w", "-R", f"rm {destination}", root)
                run("debugfs", "-w", "-R", f"write {quoted(source)} {destination}", root)
                if destination == "/bin/Installer":
                    run("debugfs", "-w", "-R", f"set_inode_field {destination} mode 0100755", root)
                dumped = work / (source.name + ".verified")
                run("debugfs", "-R", f"dump {destination} {quoted(dumped)}", root)
                # debugfs sometimes reports command errors with exit status 0.
                if not dumped.is_file() or not filecmp.cmp(dumped, source, shallow=False):
                    raise ValueError(f"Could not embed or verify {destination}")
            run("e2fsck", "-f", "-n", root)
        print("[2/5] Creating the EFI system partition", flush=True)
        esp = work / "efi.fat"
        with esp.open("wb") as stream:
            stream.truncate(ESP_SECTORS * SECTOR)
        run("mkfs.fat", "-F", "32", "-n", "SERENITYEFI", esp)
        run("mmd", "-i", esp, "::/EFI", "::/EFI/BOOT")
        run("mcopy", "-i", esp, kernel, "::/EFI/BOOT/BOOTX64.EFI")
        cmdline = work / "cmdline.txt"
        cmdline.write_text(f"root=PARTUUID:{plan['root_uuid']}\n", encoding="ascii")
        run("mcopy", "-i", esp, cmdline, "::/cmdline.txt")
        print("[3/5] Writing GPT and installing system partitions", flush=True)
        image = work / "disk.img"
        with image.open("w+b") as disk:
            disk.truncate(plan["disk_bytes"])
            write_partition_table(disk, plan)
            copy_partition(esp, disk, ESP_START)
            copy_partition(root, disk, ROOT_START)
            print("[4/5] Flushing the completed image", flush=True)
            disk.flush()
            os.fsync(disk.fileno())
        print("[5/5] Publishing the installation", flush=True)
        os.link(image, output)
    print(f"Installed: {output}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-image", required=True, type=Path, help="Offline standalone ext2 system image")
    parser.add_argument("--kernel", required=True, type=Path, help="Matching x86_64 Kernel.efi")
    parser.add_argument("--output", required=True, type=Path, help="NEW raw disk image; never overwritten")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs and print the plan without writing")
    parser.add_argument("--repair-copy", action="store_true",
                        help="Run e2fsck repairs on the temporary copy, never on the source")
    parser.add_argument("--installer-binary", type=Path, help="Embed a built native Installer into the copied filesystem")
    parser.add_argument("--payload-image", type=Path, help="Include a prepared raw image for the native installer")
    args = parser.parse_args(argv)
    try:
        plan = make_plan(args.system_image, args.kernel, args.output)
        print(json.dumps(plan, indent=2), flush=True)
        if not args.dry_run:
            install(plan, repair_copy=args.repair_copy, installer_binary=args.installer_binary, payload_image=args.payload_image)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
