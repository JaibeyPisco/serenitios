#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Exercise opt-in kernel journal replay on disposable plain-ext3 fixtures.

Requires a consistent offline standalone ext2 Serenity root. Only a new private
root copy and newly created test disks are written; the user's VM is not used.
The journal records come from the C++ producer and are independently parsed
before guests boot. No metadata checksums or unsupported ext4 features are used.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import tempfile

from jbd2_reader import crc32c, replay_plan

ROOT = Path(__file__).resolve().parent.parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


producer = module("producer", ROOT / "Meta/test-journal-producer-integration.py")
contract = producer.contract
capacity = module("capacity", ROOT / "Meta/test-ext2-capacity.py")
crash = capacity.crash
BLOCK = 4096


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_operation(folder, operation):
    folder.mkdir()
    baseline = folder / "baseline.ext3"
    with baseline.open("wb") as stream:
        stream.truncate(32 * 1024 * 1024)
    contract.command(["mke2fs", "-q", "-t", "ext3", "-F", "-b", str(BLOCK), "-I", "128",
                      "-O", "^resize_inode,^dir_index,^metadata_csum,^64bit", "-L", "SerenityJournal", baseline],
                     folder / "mkfs.log")
    files = dict(sentinel=b"\x7b" * BLOCK, a=b"A" * 32 * BLOCK,
                 b=b"B" * 32 * BLOCK, created=b"\x5a" * 32 * BLOCK)
    for name, data in files.items():
        (folder / name).write_bytes(data)
    requests = [f"write {folder / 'sentinel'} /sentinel"]
    if operation != "create":
        requests.append(f"write {folder / 'a'} /victim")
    if operation == "rename":
        requests.append(f"write {folder / 'b'} /destination")
    contract.debugfs(baseline, requests, folder, "baseline")
    contract.check(baseline, folder, "baseline-fsck")
    legacy, mapping, physical = producer.journal_geometry(baseline, folder)
    before = baseline.read_bytes()
    filesystem_blocks = struct.unpack_from("<I", before, 1028)[0]
    uuid = before[1128:1144]
    # Initialize a supported clean v3 superblock without enabling filesystem
    # metadata checksums. The C++ producer generates every transaction record.
    journal = bytearray(len(legacy))
    struct.pack_into(">9I", journal, 0, 0xC03B3998, 4, 0, BLOCK, len(mapping), 1, 1, 0, 0)
    struct.pack_into(">III", journal, 36, 0, 0x10, 0)
    journal[48:64] = uuid
    struct.pack_into(">I", journal, 64, 1)
    journal[80] = 4
    struct.pack_into(">I", journal, 252, crc32c(0xFFFFFFFF, journal[:1024]))
    with baseline.open("r+b") as stream:
        for logical, block in enumerate(mapping):
            stream.seek(block * BLOCK)
            stream.write(journal[logical * BLOCK:(logical + 1) * BLOCK])
    contract.check(baseline, folder, "v3-baseline-fsck")
    desired = folder / "desired.ext3"
    shutil.copyfile(baseline, desired)
    changes = dict(create=[f"write {folder / 'created'} /victim"], unlink=["rm /victim"],
                   rename=["rm /destination", "ln /victim /destination", "unlink /victim"],
                   truncate=["punch /victim 1", "set_inode_field /victim size 4096"])
    contract.debugfs(desired, changes[operation], folder, "desired")
    contract.check(desired, folder, "desired-fsck")
    before, after = baseline.read_bytes(), desired.read_bytes()
    targets = [index for index in range(len(before) // BLOCK)
               if before[index * BLOCK:(index + 1) * BLOCK] != after[index * BLOCK:(index + 1) * BLOCK]]
    assert targets and not set(physical).intersection(targets)
    writes = [(index, after[index * BLOCK:(index + 1) * BLOCK]) for index in targets]
    if 0 in targets:
        index = targets.index(0)
        payload = bytearray(writes[index][1])
        flags = struct.unpack_from("<I", payload, 1120)[0]
        struct.pack_into("<I", payload, 1120, flags | 4)
        writes[index] = (0, bytes(payload))
    return baseline, desired, bytes(journal), mapping, physical, uuid, filesystem_blocks, writes, files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-image", type=Path, required=True)
    parser.add_argument("--build", type=Path, default=ROOT / "Build/x86_64")
    parser.add_argument("--host-build", type=Path, default=ROOT / "Build/storage-foundation")
    parser.add_argument("--operations", nargs="+", choices=("create", "unlink", "rename", "truncate"),
                        default=["create", "unlink", "rename", "truncate"])
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--cpus", type=int, choices=(1, 2, 4, 8), default=1)
    parser.add_argument("--scenarios", nargs="+", choices=("default", "uncommitted", "committed", "partial-checkpoint", "bad-checksum", "bad-map", "readonly-fd", "bad-journal-inode", "bad-geometry", "bad-journal-allocation"),
                        help="Run only these named scenarios; omitted scenarios are not counted as passing")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    source = args.root_image.resolve(strict=True)
    build = args.build.resolve(strict=True)
    binary = build / "Tests/Kernel/TestExt2JournalReplay"
    if not source.is_file() or not binary.is_file():
        parser.error("Supply an offline regular root and build kernel_x86-64/TestExt2JournalReplay first")
    for tool in ("qemu-system-x86_64", "mke2fs", "debugfs", "e2fsck", "g++-14"):
        if not shutil.which(tool):
            parser.error(f"Missing {tool}")
    source_hash = digest(source)
    work = Path(tempfile.mkdtemp(prefix="kernel-journal-replay-", dir=ROOT / "Build"))
    print(f"Artifacts: {work}", flush=True)
    root = work / "root.ext2"
    shutil.copyfile(source, root)
    crash.command(["e2fsck", "-f", "-n", root], work / "root-fsck.log")
    crash.install(root, binary, "/bin/TestExt2JournalReplay", work / "install.log")
    adapter = work / "producer"
    producer.build_adapter("g++-14", args.host_build.resolve(strict=True), adapter)
    (work / "provenance.json").write_text(json.dumps(dict(
        source_root=str(source), source_root_sha256=source_hash,
        kernel_sha256=digest(build / "Kernel/Kernel"),
        test_sha256=digest(binary), producer_sha256=digest(adapter), cpus=args.cpus), indent=2) + "\n")
    results = []

    def execute(disk, case, operation, outcome):
        script = case / "guest.sh"
        script.write_text("#!/bin/Shell\nmknod /tmp/journal-fixture b 3 1\n"
                          f"env SERENITY_JOURNAL_DEVICE=/tmp/journal-fixture SERENITY_JOURNAL_OPERATION={operation} "
                          f"SERENITY_JOURNAL_OUTCOME={outcome} /bin/TestExt2JournalReplay\nsleep 300\n")
        crash.install(root, script, "/tmp/journal-replay.sh", case / "install-script.log")
        capacity.run_guest(build, root, disk, case, "/tmp/journal-replay.sh", "EXT2_JOURNAL_REPLAY_EXECUTED", args.timeout, cpus=args.cpus)

    for operation in args.operations:
        folder = work / operation
        baseline, desired, journal, mapping, physical, uuid, blocks, writes, files = prepare_operation(folder, operation)
        baseline_hash, desired_hash = digest(baseline), digest(desired)
        scenarios = ["default", "uncommitted", "committed", "partial-checkpoint", "bad-checksum", "bad-map"]
        if operation == "create":
            scenarios += ["readonly-fd", "bad-journal-inode", "bad-geometry", "bad-journal-allocation"]
        if args.scenarios:
            scenarios = [scenario for scenario in scenarios if scenario in args.scenarios]
        baseline_data = baseline.read_bytes()
        block_bitmap = struct.unpack_from("<I", baseline_data, BLOCK)[0]
        inode_table = struct.unpack_from("<I", baseline_data, BLOCK + 8)[0]
        for scenario in scenarios:
            case = folder / scenario
            case.mkdir()
            disk = case / "disk.ext3"
            shutil.copyfile(baseline, disk)
            committed = scenario != "uncommitted"
            case_writes = dict(writes)
            if scenario == "bad-journal-inode":
                payload = bytearray(case_writes.get(inode_table, baseline_data[inode_table * BLOCK:(inode_table + 1) * BLOCK]))
                struct.pack_into("<I", payload, 7 * 128 + 40, mapping[1])
                case_writes[inode_table] = bytes(payload)
            elif scenario == "bad-geometry":
                payload = bytearray(case_writes.get(0, baseline_data[:BLOCK]))
                struct.pack_into("<I", payload, 1028, blocks + 1)
                case_writes[0] = bytes(payload)
            elif scenario == "bad-journal-allocation":
                payload = bytearray(case_writes.get(block_bitmap, baseline_data[block_bitmap * BLOCK:(block_bitmap + 1) * BLOCK]))
                payload[mapping[0] // 8] &= ~(1 << (mapping[0] % 8))
                case_writes[block_bitmap] = bytes(payload)
            case_writes = list(case_writes.items())
            image = producer.produce(adapter, journal, mapping, physical, uuid, blocks, case_writes, committed)
            tids, plan = replay_plan(image, blocks, set(physical), uuid)
            assert plan == (case_writes if committed else [])
            with disk.open("r+b") as stream:
                for logical, block in enumerate(mapping):
                    stream.seek(block * BLOCK)
                    stream.write(image[logical * BLOCK:(logical + 1) * BLOCK])
                if scenario == "partial-checkpoint":
                    for block, data in writes[:max(1, len(writes) // 2)]:
                        stream.seek(block * BLOCK)
                        stream.write(data)
                elif scenario == "bad-checksum":
                    stream.seek(mapping[1] * BLOCK + 100)
                    stream.write(b"\xff")
                elif scenario == "bad-map":
                    # Corrupt only this disposable journal inode: duplicate two
                    # logical data blocks so recovery must refuse before writes.
                    stream.seek(BLOCK + 8)
                    inode_table = struct.unpack("<I", stream.read(4))[0]
                    stream.seek(inode_table * BLOCK + 7 * 128 + 44)
                    stream.write(struct.pack("<I", mapping[0]))
            contract.mark_needs_replay(disk)
            before_hash = digest(disk)
            outcome = ("default" if scenario == "default" else "readonly-fd" if scenario == "readonly-fd"
                       else "reject" if scenario.startswith("bad-") else "old" if scenario == "uncommitted" else "new")
            execute(disk, case, operation, outcome)
            rejected = outcome in ("default", "reject", "readonly-fd")
            if rejected:
                assert digest(disk) == before_hash, "Rejected filesystem changed"
            else:
                contract.check(disk, case, "kernel-final-fsck")
                producer.verify_exact_files(disk, operation, committed, case, files["created"])
                recovered_hash = digest(disk)
                retry = case / "second-mount"
                retry.mkdir()
                execute(disk, retry, operation, outcome)
                assert digest(disk) == recovered_hash, "Read-only remount/unmount changed recovered filesystem"
            assert digest(baseline) == baseline_hash and digest(desired) == desired_hash
            result = dict(operation=operation, scenario=scenario, passed=True,
                          rejected_volume_unchanged=rejected, readonly_mount=not rejected,
                          source_hashes_unchanged=True, artifacts=str(case))
            results.append(result)
            (work / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            print(f"PASS: {operation}/{scenario}", flush=True)
    if not results:
        raise RuntimeError("No matching scenarios were tested")
    assert digest(source) == source_hash
    print(f"Passed {len(results)} kernel journal replay scenarios; source root unchanged", flush=True)


if __name__ == "__main__":
    main()
