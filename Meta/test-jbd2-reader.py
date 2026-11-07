#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Malformed-log and recovery semantics tests for the read-only prototype."""

import struct
import unittest

from jbd2_reader import InvalidJournal, MAGIC, crc32c, replay_plan

SIZE = 1024
UUID = bytes(range(16))
SEED = crc32c(0xFFFFFFFF, UUID)
FORBIDDEN = set(range(900, 916))


def seal(block, offset, seed=SEED):
    struct.pack_into(">I", block, offset, 0)
    struct.pack_into(">I", block, offset, crc32c(seed, block))
    return block


def fixture(transactions=None, start=1, sequence=1):
    if transactions is None:
        transactions = [([(50, b"A" * SIZE)], [], True)]
    image = bytearray(SIZE * 16)
    sb = bytearray(SIZE)
    struct.pack_into(">9I", sb, 0, MAGIC, 4, 0, SIZE, 16, 1, sequence, start, 0)
    struct.pack_into(">III", sb, 36, 0, 0x11, 0)
    sb[48:64] = UUID
    struct.pack_into(">I", sb, 64, 1)
    sb[80] = 4
    image[:SIZE] = seal(sb, 252, 0xFFFFFFFF)
    position = start
    locations = []

    def emit(block):
        nonlocal position
        locations.append(position)
        image[position * SIZE:(position + 1) * SIZE] = block
        position = 1 if position == 15 else position + 1

    for updates, revoked, commit in transactions:
        if updates:
            descriptor = bytearray(SIZE)
            struct.pack_into(">III", descriptor, 0, MAGIC, 1, sequence)
            payloads = []
            for index, (number, data) in enumerate(updates):
                escaped = data[:4] == struct.pack(">I", MAGIC)
                payload = bytes(4) + data[4:] if escaped else data
                payloads.append(payload)
                offset = 12 if index == 0 else 44 + (index - 1) * 16
                flags = (0 if index == 0 else 2) | (8 if index == len(updates) - 1 else 0) | int(escaped)
                checksum = crc32c(crc32c(SEED, struct.pack(">I", sequence)), payload)
                struct.pack_into(">4I", descriptor, offset, number, flags, 0, checksum)
                if index == 0:
                    descriptor[28:44] = UUID
            emit(seal(descriptor, SIZE - 4))
            for payload in payloads:
                emit(payload)
        if revoked:
            block = bytearray(SIZE)
            struct.pack_into(">4I", block, 0, MAGIC, 5, sequence, 16 + 4 * len(revoked))
            for index, number in enumerate(revoked):
                struct.pack_into(">I", block, 16 + index * 4, number)
            emit(seal(block, SIZE - 4))
        if commit:
            block = bytearray(SIZE)
            struct.pack_into(">III", block, 0, MAGIC, 2, sequence)
            emit(seal(block, 16))
        sequence = (sequence + 1) & 0xFFFFFFFF
    return image, locations


