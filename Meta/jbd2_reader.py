# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Independent, read-only JBD2 checksum-v3 prototype. Not used by the kernel.

Input is an extracted internal journal, plus independently validated filesystem
geometry and all physical blocks belonging to the journal (including indirect
blocks). Output is an in-memory replay plan; this module performs no disk I/O.
Only 32-bit block numbers, synchronous commit and checksum v3 are supported.
Format reference: https://docs.kernel.org/filesystems/ext4/journal.html
"""

import struct

MAGIC = 0xC03B3998
MAX_JOURNAL_BYTES = 64 * 1024 * 1024


class InvalidJournal(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidJournal(message)


def crc32c(seed, data):
    for value in data:
        seed ^= value
        for _ in range(8):
            seed = (seed >> 1) ^ (0x82F63B78 if seed & 1 else 0)
    return seed


def u32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]


def checked_checksum(data, offset, seed):
    copy = bytearray(data)
    stored = u32(copy, offset)
    copy[offset:offset + 4] = bytes(4)
    require(crc32c(seed, copy) == stored, "Checksum mismatch")


def replay_plan(journal, filesystem_blocks, journal_blocks, expected_uuid):
    """Validate the entire scanned log before returning any committed writes.

    Returns (transactions, writes). Transactions includes committed sequence IDs;
    writes is an ordered list of (filesystem block, immutable after-image).
    Uncommitted tails are ignored. Corruption causes an exception and no plan.
    A caller must not mistake an empty plan for a clean filesystem or retire a
    log before all writes and device flushes have completed.
    """
    require(1024 <= len(journal) <= MAX_JOURNAL_BYTES, "Journal size limit")
    require(len(expected_uuid) == 16 and filesystem_blocks > 0, "Invalid geometry")
    require(struct.unpack_from(">III", journal)[:2] == (MAGIC, 4), "Expected v2 journal superblock")
    size, length, first, sequence, start, error = struct.unpack_from(">6I", journal, 12)
    require(size in (1024, 2048, 4096), "Unsupported block size")
    require(length * size == len(journal) and 0 < first < length, "Invalid journal bounds")
    require(start == 0 or first <= start < length, "Invalid log start")
    compat, incompat, ro = struct.unpack_from(">III", journal, 36)
    require(compat == 0 and ro == 0 and incompat in (0x10, 0x11), "Unsupported journal features")
    require(journal[80] == 4 and error == 0, "Unsupported checksum or aborted journal")
    uuid = journal[48:64]
    require(uuid == expected_uuid and u32(journal, 64) == 1, "External/shared journal unsupported")
    checked_checksum(journal[:1024], 252, 0xFFFFFFFF)
    require(len(journal_blocks) >= length and all(0 <= b < filesystem_blocks for b in journal_blocks), "Invalid journal block map")
    if start == 0:
        return [], []
    seed = crc32c(0xFFFFFFFF, uuid)
    visited = set()
    position = start
    transactions, pending, revokes = [], [], set()
    committed = []

    def take():
        nonlocal position
        require(position not in visited, "Journal loops without a valid end")
        visited.add(position)
        block = journal[position * size:(position + 1) * size]
        position += 1
        if position == length:
            position = first
        return block

    def target(number):
        require(0 <= number < filesystem_blocks, "Replay target outside filesystem")
        require(number not in journal_blocks, "Replay target overlaps journal")

    while len(visited) < length - first:
        block = take()
        magic, kind, tid = struct.unpack_from(">III", block)
        # Old records and zero-filled tails terminate the active log.
        if magic != MAGIC or tid != sequence:
            break
        if kind == 1:
            checked_checksum(block, size - 4, seed)
            offset = 12
            tags = []
            while True:
                require(offset + 16 <= size - 4, "Unterminated descriptor")
                number, flags, high, checksum = struct.unpack_from(">4I", block, offset)
                require(flags & ~0xB == 0 and high == 0, "Unsupported tag flags or high block number")
                offset += 16
                if not flags & 2:
                    require(offset + 16 <= size - 4, "Truncated tag UUID")
                    require(block[offset:offset + 16] == uuid, "Tag UUID mismatch")
                    offset += 16
                target(number)
                tags.append((number, flags, checksum))
                if flags & 8:
                    break
            for number, flags, checksum in tags:
                data = take()
                data_seed = crc32c(seed, struct.pack(">I", sequence))
                require(crc32c(data_seed, data) == checksum, "Data checksum mismatch")
                if flags & 1:
                    require(data[:4] == bytes(4), "Invalid escaped block")
                    data = struct.pack(">I", MAGIC) + data[4:]
                pending.append((number, data))
        elif kind == 5:
            require(incompat & 1, "Revoke record without feature")
            checked_checksum(block, size - 4, seed)
            count = u32(block, 12)
            require(16 <= count <= size - 4 and count % 4 == 0, "Invalid revoke length")
            for offset in range(16, count, 4):
                number = u32(block, offset)
                target(number)
                revokes.add(number)
        elif kind == 2:
            checked_checksum(block, 16, seed)
            committed.append((pending, revokes))
            transactions.append(sequence)
            pending, revokes = [], set()
            sequence = (sequence + 1) & 0xFFFFFFFF
        else:
            raise InvalidJournal("Unexpected journal record")
    else:
        raise InvalidJournal("Journal exhausted without end marker")

    # Transaction order avoids ambiguous integer comparisons at sequence wrap.
    last_revoke = {number: index for index, (_, revoked) in enumerate(committed) for number in revoked}
    writes = [(number, data) for index, (updates, _) in enumerate(committed)
              for number, data in updates if last_revoke.get(number, -1) < index]
    return transactions, writes
