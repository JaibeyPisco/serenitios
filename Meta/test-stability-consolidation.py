#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Bounded boot repetitions and three-boot normal-user ext2 persistence.

Snapshots kernel/symbols, copies the offline root, and writes only new fixtures.
Failed guests retain diagnostic traces and are never counted as passing.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("capacity", ROOT / "Meta/test-ext2-capacity.py")
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)
crash = capacity.crash


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-image", type=Path, required=True)
    parser.add_argument("--build", type=Path, default=ROOT / "Build/x86_64")
    parser.add_argument("--test-build", type=Path, default=ROOT / "Build/x86_64")
    parser.add_argument("--label", default="current")
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--cpus", type=int, choices=(1, 2, 4, 8), default=4)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--persistence", action="store_true", help="Three successive boots of the same data disk")
    parser.add_argument("--boot-only", action="store_true", help="Stop at the original workload's first mount attempt, without running recovery")
    parser.add_argument("--fixture", type=Path, help="Original disposable journal fixture for --boot-only")
    parser.add_argument("--diagnostic-types", type=Path, help="Matching full-type debug object for saved-thread inspection")
    args = parser.parse_args()
    if args.repeat <= 0 or args.timeout <= 0:
        parser.error("repeat and timeout must be positive")
    if args.boot_only and (not args.fixture or args.persistence):
        parser.error("--boot-only needs --fixture and cannot be combined with --persistence")
    source = args.root_image.resolve(strict=True)
    if not source.is_file():
        parser.error("root must be a regular offline image")
    work = Path(tempfile.mkdtemp(prefix=f"consolidation-{args.label}-", dir=ROOT / "Build"))
    print(f"Artifacts: {work}", flush=True)
    frozen = work / "frozen"
    (frozen / "Kernel").mkdir(parents=True)
    build = args.build.resolve(strict=True)
    for name in ("Kernel", "Kernel.debug"):
        shutil.copyfile(build / "Kernel" / name, frozen / "Kernel" / name)
    original_hash = sha(source)
    root = work / "root.ext2"
    shutil.copyfile(source, root)
    crash.command(["e2fsck", "-f", "-n", root], work / "root-fsck.log")
    binary = args.test_build.resolve(strict=True) / "Tests/Kernel/TestExt2Persistence"
    if not args.boot_only:
        crash.install(root, binary, "/bin/TestExt2Persistence", work / "install.log")
    fixture = work / "baseline.ext2"
    if args.boot_only:
        shutil.copyfile(args.fixture.resolve(strict=True), fixture)
    else:
        with fixture.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024)
        crash.command(["mke2fs", "-q", "-t", "ext2", "-F", "-b", "4096", "-I", "128",
                       "-L", "SerenityPersist", fixture], work / "mkfs.log")
    provenance = dict(label=args.label, cpus=args.cpus, timeout_seconds=args.timeout,
                      source_root=str(source), source_root_sha256=original_hash,
                      kernel_sha256=sha(frozen / "Kernel/Kernel"), symbols_sha256=sha(frozen / "Kernel/Kernel.debug"),
                      test_sha256=None if args.boot_only else sha(binary), boot_only=args.boot_only,
                      qemu=subprocess.check_output(["qemu-system-x86_64", "--version"], text=True).splitlines()[0])
    (work / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    results = []
    disk = work / "persistent.ext2"
    if args.persistence:
        shutil.copyfile(fixture, disk)
    for iteration in range(1, (3 if args.persistence else args.repeat) + 1):
        case = work / f"run-{iteration:02d}"
        case.mkdir()
        phase = iteration if args.persistence else 1
        if not args.persistence:
            disk = case / "disk.ext2"
            shutil.copyfile(fixture, disk)
        script = case / "guest.sh"
        script.write_text("#!/bin/Shell\nmknod /tmp/persistence-fixture b 3 1\n"
                          f"env SERENITY_PERSISTENCE_DEVICE=/tmp/persistence-fixture SERENITY_PERSISTENCE_PHASE={phase} "
                          "/bin/TestExt2Persistence\nsleep 300\n")
        if not args.boot_only:
            crash.install(root, script, "/tmp/consolidation.sh", case / "install-script.log")
        started = time.monotonic()
        error = None
        try:
            guest_script = "/tmp/journal-replay.sh" if args.boot_only else "/tmp/consolidation.sh"
            marker = "Ext2FS: Unsupported filesystem features; refusing mount without modifying the volume" if args.boot_only else "EXT2_PERSISTENCE_EXECUTED"
            capacity.run_guest(frozen, root, disk, case, guest_script, marker, args.timeout, cpus=args.cpus,
                               require_test_summary=not args.boot_only,
                               diagnostic_types=args.diagnostic_types.resolve(strict=True) if args.diagnostic_types else None)
            if not args.boot_only:
                crash.command(["e2fsck", "-f", "-n", disk], case / "fsck-final.log")
                expected_name = "renamed" if phase == 1 else "final"
                expected = b"beforeafter" if phase == 1 else b"beforeafteragain"
                assert crash.dump(disk, f"work/{expected_name}", case / "document.bin") == expected
                assert crash.dump(disk, "work/deleted", case / "deleted.bin") is None
        except Exception as failure:
            error = str(failure)
        result = dict(iteration=iteration, phase=phase, passed=error is None, error=error,
                      seconds=round(time.monotonic() - started, 2), artifacts=str(case), disk_sha256=sha(disk))
        results.append(result)
        (work / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        success = "boot reached original workload" if args.boot_only else "files and fsck verified"
        print(f"{'PASS' if error is None else 'FAIL'}: {args.label} run {iteration}, {args.cpus} CPU(s): {error or success}", flush=True)
        if error and args.persistence:
            break
    assert sha(source) == original_hash
    summary = dict(provenance, attempted=len(results), passed=sum(r["passed"] for r in results),
                   failed=sum(not r["passed"] for r in results), original_root_unchanged=True,
                   persistence=args.persistence)
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
