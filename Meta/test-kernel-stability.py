#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Run bounded memory/thread regression tests in private x86_64 QEMU guests.

Each binary runs as init in a fresh guest. Its expected exit causes a kernel
panic; only the explicit init exit code zero is accepted. Deliberate child
SIGSEGVs in protection tests are expected. Memory pressure is explicitly opt-in.
The original filesystem and existing VMs are never written.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parent.parent
TESTS = ("TestMprotect", "TestMunMap", "TestThreadSleep", "TestAnonymousMmap", "TestWXProtection")
OPTIONAL_TESTS = ("TestMemoryPressure",)


def command(args, log, accepted=(0,)):
    result = subprocess.run([str(arg) for arg in args], capture_output=True)
    log.write_bytes(result.stdout + result.stderr)
    if result.returncode not in accepted:
        raise RuntimeError(f"Command failed ({result.returncode}); see {log}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpus", nargs="+", type=int, choices=(1, 2, 4, 8), default=[1, 2])
    parser.add_argument("--tests", nargs="+", choices=TESTS + OPTIONAL_TESTS, default=list(TESTS))
    parser.add_argument("--memory-mib", type=int, choices=(128, 256, 512, 1024, 2048), default=2048)
    parser.add_argument("--stress-rounds", type=int, default=64, help="Process-exit rounds in the opt-in memory pressure test")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--diagnose", action="store_true", help="Capture GDB stacks before terminating a failed guest")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.repeat <= 0:
        parser.error("--repeat must be positive")
    if not 16 <= args.stress_rounds <= 4096:
        parser.error("--stress-rounds must be between 16 and 4096")
    if "TestMemoryPressure" in args.tests and args.memory_mib not in (128, 256):
        parser.error("TestMemoryPressure requires --memory-mib 128 or 256 in an isolated guest")
    for tool in ("qemu-system-x86_64", "debugfs", "e2fsck"):
        if not shutil.which(tool):
            parser.error(f"Missing {tool}")
    if args.diagnose and not shutil.which("gdb"):
        parser.error("--diagnose requires gdb")
    build = REPO / "Build/x86_64"
    sources = {name: build / "Tests/Kernel" / name for name in args.tests}
    for path in [build / "_disk_image", build / "Kernel/Kernel", *sources.values()]:
        if not path.is_file():
            parser.error(f"Build the missing input first: {path}")
    work = Path(tempfile.mkdtemp(prefix="kernel-stability-", dir=REPO / "Build"))
    print(f"Artifacts: {work}", flush=True)
    kernel = work / "Kernel"
    shutil.copyfile(build / "Kernel/Kernel", kernel)
    (work / "provenance.json").write_text(json.dumps({
        "kernel_sha256": hashlib.sha256(kernel.read_bytes()).hexdigest(),
        "test_sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sources.items()},
        "options": vars(args),
    }, indent=2) + "\n")
    root = work / "root.ext2"
    shutil.copyfile(build / "_disk_image", root)
    command(["e2fsck", "-f", "-y", root], work / "fsck-copy.log", accepted=(0, 1))
    if "TestMemoryPressure" in args.tests:
        config = work / "memory-pressure.config"
        config.write_text(f"{args.stress_rounds}\n")
        command(["debugfs", "-w", "-R", "rm /tmp/serenity-memory-pressure-enabled", root], work / "pressure-remove.log")
        command(["debugfs", "-w", "-R", f"write {config} /tmp/serenity-memory-pressure-enabled", root], work / "pressure-install.log")
    for name, binary in sources.items():
        # Copy to /bin so tests cannot silently use an outdated installed binary.
        destination = f"/bin/{name}"
        command(["debugfs", "-w", "-R", f"rm {destination}", root], work / f"{name}-remove.log")
        command(["debugfs", "-w", "-R", f'write "{binary}" {destination}', root], work / f"{name}-install.log")
        command(["debugfs", "-w", "-R", f"set_inode_field {destination} mode 0100755", root], work / f"{name}-mode.log")
        extracted = work / f"{name}.verified"
        command(["debugfs", "-R", f'dump {destination} "{extracted}"', root], work / f"{name}-verify.log")
        if not extracted.exists() or hashlib.sha256(extracted.read_bytes()).digest() != hashlib.sha256(binary.read_bytes()).digest():
            raise RuntimeError(f"Injected binary mismatch: {name}")
    command(["e2fsck", "-f", "-n", root], work / "fsck-final.log")
    results = []
    for cpus, iteration in [(cpus, iteration) for iteration in range(1, args.repeat + 1) for cpus in args.cpus]:
        for name in args.tests:
            case = work / f"{name}-{cpus}cpu-run{iteration}"
            case.mkdir()
            debug = case / "debug.log"
            cmd = ["qemu-system-x86_64", "-machine", "q35", "-accel", "tcg",
                   "-cpu", "max,-x2apic", "-smp", str(cpus), "-m", str(args.memory_mib),
                   "-kernel", str(kernel),
                   "-append", f"root=ahci0:0:0 serial_debug disable_kaslr smp=on init=/bin/{name}",
                   "-drive", f"file={root},if=none,format=raw,id=root,snapshot=on",
                   "-device", "ich9-ahci,id=ahci", "-device", "ide-hd,drive=root,bus=ahci.0",
                   "-display", "none", "-monitor", "none", "-nic", "none", "-no-reboot",
                   "-serial", f"file:{case}/serial.log", "-debugcon", f"file:{debug}"]
            if args.diagnose:
                cmd += ["-gdb", f"unix:{case}/gdb.sock,server=on,wait=off"]
            (case / "command.json").write_text(json.dumps(cmd, indent=2))
            started = time.monotonic()
            timed_out = False
            panic_seen_at = None
            with (case / "qemu.log").open("w") as log:
                guest = subprocess.Popen(cmd, stdout=log, stderr=log)
                try:
                    while True:
                        output = debug.read_text(errors="replace") if debug.exists() else ""
                        # Debug output arrives incrementally. Do not kill QEMU
                        # at the panic heading before the exit-code line lands.
                        if re.search(r"Init process quit unexpectedly\. Exit code: \d+\n", output) or guest.poll() is not None:
                            break
                        if "KERNEL PANIC" in output:
                            if panic_seen_at is None:
                                panic_seen_at = time.monotonic()
                            elif time.monotonic() - panic_seen_at > 2:
                                break
                        if time.monotonic() - started > args.timeout:
                            timed_out = True
                            break
                        time.sleep(0.25)
                finally:
                    output = debug.read_text(errors="replace") if debug.exists() else ""
                    if args.diagnose and guest.poll() is None and "All " not in output:
                        with (case / "gdb.log").open("w") as diagnostic:
                            try:
                                subprocess.run(["gdb", "-nx", "-batch",
                                                "-ex", "set pagination off", "-ex", "set architecture i386:x86-64",
                                                "-ex", f'add-symbol-file "{build}/Kernel/Kernel.debug" -o 0x2000200000',
                                                "-ex", f"target remote {case}/gdb.sock",
                                                "-ex", "thread apply all bt", "-ex", "thread apply all info registers",
                                                "-ex", "detach"], stdout=diagnostic, stderr=diagnostic, timeout=15)
                            except subprocess.TimeoutExpired:
                                diagnostic.write("GDB timed out\n")
                    guest.terminate()
                    try:
                        guest.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        guest.kill()
                        guest.wait()
            output = debug.read_text(errors="replace") if debug.exists() else ""
            completed = re.search(r"All (\d+) tests passed\.", output)
            all_cpus_active = all(f"Scheduler[{cpu}]: idle loop running" in output for cpu in range(cpus))
            pressure_verified = name != "TestMemoryPressure" or all(marker in output for marker in (
                "MEMORY_RESERVATION_ENOMEM_RECOVERED", "MM: no physical pages available",
                "MEMORY_OOM_CHILD_REAPED", f"MEMORY_PRESSURE_RECOVERED rounds={args.stress_rounds}"))
            kernel_assertion = any(marker in output for marker in ("VERIFICATION FAILED", "ASSERTION FAILED", "KUBSAN:"))
            passed = not timed_out and not kernel_assertion and pressure_verified and all_cpus_active and completed is not None and re.search(r"Init process quit unexpectedly\. Exit code: 0(?:\D|$)", output) is not None
            result = dict(test=name, cpus=cpus, iteration=iteration, all_cpus_active=all_cpus_active, passed=passed, timed_out=timed_out,
                          test_cases=int(completed.group(1)) if completed else None, memory_mib=args.memory_mib,
                          pressure_verified=pressure_verified, kernel_assertion=kernel_assertion,
                          seconds=round(time.monotonic() - started, 2), artifacts=str(case))
            results.append(result)
            (work / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            print(f"{'PASS' if passed else 'FAIL'}: {name}, {cpus} CPU(s), {result['seconds']}s", flush=True)
    print(f"Passed {sum(result['passed'] for result in results)}/{len(results)} guests; results: {work / 'results.json'}", flush=True)
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
