#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Prove unsupported ext2 feature flags are rejected without any volume writes."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import tempfile

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("capacity", REPO / "Meta/test-ext2-capacity.py")
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)
crash = capacity.crash


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-image", type=Path, required=True)
    args = parser.parse_args()
    source = args.root_image.resolve(strict=True)
    build = REPO / "Build/x86_64"
    binary = build / "Tests/Kernel/TestExt2Features"
    if not source.is_file() or not binary.is_file():
        parser.error("Supply a regular offline root and build TestExt2Features first")
    work = Path(tempfile.mkdtemp(prefix="ext2-features-", dir=REPO / "Build"))
    print(f"Artifacts: {work}", flush=True)
    root = work / "root.ext2"
    shutil.copyfile(source, root)
    crash.command(["e2fsck", "-f", "-n", root], work / "root-fsck.log")
    crash.install(root, binary, "/bin/TestExt2Features", work / "install.log")
    script = work / "guest.sh"
    script.write_text("#!/bin/Shell\n" + "".join(f"mknod /tmp/feature{index} b 3 {index + 1}\n" for index in range(3))
                      + "env SERENITY_EXT2_FEATURE_DEVICE_PREFIX=/tmp/feature /bin/TestExt2Features\nsleep 300\n")
    crash.install(root, script, "/tmp/ext2-features.sh", work / "install-script.log")
    disks = []
    hashes = []
    for name, offset, flag in (("journal", 92, 4), ("extents", 96, 0x40), ("metadata-checksum", 100, 0x400)):
        disk = work / f"{name}.ext2"
        with disk.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024)
        crash.command(["mke2fs", "-q", "-t", "ext2", "-F", "-b", "4096", "-I", "128", "-L", "SerenityFeat", disk],
                      work / f"mkfs-{name}.log")
        with disk.open("r+b") as stream:
            stream.seek(1024 + offset)
            previous, = struct.unpack("<I", stream.read(4))
            stream.seek(1024 + offset)
            stream.write(struct.pack("<I", previous | flag))
        disks.append(disk)
        hashes.append(digest(disk))
    capacity.run_guest(build, root, disks[0], work, "/tmp/ext2-features.sh", "EXT2_UNSUPPORTED_FEATURES_REJECTED", 120,
                       extra_disks=disks[1:])
    assert all(digest(path) == before for path, before in zip(disks, hashes)), "Refused volume was modified"
    (work / "results.json").write_text(json.dumps(dict(rejected=[path.stem for path in disks],
                                                      all_volume_hashes_unchanged=True), indent=2) + "\n")
    print("PASS: journal, extents and metadata checksums rejected; every volume byte unchanged", flush=True)


if __name__ == "__main__":
    main()
