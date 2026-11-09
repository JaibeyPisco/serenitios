#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Host-only regression checks of crash inspection policy; no guest or disk tools.

Extraction is mocked so corruption and incomplete-operation outcomes can be
tested deterministically. These checks do not establish filesystem durability.
"""

import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("ext2_crash", Path(__file__).with_name("test-ext2-crash-consistency.py"))
crash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crash)
BASELINE = b"\x41" * (32 * 4096)
DESTINATION = b"\x42" * (32 * 4096)
CREATED = b"\x5a" * (32 * 4096)


class InspectionTests(unittest.TestCase):
    def files(self, victim=None, destination=None):
        return {"victim": victim, "destination": destination,
                "sentinel": b"\x7b" * 4096,
                "witness-small": b"\x19" * 4096,
                "witness-indirect": b"\xe3" * (37 * 4096)}

    def inspect_contents(self, operation, files):
        with patch.object(crash, "dump", side_effect=lambda image, name, output: files[name]):
            return crash.inspect_contents(Path("unused-image"), Path("unused-artifacts"), operation, "original")

    def test_interrupted_create_accepts_absent_empty_and_partial_expected_data(self):
        for data in (None, b"", CREATED[:17], CREATED):
            with self.subTest(size=None if data is None else len(data)):
                self.assertTrue(self.inspect_contents("create", self.files(data))["allowed_operation_state"])

    def test_create_rejects_foreign_bytes_oversize_and_unexpected_destination(self):
        for victim, destination in ((b"bad", None), (CREATED + b"\x5a", None), (CREATED, DESTINATION)):
            with self.subTest(victim_size=len(victim), destination_present=destination is not None):
                self.assertFalse(self.inspect_contents("create", self.files(victim, destination))["allowed_operation_state"])

    def test_unlink_accepts_old_or_new_state_only(self):
        for victim, expected in ((BASELINE, True), (None, True), (BASELINE[:-1], False), (b"", False)):
            with self.subTest(size=None if victim is None else len(victim)):
                self.assertEqual(self.inspect_contents("unlink", self.files(victim))["allowed_operation_state"], expected)

    def test_rename_accepts_complete_old_and_new_states(self):
        for victim, destination in ((BASELINE, DESTINATION), (None, BASELINE)):
            self.assertTrue(self.inspect_contents("rename", self.files(victim, destination))["allowed_operation_state"])

    def test_rename_rejects_lost_files_mixed_states_and_corrupt_bytes(self):
        for victim, destination in ((None, None), (None, DESTINATION), (BASELINE, None),
                                    (BASELINE, BASELINE), (None, BASELINE[:-1]), (BASELINE, b"bad")):
            with self.subTest(victim_present=victim is not None, destination_size=None if destination is None else len(destination)):
                self.assertFalse(self.inspect_contents("rename", self.files(victim, destination))["allowed_operation_state"])

    def test_truncate_accepts_original_or_requested_size_only(self):
        for victim, expected in ((BASELINE, True), (BASELINE[:4096], True), (BASELINE[:8192], False),
                                  (None, False), (b"\x42" * 4096, False)):
            with self.subTest(size=None if victim is None else len(victim)):
                self.assertEqual(self.inspect_contents("truncate", self.files(victim))["allowed_operation_state"], expected)

    def test_each_witness_detects_absence_truncation_extension_and_bit_corruption(self):
        for name in ("witness-small", "witness-indirect"):
            original = self.files()[name]
            for damaged in (None, original[:-1], original + b"\x00", original[:-1] + b"\x00"):
                with self.subTest(name=name, size=None if damaged is None else len(damaged)):
                    files = self.files()
                    files[name] = damaged
                    result = self.inspect_contents("unlink", files)
                    self.assertFalse(result["witnesses_intact"])
                    self.assertFalse(result["files"][name]["intact"])

    def test_file_report_distinguishes_missing_empty_and_complete_contents(self):
        for victim in (None, b"", CREATED):
            with self.subTest(size=None if victim is None else len(victim)):
                result = self.inspect_contents("create", self.files(victim))
                file = result["files"]["victim"]
                self.assertEqual(file["present"], victim is not None)
                self.assertEqual(file["size"], None if victim is None else len(victim))
                self.assertEqual(file["sha256"], None if victim is None else hashlib.sha256(victim).hexdigest())
                self.assertTrue(result["witnesses_intact"])

    def inspect_control(self, operation, files, fsck=0, state=1):
        with tempfile.TemporaryDirectory(prefix="ext2-inspection-test-") as directory:
            case = Path(directory)
            # Only the two-byte clean-state field is consumed; no disk tool runs.
            image = bytearray(1024 + 60)
            struct.pack_into("<H", image, 1024 + 58, state)
            (case / "disk.ext2").write_bytes(image)
            with patch.object(crash, "command", return_value=fsck), \
                    patch.object(crash, "dump", side_effect=lambda image, name, output: files[name]):
                return crash.inspect(case, operation, 0)

    def test_controls_require_completed_operation(self):
        outcomes = (("create", CREATED, None), ("unlink", None, None),
                    ("rename", None, BASELINE), ("truncate", BASELINE[:4096], None))
        for operation, victim, destination in outcomes:
            with self.subTest(operation=operation):
                result = self.inspect_control(operation, self.files(victim, destination))
                self.assertTrue(result["original_contents"]["allowed_operation_state"])
        incomplete = (("create", CREATED[:17], None), ("unlink", BASELINE, None),
                      ("rename", BASELINE, DESTINATION), ("truncate", BASELINE, None))
        for operation, victim, destination in incomplete:
            with self.subTest(operation=operation):
                with self.assertRaises(AssertionError):
                    self.inspect_control(operation, self.files(victim, destination))

    def test_controls_reject_bad_fsck_state_sentinel_or_witness(self):
        for fsck, state in ((4, 1), (0, 2)):
            with self.subTest(fsck=fsck, state=state):
                with self.assertRaises(AssertionError):
                    self.inspect_control("create", self.files(CREATED), fsck, state)
        for name in ("sentinel", "witness-small", "witness-indirect"):
            with self.subTest(name=name):
                files = self.files(CREATED)
                files[name] = None
                with self.assertRaises(AssertionError):
                    self.inspect_control("create", files)


if __name__ == "__main__":
    unittest.main()
