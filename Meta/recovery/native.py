#!/usr/bin/python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Read-only diagnosis and blank-disk clone recovery in a RAM-only Linux rescue."""

import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import time
import zlib

image_module = Path(__file__).with_name("image.py")
if not image_module.exists():
    image_module = Path(__file__).resolve().parent.parent / "recover-serenity-image.py"
spec = importlib.util.spec_from_file_location("recovery", image_module)
image = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image)
BLKROSET = 0x125D
BLKRRPART = 0x125F
CHUNK = 4 * 1024 * 1024


def devices():
    found = {}
    for entry in Path("/sys/class/block").iterdir():
        name = entry.name
        if not re.fullmatch(r"(?:sd[a-z]+|vd[a-z]+|nvme\d+n\d+)", name) or (entry / "partition").exists():
            continue
        def text(relative, default=""):
            try:
                return (entry / relative).read_text().strip()
            except OSError:
                return default
        found[name] = dict(name=name, path=f"/dev/{name}", bytes=int(text("size", "0")) * 512,
                           sector=int(text("queue/logical_block_size", "0")), readonly=text("ro") == "1",
                           identity=" ".join(filter(None, (text("device/model"), text("device/serial")))))
    return found


def require_disk(name, writable=False):
    marker = Path("/etc/serenity-recovery-media")
    if not marker.is_file() or marker.read_text().strip() != "Serenity Recovery v1":
        raise ValueError("Device operations are only supported inside the built recovery environment")
    disk = devices().get(name)
    if not disk or not stat.S_ISBLK(os.stat(disk["path"]).st_mode) or disk["sector"] != 512:
        raise ValueError("Choose a listed whole disk with 512-byte logical sectors")
    if writable and disk["readonly"]:
        raise ValueError("Destination is read-only")
    # No mounted disk or child partition may participate in recovery.
    family = {Path(f"/sys/class/block/{name}/dev").read_text().strip()}
    for child in Path(f"/sys/class/block/{name}").iterdir():
        if (child / "partition").exists():
            family.add((child / "dev").read_text().strip())
    if any(line.split()[2] in family for line in Path("/proc/self/mountinfo").read_text().splitlines()):
        raise ValueError("Disk or one of its partitions is mounted")
    return disk


def partition_path(disk, layout):
    if layout["kind"] != "gpt-ext2":
        raise ValueError("Native recovery currently requires GPT with one ext2 root partition")
    number = layout["partition"]
    name = disk["name"] + ("p" if disk["name"][-1].isdigit() else "") + str(number)
    node = Path("/dev") / name
    sysfs = Path("/sys/class/block") / name
    if (not node.exists() or int((sysfs / "start").read_text()) * 512 != layout["offset"]
            or int((sysfs / "size").read_text()) * 512 != layout["length"]):
        raise ValueError("Kernel partition geometry does not match the validated GPT")
    metadata = node.stat()
    if (not stat.S_ISBLK(metadata.st_mode)
            or f"{os.major(metadata.st_rdev)}:{os.minor(metadata.st_rdev)}" != (sysfs / "dev").read_text().strip()):
        raise ValueError("Partition node identity does not match sysfs")
    return node


def make_source_readonly(disk, partition):
    for path in (Path(disk["path"]), partition):
        with path.open("rb", buffering=0) as stream:
            fcntl.ioctl(stream.fileno(), BLKROSET, struct.pack("I", 1))


def inspect(name):
    disk = require_disk(name)
    layout = image.layout(Path(disk["path"]), disk["bytes"])
    partition = partition_path(disk, layout)
    make_source_readonly(disk, partition)
    code, log = image.run_fsck(partition)
    print(log, flush=True)
    if code not in (0, 4):
        raise RuntimeError(f"Filesystem check could not finish: {code}")
    dirty = image.unfixed_errors(code, log) or image.ext2_state(partition, 0, layout["length"]) != 1
    print("CHECK_RESULT " + json.dumps(dict(disk=name, needs_repair=dirty, source_readonly=True)), flush=True)
    return disk, layout


def read_exact(fd, size):
    data = bytearray()
    while len(data) < size:
        block = os.read(fd, size - len(data))
        if not block:
            raise OSError("Unexpected end of device")
        data.extend(block)
    return bytes(data)


def write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError("Short device write")
        view = view[count:]


def hash_fd(fd, size):
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while size:
        block = read_exact(fd, min(size, CHUNK))
        digest.update(block)
        size -= len(block)
    return digest.hexdigest()


