#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Exercise the real asynchronous ZIP code in a disposable 1024x768 desktop.

The guest asserts names, contents, live event-loop ticks and child reaping.
Screenshots require separate visual review. No real disk or source image is edited.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parent.parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / "Meta" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def build_fixture(build, work):
    entry = next(x for x in json.loads((build / "compile_commands.json").read_text()) if x["file"].endswith("/FileManager/ArchiveOperations.cpp"))
    command = shlex.split(entry["command"])
    obj = work / "fixture.o"
    for option in ("-MF", "-MT"):
        if option in command:
            index = command.index(option)
            del command[index:index + 2]
    if "-MD" in command:
        command.remove("-MD")
    command[command.index("-o") + 1] = str(obj)
    command[command.index(entry["file"])] = str(REPO / "Meta/test-async-zip.cpp")
    with (work / "fixture-build.log").open("w") as log:
        subprocess.run(command, cwd=entry["directory"], stdout=log, stderr=subprocess.STDOUT, check=True)
        links = subprocess.check_output(["ninja", "-C", str(build), "-t", "commands", "FileManager"], text=True)
        link = shlex.split(links.splitlines()[-1])[2:]
        if "&&" in link:
            link = link[:link.index("&&")]
        link = [x for x in link if not x.endswith(".o") or x.endswith("ArchiveOperations.cpp.o")]
        binary = work / "TestAsyncZip"
        link[link.index("-o") + 1] = str(binary)
        link.insert(link.index("-o"), str(obj))
        link.append(str(build / "Userland/Libraries/LibCompress/libcompress.so"))
        subprocess.run(link, cwd=build, stdout=log, stderr=subprocess.STDOUT, check=True)
    return binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New artifact directory")
    parser.add_argument("--build", type=Path, default=REPO / "Build/x86_64")
    parser.add_argument("--baseline-zip", type=Path, required=True)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    source = args.root_image.resolve(strict=True)
    if not source.is_file() or args.timeout <= 0:
        parser.error("An offline regular root image and positive timeout are required")
    build = args.build.resolve(strict=True)
    work = args.output.resolve()
    work.mkdir(parents=True, exist_ok=False)
    print(f"Artifacts: {work}", flush=True)
    run = load("run_status", "test-run-process-status.py")
    crash = load("crash", "test-ext2-crash-consistency.py")
    fixture = build_fixture(build, work)
    source_hash = run.digest(source)
    root = work / "root.ext2"
    shutil.copyfile(source, root)
    crash.command(["e2fsck", "-f", "-n", root], work / "fsck-before.log")
    installed = {}
    for name, binary in (
        ("FileManager", build / "Userland/Applications/FileManager/FileManager"),
        ("zip", build / "Userland/Utilities/zip"), ("unzip", build / "Userland/Utilities/unzip"),
        ("Run", build / "Userland/Applications/Run/Run"),
        ("FileOperation", build / "Userland/Services/FileOperation/FileOperation"),
        ("zip-before", args.baseline_zip.resolve(strict=True)), ("TestAsyncZip", fixture),
    ):
        crash.install(root, binary, f"/bin/{name}", work / f"install-{name}.log")
        extracted = work / f"installed-{name}"
        crash.command(["debugfs", "-R", f'dump /bin/{name} "{extracted}"', root], work / f"dump-{name}.log")
        assert run.digest(binary) == run.digest(extracted)
        installed[name] = run.digest(binary)
    crash.command(["e2fsck", "-f", "-n", root], work / "fsck-installed.log")
    backing_hash = run.digest(root)
    (work / "provenance.json").write_text(json.dumps(dict(source_sha256=source_hash, installed=installed, kernel_sha256=run.digest(build / "Kernel/Kernel")), indent=2) + "\n")
    serial = work / "serial.log"
    results = []
    with tempfile.TemporaryDirectory(prefix="async-zip-") as directory:
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

            def output(offset=0):
                return serial.read_text(errors="replace")[offset:] if serial.exists() else ""

            def wait_for(marker, offset=0):
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    text = output(offset)
                    if marker in text:
                        return text
                    if guest.poll() is not None or any(x in text for x in ("VERIFICATION FAILED", "ASSERTION FAILED", "KERNEL PANIC")):
                        raise RuntimeError(f"Guest failed; see {work}")
                    time.sleep(0.1)
                raise TimeoutError(f"Timed out waiting for {marker}")

            def launch(scenario):
                monitor.click(180, 110)
                monitor.key("ctrl-u")
                monitor.type(f"TestAsyncZip {scenario}")
                monitor.key("ret")

            def move_window():
                for axis, value in (("x", 350 * 32767 // 1024), ("y", 158 * 32767 // 768)):
                    monitor.execute("input-send-event", {"events": [{"type": "abs", "data": {"axis": axis, "value": value}}]})
                monitor.execute("input-send-event", {"events": [{"type": "btn", "data": {"button": "left", "down": True}}]})
                time.sleep(.2)
                monitor.execute("input-send-event", {"events": [
                    {"type": "abs", "data": {"axis": "x", "value": 450 * 32767 // 1024}},
                    {"type": "abs", "data": {"axis": "y", "value": 228 * 32767 // 768}}]})
                time.sleep(.2)
                monitor.execute("input-send-event", {"events": [{"type": "btn", "data": {"button": "left", "down": False}}]})

            try:
                wait_for("MemoryGraph")
                monitor = run.Monitor(qmp)
                time.sleep(3)
                launch("probe")
                wait_for("ZIP BASELINE reproduced: unreadable input, exit=0, archive members=0")
                monitor.screenshot(work / "baseline-probe.png")
                print("Confirmed baseline zip false-success", flush=True)
                if not args.probe_only:
                    for scenario in ("cycle", "repeat", "invalid", "unreadable", "long", "close"):
                        offset = len(output())
                        launch(scenario)
                        wait_for("ZIP operation started:", offset)
                        if scenario in ("long", "close"):
                            time.sleep(1)
                            monitor.screenshot(work / f"{scenario}-running.png")
                            if scenario == "long":
                                monitor.click(470, 218)
                                wait_for("ZIP TEST interaction:", offset)
                                assert "running=true" in output(offset) or "running=1" in output(offset)
                                move_window()
                                time.sleep(1)
                                monitor.screenshot(work / "long-moved.png")
                            else:
                                monitor.click(760, 158)
                                time.sleep(1)
                                monitor.screenshot(work / "close-parent.png")
                        if scenario in ("invalid", "unreadable"):
                            wait_for("ZIP operation failed:", offset)
                            time.sleep(3)
                            monitor.screenshot(work / f"{scenario}-error.png")
                            monitor.key("ret")
                        if scenario != "close":
                            wait_for(f"ZIP TEST verified: {scenario}", offset)
                            monitor.screenshot(work / f"{scenario}-verified.png")
                            monitor.click(570 if scenario == "long" else 470, 288 if scenario == "long" else 218)
                        wait_for(f"ZIP TEST PASS {scenario}: no remaining children or zombies", offset)
                        monitor.screenshot(work / f"{scenario}-result.png")
                        (work / f"{scenario}.log").write_text(output(offset))
                        results.append(dict(case=scenario, passed=True, no_remaining_children=True, screenshots_require_review=True))
                        (work / "results.json").write_text(json.dumps(dict(complete=False, cases=results), indent=2) + "\n")
                        print(f"PASS: {scenario}", flush=True)
            finally:
                if monitor:
                    monitor.close()
                guest.terminate()
                try:
                    guest.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    guest.kill()
                    guest.wait()
    assert run.digest(root) == backing_hash and run.digest(source) == source_hash
    (work / "results.json").write_text(json.dumps(dict(complete=True, probe_only=args.probe_only, baseline_false_success_confirmed=True,
                                                      cases=results, cpus=1, snapshot=True, source_unchanged=True, backing_unchanged=True), indent=2) + "\n")


if __name__ == "__main__":
    main()
