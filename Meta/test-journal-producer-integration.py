#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Validate C++ journal after-images with Python and Linux on disposable files.

This tests the producer's format and replay semantics, not device durability or
Serenity filesystem integration. All filesystem mutations use new fixture files;
the C++ adapter itself emits bytes and performs no device I/O.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

from jbd2_reader import replay_plan

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("contract", ROOT / "Meta/test-ext3-journal-contract.py")
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
BLOCK = contract.BLOCK


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def journal_geometry(disk, folder):
    physical = [int(value) for value in contract.command(
        ["debugfs", "-n", "-R", "blocks <8>", disk], folder / "journal-blocks.log").split()]
    extracted = folder / "clean-journal.bin"
    contract.command(["debugfs", "-n", "-R", f"dump <8> {extracted}", disk], folder / "journal-dump.log")
    journal = extracted.read_bytes()
    length = struct.unpack_from(">I", journal, 16)[0]
    commands = folder / "journal-bmap.commands"
    commands.write_text("".join(f"bmap <8> {index}\n" for index in range(length)))
    output = contract.command(["debugfs", "-n", "-f", commands, disk], folder / "journal-bmap.log")
    # debugfs echoes requests, then prints exactly one decimal physical block.
    mapping = [int(line) for line in output.splitlines() if re.fullmatch(r"\d+", line)]
    assert len(mapping) == length and len(set(mapping)) == length
    assert set(mapping).issubset(physical)
    return journal, mapping, physical


def produce(binary, journal, mapping, physical, uuid, filesystem_blocks, writes, committed):
    wire = struct.pack(">7I", filesystem_blocks, BLOCK, len(mapping), len(physical), len(uuid), len(writes), int(committed))
    wire += b"".join(struct.pack(">I", block) for block in mapping + physical)
    wire += uuid + struct.pack(">I", len(journal)) + journal
    wire += b"".join(struct.pack(">I", block) + data for block, data in writes)
    result = subprocess.run([str(binary)], input=wire, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"C++ producer failed ({result.returncode}): {result.stderr!r}")
    assert len(result.stdout) == len(journal)
    return result.stdout


def build_adapter(cxx, build, binary):
    subprocess.run([
        cxx, "-std=c++26", "-Wall", "-Wextra", "-Werror", "-Wno-literal-suffix",
        f"-I{ROOT}", f"-I{ROOT / 'Userland/Libraries'}", f"-I{build}",
        str(ROOT / "Meta/test-journal-producer.cpp"), f"-L{build / 'lib'}",
        "-llagom-ak", f"-Wl,-rpath,{build / 'lib'}", "-o", str(binary),
    ], check=True)


def verify_exact_files(disk, operation, new, folder, created):
    expected = {"sentinel": b"\x7b" * BLOCK, "victim": None, "destination": None}
    if operation == "create":
        expected["victim"] = created if new else None
    elif operation == "unlink":
        expected["victim"] = None if new else b"A" * 32 * BLOCK
    elif operation == "rename":
        expected["victim"] = None if new else b"A" * 32 * BLOCK
        expected["destination"] = (b"A" if new else b"B") * 32 * BLOCK
    else:
        expected["victim"] = b"A" * (BLOCK if new else 32 * BLOCK)
    for name, contents in expected.items():
        assert contract.dump(disk, name, folder) == contents, f"Unexpected {name} contents"


