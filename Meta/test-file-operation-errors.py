#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Test FileOperation and its real GUI consumer in a disposable anon desktop.

Requires a normal 1024x768 desktop image. Log/filesystem assertions are automatic;
review the saved screenshots separately. No source image or real disk is edited.
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


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / "Meta" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def build_fixture(build, work):
    database = json.loads((build / "compile_commands.json").read_text())
    entry = next(x for x in database if x["file"].endswith("/FileManager/FileOperationProgressWidget.cpp"))
    command = shlex.split(entry["command"])
    obj = work / "fixture.o"
    for option in ("-MF", "-MT"):
        if option in command:
            index = command.index(option)
            del command[index:index + 2]
    if "-MD" in command:
        command.remove("-MD")
    command[command.index("-o") + 1] = str(obj)
    command[command.index(entry["file"])] = str(REPO / "Meta/test-file-operation-errors.cpp")
    with (work / "fixture-build.log").open("w") as log:
        subprocess.run(command, cwd=entry["directory"], stdout=log, stderr=subprocess.STDOUT, check=True)
        links = subprocess.check_output(["ninja", "-C", str(build), "-t", "commands", "FileManager"], text=True)
        link = shlex.split(links.splitlines()[-1])
        link = link[2:] # Skip CMake's ': &&'.
        if "&&" in link:
            link = link[:link.index("&&")]
        link = [x for x in link if not x.endswith(".o") or x.endswith(("FileUtils.cpp.o", "FileOperationProgressWidget.cpp.o"))]
        binary = work / "TestFileOperationErrors"
        link[link.index("-o") + 1] = str(binary)
        link.insert(link.index("-o"), str(obj))
        subprocess.run(link, cwd=build, stdout=log, stderr=subprocess.STDOUT, check=True)
    return binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-image", required=True, type=Path)
    parser.add_argument("--build", type=Path, default=REPO / "Build/x86_64")
    parser.add_argument("--output", required=True, type=Path, help="New output directory")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    source = args.root_image.resolve(strict=True)
    if not source.is_file():
        parser.error("--root-image must be an offline regular file")
    build = args.build.resolve(strict=True)
    work = args.output.resolve()
    work.mkdir(parents=True, exist_ok=False)
    print(f"Artifacts: {work}", flush=True)
    run = module("run_status", "test-run-process-status.py")
    crash = module("crash", "test-ext2-crash-consistency.py")
    fixture = build_fixture(build, work)
    original = run.digest(source)
    root = work / "root.ext2"
    shutil.copyfile(source, root)
    crash.command(["e2fsck", "-f", "-n", root], work / "fsck-before.log")
    installed = {}
    for name, binary in (
        ("FileManager", build / "Userland/Applications/FileManager/FileManager"),
        ("FileOperation", build / "Userland/Services/FileOperation/FileOperation"),
        ("TestFileOperationErrors", fixture),
        ("Run", build / "Userland/Applications/Run/Run"),
    ):
        crash.install(root, binary, f"/bin/{name}", work / f"install-{name}.log")
        extracted = work / f"installed-{name}"
        crash.command(["debugfs", "-R", f'dump /bin/{name} "{extracted}"', root], work / f"verify-{name}.log")
        assert run.digest(extracted) == run.digest(binary)
        installed[name] = run.digest(binary)
    crash.command(["e2fsck", "-f", "-n", root], work / "fsck-installed.log")
    backing = run.digest(root)
    (work / "provenance.json").write_text(json.dumps(dict(source_sha256=original, installed=installed,
                                                          kernel_sha256=run.digest(build / "Kernel/Kernel")), indent=2) + "\n")
    serial = work / "serial.log"
    cases = []
    with tempfile.TemporaryDirectory(prefix="fm-errors-") as directory:
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
                        return
                    if guest.poll() is not None or any(x in text for x in ("VERIFICATION FAILED", "ASSERTION FAILED", "KERNEL PANIC")):
                        raise RuntimeError(f"Guest failed; see {work}")
                    time.sleep(0.1)
                raise TimeoutError(f"Timed out waiting for {marker}")

            def terminal(command):
                monitor.click(180, 110)
                monitor.key("ctrl-u")
                monitor.type(command)
                monitor.key("ret")

            try:
                wait_for("MemoryGraph")
                monitor = run.Monitor(qmp)
                time.sleep(3)
                terminal("TestFileOperationErrors --check")
                wait_for("FileOperation CLI cases passed: 12")
                time.sleep(2)
                monitor.screenshot(work / "cli-result.png")
                for scenario in ("copy", "move", "delete", "denied", "missing", "invalid", "partial", "disconnect", "duplicate"):
                    offset = len(output())
                    terminal(f"TestFileOperationErrors {scenario}")
                    successful = scenario in ("copy", "move", "delete")
                    wait_for("File operation finished" if successful else "Copy Failed:", offset)
                    time.sleep(3)
                    text = output(offset)
                    if not successful:
                        assert text.count("Copy Failed:") == 1
                        assert "File operation finished" not in text
                        if scenario == "disconnect":
                            assert "stopped unexpectedly" in text
                        elif scenario == "duplicate":
                            assert "Duplicate notification" not in text
                        else:
                            assert "Could not copy" in text
                            assert "Read from pipe" not in text
                    monitor.screenshot(work / f"{scenario}-result.png")
                    if not successful:
                        monitor.key("ret")
                        time.sleep(2)
                    monitor.click(490, 150)
                    wait_for(f"FileOperation GUI responsive and files verified: {scenario}", offset)
                    time.sleep(2)
                    monitor.screenshot(work / f"{scenario}-responsive.png")
                    (work / f"{scenario}.log").write_text(output(offset))
                    cases.append(dict(case=scenario, protocol_and_files_verified=True, responsive_after_result=True,
                                      screenshot=f"{scenario}-result.png", visual_review_required=True))
                    (work / "results.json").write_text(json.dumps(dict(complete=False, cli_cases=12, gui_cases=cases), indent=2) + "\n")
                    print(f"PASS: GUI {scenario}", flush=True)
            finally:
                if monitor:
                    monitor.close()
                guest.terminate()
                try:
                    guest.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    guest.kill()
                    guest.wait()
    assert run.digest(root) == backing
    assert run.digest(source) == original
    (work / "results.json").write_text(json.dumps(dict(complete=True, cli_cases=12, gui_cases=cases,
                                                      cpus=1, snapshot=True, source_unchanged=True,
                                                      backing_unchanged=True, visual_review_required=True), indent=2) + "\n")
    print("PASS: 12 helper cases and nine GUI cases; review the saved screenshots.")


if __name__ == "__main__":
    main()
