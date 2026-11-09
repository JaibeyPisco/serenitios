/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/Array.h>
#include <AK/ByteBuffer.h>
#include <AK/Error.h>
#include <AK/HashMap.h>
#include <AK/HashTable.h>
#include <AK/Vector.h>

namespace Kernel::Ext2Journal {

// A deliberately read-only, bounded JBD2 checksum-v3 parser. The caller must
// independently validate the filesystem geometry and supply EVERY journal
// physical block (including indirect blocks). No disk I/O is performed here.
// A successful empty plan does not mean the filesystem is clean. Applying or
// retiring a plan requires a separate crash-safe protocol and device barriers.
struct Write {
    u32 block;
    ByteBuffer data;
};

struct ReplayPlan {
    Vector<u32> transactions;
    Vector<Write> writes;
};

constexpr u32 magic = 0xc03b3998;
constexpr size_t maximum_journal_bytes = 64 * 1024 * 1024;
// Bound auxiliary allocations as well as input bytes. Count every tag/revoke
// record, including duplicates, so adversarial logs cannot amplify a bounded
// input into millions of hash entries. These conservative prototype limits may
// reject otherwise valid journals; rejection must never enable writable mount.
constexpr size_t maximum_metadata_records = 16 * 1024;
constexpr size_t maximum_transactions = 4096;

inline u32 read_u32(ReadonlyBytes bytes, size_t offset)
{
    return (static_cast<u32>(bytes[offset]) << 24) | (static_cast<u32>(bytes[offset + 1]) << 16) | (static_cast<u32>(bytes[offset + 2]) << 8) | bytes[offset + 3];
}

inline u32 crc32c(u32 seed, ReadonlyBytes bytes)
{
    for (auto byte : bytes) {
        seed ^= byte;
        for (unsigned bit = 0; bit < 8; ++bit)
            seed = (seed >> 1) ^ ((seed & 1) ? 0x82f63b78u : 0);
    }
    return seed;
}

inline bool checksum_matches(ReadonlyBytes bytes, size_t offset, u32 seed)
{
    auto stored = read_u32(bytes, offset);
    seed = crc32c(seed, bytes.slice(0, offset));
    Array<u8, 4> zeros {};
    seed = crc32c(seed, zeros.span());
    return crc32c(seed, bytes.slice(offset + 4)) == stored;
}

inline ErrorOr<ReplayPlan> replay_plan(ReadonlyBytes journal, u32 filesystem_blocks, HashTable<u32> const& journal_blocks, ReadonlyBytes expected_uuid)
{
    auto invalid = [] { return Error::from_errno(EINVAL); };
    if (journal.size() < 1024 || journal.size() > maximum_journal_bytes || expected_uuid.size() != 16 || filesystem_blocks == 0)
        return invalid();
    if (read_u32(journal, 0) != magic || read_u32(journal, 4) != 4)
        return invalid();
    auto size = read_u32(journal, 12);
    auto length = read_u32(journal, 16);
    auto first = read_u32(journal, 20);
    auto sequence = read_u32(journal, 24);
    auto position = read_u32(journal, 28);
    auto incompat = read_u32(journal, 40);
    if ((size != 1024 && size != 2048 && size != 4096) || static_cast<u64>(length) * size != journal.size() || first == 0 || first >= length)
        return invalid();
    if (position != 0 && (position < first || position >= length))
        return invalid();
    if (read_u32(journal, 32) != 0 || read_u32(journal, 36) != 0 || read_u32(journal, 44) != 0 || (incompat != 0x10 && incompat != 0x11) || journal[80] != 4)
        return invalid();
    auto uuid = journal.slice(48, 16);
    if (uuid != expected_uuid || read_u32(journal, 64) != 1 || !checksum_matches(journal.slice(0, 1024), 252, 0xffffffff))
        return invalid();
    if (journal_blocks.size() < length)
        return invalid();
    for (auto block : journal_blocks) {
        if (block >= filesystem_blocks)
            return invalid();
    }
    ReplayPlan plan;
    if (position == 0)
        return plan;

    struct PendingWrite {
        u32 block;
        u32 journal_position;
        bool escaped;
    };
    struct Transaction {
        Vector<PendingWrite> writes;
        HashTable<u32> revokes;
    };
    Vector<Transaction> committed;
    Transaction pending;
    HashTable<u32> visited;
    auto seed = crc32c(0xffffffff, uuid);
    size_t metadata_records = 0;
    auto take = [&]() -> ErrorOr<ReadonlyBytes> {
        if (visited.contains(position))
            return invalid();
        TRY(visited.try_set(position));
        auto block = journal.slice(static_cast<size_t>(position) * size, size);
        if (++position == length)
            position = first;
        return block;
    };
    auto target_is_valid = [&](u32 block) { return block < filesystem_blocks && !journal_blocks.contains(block); };
    bool ended = false;
    while (visited.size() < length - first) {
        auto block = TRY(take());
        if (read_u32(block, 0) != magic || read_u32(block, 8) != sequence) {
            ended = true;
            break;
        }
        auto kind = read_u32(block, 4);
        if (kind == 1) {
            if (!checksum_matches(block, size - 4, seed))
                return invalid();
            struct Tag {
                u32 number;
                u32 flags;
                u32 checksum;
            };
            Vector<Tag> tags;
            size_t offset = 12;
            for (;;) {
                if (offset + 16 > size - 4)
                    return invalid();
                auto number = read_u32(block, offset);
                auto flags = read_u32(block, offset + 4);
                auto high = read_u32(block, offset + 8);
                auto checksum = read_u32(block, offset + 12);
                offset += 16;
                if ((flags & ~0xbu) != 0 || high != 0 || !target_is_valid(number))
                    return invalid();
                if (metadata_records == maximum_metadata_records)
                    return Error::from_errno(E2BIG);
                ++metadata_records;
                if (!(flags & 2)) {
                    if (offset + 16 > size - 4 || block.slice(offset, 16) != uuid)
                        return invalid();
                    offset += 16;
                }
                TRY(tags.try_append({ number, flags, checksum }));
                if (flags & 8)
                    break;
            }
            Array<u8, 4> sequence_bytes { static_cast<u8>(sequence >> 24), static_cast<u8>(sequence >> 16), static_cast<u8>(sequence >> 8), static_cast<u8>(sequence) };
            auto data_seed = crc32c(seed, sequence_bytes.span());
            for (auto const& tag : tags) {
                auto data_position = position;
                auto data = TRY(take());
                if (crc32c(data_seed, data) != tag.checksum || ((tag.flags & 1) && read_u32(data, 0) != 0))
                    return invalid();
                TRY(pending.writes.try_append({ tag.number, data_position, (tag.flags & 1) != 0 }));
            }
        } else if (kind == 5) {
            if (!(incompat & 1) || !checksum_matches(block, size - 4, seed))
                return invalid();
            auto count = read_u32(block, 12);
            if (count < 16 || count > size - 4 || count % 4 != 0)
                return invalid();
            for (size_t offset = 16; offset < count; offset += 4) {
                auto number = read_u32(block, offset);
                if (!target_is_valid(number))
                    return invalid();
                if (metadata_records == maximum_metadata_records)
                    return Error::from_errno(E2BIG);
                ++metadata_records;
                TRY(pending.revokes.try_set(number));
            }
        } else if (kind == 2) {
            if (!checksum_matches(block, 16, seed))
                return invalid();
            if (committed.size() == maximum_transactions)
                return Error::from_errno(E2BIG);
            TRY(committed.try_append(move(pending)));
            TRY(plan.transactions.try_append(sequence));
            pending = {};
            ++sequence;
        } else {
            return invalid();
        }
    }
    if (!ended)
        return invalid();
    HashMap<u32, size_t> last_revoke;
    for (size_t index = 0; index < committed.size(); ++index) {
        for (auto number : committed[index].revokes)
            TRY(last_revoke.try_set(number, index));
    }
    // Copy only after the COMPLETE log validates. No returned data borrows the
    // caller's journal, and a corrupt later transaction exposes no partial plan.
    for (size_t index = 0; index < committed.size(); ++index) {
        for (auto const& write : committed[index].writes) {
            auto revoked = last_revoke.find(write.block);
            if (revoked != last_revoke.end() && revoked->value >= index)
                continue;
            auto data = TRY(ByteBuffer::copy(journal.slice(static_cast<size_t>(write.journal_position) * size, size)));
            if (write.escaped) {
                data[0] = 0xc0;
                data[1] = 0x3b;
                data[2] = 0x39;
                data[3] = 0x98;
            }
            TRY(plan.writes.try_append({ write.block, move(data) }));
        }
    }
    return plan;
}

}
