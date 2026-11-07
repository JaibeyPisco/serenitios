#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Cut isolated x86_64 QEMU guests between completed AHCI writes and inspect ext2.

Requires an offline, standalone ext2 Serenity root image, a built kernel and
TestExt2CrashConsistency, QEMU, GDB with Python, debugfs, mke2fs and e2fsck.
Only a new output directory is written. The input root image is copied and the
copy is booted with snapshot=on. Original crash images are never repaired.

This models guest interruption between completed commands, not torn sectors or
loss of a real controller's volatile cache. See Documentation/Kernel/Ext2CrashTesting.md.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import time

REPO = Path(__file__).resolve().parent.parent
FIXTURE_SIZE = 64 * 1024 * 1024


def command(args, log, accepted=(0,)):
    result = subprocess.run([str(arg) for arg in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log.write_text(result.stdout)
    if result.returncode not in accepted:
        raise RuntimeError(f"{args[0]} exited {result.returncode}; see {log}")
    return result.returncode


def install(root, source, destination, log):
    def quote(path):
        return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"') + '"'
    subprocess.run(["debugfs", "-w", "-R", f"rm {destination}", str(root)], capture_output=True, check=True)
    command(["debugfs", "-w", "-R", f"write {quote(source)} {destination}", root], log)
    command(["debugfs", "-w", "-R", f"set_inode_field {destination} mode 0100755", root], log)
    command(["debugfs", "-R", f"stat {destination}", root], log)
    if "Inode:" not in log.read_text():
        raise RuntimeError(f"debugfs did not install {destination}")


def dump(image, name, output):
    # All generated artifact paths are absolute; debugfs does not use a shell.
    subprocess.run(["debugfs", "-R", f'dump /{name} "{output}"', str(image)], capture_output=True, check=True)
    return output.read_bytes() if output.exists() else None


def inspect(case, operation, cut):
    image = case / "disk.ext2"
    fsck = command(["e2fsck", "-f", "-n", image], case / "fsck-original.log", accepted=(0, 4))
    with image.open("rb") as disk:
        disk.seek(1024 + 58)
        state = struct.unpack("<H", disk.read(2))[0]
    sentinel = dump(image, "sentinel", case / "sentinel-original.bin") == b"\x7b" * 4096
    result = {"fsck_exit": fsck, "state": state, "sentinel_intact": sentinel}
    for name in ("victim", "destination"):
        command(["debugfs", "-R", f"stat /{name}", image], case / f"{name}-original.log")
    if cut:
        recovered = case / "recovered.ext2"
        shutil.copyfile(image, recovered)
        result["repair_exit"] = command(
            ["e2fsck", "-f", "-y", recovered], case / "fsck-repair.log", accepted=(0, 1, 2, 3))
        result["recovered_fsck_exit"] = command(["e2fsck", "-f", "-n", recovered], case / "fsck-recovered.log")
        result["recovered_sentinel_intact"] = (
            dump(recovered, "sentinel", case / "sentinel-recovered.bin") == b"\x7b" * 4096)
    else:
        assert fsck == 0 and state == 1 and sentinel, f"invalid control: {case}"
        victim = dump(image, "victim", case / "victim-control.bin")
        if operation == "create":
            assert victim == b"\x5a" * (32 * 4096)
        elif operation == "unlink":
            assert victim is None
        elif operation == "rename":
            assert victim is None
            assert dump(image, "destination", case / "destination-control.bin") == b"\x41" * (32 * 4096)
        else:
            assert victim == b"\x41" * 4096
    return result


def run_case(options, root, operation, cut, sync_symbol):
    case = options.output / f"{operation}-{'control' if not cut else str(cut)}"
    case.mkdir()
    fixture = case / "disk.ext2"
    with fixture.open("wb") as disk:
        disk.truncate(FIXTURE_SIZE)
    command(["mke2fs", "-q", "-t", "ext2", "-F", "-b", "4096", "-I", "128", "-L", "SerenityCrash", fixture],
            case / "mkfs.log")
    script = case / "guest.sh"
    script.write_text("#!/bin/Shell\nmknod /tmp/ext2-crash-device b 3 1\n"
                      "env SERENITY_EXT2_CRASH_DEVICE=/tmp/ext2-crash-device "
                      f"SERENITY_EXT2_CRASH_OPERATION={operation} "
                      "/usr/Tests/Kernel/TestExt2CrashConsistency\nsleep 300\n")
    install(root, script, "/tmp/ext2-crash.sh", case / "install.log")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    qemu_args = [options.qemu, "-machine", "q35", "-accel", "tcg", "-cpu", "max,-x2apic", "-m", "512M", "-smp", "1",
                 "-kernel", str(options.build / "Kernel/Kernel"), "-append",
                 "root=ahci0:0:0 serial_debug disable_kaslr init=/bin/Shell init_args=/tmp/ext2-crash.sh",
                 "-drive", f"file={root},if=none,format=raw,id=root,snapshot=on", "-device", "ich9-ahci,id=ahci",
                 "-device", "ide-hd,drive=root,bus=ahci.0", "-drive",
                 f"file={fixture},if=none,format=raw,id=fixture,snapshot=off",
                 "-device", "ide-hd,drive=fixture,bus=ahci.1", "-display", "none",
                 "-serial", f"file:{case / 'serial.log'}",
                 "-monitor", "none", "-nic", "none", "-no-reboot", "-S", "-gdb", f"tcp:127.0.0.1:{port}"]
    with (case / "qemu.log").open("w") as qemu_log:
        qemu = subprocess.Popen(qemu_args, stdout=qemu_log, stderr=subprocess.STDOUT)
        try:
            config = case / "config.json"
            config.write_text(json.dumps({"qemu_pid": qemu.pid, "cut": cut, "probe_lba": FIXTURE_SIZE // 512 - 1,
                                          "result": str(case / "cut.json"), "sync_symbol": sync_symbol}))
            gdb_script = case / "run.gdb"
            gdb_script.write_text("set pagination off\nset confirm off\nset architecture i386:x86-64\n"
                                  f"add-symbol-file {json.dumps(str(options.build / 'Kernel/Kernel.debug'))} "
                                  "-o 0x2000200000\n"
                                  f"target remote 127.0.0.1:{port}\nsource {REPO / 'Meta/ext2-crash-gdb.py'}\n")
            env = dict(os.environ, SERENITY_EXT2_CRASH_CONFIG=str(config))
            if options.gdb_library_path:
                env["LD_LIBRARY_PATH"] = options.gdb_library_path
            # Wait for this owned VM to bind its debugger port, without resuming it.
            deadline = time.monotonic() + 10
            while True:
                if qemu.poll() is not None:
                    raise RuntimeError(f"QEMU exited; see {case / 'qemu.log'}")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError("QEMU debugger did not start")
                    time.sleep(0.05)
            with (case / "gdb.log").open("w") as log:
                debugger = subprocess.Popen([options.gdb, "-q", "-batch", "-x", str(gdb_script)], env=env, stdout=log,
                                            stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + options.timeout
                    while debugger.poll() is None:
                        serial = case / "serial.log"
                        text = serial.read_text() if serial.exists() else ""
                        if any(marker in text for marker in ("VERIFICATION FAILED", "KERNEL PANIC", "KUBSAN:")):
                            raise RuntimeError(f"guest aborted before its cut point; see {case}")
                        if time.monotonic() > deadline:
                            raise RuntimeError(f"debugger timed out; see {case}")
                        time.sleep(0.1)
                finally:
                    if debugger.poll() is None:
                        debugger.kill()
                    debugger.wait(timeout=10)
            result = json.loads((case / "cut.json").read_text())
            expected = "cut" if cut else "complete"
            if result["status"] != expected:
                raise RuntimeError(f"expected {expected}, got {result}; see {case}")
            if cut:
                qemu.wait(timeout=10)
                assert qemu.returncode == -9
            else:
                assert debugger.returncode == 0
                deadline = time.monotonic() + options.timeout
                while "All 1 tests passed." not in (case / "serial.log").read_text():
                    text = (case / "serial.log").read_text()
                    if (qemu.poll() is not None or "KERNEL PANIC" in text or "KUBSAN:" in text
                            or time.monotonic() > deadline):
                        raise RuntimeError(f"guest control failed; see {case}")
                    time.sleep(0.1)
            result.update(inspect(case, operation, cut))
            result.update(operation=operation, cut=cut, directory=str(case))
            return result
        finally:
            if qemu.poll() is None:
                qemu.kill()
            qemu.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new artifact directory; must not exist")
    parser.add_argument("--build", type=Path, default=REPO / "Build/x86_64")
    parser.add_argument("--operations", nargs="+", choices=("create", "unlink", "rename", "truncate"),
                        default=["create", "unlink", "rename", "truncate"])
    parser.add_argument("--all-cuts", action="store_true",
                        help="test every observed command boundary instead of first/middle/last")
    parser.add_argument("--cuts", type=int, nargs="+", help="explicit completed-write boundaries; missed cuts still fail")
    parser.add_argument("--qemu", default="qemu-system-x86_64")
    parser.add_argument("--gdb", default="gdb")
    parser.add_argument("--gdb-library-path")
    parser.add_argument("--timeout", type=float, default=90)
    options = parser.parse_args()
    if options.timeout <= 0:
        parser.error("--timeout must be positive")
    if options.cuts and (options.all_cuts or any(cut <= 0 for cut in options.cuts)):
        parser.error("--cuts requires positive boundaries and cannot be combined with --all-cuts")
    options.root_image = options.root_image.resolve(strict=True)
    options.build = options.build.resolve(strict=True)
    options.output = options.output.resolve()
    if not options.root_image.is_file():
        parser.error("--root-image must be a regular offline image file")
    binary = options.build / "Tests/Kernel/TestExt2CrashConsistency"
    if not binary.is_file():
        parser.error("build kernel_x86-64 and TestExt2CrashConsistency first")
    options.output.mkdir(parents=True, exist_ok=False)
    root = options.output / "root.ext2"
    shutil.copyfile(options.root_image, root)
    command(["e2fsck", "-f", "-n", root], options.output / "root-fsck.log")
    install(root, binary, "/usr/Tests/Kernel/TestExt2CrashConsistency", options.output / "install.log")
    symbols = subprocess.check_output(["nm", str(options.build / "Kernel/Kernel.debug")], text=True)
    sync_symbol = next(line.split()[-1] for line in symbols.splitlines() if line.endswith("sys$syncEv"))
    results = []
    report = options.output / "results.json"
    try:
        for operation in options.operations:
            control = run_case(options, root, operation, 0, sync_symbol)
            results.append(control)
            total = len(control["writes"])
            assert total > 0
            if options.cuts:
                cuts = sorted(set(options.cuts))
            elif options.all_cuts:
                cuts = range(1, total + 1)
            else:
                cuts = sorted({1, max(1, (total + 1) // 2), total})
            if any(cut > total for cut in cuts):
                raise RuntimeError(f"Requested cut exceeds control write count {total} for {operation}")
            print(f"{operation}: control passed; {total} completed writes", flush=True)
            for cut in cuts:
                result = run_case(options, root, operation, cut, sync_symbol)
                results.append(result)
                report.write_text(json.dumps(results, indent=2))
                print(f"{operation}: cut {cut}; fsck={result['fsck_exit']}; sentinel={result['sentinel_intact']}; "
                      f"recovered_sentinel={result['recovered_sentinel_intact']}", flush=True)
    finally:
        report.write_text(json.dumps(results, indent=2))
    failures = [result for result in results
                if result["fsck_exit"] != 0 or not result["sentinel_intact"]
                or not result.get("recovered_sentinel_intact", True)
                or result["state"] != (2 if result["cut"] else 1)]
    summary = dict(images_checked=len(results), controls=sum(not result["cut"] for result in results),
                   interrupted=sum(bool(result["cut"]) for result in results),
                   metadata_inconsistencies=sum(result["fsck_exit"] != 0 for result in results),
                   acknowledged_data_loss=sum(not result["sentinel_intact"] for result in results),
                   acknowledged_data_loss_after_repair=sum(not result.get("recovered_sentinel_intact", True) for result in results),
                   strict_failures=len(failures))
    (options.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"{len(results)} images checked; {summary['metadata_inconsistencies']} metadata inconsistencies; "
          f"{summary['acknowledged_data_loss']} acknowledged-data losses; "
          f"{summary['acknowledged_data_loss_after_repair']} acknowledged-data losses after repair. "
          f"Artifacts: {options.output}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