def clone_to_blank(source_fd, target_fd, source_bytes, target_bytes, progress=print):
    # This lower-level routine also works on disposable regular-file fixtures
    # in host tests. The interactive entry point only accepts real block nodes.
    source_stat, target_stat = os.fstat(source_fd), os.fstat(target_fd)
    if (source_stat.st_dev, source_stat.st_ino) == (target_stat.st_dev, target_stat.st_ino):
        raise ValueError("Source and destination are the same device")
    if (stat.S_ISBLK(source_stat.st_mode) and stat.S_ISBLK(target_stat.st_mode)
            and source_stat.st_rdev == target_stat.st_rdev):
        raise ValueError("Source and destination alias the same device")
    if source_bytes <= 0 or source_bytes % 512 or target_bytes < source_bytes or target_bytes % 512:
        raise ValueError("Destination must be sector-aligned and at least as large as the source")
    layout = image.layout(Path(f"/proc/self/fd/{source_fd}"), source_bytes)
    if layout["kind"] != "gpt-ext2":
        raise ValueError("Only a supported GPT/ext2 source can be cloned")
    os.lseek(target_fd, 0, os.SEEK_SET)
    checked = 0
    last_percent = -1
    while checked < target_bytes:
        data = read_exact(target_fd, min(CHUNK, target_bytes - checked))
        if data.count(0) != len(data):
            raise ValueError("Destination contains data; nothing was written")
        checked += len(data)
        percent = checked * 100 // target_bytes
        if percent != last_percent:
            progress(f"Checking empty destination: {percent}%")
            last_percent = percent
    os.lseek(source_fd, 0, os.SEEK_SET)
    os.lseek(target_fd, 0, os.SEEK_SET)
    copied = 0
    checksum = hashlib.sha256()
    last_percent = -1
    while copied < source_bytes:
        data = read_exact(source_fd, min(CHUNK, source_bytes - copied))
        checksum.update(data)
        write_all(target_fd, data)
        copied += len(data)
        percent = copied * 100 // source_bytes
        if percent != last_percent:
            progress(f"Copying source: {percent}%")
            last_percent = percent
    os.fsync(target_fd)
    if hash_fd(target_fd, source_bytes) != checksum.hexdigest() or hash_fd(source_fd, source_bytes) != checksum.hexdigest():
        raise RuntimeError("Clone verification failed or source changed")
    # Relocate backup GPT when the destination is larger. Root and EFI
    # partition sizes and identities stay unchanged.
    old_last, new_last = source_bytes // 512 - 1, target_bytes // 512 - 1
    if new_last != old_last:
        os.lseek(target_fd, 512, os.SEEK_SET)
        primary = bytearray(read_exact(target_fd, 512))
        os.lseek(target_fd, 1024, os.SEEK_SET)
        entries = read_exact(target_fd, 16384)
        struct.pack_into("<Q", primary, 32, new_last)
        struct.pack_into("<Q", primary, 48, new_last - 33)
        struct.pack_into("<I", primary, 16, 0)
        struct.pack_into("<I", primary, 16, zlib.crc32(primary[:92]))
        backup = bytearray(primary)
        struct.pack_into("<Q", backup, 24, new_last)
        struct.pack_into("<Q", backup, 32, 1)
        struct.pack_into("<Q", backup, 72, new_last - 32)
        struct.pack_into("<I", backup, 16, 0)
        struct.pack_into("<I", backup, 16, zlib.crc32(backup[:92]))
        for offset, data in (((old_last - 32) * 512, bytes(33 * 512)),
                             ((new_last - 32) * 512, entries), (new_last * 512, backup), (512, primary)):
            os.lseek(target_fd, offset, os.SEEK_SET)
            write_all(target_fd, data)
        # The protective MBR must cover the new complete disk as well.
        os.lseek(target_fd, 0, os.SEEK_SET)
        mbr = bytearray(read_exact(target_fd, 512))
        struct.pack_into("<I", mbr, 458, min(new_last, 0xFFFFFFFF))
        os.lseek(target_fd, 0, os.SEEK_SET)
        write_all(target_fd, mbr)
        os.fsync(target_fd)
    copied_layout = image.layout(Path(f"/proc/self/fd/{target_fd}"), target_bytes)
    if any(copied_layout[key] != layout[key] for key in ("offset", "length", "partition")):
        raise RuntimeError("Destination root geometry unexpectedly changed")
    return dict(source_sha256=checksum.hexdigest(), clone_verified=True, layout=copied_layout)


