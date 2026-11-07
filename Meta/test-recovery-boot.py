#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Boot the recovery RAM environment against disposable SATA/NVMe fixtures.

No physical disks or existing VM configurations are accessed. Exercises the
interactive serial menu; there is no unattended recovery backdoor in the media.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import select
import shutil
import socket
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("fixtures", REPO / "Meta/test-recover-serenity-image.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class Serial:
    def __init__(self, connection, log):
        self.connection = connection
        self.log = log
        self.data = bytearray()
        self.position = 0

    def until(self, marker, timeout=120):
        encoded = marker.encode()
        deadline = time.monotonic() + timeout
        while encoded not in self.data[self.position:]:
            if time.monotonic() > deadline:
                raise RuntimeError(f"Timed out waiting for {marker}; see serial.log")
            if select.select([self.connection], [], [], 0.2)[0]:
                block = self.connection.recv(65536)
                if not block:
                    raise RuntimeError(f"Console closed before {marker}")
                self.data.extend(block)
                self.log.write(block)
                self.log.flush()
        end = self.data.index(encoded, self.position) + len(encoded)
        output = bytes(self.data[self.position:end]).decode(errors="replace")
        self.position = end
        return output

    def send(self, command):
        self.connection.sendall(command.encode() + b"\n")


def run(media, source_image=None, nvme=False):
    work = Path(tempfile.mkdtemp(prefix="native-recovery-boot-", dir=REPO / "Build"))
    print(f"Artifacts: {work}", flush=True)
    fixture = None
    if source_image:
        source = work / "source.img"
        shutil.copyfile(source_image, source)
    else:
        fixture = fixtures.RecoveryTests()
        fixture.setUp()
        source = work / "source.img"
        shutil.copyfile(fixture.gpt(), source)
    selected = fixtures.recovery.layout(source)
    with source.open("r+b") as stream:
        stream.seek(selected["offset"] + 1024 + 58)
        stream.write(b"\x02\x00")
    source_hash = fixtures.recovery.digest(source)
    target = work / "target.img"
    occupied = work / "occupied.img"
    target_size = source.stat().st_size + 2 * 1024 * 1024
    for path in (target, occupied):
        with path.open("wb") as stream:
            stream.truncate(target_size)
    with occupied.open("r+b") as stream:
        marker = b"preserve"
        stream.seek(target_size - len(marker))
        stream.write(marker)
    occupied_hash = fixtures.recovery.digest(occupied)
    for name in ("vmlinuz", "initrd.gz"):
        subprocess.run(["mcopy", "-i", f"{media}@@1048576", f"::/boot/{name}", str(work / name)], check=True)
    console = work / "console.sock"
    cmd = ["qemu-system-x86_64", "-machine", "q35", "-accel", "tcg", "-cpu", "max", "-m", "768M",
           "-kernel", str(work / "vmlinuz"), "-initrd", str(work / "initrd.gz"),
           "-append", "rdinit=/init console=ttyS0,115200 quiet panic=0",
           "-drive", f"file={media},if=none,id=media,format=raw,snapshot=on",
           "-drive", f"file={source},if=none,id=source,format=raw",
           "-drive", f"file={target},if=none,id=target,format=raw",
           "-drive", f"file={occupied},if=none,id=occupied,format=raw",
           "-device", "ich9-ahci,id=ahci", "-device", "ide-hd,drive=media,bus=ahci.0",
           "-device", "ide-hd,drive=source,bus=ahci.1",
           "-device", "ide-hd,drive=occupied,bus=ahci.3",
           "-display", "none", "-monitor", "none", "-nic", "none", "-no-reboot",
           "-chardev", f"socket,id=console,path={console},server=on,wait=off", "-serial", "chardev:console"]
    target_name = "nvme0n1" if nvme else "sdc"
    occupied_name = "sdc" if nvme else "sdd"
    cmd += ["-device", "nvme,drive=target,serial=SERENITY-RECOVERY-TEST"] if nvme else ["-device", "ide-hd,drive=target,bus=ahci.2"]
    (work / "command.json").write_text(json.dumps(cmd, indent=2) + "\n")
    try:
        with (work / "qemu.log").open("w") as qemu_log, (work / "serial.log").open("wb") as serial_log:
            guest = subprocess.Popen(cmd, stdout=qemu_log, stderr=qemu_log)
            try:
                deadline = time.monotonic() + 30
                while not console.exists():
                    if guest.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError(f"QEMU did not start; see {work}")
                    time.sleep(0.1)
                with socket.socket(socket.AF_UNIX) as connection:
                    connection.connect(str(console))
                    terminal = Serial(connection, serial_log)
                    terminal.until("RESCUE> ")
                    terminal.send("list")
                    inventory = terminal.until("RESCUE> ")
                    if target_name not in inventory or "sdb" not in inventory:
                        raise RuntimeError(f"Expected disks missing: {inventory}")
                    terminal.send("check sdb")
                    diagnostic = terminal.until("RESCUE> ")
                    if '"needs_repair": true' not in diagnostic or '"source_readonly": true' not in diagnostic:
                        raise RuntimeError(f"Inspection did not detect unclean source: {diagnostic}")
                    terminal.send(f"recover sdb {target_name}")
                    terminal.until("CONFIRM> ")
                    terminal.send("CANCEL")
                    cancelled = terminal.until("RESCUE> ")
                    if "Cancelled; nothing was written" not in cancelled:
                        raise RuntimeError("Cancellation guard failed")
                    terminal.send(f"recover sdb {occupied_name}")
                    terminal.until("CONFIRM> ")
                    terminal.send(f"RECOVER sdb TO {occupied_name}")
                    refused = terminal.until("RESCUE> ")
                    if "Destination contains data; nothing was written" not in refused:
                        raise RuntimeError(f"Occupied target guard failed: {refused}")
                    terminal.send(f"recover sdb {target_name}")
                    terminal.until("CONFIRM> ")
                    terminal.send(f"RECOVER sdb TO {target_name}")
                    result = terminal.until("RESCUE> ", timeout=600)
                    if "RECOVERY_COMPLETE " not in result:
                        raise RuntimeError(f"Native recovery failed: {result[-4000:]}")
                    terminal.send("poweroff")
                    guest.wait(timeout=30)
            finally:
                if guest.poll() is None:
                    guest.terminate()
                    try:
                        guest.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        guest.kill()
                        guest.wait()
        assert fixtures.recovery.digest(source) == source_hash
        assert fixtures.recovery.digest(occupied) == occupied_hash
        result_layout = fixtures.recovery.layout(target)
        assert result_layout["image_bytes"] == target_size
        assert fixtures.recovery.ext2_state(target, result_layout["offset"], result_layout["length"]) == 1
        root = work / "verified-root.ext2"
        root.touch()
        fixtures.recovery.copy_range(target, root, result_layout["offset"], 0, result_layout["length"])
        code, log = fixtures.recovery.run_fsck(root)
        (work / "fsck-final.log").write_text(log)
        assert not fixtures.recovery.unfixed_errors(code, log)
        if fixture:
            output = work / "sentinel.bin"
            subprocess.run(["debugfs", "-R", f"dump /sentinel {output}", str(root)], check=True, capture_output=True)
            assert output.read_bytes() == b"\x7b" * 4096
        report = dict(source_unchanged=True, occupied_target_unchanged=True, cancellation_guard_passed=True,
                      native_inspection_passed=True, clone_and_repair_passed=True, gpt_relocated=True,
                      filesystem_clean=True, target_bus="nvme" if nvme else "sata", target=str(target))
        (work / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        print("PASS: native menu, read-only inspection, cancellation, occupied-disk rejection, clone, repair and GPT relocation", flush=True)
        return work
    finally:
        if fixture:
            fixture.doCleanups()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--source", type=Path, help="optional OFFLINE raw UEFI image; copied before testing")
    parser.add_argument("--nvme-target", action="store_true")
    args = parser.parse_args()
    run(args.media.resolve(strict=True), args.source.resolve(strict=True) if args.source else None, args.nvme_target)
