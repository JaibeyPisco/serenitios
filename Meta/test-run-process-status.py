#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Exercise Run's child outcomes in a disposable 1024x768 graphical QEMU guest.

Requires an offline root image with the normal anon desktop/Taskbar session and
the built Run binary. Log assertions are automatic; review the saved screenshots
to verify messages, editable commands, and absence of duplicate modal dialogs.
Only the temporary copy is edited, and QEMU writes into a discarded snapshot.
"""

import argparse
import binascii
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import zlib

REPO = Path(__file__).resolve().parent.parent


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Monitor:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(5)
        self.socket.connect(str(path))
        self.stream = self.socket.makefile("rwb", buffering=0)
        self.stream.readline()
        self.execute("qmp_capabilities")

    def execute(self, command, arguments=None):
        self.stream.write((json.dumps(dict(execute=command, arguments=arguments or {})) + "\n").encode())
        while True:
            result = json.loads(self.stream.readline())
            if "error" in result:
                raise RuntimeError(result["error"])
            if "return" in result:
                return result["return"]

    def key(self, key):
        self.execute("human-monitor-command", {"command-line": f"sendkey {key} 50"})
        time.sleep(0.15)

    def type(self, value):
        symbols = {" ": "spc", "/": "slash", "-": "minus", "_": "shift-minus", "$": "shift-4", ".": "dot"}
        for char in value:
            self.key(symbols.get(char, f"shift-{char.lower()}" if char.isupper() else char))

    def click(self, x, y):
        self.execute("input-send-event", {"events": [
            {"type": "abs", "data": {"axis": "x", "value": int(x * 32767 / 1024)}},
            {"type": "abs", "data": {"axis": "y", "value": int(y * 32767 / 768)}}]})
        self.execute("input-send-event", {"events": [{"type": "btn", "data": {"down": True, "button": "left"}}]})
        time.sleep(0.15)
        self.execute("input-send-event", {"events": [{"type": "btn", "data": {"down": False, "button": "left"}}]})
        time.sleep(0.5)

    def screenshot(self, path):
        ppm = path.with_suffix(".ppm")
        self.execute("screendump", {"filename": str(ppm)})
        magic, geometry, maximum, pixels = ppm.read_bytes().split(b"\n", 3)
        assert magic == b"P6" and maximum == b"255"
        width, height = map(int, geometry.split())
        assert (width, height) == (1024, 768), "This UI fixture requires a 1024x768 desktop"
        assert len(pixels) == width * height * 3

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data))

        scanlines = b"".join(b"\0" + pixels[row * width * 3:(row + 1) * width * 3] for row in range(height))
        path.write_bytes(b"\x89PNG\r\n\x1a\n"
                         + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                         + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))

    def close(self):
        self.stream.close()
        self.socket.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-image", required=True, type=Path)
    parser.add_argument("--build", type=Path, default=REPO / "Build/x86_64")
    parser.add_argument("--output", type=Path, required=True, help="New artifact directory; must not exist")
    parser.add_argument("--timeout", type=float, default=45)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    source = args.root_image.resolve(strict=True)
    if not source.is_file():
        parser.error("--root-image must be an offline regular file")
    build = args.build.resolve(strict=True)
    binary = build / "Userland/Applications/Run/Run"
    if not binary.is_file():
        parser.error("Build Run first")
    work = args.output.resolve()
    work.mkdir(parents=True, exist_ok=False)
    print(f"Artifacts: {work}", flush=True)
    original_hash = digest(source)
    spec = importlib.util.spec_from_file_location("crash", REPO / "Meta/test-ext2-crash-consistency.py")
    crash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(crash)
    root = work / "root.ext2"
    shutil.copyfile(source, root)
    crash.command(["e2fsck", "-f", "-n", root], work / "fsck-before.log")
    crash.install(root, binary, "/bin/Run", work / "install.log")
    installed = work / "installed-Run"
    crash.command(["debugfs", "-R", f'dump /bin/Run "{installed}"', root], work / "dump.log")
    assert digest(installed) == digest(binary)
    crash.command(["e2fsck", "-f", "-n", root], work / "fsck-installed.log")
    root_hash = digest(root)
    serial = work / "serial.log"
    results = []
    provenance = dict(source=str(source), source_sha256=original_hash, run_sha256=digest(binary), kernel_sha256=digest(build / "Kernel/Kernel"))
    (work / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")

    with tempfile.TemporaryDirectory(prefix="run-status-") as directory:
        qmp = Path(directory) / "qmp.sock"
        command = ["qemu-system-x86_64", "-machine", "q35", "-accel", "tcg", "-cpu", "max,-x2apic", "-m", "1G", "-smp", "1",
                   "-kernel", str(build / "Kernel/Kernel"), "-append", "root=ahci0:0:0 serial_debug disable_kaslr",
                   "-drive", f"file={root},if=none,format=raw,id=root,snapshot=on", "-device", "ich9-ahci,id=ahci",
                   "-device", "ide-hd,drive=root,bus=ahci.0", "-vga", "std", "-display", "none", "-nic", "none",
                   "-qmp", f"unix:{qmp},server=on,wait=off", "-serial", f"file:{serial}", "-debugcon", f"file:{work}/debug.log"]
        (work / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        monitor = None
        with (work / "qemu.log").open("w") as log:
            guest = subprocess.Popen(command, stdout=log, stderr=log)

            def wait_for(marker, offset=0):
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    output = serial.read_text(errors="replace")[offset:] if serial.exists() else ""
                    if marker in output:
                        return output
                    if guest.poll() is not None or any(x in output for x in ("KERNEL PANIC", "VERIFICATION FAILED", "ASSERTION FAILED")):
                        raise RuntimeError(f"Guest failed while waiting for {marker}")
                    time.sleep(0.1)
                raise TimeoutError(f"Timed out waiting for {marker}")

            try:
                wait_for("MemoryGraph")
                monitor = Monitor(qmp)
                time.sleep(3)
                needs_open = True
                cases = [
                    ("valid", "exit 0", "Child shell exited with code 0", "success"),
                    ("missing", "serenity_run_missing_command_20261109", "Child shell exited with code 127", "dialog"),
                    ("permission", "exit 126", "Child shell exited with code 126", "dialog"),
                    ("nonzero", "exit 1", "Child shell exited with code 1", "dialog"),
                    ("signal", "kill -TERM $$", "Child shell terminated by signal 15", "inline"),
                    ("retry", "exit 0", "Child shell exited with code 0", "success"),
                    ("crash", "kill -ABRT $$", "Child shell terminated by signal 6", "crash"),
                ]
                for name, text, marker, outcome in cases:
                    if needs_open:
                        # Taskbar's application scan can still be running after
                        # the other applets appear. Start from the default
                        # terminal instead of racing its Super-key handler.
                        monitor.click(180, 110)
                        monitor.key("ctrl-u")
                        monitor.type("Run")
                        monitor.key("ret")
                        time.sleep(5)
                    monitor.click(160, 679)
                    monitor.key("ctrl-a")
                    monitor.type(text)
                    time.sleep(2)
                    monitor.screenshot(work / f"{name}-input.png")
                    offset = len(serial.read_text(errors="replace"))
                    monitor.key("ret")
                    wait_for(marker, offset)
                    if outcome == "success":
                        wait_for("Ran via command shell.", offset)
                    if outcome == "crash":
                        wait_for("Generating backtrace took", offset)
                    time.sleep(3)
                    output = serial.read_text(errors="replace")[offset:]
                    if outcome != "success":
                        assert "Ran via command shell." not in output
                    if outcome in ("inline", "crash"):
                        assert "Child shell exited with code" not in output
                    monitor.screenshot(work / f"{name}-result.png")
                    (work / f"{name}.log").write_text(output)
                    results.append(dict(case=name, command=text, expected=outcome, log_assertions_passed=True,
                                        screenshot=f"{name}-result.png", visual_review_required=True))
                    (work / "results.json").write_text(json.dumps(dict(cases=results, complete=False), indent=2) + "\n")
                    print(f"PASS log assertions: {name}", flush=True)
                    needs_open = outcome == "success"
                    if outcome == "dialog":
                        monitor.key("ret")
                        time.sleep(2)
            finally:
                if monitor:
                    monitor.close()
                guest.terminate()
                try:
                    guest.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    guest.kill()
                    guest.wait()

    assert digest(root) == root_hash
    assert digest(source) == original_hash
    (work / "results.json").write_text(json.dumps(dict(cases=results, complete=True, cpus=1, snapshot=True,
                                                      source_unchanged=True, temporary_root_unchanged=True,
                                                      visual_review_required=True), indent=2) + "\n")
    print("PASS: seven Run outcomes; source/root unchanged. Review the saved screenshots.")


if __name__ == "__main__":
    main()
