#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

import importlib.util
import os
from pathlib import Path
import shutil
import struct
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

native = load("native", ROOT / "recovery/native.py")
fixtures = load("fixtures", ROOT / "test-recover-serenity-image.py")


@unittest.skipUnless(all(shutil.which(tool) for tool in ("mke2fs", "debugfs", "e2fsck")), "requires e2fsprogs")
class NativeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.source = self.fixture.gpt()
        self.target = self.fixture.work / "target.img"
        self.target_bytes = self.source.stat().st_size + 2 * 1024 * 1024
        with self.target.open("wb") as stream:
            stream.truncate(self.target_bytes)

    def clone(self, target_bytes=None):
        with self.source.open("rb", buffering=0) as source, self.target.open("r+b", buffering=0) as target:
            return native.clone_to_blank(source.fileno(), target.fileno(), self.source.stat().st_size,
                                         self.target_bytes if target_bytes is None else target_bytes, lambda _: None)

    def test_blank_larger_clone_relocates_gpt_and_preserves_source(self):
        before = native.image.digest(self.source)
        report = self.clone()
        self.assertTrue(report["clone_verified"])
        self.assertEqual(native.image.digest(self.source), before)
        selected = native.image.layout(self.target)
        self.assertEqual(selected["offset"], self.fixture.root_start * 512)
        self.assertEqual(selected["image_bytes"], self.target_bytes)
        with self.target.open("rb") as stream:
            stream.seek(458)
            self.assertEqual(struct.unpack("<I", stream.read(4))[0], self.target_bytes // 512 - 1)
            stream.seek(2048 * 512)
            expected = b"EFI-content-must-survive"
            self.assertEqual(stream.read(len(expected)), expected)

    def test_same_size_clone_is_byte_identical(self):
        size = self.source.stat().st_size
        with self.target.open("r+b") as stream:
            stream.truncate(size)
        self.clone(size)
        self.assertEqual(native.image.digest(self.source), native.image.digest(self.target))

    def test_nonempty_tail_rejects_without_any_writes(self):
        with self.target.open("r+b") as stream:
            stream.seek(self.target_bytes - 1)
            stream.write(b"x")
        before = native.image.digest(self.target)
        with self.assertRaisesRegex(ValueError, "contains data"):
            self.clone()
        self.assertEqual(native.image.digest(self.target), before)

    def test_small_destination_rejects_without_any_writes(self):
        before = native.image.digest(self.target)
        with self.assertRaisesRegex(ValueError, "at least as large"):
            self.clone(self.source.stat().st_size - 512)
        self.assertEqual(native.image.digest(self.target), before)

    def test_source_alias_rejects_without_any_writes(self):
        before = native.image.digest(self.source)
        with self.source.open("rb", buffering=0) as source, self.source.open("r+b", buffering=0) as target:
            with self.assertRaisesRegex(ValueError, "same device"):
                native.clone_to_blank(source.fileno(), target.fileno(), self.source.stat().st_size,
                                      self.source.stat().st_size, lambda _: None)
        self.assertEqual(native.image.digest(self.source), before)

    def test_bad_gpt_rejects_before_writing_destination(self):
        with self.source.open("r+b") as stream:
            stream.seek(528)
            stream.write(bytes(4))
        before = native.image.digest(self.target)
        with self.assertRaises(ValueError):
            self.clone()
        self.assertEqual(native.image.digest(self.target), before)

    def test_host_device_menu_is_disabled(self):
        with self.assertRaisesRegex(ValueError, "recovery environment"):
            native.require_disk("sda")

    def test_cancelled_confirmation_does_not_touch_devices(self):
        info = dict(path=str(self.source), name="sda", bytes=self.source.stat().st_size,
                    sector=512, identity="fixture", readonly=False)
        with patch.object(native, "require_disk", return_value=info), \
                patch.object(native, "partition_path", return_value=self.fixture.root), \
                patch.object(native, "make_source_readonly", side_effect=AssertionError("Not authorized")), \
                self.assertRaisesRegex(ValueError, "Cancelled"):
            native.recover("sda", "sdb", lambda _: "wrong")


if __name__ == "__main__":
    unittest.main()
