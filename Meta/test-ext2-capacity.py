#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Fill and reclaim an isolated ext2 disk, then inspect its persisted contents."""

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("crash", REPO / "Meta/test-ext2-crash-consistency.py")
crash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crash)


def run_guest(build, root, disk, work, guest_script, marker, timeout, extra_disks=(), cpus=1, require_test_summary=True, diagnostic_types=None, snapshot_data=False):
    diagnostic_port = None
    if cpus > 1:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            diagnostic_port = listener.getsockname()[1]
    cmd = ["qemu-system-x86_64", "-machine", "q35", "-accel", "tcg", "-cpu", "max,-x2apic",
           "-m", "512M", "-smp", str(cpus), "-kernel", str(build / "Kernel/Kernel"),
           "-append", f"root=ahci0:0:0 serial_debug disable_kaslr {'smp=on ' if cpus > 1 else ''}init=/bin/Shell init_args={guest_script}",
           "-drive", f"file={root},if=none,format=raw,id=root,snapshot=on",
           "-device", "ich9-ahci,id=ahci", "-device", "ide-hd,drive=root,bus=ahci.0",
           "-drive", f"file={disk},if=none,format=raw,id=test" + (",snapshot=on" if snapshot_data else ""), "-device", "ide-hd,drive=test,bus=ahci.1",
           "-display", "none", "-monitor", "none", "-nic", "none", "-no-reboot",
           "-serial", f"file:{work}/serial.log", "-debugcon", f"file:{work}/debug.log"]
    for index, extra in enumerate(extra_disks):
        cmd += ["-drive", f"file={extra},if=none,format=raw,id=extra{index}",
                "-device", f"ide-hd,drive=extra{index},bus=ahci.{index + 2}"]
    if diagnostic_port is not None:
        cmd += ["-gdb", f"tcp:127.0.0.1:{diagnostic_port}"]
    (work / "command.json").write_text(json.dumps(cmd, indent=2) + "\n")
    with (work / "qemu.log").open("w") as log:
        guest = subprocess.Popen(cmd, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + timeout
            while True:
                output = (work / "serial.log").read_text(errors="replace") if (work / "serial.log").exists() else ""
                if marker in output and (not require_test_summary or "All 1 tests passed." in output):
                    if cpus > 1 and any(f"Scheduler[{cpu}]: idle loop running" not in output for cpu in range(cpus)):
                        raise RuntimeError(f"Not all requested CPUs reached the scheduler; see {work}")
                    break
                if any(marker in output for marker in ("VERIFICATION FAILED", "ASSERTION FAILED", "KERNEL PANIC", "KUBSAN:")) or guest.poll() is not None:
                    raise RuntimeError(f"Guest failed; see {work}")
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Guest timed out; see {work}")
                time.sleep(0.1)
        except Exception as error:
            (work / "failure.json").write_text(json.dumps(dict(passed=False, error=str(error), cpus=cpus), indent=2) + "\n")
            if diagnostic_port is not None and guest.poll() is None and shutil.which("gdb"):
                script = work / "failure.gdb"
                extra = (f"add-symbol-file {json.dumps(str(diagnostic_types))} 0\n"
                         f"source {REPO / 'Meta/gdb-dump-kernel-threads.py'}\n") if diagnostic_types else ""
                script.write_text("set pagination off\nset confirm off\nset architecture i386:x86-64\n"
                                  f"add-symbol-file {json.dumps(str(build / 'Kernel/Kernel.debug'))} -o 0x2000200000\n"
                                  f"target remote 127.0.0.1:{diagnostic_port}\ninfo threads\nthread apply all bt\n{extra}detach\n")
                with (work / "failure-backtrace.log").open("w") as trace:
                    try:
                        subprocess.run(["gdb", "-q", "-batch", "-x", str(script)], stdout=trace,
                                       stderr=subprocess.STDOUT, timeout=20)
                    except subprocess.TimeoutExpired:
                        trace.write("Debugger timed out\n")
            raise
        finally:
            guest.terminate()
            try:
                guest.wait(timeout=10)
            except subprocess.TimeoutExpired:
                guest.kill()
                guest.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-image", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    for tool in ("qemu-system-x86_64", "mke2fs", "debugfs", "e2fsck"):
        if not shutil.which(tool):
            parser.error(f"Missing {tool}")
    source = args.root_image.resolve(strict=True)
    if not source.is_file():
        parser.error("--root-image must be a regular offline image")
    build = REPO / "Build/x86_64"
    binary = build / "Tests/Kernel/TestExt2Capacity"
    if not binary.is_file():
        parser.error("Build TestExt2Capacity first")
    work = Path(tempfile.mkdtemp(prefix="ext2-capacity-", dir=REPO / "Build"))
    print(f"Artifacts: {work}", flush=True)
    root = work / "root.ext2"
    shutil.copyfile(source, root)
    crash.command(["e2fsck", "-f", "-n", root], work / "root-fsck.log")
    crash.install(root, binary, "/bin/TestExt2Capacity", work / "install.log")
    script = work / "guest.sh"
    script.write_text("#!/bin/Shell\nmknod /tmp/ext2-space-device b 3 1\n"
                      "env SERENITY_EXT2_CAPACITY_DEVICE=/tmp/ext2-space-device /bin/TestExt2Capacity\n"
                      "sleep 300\n")
    crash.install(root, script, "/tmp/ext2-space.sh", work / "install-script.log")
    disk = work / "disk.ext2"
    with disk.open("wb") as stream:
        stream.truncate(16 * 1024 * 1024)
    crash.command(["mke2fs", "-q", "-t", "ext2", "-F", "-b", "4096", "-I", "128",
                   "-L", "SerenitySpace", disk], work / "mkfs.log")
    run_guest(build, root, disk, work, "/tmp/ext2-space.sh", "EXT2_CAPACITY_EXECUTED", args.timeout)
    crash.command(["e2fsck", "-f", "-n", disk], work / "fsck-final.log")
    sentinel = crash.dump(disk, "sentinel", work / "sentinel.bin") == b"\x7b" * 4096
    reclaimed = crash.dump(disk, "reclaimed", work / "reclaimed.bin") == b"\x3c" * 4096
    filler_absent = crash.dump(disk, "filler", work / "filler.bin") is None
    result = dict(enospc_handled=True, sentinel_intact=sentinel, reclaimed_data_intact=reclaimed,
                  filler_absent=filler_absent, fsck_exit=0)
    (work / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    if not (sentinel and reclaimed and filler_absent):
        raise RuntimeError(f"Persisted contents mismatch; see {work}")
    print("PASS: ENOSPC handled, synced data preserved, space reclaimed, clean unmount and e2fsck", flush=True)


if __name__ == "__main__":
    main()