def run(binary):
    work = Path(tempfile.mkdtemp(prefix="journal-producer-", dir=ROOT / "Build"))
    print(f"Artifacts: {work}", flush=True)
    results = []
    for label in ("create", "create-escaped", "unlink", "rename", "truncate"):
        operation = "create" if label == "create-escaped" else label
        folder = work / label
        folder.mkdir()
        baseline = folder / "baseline.ext3"
        with baseline.open("wb") as stream:
            stream.truncate(32 * 1024 * 1024)
        contract.command(["mke2fs", "-q", "-t", "ext3", "-F", "-b", str(BLOCK), "-I", "128",
                          "-O", "metadata_csum,^64bit", baseline], folder / "mkfs.log")
        files = dict(sentinel=b"\x7b" * BLOCK, a=b"A" * 32 * BLOCK,
                     b=b"B" * 32 * BLOCK, created=b"\x5a" * 32 * BLOCK)
        if label == "create-escaped":
            # Journal magic at the start of every new data block requires the
            # producer's escape flag and Linux replay's exact restoration.
            files["created"] = (struct.pack(">I", 0xC03B3998) + b"\x5a" * (BLOCK - 4)) * 32
        for name, data in files.items():
            (folder / name).write_bytes(data)
        requests = [f"write {folder / 'sentinel'} /sentinel"]
        if operation != "create":
            requests.append(f"write {folder / 'a'} /victim")
        if operation == "rename":
            requests.append(f"write {folder / 'b'} /destination")
        requests += ["journal_open -c -v 3", "journal_close"]
        contract.debugfs(baseline, requests, folder, "baseline")
        contract.check(baseline, folder, "baseline-fsck")
        journal, mapping, physical = journal_geometry(baseline, folder)
        desired = folder / "desired.ext3"
        shutil.copyfile(baseline, desired)
        changes = dict(create=[f"write {folder / 'created'} /victim"], unlink=["rm /victim"],
                       rename=["rm /destination", "ln /victim /destination", "unlink /victim"],
                       truncate=["punch /victim 1", "set_inode_field /victim size 4096"])
        contract.debugfs(desired, changes[operation], folder, "desired")
        contract.check(desired, folder, "desired-fsck")
        verify_exact_files(desired, operation, True, folder, files["created"])
        before, after = baseline.read_bytes(), desired.read_bytes()
        targets = [block for block in range(len(before) // BLOCK)
                   if before[block * BLOCK:(block + 1) * BLOCK] != after[block * BLOCK:(block + 1) * BLOCK]]
        assert targets and not set(physical).intersection(targets)
        writes = [(block, after[block * BLOCK:(block + 1) * BLOCK]) for block in targets]
        # Keep needs_recovery in every logged superblock image until checkpoint.
        if 0 in targets:
            index = targets.index(0)
            payload = bytearray(writes[index][1])
            flags = struct.unpack_from("<I", payload, 1120)[0]
            struct.pack_into("<I", payload, 1120, flags | 4)
            struct.pack_into("<I", payload, 2044, contract.crc32c(0xFFFFFFFF, payload[1024:2044]))
            writes[index] = (0, bytes(payload))
        uuid = before[1128:1144]
        filesystem_blocks = struct.unpack_from("<I", before, 1028)[0]
        source_hashes = {str(path): sha256(path) for path in (baseline, desired)}
        for scenario in ("uncommitted", "committed", "partial-checkpoint", "after-images-twice"):
            case = folder / scenario
            case.mkdir()
            disk = case / "disk.ext3"
            shutil.copyfile(baseline, disk)
            rounds = 2 if scenario == "after-images-twice" else 1
            for iteration in range(rounds):
                image = produce(binary, journal, mapping, physical, uuid, filesystem_blocks,
                                writes, scenario != "uncommitted")
                tids, planned = replay_plan(image, filesystem_blocks, set(physical), uuid)
                assert planned == ([] if scenario == "uncommitted" else writes)
                assert len(tids) == (0 if scenario == "uncommitted" else 1)
                (case / f"journal-{iteration}.bin").write_bytes(image)
                with disk.open("r+b") as stream:
                    for logical, physical_block in enumerate(mapping):
                        stream.seek(physical_block * BLOCK)
                        stream.write(image[logical * BLOCK:(logical + 1) * BLOCK])
                    if scenario == "partial-checkpoint":
                        for block, data in writes[:max(1, len(writes) // 2)]:
                            stream.seek(block * BLOCK)
                            stream.write(data)
                contract.mark_needs_replay(disk)
                contract.check(disk, case, f"linux-replay-{iteration}", repair=True)
                contract.check(disk, case, f"verified-{iteration}")
                verify_exact_files(disk, operation, scenario != "uncommitted", case, files["created"])
            assert all(sha256(Path(path)) == digest for path, digest in source_hashes.items())
            result = dict(operation=label, scenario=scenario, passed=True, logged_blocks=len(writes),
                          producer="cpp", python_plan_matches=True, linux_replay_clean=True,
                          exact_file_contents=True, source_hashes=source_hashes, sources_unchanged=True,
                          adapter_sha256=sha256(binary), artifacts=str(case))
            results.append(result)
            (work / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            print(f"PASS: {label}/{scenario}; {len(writes)} C++ after-images", flush=True)
    print(f"Passed {len(results)} C++ producer/Linux replay scenarios; no Serenity writable mount enabled", flush=True)
    return work


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=ROOT / "Build/storage-foundation")
    parser.add_argument("--cxx", default="g++-14")
    parser.add_argument("--producer", type=Path, help="Use an already built C++ producer adapter")
    args = parser.parse_args()
    for tool in ("mke2fs", "debugfs", "e2fsck"):
        if not shutil.which(tool):
            parser.error(f"Missing {tool}")
    if args.producer:
        run(args.producer.resolve(strict=True))
    else:
        with tempfile.TemporaryDirectory(prefix="journal-producer-adapter-") as temporary:
            binary = Path(temporary) / "producer"
            build_adapter(args.cxx, args.build.resolve(strict=True), binary)
            run(binary)


if __name__ == "__main__":
    main()
