#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Boot V8.1 from virtual USB and verify read-only disk selection/planning.

Creates only new virtual fixtures. No host block devices, installation, existing
partitions or development disks are attached to the guest.
"""
import argparse
import binascii
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import zlib

REPO = Path(__file__).resolve().parent.parent


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_gpt(image):
    """Validate the on-disk GPT independently of the builder's Python module."""
    sectors = image.stat().st_size // 512
    assert image.stat().st_size % 512 == 0
    with image.open("rb") as stream:
        mbr = stream.read(512)
        assert mbr[510:] == b"\x55\xaa" and mbr[450] == 0xEE
        copies = []
        for current, other in [(1, sectors - 1), (sectors - 1, 1)]:
            stream.seek(current * 512)
            header = bytearray(stream.read(512))
            assert header[:8] == b"EFI PART" and struct.unpack_from("<I", header, 12)[0] == 92
            checksum = struct.unpack_from("<I", header, 16)[0]
            struct.pack_into("<I", header, 16, 0)
            assert zlib.crc32(header[:92]) == checksum
            assert struct.unpack_from("<QQ", header, 24) == (current, other)
            entries_lba, count, size, entries_crc = struct.unpack_from("<QIII", header, 72)
            assert count == 128 and size == 128
            stream.seek(entries_lba * 512)
            entries = stream.read(count * size)
            assert zlib.crc32(entries) == entries_crc
            copies.append(entries)
        assert copies[0] == copies[1]
        first_start, first_end = struct.unpack_from("<QQ", copies[0], 32)
        second_start, second_end = struct.unpack_from("<QQ", copies[0], 128 + 32)
        assert 34 <= first_start <= first_end < second_start <= second_end < sectors - 33


