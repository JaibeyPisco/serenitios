#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Run the independent reader's malformed-log suite through the C++ parser.

Builds a host adapter against an existing Lagom AK library in a new temporary
directory. Only disposable ordinary files are created; no guest/disk writes.
The Python reader remains the independent oracle for every accepted plan.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import tempfile
import unittest

from jbd2_reader import InvalidJournal, replay_plan as reference_plan


def invoke_cpp_reader(binary, journal, filesystem_blocks, journal_blocks, uuid):
    block_map = sorted(journal_blocks)
    wire = struct.pack(">III", filesystem_blocks, len(block_map), len(uuid))
    wire += b"".join(struct.pack(">I", block) for block in block_map) + uuid + journal
    return subprocess.run([str(binary)], input=wire, capture_output=True)


def decode_cpp_plan(result):
    if result.returncode != 0:
        raise AssertionError(f"C++ reader failure {result.returncode}: {result.stderr!r}")
    transactions, writes = [], []
    for line in result.stdout.decode("ascii").splitlines():
        fields = line.split()
        if fields[0] == "t":
            transactions.append(int(fields[1]))
        elif fields[0] == "w":
            writes.append((int(fields[1]), bytes.fromhex(fields[2])))
        else:
            raise AssertionError(f"Unexpected adapter output: {line!r}")
    return transactions, writes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=Path("Build/lagom"))
    parser.add_argument("--cxx", default="g++-14")
    parser.add_argument("--kernel-build", type=Path, help="Also check with this configured kernel's actual compiler flags")
    parser.add_argument("--integration", action="store_true", help="Also replay C++ plans on disposable filesystem copies and check with e2fsprogs")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    build = args.build.resolve()
    spec = importlib.util.spec_from_file_location("reader_tests", root / "Meta/test-jbd2-reader.py")
    tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tests)
    with tempfile.TemporaryDirectory(prefix="kernel-journal-reader-") as temporary:
        binary = Path(temporary) / "reader"
        if args.kernel_build:
            database = json.loads((args.kernel_build.resolve() / "compile_commands.json").read_text())
            entry = next(item for item in database if item["file"].endswith("Ext2FS/FileSystem.cpp"))
            original = shlex.split(entry["command"])
            command = []
            index = 0
            while index < len(original):
                if original[index] in ("-o", "-c"):
                    index += 2
                else:
                    command.append(original[index])
                    index += 1
            smoke = Path(temporary) / "kernel-smoke.cpp"
            smoke.write_text("#include <Kernel/FileSystem/Ext2FS/Journal.h>\n"
                             "#include <Kernel/FileSystem/Ext2FS/TransactionBuffer.h>\n"
                             "#include <Kernel/FileSystem/Ext2FS/JournalCommit.h>\n"
                             "#include <Kernel/FileSystem/Ext2FS/JournalReplay.h>\n"
                             "#include <Kernel/FileSystem/Ext2FS/JournalInode.h>\n")
            subprocess.run(command + ["-fsyntax-only", str(smoke)], cwd=entry["directory"], check=True)
        subprocess.run([
            args.cxx, "-std=c++26", "-Wall", "-Wextra", "-Werror", "-Wno-literal-suffix",
            f"-I{root}", f"-I{root / 'Userland/Libraries'}", f"-I{build}",
            str(root / "Meta/test-kernel-journal-reader.cpp"), f"-L{build / 'lib'}",
            "-llagom-ak", f"-Wl,-rpath,{build / 'lib'}", "-o", str(binary),
        ], check=True)

        def invoke(journal, filesystem_blocks, journal_blocks, uuid):
            return invoke_cpp_reader(binary, journal, filesystem_blocks, journal_blocks, uuid)

        def cpp_plan(journal, filesystem_blocks, journal_blocks, uuid):
            result = invoke(journal, filesystem_blocks, journal_blocks, uuid)
            if result.returncode == 1:
                # Also verify that the independent implementation rejects it.
                try:
                    reference_plan(journal, filesystem_blocks, journal_blocks, uuid)
                except InvalidJournal:
                    raise InvalidJournal("C++ parser rejected journal") from None
                raise AssertionError("C++ rejected a journal accepted by Python")
            actual = decode_cpp_plan(result)
            expected = reference_plan(journal, filesystem_blocks, journal_blocks, uuid)
            if actual != expected:
                raise AssertionError("C++ replay plan differs from independent Python oracle")
            return actual

        class ResourceLimits(unittest.TestCase):
            def test_supported_block_sizes(self):
                for size in (1024, 2048, 4096):
                    with self.subTest(size=size):
                        image = bytearray(16 * size)
                        image[:tests.SIZE] = tests.fixture()[0][:tests.SIZE]
                        struct.pack_into(">I", image, 12, size)
                        image[:tests.SIZE] = tests.seal(image[:tests.SIZE], 252, 0xFFFFFFFF)
                        payload = b"A" * size
                        descriptor = bytearray(size)
                        struct.pack_into(">III", descriptor, 0, tests.MAGIC, 1, 1)
                        checksum = tests.crc32c(tests.crc32c(tests.SEED, struct.pack(">I", 1)), payload)
                        struct.pack_into(">4I", descriptor, 12, 50, 8, 0, checksum)
                        descriptor[28:44] = tests.UUID
                        image[size:2 * size] = tests.seal(descriptor, size - 4)
                        image[2 * size:3 * size] = payload
                        commit = bytearray(size)
                        struct.pack_into(">III", commit, 0, tests.MAGIC, 2, 1)
                        image[3 * size:4 * size] = tests.seal(commit, 16)
                        self.assertEqual(cpp_plan(image, 1000, tests.FORBIDDEN, tests.UUID), ([1], [(50, payload)]))

            def revoke_fixture(self, records):
                image = bytearray(128 * tests.SIZE)
                image[:tests.SIZE] = tests.fixture()[0][:tests.SIZE]
                struct.pack_into(">I", image, 16, 128)
                image[:tests.SIZE] = tests.seal(image[:tests.SIZE], 252, 0xFFFFFFFF)
                position = 1
                for begin in range(0, records, 251):
                    numbers = list(range(begin, min(begin + 251, records)))
                    block = bytearray(tests.SIZE)
                    struct.pack_into(">4I", block, 0, tests.MAGIC, 5, 1, 16 + 4 * len(numbers))
                    for index, number in enumerate(numbers):
                        struct.pack_into(">I", block, 16 + 4 * index, number)
                    image[position * tests.SIZE:(position + 1) * tests.SIZE] = tests.seal(block, tests.SIZE - 4)
                    position += 1
                commit = bytearray(tests.SIZE)
                struct.pack_into(">III", commit, 0, tests.MAGIC, 2, 1)
                image[position * tests.SIZE:(position + 1) * tests.SIZE] = tests.seal(commit, 16)
                return image

            def test_metadata_budget_boundary(self):
                self.assertEqual(cpp_plan(self.revoke_fixture(16384), 200000, set(range(100000, 100128)), tests.UUID), ([1], []))

            def test_metadata_amplification_rejected(self):
                image = self.revoke_fixture(16385)
                block_map = set(range(100000, 100128))
                # Valid format, deliberately outside the C++ parser's resource policy.
                self.assertEqual(reference_plan(image, 200000, block_map, tests.UUID), ([1], []))
                self.assertEqual(invoke(image, 200000, block_map, tests.UUID).returncode, 3)

            def test_transaction_budget_rejected(self):
                image = bytearray(8192 * tests.SIZE)
                image[:tests.SIZE] = tests.fixture()[0][:tests.SIZE]
                struct.pack_into(">I", image, 16, 8192)
                image[:tests.SIZE] = tests.seal(image[:tests.SIZE], 252, 0xFFFFFFFF)
                for sequence in range(1, 4098):
                    commit = bytearray(tests.SIZE)
                    struct.pack_into(">III", commit, 0, tests.MAGIC, 2, sequence)
                    image[sequence * tests.SIZE:(sequence + 1) * tests.SIZE] = tests.seal(commit, 16)
                block_map = set(range(100000, 108192))
                expected = reference_plan(image, 200000, block_map, tests.UUID)
                self.assertEqual(len(expected[0]), 4097)
                self.assertEqual(invoke(image, 200000, block_map, tests.UUID).returncode, 3)

        tests.replay_plan = cpp_plan
        suite = unittest.defaultTestLoader.loadTestsFromModule(tests)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ResourceLimits))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            return 1
        if args.integration:
            subprocess.run([
                sys.executable, str(root / "Meta/test-jbd2-reader-integration.py"),
                "--cpp-reader", str(binary),
            ], cwd=root, check=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
