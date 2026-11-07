#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Exercise the native installation backend in an isolated x86_64 QEMU guest.

Only copies and freshly allocated fixture files are written. The small GPT
fixture tests disk copying and relocation; it is not a bootable OS image.
Artifacts are retained in a new temporary directory for diagnosis.
"""

import hashlib
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import uuid
import zlib

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("installer", REPO / "Meta/install-serenity.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def command(*args, accepted=(0,)):
    result = subprocess.run([str(arg) for arg in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode not in accepted:
        raise RuntimeError(result.stdout.decode(errors="replace"))


def inject(root, source, destination):
    def quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'
    command("debugfs", "-w", "-R", f"rm {destination}", root)
    command("debugfs", "-w", "-R", f"write {quote(source)} {destination}", root)
    command("debugfs", "-w", "-R", f"set_inode_field {destination} mode 0100755", root)


def fixture(path):
    size = 8 * 1024 * 1024
    last = size // 512 - 1
    entries = bytearray(16384)
    struct.pack_into("<16s16sQQQ72s", entries, 0, installer.LINUX_TYPE.bytes_le,
                     uuid.uuid4().bytes_le, 2048, last - 33, 0, b"fixture")
    disk_uuid = str(uuid.uuid4())
    crc = zlib.crc32(entries)
    mbr = bytearray(512)
    mbr[450] = 0xEE
    struct.pack_into("<II", mbr, 454, 1, last)
    mbr[510:] = b"\x55\xaa"
    with path.open("w+b") as stream:
        stream.truncate(size)
        for offset, data in ((0, mbr), (512, installer.gpt_header(1, last, 2, last, disk_uuid, crc)),
                             (1024, entries), ((last - 32) * 512, entries),
                             (last * 512, installer.gpt_header(last, 1, last - 32, last, disk_uuid, crc)),
                             (2048 * 512, bytes(range(256)) * 4096)):
            stream.seek(offset)
            stream.write(data)


def main():
    for tool in ("qemu-system-x86_64", "debugfs", "e2fsck"):
        if not shutil.which(tool):
            raise RuntimeError(f"Missing {tool}")
    work = Path(tempfile.mkdtemp(prefix="serenity-native-install-test-"))
    print(f"Artifacts: {work}", flush=True)
    root = work / "root.ext2"
    shutil.copyfile(REPO / "Build/x86_64/_disk_image", root)
    command("e2fsck", "-f", "-y", root, accepted=(0, 1))
    image = work / "fixture.img"
    fixture(image)
    target = work / "target.img"
    with target.open("wb") as stream:
        stream.truncate(16 * 1024 * 1024)
    inject(root, REPO / "Build/x86_64/Userland/Applications/Installer/Installer", "/bin/Installer")
    inject(root, image, "/tmp/install-fixture.img")
    devices = work / "devices.debugfs"
    devices.write_text("cd /tmp\nmknod install-target b 3 1\nmknod install-root b 3 0\n")
    command("debugfs", "-w", "-f", devices, root)
    command("e2fsck", "-f", "-n", root)
    args = ["qemu-system-x86_64", "-machine", "q35", "-accel", "tcg", "-cpu", "max,-x2apic",
            "-m", "2G", "-kernel", str(REPO / "Build/x86_64/Kernel/Kernel"),
            "-append", "",
            "-drive", f"file={root},if=none,format=raw,id=root,snapshot=on",
            "-device", "ich9-ahci,id=ahci", "-device", "ide-hd,drive=root,bus=ahci.0",
            "-drive", f"file={target},if=none,format=raw,id=target", "-device", "ide-hd,drive=target,bus=ahci.1",
            "-display", "none", "-serial", f"file:{work}/serial.log", "-debugcon", f"file:{work}/debug.log",
            "-monitor", "none", "-nic", "none", "-no-reboot"]
    # Run the backend directly as init, avoiding shell/GUI services. An init exit
    # deliberately causes a kernel panic; only the expected init exit is accepted.
    for case, device, expected_exit in (("root-rejected", "install-root", 1),
                                        ("copy", "install-target", 0),
                                        ("nonempty-rejected", "install-target", 1)):
        target_before = hashlib.sha256(target.read_bytes()).digest()
        case_args = list(args)
        case_args[case_args.index("-append") + 1] = (
            "root=ahci0:0:0 serial_debug disable_kaslr init=/bin/Installer "
            f"init_args=--image;/tmp/install-fixture.img;--target;/tmp/{device};--confirm-empty-disk")
        serial = work / f"{case}-serial.log"
        debug = work / f"{case}-debug.log"
        case_args[case_args.index("-serial") + 1] = f"file:{serial}"
        case_args[case_args.index("-debugcon") + 1] = f"file:{debug}"
        with (work / f"{case}-qemu.log").open("w") as log:
            guest = subprocess.Popen(case_args, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    logs = debug.read_text(errors="replace") if debug.exists() else ""
                    if "KERNEL PANIC" in logs or guest.poll() is not None:
                        break
                    time.sleep(0.5)
                else:
                    raise RuntimeError(f"Guest timed out; see {work}")
            finally:
                guest.terminate()
                try:
                    guest.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    guest.kill()
                    guest.wait()
        expected = f"Init process quit unexpectedly. Exit code: {expected_exit}"
        if expected not in logs:
            raise RuntimeError(f"Unexpected guest result in {case}; see {debug}")
        if expected_exit and "This disk contains data. Nothing was written." not in logs:
            raise RuntimeError(f"Wrong rejection in {case}; see {debug}")
        if expected_exit and hashlib.sha256(target.read_bytes()).digest() != target_before:
            raise RuntimeError(f"Rejected installation modified the destination in {case}")
        print(f"PASS: {case}", flush=True)
    original = image.read_bytes()
    installed = target.read_bytes()
    assert installed[1024:len(original)] == original[1024:]
    last = len(installed) // 512 - 1
    for lba in (1, last):
        header = bytearray(installed[lba * 512:(lba + 1) * 512])
        checksum, = struct.unpack_from("<I", header, 16)
        struct.pack_into("<I", header, 16, 0)
        assert zlib.crc32(header[:92]) == checksum
        assert struct.unpack_from("<Q", header, 24)[0] == lba
        assert struct.unpack_from("<Q", header, 32)[0] == (last if lba == 1 else 1)
        assert struct.unpack_from("<Q", header, 48)[0] == last - 33
    assert installed[(last - 32) * 512:last * 512] == original[1024:17408]
    assert struct.unpack_from("<I", installed, 458)[0] == last
    print("PASS: root disk rejected, native copy verified, occupied disk rejected, GPT relocated and checked.")


if __name__ == "__main__":
    main()