def screenshot(monitor, work):
    ppm = work / "screen.ppm"
    monitor.execute("screendump", {"filename": str(ppm)})
    magic, dimensions, maximum, pixels = ppm.read_bytes().split(b"\n", 3)
    width, height = map(int, dimensions.split())
    assert magic == b"P6" and maximum == b"255"

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data))

    (work / "screen.png").write_bytes(b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))))
        + chunk(b"IEND", b""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="NEW evidence/fixture directory")
    parser.add_argument("--usb", choices=["xhci", "uhci"], default="xhci")
    parser.add_argument("--verify-payload", action="store_true")
    args = parser.parse_args()
    image = args.image.resolve(strict=True)
    work = args.output.absolute()
    if not image.is_file() or work.exists() or work.is_symlink() or Path("/dev") in work.resolve().parents:
        raise ValueError("Regular image and new output directory outside /dev required")
    if any("," in str(path) for path in [image, work]):
        raise ValueError("QEMU drive paths cannot contain commas")
    work.mkdir(parents=True)
    shutil.copyfile("/usr/share/OVMF/OVMF_VARS_4M.fd", work / "vars.fd")
    target = work / "target.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(target), "8G"], check=True)
    guard = work / "protected.img"
    with guard.open("wb") as stream:
        stream.truncate(128 * 1024 * 1024)
        mbr = bytearray(512)
        struct.pack_into("<B3sB3sII", mbr, 446, 0, b"\0\x02\0", 0x83, b"\xff\xff\xff", 2048, 128 * 2048 - 2048)
        mbr[510:] = b"\x55\xaa"
        stream.seek(0)
        stream.write(mbr)
        stream.seek(1024 * 1024 + 4096)
        stream.write(b"PISCO_V81_EXISTING_DATA_MUST_SURVIVE\n")
    before = {str(path): sha(path) for path in [image, target, guard]}
    cmd = ["qemu-system-x86_64", "-machine", "q35", "-accel", "tcg", "-cpu", "max,-x2apic", "-m", "1G", "-smp", "1",
           "-display", "none", "-vga", "std", "-nic", "none",
           "-drive", "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd",
           "-drive", "if=pflash,format=raw,file=" + str(work / "vars.fd"),
           "-drive", "if=none,id=installer,format=raw,readonly=on,file=" + str(image),
           "-device", ("qemu-xhci,id=usb" if args.usb == "xhci" else "piix3-usb-uhci,id=usb"),
           "-device", "usb-storage,drive=installer,bus=usb.0,removable=on,bootindex=1,serial=PISCO_V81_SOURCE",
           "-device", "ich9-ahci,id=ahci", "-drive", "if=none,id=target,format=qcow2,snapshot=on,file=" + str(target),
           "-device", "ide-hd,drive=target,bus=ahci.0,serial=PISCO_V81_TARGET",
           "-drive", "if=none,id=guard,format=raw,snapshot=on,file=" + str(guard),
           "-device", "ide-hd,drive=guard,bus=ahci.1,serial=PISCO_V81_PROTECTED",
           "-chardev", "socket,id=console,path=" + str(work / "console.sock") + ",server=on,wait=off,logfile=" + str(work / "serial.log"),
           "-serial", "chardev:console", "-qmp", "unix:" + str(work / "qmp.sock") + ",server=on,wait=off"]
    (work / "command.json").write_text(json.dumps(cmd, indent=2) + "\n")
    spec = importlib.util.spec_from_file_location("qmp", REPO / "Meta/test-run-process-status.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class InstallerMonitor(module.Monitor):
        def __init__(self, path):
            self.socket = socket.socket(socket.AF_UNIX)
            # Firmware startup under TCG and host image I/O can exceed the
            # shared helper's five-second timeout. Keep this override local.
            self.socket.settimeout(30)
            self.socket.connect(str(path))
            self.stream = self.socket.makefile("rwb", buffering=0)
            self.stream.readline()
            self.execute("qmp_capabilities")
    monitor = None
    console = socket.socket(socket.AF_UNIX)
    results = []

    def drain_console():
        # A socket chardev applies backpressure. Keep consuming output even
        # though QEMU's logfile is the authoritative test transcript.
        try:
            while console.recv(65536):
                pass
        except OSError:
            pass

    def log():
        return (work / "serial.log").read_text(errors="replace") if (work / "serial.log").exists() else ""

    def wait(marker, offset=0, timeout=100):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = log()[offset:]
            if marker in text:
                return
            if any(value in text for value in ["V81 FATAL", "Kernel panic", "end Kernel panic"]):
                raise RuntimeError(text[-3000:])
            time.sleep(0.1)
        raise TimeoutError(marker + "\n" + log()[-2500:])

    def record(case):
        results.append(dict(case=case, passed=True))
        (work / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(case, "PASS", flush=True)

    check_gpt(image)
    record("primary-and-backup-gpt-crc-and-partition-ranges")
    with (work / "qemu.log").open("w") as output:
        proc = subprocess.Popen(cmd, stdout=output, stderr=output)
        try:
            deadline = time.monotonic() + 15
            while not (work / "console.sock").exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            console.connect(str(work / "console.sock"))
            threading.Thread(target=drain_console, daemon=True).start()
            monitor = InstallerMonitor(work / "qmp.sock")
            wait("V81 READY:")
            assert "Linux version" in log() and "writer installed" in log()
            source = re.findall(r"^SOURCE\|(/dev/\w+)\r?$", log(), re.M)[-1]
            lines = re.findall(r"^DISK\|([^\r\n]+)", log(), re.M)
            candidates = [line for line in lines if "candidate-unverified" in line]
            assert len(candidates) == 1, lines
            target_name = candidates[0].split("|")[0]
            assert "bytes=8589934592" in candidates[0] and "PISCO_V81_TARGET" in candidates[0]
            assert target_name != source
            assert any(line.startswith(source + "|") and "transport=usb" in line and "installer-source" in line for line in lines)
            protected = [line.split("|")[0] for line in lines if "PISCO_V81_PROTECTED" in line]
            assert len(protected) == 1 and any("existing-partitions" in line and protected[0] in line for line in lines)
            record("autonomous-usb-linux-boot-and-disk-identities")
            time.sleep(0.5)
            for disk in [source, protected[0], source + "1"]:
                offset = len(log())
                console.sendall(("pisco-plan " + disk + "\n").encode())
                wait("V81 REJECTED", offset)
            record("reject-source-partitions-and-existing-data")
            offset = len(log())
            console.sendall(("pisco-plan " + target_name + "\n").encode())
            wait("Type exactly:", offset)
            console.sendall(b"NO\n")
            wait("V81 PLAN CANCELLED", offset)
            record("explicit-selection-and-cancellation")
            offset = len(log())
            console.sendall(("pisco-plan " + target_name + "\n").encode())
            wait("Type exactly:", offset)
            console.sendall(("PLAN " + target_name + "\n").encode())
            wait("V81 PLAN ONLY target=" + target_name, offset)
            record("confirmed-plan-without-disk-writer")
            if args.verify_payload:
                offset = len(log())
                console.sendall(b"pisco-verify\n")
                wait("V81 PAYLOAD VERIFIED", offset, timeout=240)
                record("embedded-native-payload-checksums")
            screenshot(monitor, work)
            console.sendall(b"poweroff -f\n")
            proc.wait(timeout=20)
            assert all(sha(Path(path)) == digest for path, digest in before.items())
            record("all-source-target-and-protected-hashes-unchanged")
            (work / "complete.json").write_text(json.dumps(dict(passed=True, usb=args.usb, cases=len(results),
                source=source, target=target_name, protected=protected[0], installation_tested=False), indent=2) + "\n")
        finally:
            console.close()
            if proc.poll() is None:
                if monitor:
                    screenshot(monitor, work)
                    monitor.execute("quit")
                else:
                    proc.terminate()
                proc.wait(timeout=10)
            if monitor:
                monitor.close()


if __name__ == "__main__":
    main()