def recover(source_name, target_name, confirmation=input):
    source = require_disk(source_name)
    target = require_disk(target_name, writable=True)
    if source_name == target_name:
        raise ValueError("Choose two different disks")
    layout = image.layout(Path(source["path"]), source["bytes"])
    source_partition = partition_path(source, layout)
    if target["bytes"] < source["bytes"]:
        raise ValueError("Destination is smaller than source")
    phrase = f"RECOVER {source_name} TO {target_name}"
    print(f"Source (preserved): {source_name}, {source['bytes']} bytes, {source['identity']}")
    print(f"Destination (must be entirely empty): {target_name}, {target['bytes']} bytes, {target['identity']}")
    print(f"Type exactly: {phrase}", flush=True)
    if confirmation("CONFIRM> ").strip() != phrase:
        raise ValueError("Cancelled; nothing was written")
    # Recheck mounted/readonly state after the user has selected the devices.
    current_source, current_target = require_disk(source_name), require_disk(target_name, writable=True)
    for previous, current in ((source, current_source), (target, current_target)):
        if any(previous[key] != current[key] for key in ("bytes", "sector", "identity", "path")):
            raise ValueError("Device identity changed during confirmation; nothing was written")
    source, target = current_source, current_target
    layout = image.layout(Path(source["path"]), source["bytes"])
    source_partition = partition_path(source, layout)
    make_source_readonly(source, source_partition)
    source_fd = os.open(source["path"], os.O_RDONLY | os.O_EXCL)
    try:
        target_fd = os.open(target["path"], os.O_RDWR | os.O_EXCL)
        try:
            report = clone_to_blank(source_fd, target_fd, source["bytes"], target["bytes"], lambda message: print(message, flush=True))
            fcntl.ioctl(target_fd, BLKRRPART)
        finally:
            os.close(target_fd)
    finally:
        os.close(source_fd)
    subprocess.run(["/bin/busybox", "mdev", "-s"], check=True)
    destination_partition = partition_path(target, report["layout"])
    code, log = image.run_fsck(destination_partition, repair=True)
    print(log, flush=True)
    if code not in (0, 1, 2, 3):
        raise RuntimeError("Clone was copied, but filesystem repair failed")
    code, log = image.run_fsck(destination_partition)
    print(log, flush=True)
    if image.unfixed_errors(code, log) or image.ext2_state(destination_partition, 0, report["layout"]["length"]) != 1:
        raise RuntimeError("Repaired clone did not pass the final filesystem check")
    with destination_partition.open("rb", buffering=0) as stream:
        os.fsync(stream.fileno())
    report.update(source=source_name, destination=target_name, filesystem_clean=True, source_readonly=True)
    Path("/tmp/last-recovery.json").write_text(json.dumps(report, indent=2) + "\n")
    print("RECOVERY_COMPLETE " + json.dumps(report), flush=True)
    print("Power off. Disconnect the original disk before booting the recovered copy (partition identities are cloned).", flush=True)


def main():
    marker = Path("/etc/serenity-recovery-media")
    if not marker.is_file() or marker.read_text().strip() != "Serenity Recovery v1":
        raise SystemExit("Boot the recovery medium; do not run this device menu on the host")
    print("\nSERENITY RECOVERY - UEFI / RAM-only Linux rescue", flush=True)
    print("No disk is mounted automatically. Inspection never repairs the source.")
    print("Commands: list | check DISK | recover SOURCE EMPTY_TARGET | poweroff | reboot")
    # A readiness banner is useful on a physical serial console even when the
    # interactive menu is on the screen. It accepts no alternate command path.
    try:
        serial = os.open("/dev/ttyS0", os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            os.write(serial, b"SERENITY_RECOVERY_READY\n")
        finally:
            os.close(serial)
    except OSError:
        pass
    while True:
        try:
            words = input("RESCUE> ").split()
            if words == ["list"]:
                for disk in devices().values():
                    print(json.dumps(disk), flush=True)
            elif len(words) == 2 and words[0] == "check":
                inspect(words[1])
            elif len(words) == 3 and words[0] == "recover":
                recover(words[1], words[2])
            elif words in (["poweroff"], ["reboot"]):
                subprocess.run(["/bin/busybox", "sync"], check=True)
                subprocess.run(["/bin/busybox", words[0], "-f"], check=True)
            elif words:
                print("Unknown command. Use list, check DISK, recover SOURCE EMPTY_TARGET, poweroff or reboot.")
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
            print(f"RECOVERY_ERROR: {error}", flush=True)
        except (EOFError, KeyboardInterrupt):
            print("Use poweroff to exit safely.", flush=True)
            time.sleep(1)


if __name__ == "__main__":
    main()
