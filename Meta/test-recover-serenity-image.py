#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zlib

spec = importlib.util.spec_from_file_location("recovery", Path(__file__).with_name("recover-serenity-image.py"))
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@unittest.skipUnless(all(shutil.which(tool) for tool in ("e2fsck", "mke2fs", "debugfs")), "requires e2fsprogs")
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.root = self.work / "root.ext2"
        with self.root.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024)
        self.run_tool("mke2fs", "-q", "-t", "ext2", "-F", "-b", "4096", "-I", "128", self.root)
        sentinel = self.work / "sentinel.bin"
        sentinel.write_bytes(b"\x7b" * 4096)
        self.run_tool("debugfs", "-w", "-R", f'write "{sentinel}" /sentinel', self.root)
        self.output = self.work / "recovered.img"

    def run_tool(self, *args):
        return subprocess.run([str(arg) for arg in args], check=True, capture_output=True)

    def damage(self, path, offset=0):
        with path.open("r+b") as stream:
            stream.seek(offset + 1024 + 12)
            count, = struct.unpack("<I", stream.read(4))
            stream.seek(offset + 1024 + 12)
            stream.write(struct.pack("<I", count + 1))

    def sentinel(self, path):
        output = self.work / "extracted.bin"
        if output.exists():
            output.unlink()
        self.run_tool("debugfs", "-R", f'dump /sentinel "{output}"', path)
        return output.read_bytes()

    def gpt(self):
        disk = self.work / "disk.img"
        self.last = 43040
        self.root_start = 10240
        self.root_end = 43007
        self.entries = bytearray(16384)
        for index, kind, first, end in ((0, uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b").bytes_le, 2048, 10239),
                                        (1, recovery.LINUX, self.root_start, self.root_end)):
            struct.pack_into("<16s16sQQQ72s", self.entries, index * 128, kind, uuid.uuid4().bytes_le, first, end, 0, bytes(72))
        self.disk_id = uuid.uuid4().bytes_le
        with disk.open("wb") as stream:
            stream.truncate((self.last + 1) * 512)
        self.write_tables(disk)
        recovery.copy_range(self.root, disk, 0, self.root_start * 512, self.root.stat().st_size)
        with disk.open("r+b") as stream:
            stream.seek(2048 * 512)
            stream.write(b"EFI-content-must-survive")
        return disk

    def write_tables(self, disk):
        def header(current, alternate, table):
            data = bytearray(512)
            struct.pack_into("<8sIIIIQQQQ16sQIII", data, 0, b"EFI PART", 0x10000, 92, 0, 0,
                             current, alternate, 34, self.last - 33, self.disk_id, table, 128, 128, zlib.crc32(self.entries))
            struct.pack_into("<I", data, 16, zlib.crc32(data[:92]))
            return data
        mbr = bytearray(512)
        mbr[450] = 0xEE
        struct.pack_into("<II", mbr, 454, 1, self.last)
        mbr[510:] = b"\x55\xaa"
        with disk.open("r+b") as stream:
            for offset, data in ((0, mbr), (512, header(1, self.last, 2)), (1024, self.entries),
                                 ((self.last - 32) * 512, self.entries), (self.last * 512, header(self.last, 1, self.last - 32))):
                stream.seek(offset)
                stream.write(data)

    def test_check_only_detects_damage_without_changing_source(self):
        self.damage(self.root)
        before = recovery.digest(self.root)
        report = recovery.recover(self.root)
        self.assertEqual(report["status"], "needs-repair")
        self.assertEqual(recovery.digest(self.root), before)
        self.assertFalse(self.output.exists())

    def test_repairs_copy_and_preserves_synced_file(self):
        self.damage(self.root)
        before = recovery.digest(self.root)
        report = recovery.recover(self.root, self.output)
        self.assertEqual(report["status"], "recovered-copy")
        self.assertEqual(report["check_after"], 0)
        self.assertEqual(report["state_after"], 1)
        self.assertEqual(recovery.digest(self.root), before)
        self.assertEqual(self.sentinel(self.output), b"\x7b" * 4096)

    def test_clean_copy_is_identical(self):
        report = recovery.recover(self.root, self.output)
        self.assertEqual(report["status"], "clean-copy")
        self.assertEqual(recovery.digest(self.root), recovery.digest(self.output))

    def test_preserves_gpt_efi_and_partition_identifiers(self):
        disk = self.gpt()
        offset = self.root_start * 512
        length = self.root.stat().st_size
        self.damage(disk, offset)
        before = recovery.digest(disk)
        prefix, suffix = recovery.digest(disk, 0, offset), recovery.digest(disk, offset + length)
        recovery.recover(disk, self.output)
        self.assertEqual(recovery.digest(disk), before)
        self.assertEqual(recovery.digest(self.output, 0, offset), prefix)
        self.assertEqual(recovery.digest(self.output, offset + length), suffix)
        extracted = self.work / "root-repaired.ext2"
        extracted.touch()
        recovery.copy_range(self.output, extracted, offset, 0, length)
        self.assertEqual(self.sentinel(extracted), b"\x7b" * 4096)

    def test_rejects_corrupt_backup_gpt(self):
        disk = self.gpt()
        with disk.open("r+b") as stream:
            stream.seek(self.last * 512 + 16)
            stream.write(b"\x00\x00\x00\x00")
        with self.assertRaises(ValueError):
            recovery.recover(disk, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_damaged_primary_instead_of_treating_disk_as_ext2(self):
        disk = self.gpt()
        with disk.open("r+b") as stream:
            stream.seek(512)
            stream.write(bytes(8))
        with self.assertRaisesRegex(ValueError, "Primary GPT"):
            recovery.recover(disk, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_duplicate_partition_identifiers(self):
        disk = self.gpt()
        self.entries[128 + 16:128 + 32] = self.entries[16:32]
        self.write_tables(disk)
        with self.assertRaisesRegex(ValueError, "duplicate partition"):
            recovery.recover(disk, self.output)

    def test_rejects_overlapping_partitions(self):
        disk = self.gpt()
        struct.pack_into("<Q", self.entries, 128 + 32, 2048)
        self.write_tables(disk)
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            recovery.recover(disk, self.output)

    def test_rejects_ambiguous_root_partitions(self):
        disk = self.gpt()
        struct.pack_into("<16s16sQQQ72s", self.entries, 256, recovery.LINUX, uuid.uuid4().bytes_le, 34, 1023, 0, bytes(72))
        self.write_tables(disk)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            recovery.recover(disk, self.output)

    def test_refuses_existing_destination_and_symlink(self):
        self.output.write_bytes(b"preserve")
        with self.assertRaises(ValueError):
            recovery.recover(self.root, self.output)
        self.assertEqual(self.output.read_bytes(), b"preserve")
        self.output.unlink()
        self.output.symlink_to(self.work / "missing")
        with self.assertRaises(ValueError):
            recovery.recover(self.root, self.output)

    def test_racing_destination_is_not_overwritten(self):
        original_link = recovery.os.link
        def competing_link(source, output):
            Path(output).write_bytes(b"competitor")
            return original_link(source, output)
        with patch.object(recovery.os, "link", side_effect=competing_link), self.assertRaises(FileExistsError):
            recovery.recover(self.root, self.output)
        self.assertEqual(self.output.read_bytes(), b"competitor")

    def test_failed_repair_publishes_nothing(self):
        before = recovery.digest(self.root)
        with patch.object(recovery, "run_fsck", side_effect=[(4, "damage"), (4, "unrepaired")]), self.assertRaises(RuntimeError):
            recovery.recover(self.root, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(recovery.digest(self.root), before)

    def test_zero_exit_with_unfixed_errors_is_not_published(self):
        with patch.object(recovery, "run_fsck", side_effect=[(4, "damage"), (1, "repaired"), (0, "Fix? no")]), self.assertRaises(RuntimeError):
            recovery.recover(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_source_change_prevents_publication(self):
        original_digest = recovery.digest
        source_checks = 0
        def changing_digest(path, offset=0, length=None):
            nonlocal source_checks
            if path == self.root:
                source_checks += 1
                if source_checks > 1:
                    return "source-changed"
            return original_digest(path, offset, length)
        with patch.object(recovery, "digest", side_effect=changing_digest), self.assertRaisesRegex(RuntimeError, "Source changed"):
            recovery.recover(self.root, self.output)
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(shutil.which("qemu-img"), "requires qemu-img")
    def test_vdi_input_and_output_preserve_gpt_and_data(self):
        disk = self.gpt()
        self.damage(disk, self.root_start * 512)
        source = self.work / "source.vdi"
        output = self.work / "recovered.vdi"
        self.run_tool("qemu-img", "convert", "-f", "raw", "-O", "vdi", disk, source)
        before = recovery.digest(source)
        report = recovery.recover(source, output, "vdi", "vdi")
        self.assertEqual(report["check_after"], 0)
        self.assertEqual(recovery.digest(source), before)
        raw = self.work / "converted.img"
        self.run_tool("qemu-img", "convert", "-f", "vdi", "-O", "raw", output, raw)
        self.assertEqual(recovery.layout(raw)["kind"], "gpt-ext2")
        self.assertEqual(recovery.ext2_state(raw, self.root_start * 512, self.root.stat().st_size), 1)


if __name__ == "__main__":
    unittest.main()