class ReaderTests(unittest.TestCase):
    def test_crc32c_known_vector(self):
        self.assertEqual(crc32c(0xFFFFFFFF, b"123456789"), 0x1CF96D7C)

    def plan(self, image):
        return replay_plan(image, 1000, FORBIDDEN, UUID)

    def mutate(self, image, block, offset, value, checksum=None, seed=SEED):
        copy = bytearray(image)
        struct.pack_into(">I", copy, block * SIZE + offset, value)
        if checksum is not None:
            begin = block * SIZE
            copy[begin:begin + SIZE] = seal(copy[begin:begin + SIZE], checksum, seed)
        return copy

    def test_committed_after_image(self):
        self.assertEqual(self.plan(fixture()[0]), ([1], [(50, b"A" * SIZE)]))

    def test_uncommitted_tail_not_applied(self):
        image, _ = fixture([([(50, b"A" * SIZE)], [], False)])
        self.assertEqual(self.plan(image), ([], []))

    def test_corrupt_later_transaction_prevents_partial_plan(self):
        image, _ = fixture([([(50, b"A" * SIZE)], [], True), ([(51, b"B" * SIZE)], [], True)])
        image[5 * SIZE + 100] ^= 1
        with self.assertRaises(InvalidJournal):
            self.plan(image)

    def test_full_ring_without_end_marker_rejected(self):
        image, _ = fixture([([], [], True)] * 15)
        with self.assertRaises(InvalidJournal):
            self.plan(image)

    def test_incomplete_journal_map_rejected(self):
        image, _ = fixture()
        with self.assertRaises(InvalidJournal):
            replay_plan(image, 1000, {900}, UUID)

    def test_uncommitted_revoke_cannot_suppress_committed_write(self):
        image, _ = fixture([([(50, b"A" * SIZE)], [], True), ([], [50], False)])
        self.assertEqual(self.plan(image)[1], [(50, b"A" * SIZE)])

    def test_later_committed_revoke_suppresses_old_image(self):
        image, _ = fixture([([(50, b"A" * SIZE)], [], True), ([], [50], True)])
        self.assertEqual(self.plan(image), ([1, 2], []))

    def test_write_after_revoke_is_preserved(self):
        image, _ = fixture([([], [50], True), ([(50, b"B" * SIZE)], [], True)])
        self.assertEqual(self.plan(image)[1], [(50, b"B" * SIZE)])

    def test_ring_and_sequence_wrap(self):
        image, _ = fixture([([(50, b"A" * SIZE)], [], True), ([(51, b"B" * SIZE)], [], True)], start=14, sequence=0xFFFFFFFF)
        self.assertEqual(self.plan(image), ([0xFFFFFFFF, 0], [(50, b"A" * SIZE), (51, b"B" * SIZE)]))

    def test_escaped_magic_restored(self):
        data = struct.pack(">I", MAGIC) + bytes(SIZE - 4)
        self.assertEqual(self.plan(fixture([([(50, data)], [], True)])[0])[1], [(50, data)])

    def test_multiple_tags(self):
        updates = [(50, b"A" * SIZE), (51, b"B" * SIZE)]
        self.assertEqual(self.plan(fixture([(updates, [], True)])[0])[1], updates)

    def test_corrupt_records_rejected_before_any_plan(self):
        image, locations = fixture([([(50, b"A" * SIZE)], [51], True)])
        for block, offset in ((0, 200), (locations[0], 80), (locations[1], 400), (locations[2], 20), (locations[3], 60)):
            with self.subTest(block=block):
                corrupt = bytearray(image)
                corrupt[block * SIZE + offset] ^= 1
                with self.assertRaises(InvalidJournal):
                    self.plan(corrupt)

    def test_unsupported_features_and_abort(self):
        image, _ = fixture()
        for offset, value in ((36, 1), (40, 0x12), (44, 1), (32, 5), (64, 2), (80, 0)):
            with self.subTest(offset=offset):
                with self.assertRaises(InvalidJournal):
                    self.plan(self.mutate(image, 0, offset, value, 252, 0xFFFFFFFF))

    def test_invalid_geometry(self):
        image, _ = fixture()
        for offset, value in ((12, 8192), (16, 17), (20, 16), (28, 16)):
            with self.subTest(offset=offset):
                with self.assertRaises(InvalidJournal):
                    self.plan(self.mutate(image, 0, offset, value, 252, 0xFFFFFFFF))

    def test_out_of_range_and_journal_targets(self):
        image, _ = fixture()
        for target in (1000, 0xFFFFFFFF, 900):
            with self.subTest(target=target):
                with self.assertRaises(InvalidJournal):
                    self.plan(self.mutate(image, 1, 12, target, SIZE - 4))

    def test_bad_tag_fields_with_valid_descriptor_checksum(self):
        image, _ = fixture()
        for offset, value in ((16, 0x10), (20, 1), (28, 1234), (16, 0)):
            with self.subTest(offset=offset, value=value):
                with self.assertRaises(InvalidJournal):
                    self.plan(self.mutate(image, 1, offset, value, SIZE - 4))

    def test_invalid_revoke_length_and_target(self):
        image, _ = fixture([([], [50], True)])
        for offset, value in ((12, 15), (12, 1024), (12, 19), (16, 900)):
            with self.subTest(offset=offset, value=value):
                with self.assertRaises(InvalidJournal):
                    self.plan(self.mutate(image, 1, offset, value, SIZE - 4))

    def test_revoke_requires_feature(self):
        image, _ = fixture([([], [50], True)])
        with self.assertRaises(InvalidJournal):
            self.plan(self.mutate(image, 0, 40, 0x10, 252, 0xFFFFFFFF))

    def test_uuid_mismatch(self):
        image, _ = fixture()
        with self.assertRaises(InvalidJournal):
            replay_plan(image, 1000, FORBIDDEN, bytes(16))

    def test_truncated_input(self):
        image, _ = fixture()
        for length in (0, 24, 1023, len(image) - 1):
            with self.subTest(length=length):
                with self.assertRaises(InvalidJournal):
                    self.plan(image[:length])


if __name__ == "__main__":
    unittest.main()
