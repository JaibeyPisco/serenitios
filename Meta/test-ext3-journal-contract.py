#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Host-only JBD2 interoperability experiment; NOT a Serenity journal implementation.

Uses e2fsprogs to log complete block after-images in disposable ext3 files.
Tests incomplete transactions, committed redo, interrupted checkpoint and
repeated after-images. Does not modify existing images or enable kernel support.
"""

import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

from jbd2_reader import crc32c

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("recovery", REPO / "Meta/recover-serenity-image.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
BLOCK = 4096


def command(args, log):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True)
    output = result.stdout + result.stderr
    log.write_text(output)
    if result.returncode:
        raise RuntimeError(f"Command failed; see {log}")
    return result.stdout


def debugfs(disk, requests, folder, name):
    script = folder / f"{name}.commands"
    script.write_text("\n".join(requests) + "\n")
    output = command(["debugfs", "-w", "-f", script, disk], folder / f"{name}.log")
    # debugfs may return zero for individual failed commands.
    text = (folder / f"{name}.log").read_text()
    if any(error in text for error in ("Command not found", "Usage:", "File not found", "Invalid argument")):
        raise RuntimeError(f"debugfs request failed; see {folder / (name + '.log')}")
    return output


def check(disk, folder, name, repair=False):
    code, log = recovery.run_fsck(disk, repair=repair)
    (folder / f"{name}.log").write_text(log)
    if repair:
        if code not in (0, 1, 2, 3) or "? yes" in log:
            raise RuntimeError(f"Journal replay needed metadata repair; see {folder / (name + '.log')}")
        if "recovering journal" not in log:
            raise RuntimeError("Journal was not replayed")
    elif recovery.unfixed_errors(code, log):
        raise RuntimeError(f"Filesystem inconsistent; see {folder / (name + '.log')}")


def dump(disk, name, folder):
    output = folder / f"{name}.bin"
    if output.exists():
        output.unlink()
    subprocess.run(["debugfs", "-R", f"dump /{name} {output}", str(disk)], capture_output=True, check=True)
    return output.read_bytes() if output.exists() else None


def verify_files(disk, operation, new, folder):
    assert dump(disk, "sentinel", folder) == b"\x7b" * BLOCK
    victim = dump(disk, "victim", folder)
    destination = dump(disk, "destination", folder)
    if operation == "create":
        assert victim == (b"\x5a" * 32 * BLOCK if new else None)
    elif operation == "unlink":
        assert victim == (None if new else b"A" * 32 * BLOCK)
    elif operation == "rename":
        assert victim == (None if new else b"A" * 32 * BLOCK)
        assert destination == (b"A" if new else b"B") * 32 * BLOCK
    else:
        assert victim == b"A" * (BLOCK if new else 32 * BLOCK)


def mark_needs_replay(disk):
    with disk.open("r+b") as stream:
        stream.seek(1024)
        sb = bytearray(stream.read(1024))
        value, = struct.unpack_from("<I", sb, 96)
        struct.pack_into("<I", sb, 96, value | 4)
        if struct.unpack_from("<I", sb, 100)[0] & 0x400:
            struct.pack_into("<I", sb, 1020, crc32c(0xFFFFFFFF, sb[:1020]))
        stream.seek(1024)
        stream.write(sb)


def main(replay_observer=None, checksum_v3=False):
    for tool in ("mke2fs", "debugfs", "e2fsck"):
        if not shutil.which(tool):
            raise RuntimeError(f"Missing {tool}")
    work = Path(tempfile.mkdtemp(prefix="journal-contract-", dir=REPO / "Build"))
    print(f"Artifacts: {work}", flush=True)
    results = []
    for operation in ("create", "unlink", "rename", "truncate"):
        folder = work / operation
        folder.mkdir()
        baseline = folder / "baseline.ext3"
        with baseline.open("wb") as stream:
            stream.truncate(32 * 1024 * 1024)
        options = ["-O", "metadata_csum,^64bit"] if checksum_v3 else []
        command(["mke2fs", "-q", "-t", "ext3", "-F", "-b", str(BLOCK), "-I", "128", *options, baseline], folder / "mkfs.log")
        sentinel, a, b, created = [folder / name for name in ("sentinel-data", "A-data", "B-data", "new-data")]
        for path, data in ((sentinel, b"\x7b" * BLOCK), (a, b"A" * 32 * BLOCK), (b, b"B" * 32 * BLOCK), (created, b"\x5a" * 32 * BLOCK)):
            path.write_bytes(data)
        requests = [f"write {sentinel} /sentinel"]
        if operation != "create":
            requests.append(f"write {a} /victim")
        if operation == "rename":
            requests.append(f"write {b} /destination")
        debugfs(baseline, requests, folder, "baseline")
        check(baseline, folder, "baseline-fsck")
        journal_blocks = {int(value) for value in command(["debugfs", "-R", "blocks <8>", baseline], folder / "journal-blocks.log").split()}
        desired = folder / "desired.ext3"
        shutil.copyfile(baseline, desired)
        changes = dict(create=[f"write {created} /victim"], unlink=["rm /victim"],
                       rename=["rm /destination", "ln /victim /destination", "unlink /victim"],
                       truncate=["punch /victim 1", "set_inode_field /victim size 4096"])
        debugfs(desired, changes[operation], folder, "desired")
        check(desired, folder, "desired-fsck")
        verify_files(desired, operation, True, folder)
        before, after = baseline.read_bytes(), desired.read_bytes()
        blocks = [index for index in range(len(before) // BLOCK)
                  if before[index * BLOCK:(index + 1) * BLOCK] != after[index * BLOCK:(index + 1) * BLOCK]]
        assert blocks and not journal_blocks.intersection(blocks)
        payload = bytearray().join(after[index * BLOCK:(index + 1) * BLOCK] for index in blocks)
        # The filesystem superblock must keep needs_recovery set until the
        # committed transaction is checkpointed and its log is retired.
        if 0 in blocks:
            offset = blocks.index(0) * BLOCK + 1120
            value, = struct.unpack_from("<I", payload, offset)
            struct.pack_into("<I", payload, offset, value | 4)
            if checksum_v3:
                base = blocks.index(0) * BLOCK + 1024
                struct.pack_into("<I", payload, base + 1020, crc32c(0xFFFFFFFF, payload[base:base + 1020]))
        payload_path = folder / "after-images.bin"
        payload_path.write_bytes(payload)
        block_list = ",".join(str(index) for index in blocks)
        for scenario in ("uncommitted", "committed", "partial-checkpoint", "after-images-twice"):
            case = folder / scenario
            case.mkdir()
            disk = case / "disk.ext3"
            shutil.copyfile(baseline, disk)
            rounds = 2 if scenario == "after-images-twice" else 1
            for iteration in range(rounds):
                uncommitted = "-c " if scenario == "uncommitted" else ""
                debugfs(disk, ["journal_open -c -v 3", f"journal_write {uncommitted}-b {block_list} {payload_path}", "journal_close"],
                        case, f"log-{iteration}")
                if checksum_v3:
                    # e2fsprogs 1.47.0 do_journal.c copies the first tag UUID
                    # using typed-pointer arithmetic (jdbt + tag_bytes). Linux
                    # replay skips this UUID, but our strict reader checks it.
                    # Normalize that producer defect, not the reader's checks.
                    mapping = [int(value) for value in command(["debugfs", "-R", "blocks <8>", disk], case / "journal-map.log").split()]
                    with disk.open("r+b") as stream:
                        stream.seek(mapping[0] * BLOCK)
                        jsb = stream.read(1024)
                        stream.seek(mapping[1] * BLOCK)
                        descriptor = bytearray(stream.read(BLOCK))
                        assert struct.unpack_from(">III", descriptor)[:2] == (0xC03B3998, 1)
                        descriptor[28:44] = jsb[48:64]
                        # The stray UUID can also contaminate tag flag/high
                        # fields that the producer only partially initializes.
                        # Restore the intended 32-bit targets and flags; retain
                        # the producer's independently computed data checksums.
                        for index, number in enumerate(blocks):
                            tag_offset = 12 if index == 0 else 44 + (index - 1) * 16
                            tag_flags = (0 if index == 0 else 2) | (8 if index == len(blocks) - 1 else 0)
                            struct.pack_into(">III", descriptor, tag_offset, number, tag_flags, 0)
                        struct.pack_into(">I", descriptor, BLOCK - 4, 0)
                        struct.pack_into(">I", descriptor, BLOCK - 4,
                                         crc32c(crc32c(0xFFFFFFFF, jsb[48:64]), descriptor))
                        stream.seek(mapping[1] * BLOCK)
                        stream.write(descriptor)
                mark_needs_replay(disk)
                if scenario == "uncommitted":
                    # debugfs does not publish the uncommitted tail. Publish
                    # its start explicitly so readers actually scan it rather
                    # than immediately returning for s_start == 0.
                    journal_first = int(command(["debugfs", "-R", "blocks <8>", disk], case / "journal-map.log").split()[0])
                    with disk.open("r+b") as stream:
                        stream.seek(journal_first * BLOCK)
                        sb = bytearray(stream.read(1024))
                        assert struct.unpack_from(">I", sb, 40)[0] == (0x10 if checksum_v3 else 0)
                        struct.pack_into(">I", sb, 28, struct.unpack_from(">I", sb, 20)[0])
                        if checksum_v3:
                            struct.pack_into(">I", sb, 252, 0)
                            struct.pack_into(">I", sb, 252, crc32c(0xFFFFFFFF, sb))
                        stream.seek(journal_first * BLOCK)
                        stream.write(sb)
                if scenario == "partial-checkpoint":
                    with disk.open("r+b") as stream:
                        for position, index in enumerate(blocks[:max(1, len(blocks) // 2)]):
                            stream.seek(index * BLOCK)
                            stream.write(payload[position * BLOCK:(position + 1) * BLOCK])
                if replay_observer is not None:
                    replay_observer(disk, operation, scenario, case, iteration)
                check(disk, case, f"replay-{iteration}", repair=True)
                check(disk, case, f"verified-{iteration}")
                verify_files(disk, operation, scenario != "uncommitted", case)
            result = dict(operation=operation, scenario=scenario, passed=True, logged_blocks=len(blocks),
                          sentinel_intact=True, directory_and_file_state="old" if scenario == "uncommitted" else "new",
                          artifacts=str(case))
            results.append(result)
            (work / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            print(f"PASS: {operation}, {scenario}, {len(blocks)} block after-images", flush=True)
    print(f"Passed {len(results)} host JBD2 contract scenarios; no Serenity journaling enabled", flush=True)


if __name__ == "__main__":
    main()
