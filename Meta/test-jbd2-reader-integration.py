#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Compare the independent reader with Linux journals on disposable image copies.

e2fsprogs creates records but does not perform replay on the reader's copy.
Retiring the log below is a test-only shortcut, not a durable recovery utility.
"""

import argparse
from functools import partial
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct

from jbd2_reader import crc32c, replay_plan

spec = importlib.util.spec_from_file_location("contract", Path(__file__).with_name("test-ext3-journal-contract.py"))
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def observe(disk, operation, scenario, folder, iteration, cpp_reader=None):
    prefix = folder / f"independent-{iteration}"
    prefix.mkdir()
    before = hashlib.sha256(disk.read_bytes()).hexdigest()
    journal_path = prefix / "journal.bin"
    # A partial checkpoint can temporarily disagree with bitmap checksums.
    # Extract the unchanged journal inode without asking debugfs to replay.
    contract.command(["debugfs", "-n", "-R", f"dump <8> {journal_path}", disk], prefix / "dump.log")
    journal = journal_path.read_bytes()
    physical = [int(value) for value in contract.command(["debugfs", "-n", "-R", "blocks <8>", disk], prefix / "blocks.log").split()]
    image = disk.read_bytes()
    uuid = image[1128:1144]
    blocks = struct.unpack_from("<I", image, 1028)[0]
    tids, writes = replay_plan(journal, blocks, set(physical), uuid)
    cpp_matches = False
    if cpp_reader is not None:
        spec = importlib.util.spec_from_file_location("cpp_adapter", Path(__file__).with_name("test-kernel-journal-reader.py"))
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        cpp_plan = adapter.decode_cpp_plan(adapter.invoke_cpp_reader(cpp_reader, journal, blocks, set(physical), uuid))
        assert cpp_plan == (tids, writes), "C++ replay plan differs from independent Python reader"
        tids, writes = cpp_plan
        cpp_matches = True
    assert len(tids) == (0 if scenario == "uncommitted" else 1)
    assert bool(writes) == (scenario != "uncommitted")
    candidate = prefix / "replayed.ext3"
    shutil.copyfile(disk, candidate)
    # Reapply the same plan twice to model restarting after an interrupted
    # checkpoint. Keep the input journal intact until both passes complete.
    with candidate.open("r+b") as stream:
        for _ in range(2):
            for number, data in writes:
                stream.seek(number * contract.BLOCK)
                stream.write(data)
        # Test-only retirement, after all planned writes. Production code must
        # add ordered device flushes, error handling and persistent log state.
        superblock = bytearray(journal[:1024])
        struct.pack_into(">I", superblock, 28, 0)
        struct.pack_into(">I", superblock, 252, 0)
        struct.pack_into(">I", superblock, 252, crc32c(0xFFFFFFFF, superblock))
        stream.seek(physical[0] * contract.BLOCK)
        stream.write(superblock)
        stream.seek(1024)
        fs_sb = bytearray(stream.read(1024))
        flags, = struct.unpack_from("<I", fs_sb, 96)
        struct.pack_into("<I", fs_sb, 96, flags & ~4)
        struct.pack_into("<I", fs_sb, 1020, crc32c(0xFFFFFFFF, fs_sb[:1020]))
        stream.seek(1024)
        stream.write(fs_sb)
    contract.check(candidate, prefix, "read-only-fsck")
    contract.verify_files(candidate, operation, scenario != "uncommitted", prefix)
    assert hashlib.sha256(disk.read_bytes()).hexdigest() == before
    (prefix / "result.json").write_text(json.dumps(dict(
        transaction_ids=tids, replayed_blocks=len(writes), source_unchanged=True,
        source_sha256=before, reader="cpp" if cpp_reader is not None else "python",
        cpp_plan_matches_python=cpp_matches,
        cpp_reader_sha256=hashlib.sha256(cpp_reader.read_bytes()).hexdigest() if cpp_reader is not None else None,
        repeated_replay=True, read_only_fsck_clean=True), indent=2) + "\n")
    print(f"Independent replay PASS: {operation}/{scenario}/{iteration}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpp-reader", type=Path, help="Also compare with this compiled kernel parser adapter and replay its plan")
    args = parser.parse_args()
    reader = args.cpp_reader.resolve() if args.cpp_reader else None
    contract.main(replay_observer=partial(observe, cpp_reader=reader), checksum_v3=True)
