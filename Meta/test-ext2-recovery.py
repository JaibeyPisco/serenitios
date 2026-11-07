#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Remount copies of repaired crash-test disks in Serenity and verify synced data."""

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("capacity", REPO / "Meta/test-ext2-capacity.py")
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)
crash = capacity.crash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True, help="completed crash campaign directory")
    parser.add_argument("--root-image", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    for tool in ("qemu-system-x86_64", "debugfs", "e2fsck"):
        if not shutil.which(tool):
            parser.error(f"Missing {tool}")
    campaign = args.campaign.resolve(strict=True)
    results = json.loads((campaign / "results.json").read_text())
    build = REPO / "Build/x86_64"
    binary = build / "Tests/Kernel/TestExt2CrashConsistency"
    source = args.root_image.resolve(strict=True)
    if not source.is_file() or not binary.is_file():
        parser.error("Supply a regular offline root image and build TestExt2CrashConsistency")
    inputs = []
    for result in results:
        if not result["cut"]:
            continue
        recovered = (Path(result["directory"]) / "recovered.ext2").resolve(strict=True)
        if not recovered.is_relative_to(campaign) or not recovered.is_file():
            parser.error("Recovered input must be a regular file inside the campaign")
        inputs.append((result, recovered))
    if not inputs:
        parser.error("Campaign contains no repaired crash images")
    work = Path(tempfile.mkdtemp(prefix="ext2-recovery-", dir=REPO / "Build"))
    print(f"Artifacts: {work}", flush=True)
    root = work / "root.ext2"
    shutil.copyfile(source, root)
    crash.command(["e2fsck", "-f", "-n", root], work / "root-fsck.log")
    crash.install(root, binary, "/bin/TestExt2CrashConsistency", work / "install.log")
    script = work / "guest.sh"
    script.write_text("#!/bin/Shell\nmknod /tmp/ext2-recovery-device b 3 1\n"
                      "env SERENITY_EXT2_CRASH_DEVICE=/tmp/ext2-recovery-device "
                      "SERENITY_EXT2_CRASH_OPERATION=verify-recovery /bin/TestExt2CrashConsistency\n"
                      "sleep 300\n")
    crash.install(root, script, "/tmp/ext2-recovery.sh", work / "install-script.log")
    report = []
    for result, recovered in inputs:
        case = work / recovered.parent.name
        case.mkdir()
        disk = case / "disk.ext2"
        shutil.copyfile(recovered, disk)
        capacity.run_guest(build, root, disk, case, "/tmp/ext2-recovery.sh", "EXT2_RECOVERY_VERIFIED", args.timeout)
        crash.command(["e2fsck", "-f", "-n", disk], case / "fsck-final.log")
        sentinel = crash.dump(disk, "sentinel", case / "sentinel.bin") == b"\x7b" * 4096
        if not sentinel:
            raise RuntimeError(f"Acknowledged data mismatch after guest remount: {case}")
        report.append(dict(operation=result["operation"], cut=result["cut"], guest_remount_passed=True,
                           sentinel_intact=True, fsck_exit=0, source=str(recovered), artifacts=str(case)))
        (work / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"PASS: {result['operation']} cut {result['cut']}; remount, data, unmount, e2fsck", flush=True)
    print(f"Passed {len(report)}/{len(inputs)} repaired-image remounts", flush=True)


if __name__ == "__main__":
    main()
