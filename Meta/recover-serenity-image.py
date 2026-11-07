#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Check or repair an OFFLINE Serenity ext2/UEFI image into a NEW image file.

Never writes the input, physical devices, or an existing output. Requires
e2fsck; explicit VDI input/output additionally requires qemu-img. This is host
recovery, not an automatic recovery environment inside SerenityOS.
"""

import argparse
import hashlib
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
LINUX = uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4").bytes_le


def digest(path, offset=0, length=None):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        stream.seek(offset)
        while length is None or length:
            block = stream.read(1024 * 1024 if length is None else min(length, 1024 * 1024))
            if not block:
                if length:
                    raise ValueError("Truncated image while hashing")
                break
            checksum.update(block)
            if length is not None:
                length -= len(block)
    return checksum.hexdigest()


def copy_range(source, destination, source_offset, destination_offset, length):
    with source.open("rb") as src, destination.open("r+b") as dst:
        src.seek(source_offset)
        dst.seek(destination_offset)
        while length:
            data = src.read(min(length, 1024 * 1024))
            if not data:
                raise ValueError("Truncated image while copying")
            dst.write(data)
            length -= len(data)


def ext2_state(path, offset, capacity):
    with path.open("rb") as stream:
        stream.seek(offset + 1024)
        block = stream.read(1024)
    if len(block) != 1024 or struct.unpack_from("<H", block, 56)[0] != 0xEF53:
        raise ValueError("No supported ext2 superblock at the selected filesystem")
    count, = struct.unpack_from("<I", block, 4)
    shift, = struct.unpack_from("<I", block, 24)
    compat, incompat, ro_compat = struct.unpack_from("<III", block, 92)
    if shift > 2 or not count or count * (1024 << shift) > capacity:
        raise ValueError("Invalid or truncated ext2 filesystem")
    if compat & 4 or incompat & ~2 or ro_compat & ~7:
        raise ValueError("Journaled/ext4 or unsupported filesystem features")
    return struct.unpack_from("<H", block, 58)[0]


def layout(path, image_size=None):
    # Native recovery supplies a block device's ioctl-derived size. The public
    # host recover() entry point continues to reject physical devices.
    size = path.stat().st_size if image_size is None else image_size
    if size < 2048 or size % SECTOR:
        raise ValueError("Image size must be sector-aligned and contain a filesystem")
    with path.open("rb") as stream:
        stream.seek(SECTOR)
        is_gpt = stream.read(8) == b"EFI PART"
        if not is_gpt:
            stream.seek(size - SECTOR)
            backup_gpt = stream.read(8) == b"EFI PART"
            stream.seek(0)
            first_sector = stream.read(SECTOR)
            if backup_gpt or (first_sector[510:512] == b"\x55\xaa" and first_sector[450] == 0xEE):
                raise ValueError("Primary GPT is damaged; partition-table recovery is not supported")
            ext2_state(path, 0, size)
            return dict(kind="ext2", offset=0, length=size, image_bytes=size)
        last = size // SECTOR - 1

        def header(lba, alternate, table_lba):
            stream.seek(lba * SECTOR)
            data = bytearray(stream.read(SECTOR))
            if len(data) != SECTOR:
                raise ValueError("Truncated GPT header")
            values = struct.unpack_from("<8sIIIIQQQQ16sQIII", data)
            signature, revision, length, crc, reserved, current, backup, first, end, disk_id, table, count, entry_size, table_crc = values
            if (signature != b"EFI PART" or revision != 0x10000 or length != 92 or reserved
                    or current != lba or backup != alternate or table != table_lba
                    or count != 128 or entry_size != 128 or first < 34 or end > last - 33 or first > end):
                raise ValueError("Unsupported or invalid GPT layout")
            struct.pack_into("<I", data, 16, 0)
            if zlib.crc32(data[:length]) != crc:
                raise ValueError("GPT header checksum mismatch")
            stream.seek(table * SECTOR)
            entries = stream.read(count * entry_size)
            if len(entries) != count * entry_size or zlib.crc32(entries) != table_crc:
                raise ValueError("GPT partition-table checksum mismatch")
            return (first, end, disk_id, entries)

        primary = header(1, last, 2)
        backup = header(last, 1, last - 32)
        if primary != backup:
            raise ValueError("Primary and backup GPT disagree; partition-table recovery is not supported")
        stream.seek(0)
        mbr = stream.read(SECTOR)
        if (mbr[510:512] != b"\x55\xaa" or mbr[450] != 0xEE
                or struct.unpack_from("<II", mbr, 454) != (1, min(last, 0xFFFFFFFF))
                or any(mbr[446 + index * 16 + 4] for index in range(1, 4))):
            raise ValueError("Missing protective MBR")
        first_usable, last_usable, _, entries = primary
        partitions = []
        identifiers = set()
        roots = []
        for index in range(128):
            entry = entries[index * 128:(index + 1) * 128]
            if entry[:16] == bytes(16):
                continue
            identifier = entry[16:32]
            if identifier == bytes(16) or identifier in identifiers:
                raise ValueError("Missing or duplicate partition identifier")
            identifiers.add(identifier)
            first, end = struct.unpack_from("<QQ", entry, 32)
            if first < first_usable or first > end or end > last_usable:
                raise ValueError("Partition lies outside the GPT usable range")
            if any(first <= other_end and other_first <= end for other_first, other_end in partitions):
                raise ValueError("Overlapping partitions")
            partitions.append((first, end))
            if entry[:16] == LINUX:
                roots.append((first * SECTOR, (end - first + 1) * SECTOR, index + 1))
        if len(roots) != 1:
            raise ValueError("Expected exactly one Linux/ext2 root partition")
        offset, length, partition = roots[0]
        ext2_state(path, offset, length)
        return dict(kind="gpt-ext2", offset=offset, length=length, partition=partition, image_bytes=size)


def run_fsck(path, repair=False):
    result = subprocess.run(["e2fsck", "-f", "-y" if repair else "-n", str(path)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env=dict(os.environ, LC_ALL="C"))
    return result.returncode, result.stdout


def unfixed_errors(code, log):
    # e2fsck 1.47 can return zero for an unfixed superblock counter mismatch.
    # In read-only mode its declined repair questions are also a dirty result.
    return code != 0 or "? no" in log or "Filesystem still has errors" in log


def recover(image, output=None, input_format="raw", output_format="raw"):
    if input_format not in ("raw", "vdi") or output_format not in ("raw", "vdi"):
        raise ValueError("Image formats must be raw or vdi")
    image = Path(image).resolve(strict=True)
    if not image.is_file() or image == Path("/dev") or Path("/dev") in image.parents:
        raise ValueError("Input must be a regular OFFLINE image file, not a device")
    output = Path(output).absolute() if output is not None else None
    if output is not None:
        if output.exists() or output.is_symlink():
            raise ValueError("Output exists and will not be overwritten")
        parent = output.parent.resolve(strict=True)
        if not parent.is_dir() or parent == Path("/dev") or Path("/dev") in parent.parents:
            raise ValueError("Output must be a new file outside /dev")
        output = parent / output.name
        if output_format == "raw" and output.suffix.lower() == ".vdi":
            raise ValueError("Select --output-format vdi for a .vdi destination")
    if not shutil.which("e2fsck"):
        raise ValueError("Missing e2fsck (Ubuntu: sudo apt install e2fsprogs)")
    if (input_format == "vdi" or output_format == "vdi") and not shutil.which("qemu-img"):
        raise ValueError("Missing qemu-img (Ubuntu: sudo apt install qemu-utils)")
    before = digest(image)
    with tempfile.TemporaryDirectory(prefix=".serenity-recovery-", dir=output.parent if output else None) as directory:
        work = Path(directory)
        raw = work / "disk.img"
        if input_format == "vdi":
            subprocess.run(["qemu-img", "convert", "-f", "vdi", "-O", "raw", str(image), str(raw)], check=True, capture_output=True)
        else:
            shutil.copyfile(image, raw)
        selected = layout(raw)
        root = work / "root.ext2"
        root.touch()
        copy_range(raw, root, selected["offset"], 0, selected["length"])
        state_before = ext2_state(root, 0, selected["length"])
        check_before, before_log = run_fsck(root)
        if check_before not in (0, 4):
            raise RuntimeError(f"Filesystem check failed ({check_before}): {before_log}")
        needs_repair = unfixed_errors(check_before, before_log) or state_before != 1
        report = dict(source=str(image), source_sha256=before, layout=selected, check_before=check_before,
                      state_before=state_before, needs_repair=needs_repair, check_log=before_log)
        if output is None:
            if digest(image) != before:
                raise RuntimeError("Source changed during inspection; shut down its VM first")
            report["status"] = "needs-repair" if needs_repair else "clean"
            return report
        if needs_repair:
            code, repair_log = run_fsck(root, repair=True)
            report.update(repair_exit=code, repair_log=repair_log)
            if code not in (0, 1, 2, 3):
                raise RuntimeError(f"Copy could not be repaired ({code}): {repair_log}")
        check_after, after_log = run_fsck(root)
        state_after = ext2_state(root, 0, selected["length"])
        if unfixed_errors(check_after, after_log) or state_after != 1:
            raise RuntimeError(f"Repaired copy is not clean: {after_log}")
        # Only filesystem bytes are replaced. GPT, EFI and other partitions
        # retain their original contents and identities.
        prefix = digest(raw, 0, selected["offset"])
        end = selected["offset"] + selected["length"]
        suffix = digest(raw, end, selected["image_bytes"] - end)
        copy_range(root, raw, 0, selected["offset"], selected["length"])
        if digest(raw, 0, selected["offset"]) != prefix or digest(raw, end, selected["image_bytes"] - end) != suffix:
            raise RuntimeError("Non-filesystem bytes unexpectedly changed")
        staged = raw
        if output_format == "vdi":
            staged = work / "disk.vdi"
            subprocess.run(["qemu-img", "convert", "-f", "raw", "-O", "vdi", str(raw), str(staged)], check=True, capture_output=True)
            subprocess.run(["qemu-img", "compare", "-f", "raw", "-F", "vdi", str(raw), str(staged)], check=True, capture_output=True)
        output_hash = digest(staged)
        if digest(image) != before:
            raise RuntimeError("Source changed during recovery; no output published")
        os.chmod(staged, 0o600)
        with staged.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(staged, output)  # Atomic no-overwrite publication, including races.
        report.update(status="recovered-copy" if needs_repair else "clean-copy", output=str(output),
                      output_sha256=output_hash, check_after=check_after, state_after=state_after,
                      non_filesystem_bytes_unchanged=True, original_unchanged=True)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="NEW recovered image; omit to inspect only")
    parser.add_argument("--input-format", choices=("raw", "vdi"), default="raw")
    parser.add_argument("--output-format", choices=("raw", "vdi"), default="raw")
    args = parser.parse_args()
    try:
        report = recover(args.image, args.output, args.input_format, args.output_format)
        print(json.dumps(report, indent=2))
        return 1 if report["status"] == "needs-repair" else 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Recovery failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
