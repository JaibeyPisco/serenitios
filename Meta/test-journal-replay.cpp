/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/Array.h>
#include <Kernel/FileSystem/Ext2FS/JournalReplay.h>
#include <stdio.h>

#define CHECK(condition) do { if (!(condition)) { fprintf(stderr, "Failed at line %d\n", __LINE__); return 1; } } while (0)

using namespace Kernel::Ext2Journal;
constexpr size_t block_size = 1024;
constexpr size_t disk_blocks = 128;

struct Device {
    ByteBuffer durable;
    ByteBuffer cached;
    size_t calls { 0 };
    size_t fail_at { 0 };
    bool persist_on_write { false };
    bool persist_before_failure { false };

    ErrorOr<void> write_block(u32 block, ReadonlyBytes data)
    {
        VERIFY(block < disk_blocks && data.size() == block_size);
        bool fail = ++calls == fail_at;
        if (!fail || persist_before_failure) {
            __builtin_memcpy(cached.data() + block * block_size, data.data(), block_size);
            if (persist_on_write || (fail && persist_before_failure))
                __builtin_memcpy(durable.data() + block * block_size, data.data(), block_size);
        }
        if (fail)
            return Error::from_errno(EIO);
        return {};
    }

    ErrorOr<void> flush()
    {
        bool fail = ++calls == fail_at;
        if (!fail || persist_before_failure)
            __builtin_memcpy(durable.data(), cached.data(), cached.size());
        if (fail)
            return Error::from_errno(EIO);
        return {};
    }
};

int main(int argc, char** argv)
{
    CHECK(argc == 2);
    auto* input = fopen(argv[1], "rb");
    CHECK(input);
    auto journal = MUST(ByteBuffer::create_zeroed(16 * block_size));
    CHECK(fread(journal.data(), 1, journal.size(), input) == journal.size());
    CHECK(fgetc(input) == EOF);
    fclose(input);
    Array<u8, 16> uuid {};
    Array<u32, 16> mapping {};
    HashTable<u32> journal_blocks;
    for (u32 i = 0; i < 16; ++i) {
        uuid[i] = i;
        mapping[i] = 96 + (i * 7) % 16;
        MUST(journal_blocks.try_set(mapping[i]));
    }
    auto source_plan = MUST(replay_plan(journal.bytes(), disk_blocks, journal_blocks, uuid.span()));
    auto initial = MUST(ByteBuffer::create_zeroed(disk_blocks * block_size));
    __builtin_memset(initial.data(), 0x44, initial.size());
    for (size_t index = 0; index < mapping.size(); ++index)
        __builtin_memcpy(initial.data() + mapping[index] * block_size, journal.data() + index * block_size, block_size);
    auto expected = MUST(ByteBuffer::copy(initial.bytes()));
    for (auto const& write : source_plan.writes)
        __builtin_memcpy(expected.data() + write.block * block_size, write.data.data(), block_size);
    auto extract_journal = [&](ReadonlyBytes disk) {
        auto extracted = MUST(ByteBuffer::create_zeroed(journal.size()));
        for (size_t index = 0; index < mapping.size(); ++index)
            __builtin_memcpy(extracted.data() + index * block_size, disk.data() + mapping[index] * block_size, block_size);
        return extracted;
    };

    auto invalid_mapping = mapping;
    invalid_mapping[1] = invalid_mapping[0];
    CHECK(PreparedReplay::create(journal.bytes(), disk_blocks, invalid_mapping.span(), journal_blocks, uuid.span()).is_error());
    CHECK(PreparedReplay::create(journal.bytes(), disk_blocks, mapping.span().slice(1), journal_blocks, uuid.span()).is_error());
    auto corrupt = MUST(ByteBuffer::copy(journal.bytes()));
    corrupt[252] ^= 1;
    CHECK(PreparedReplay::create(corrupt.bytes(), disk_blocks, mapping.span(), journal_blocks, uuid.span()).is_error());

    size_t attempts = 0;
    size_t expected_calls = source_plan.writes.size() + 3;
    for (bool eager : { false, true }) {
        for (bool ambiguous : { false, true }) {
            for (size_t fail_at = 0; fail_at <= expected_calls; ++fail_at) {
                Device device { MUST(ByteBuffer::copy(initial.bytes())), MUST(ByteBuffer::copy(initial.bytes())), 0, fail_at, eager, ambiguous };
                auto replay = MUST(PreparedReplay::create(journal.bytes(), disk_blocks, mapping.span(), journal_blocks, uuid.span()));
                auto moved = move(replay);
                CHECK(replay.execute(device).is_error() && device.calls == 0);
                auto outcome = moved.execute(device);
                CHECK(outcome.is_error() == (fail_at != 0));
                CHECK(moved.is_complete() == (fail_at == 0));
                auto calls = device.calls;
                CHECK(moved.execute(device).is_error() && device.calls == calls);

                // Lose volatile state, re-read journal and repeat recovery.
                device.cached = MUST(ByteBuffer::copy(device.durable.bytes()));
                auto persisted_journal = extract_journal(device.durable.bytes());
                auto retry = MUST(PreparedReplay::create(persisted_journal.bytes(), disk_blocks, mapping.span(), journal_blocks, uuid.span()));
                device.fail_at = 0;
                MUST(retry.execute(device));
                CHECK(retry.is_complete());
                for (u32 block = 0; block < disk_blocks; ++block) {
                    if (!journal_blocks.contains(block))
                        CHECK(device.durable.bytes().slice(block * block_size, block_size) == expected.bytes().slice(block * block_size, block_size));
                }
                // An unrelated block must survive every interrupted replay.
                CHECK(device.durable.bytes().slice(70 * block_size, block_size) == initial.bytes().slice(70 * block_size, block_size));
                if (source_plan.writes.is_empty())
                    CHECK(device.durable.bytes().slice(50 * block_size, block_size) == initial.bytes().slice(50 * block_size, block_size));
                auto retired = extract_journal(device.durable.bytes());
                auto final_plan = MUST(replay_plan(retired.bytes(), disk_blocks, journal_blocks, uuid.span()));
                CHECK(final_plan.transactions.is_empty() && final_plan.writes.is_empty());
                auto next = source_plan.transactions.is_empty() ? read_u32(journal.bytes(), 24) : source_plan.transactions.last() + 1;
                CHECK(read_u32(retired.bytes(), 24) == next && read_u32(retired.bytes(), 28) == 0);
                ++attempts;
            }
        }
    }
    printf("Replay passed %zu interruption/persistence cases\n", attempts);
    return 0;
}
