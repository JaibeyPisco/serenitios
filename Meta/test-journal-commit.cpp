/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <Kernel/FileSystem/Ext2FS/JournalCommit.h>
#include <stdio.h>

using namespace Kernel::Ext2Journal;
#define CHECK(condition) do { if (!(condition)) { fprintf(stderr, "Failed line %d\n", __LINE__); return 1; } } while (0)

static int allocation_budget = -1;
static size_t allocation_calls;
extern "C" void* __real_malloc(size_t);
extern "C" void* __wrap_malloc(size_t size)
{
    ++allocation_calls;
    if (allocation_budget == 0)
        return nullptr;
    if (allocation_budget > 0)
        --allocation_budget;
    return __real_malloc(size);
}

struct Device {
    ByteBuffer durable;
    ByteBuffer cache;
    size_t block_size;
    size_t calls { 0 };
    size_t fail_at { 0 };
    bool persist_on_failure { false };
    ErrorOr<void> finish(bool flush)
    {
        ++calls;
        if (calls == fail_at) {
            if (persist_on_failure)
                __builtin_memcpy(durable.data(), cache.data(), cache.size());
            return Error::from_errno(EIO);
        }
        if (flush)
            __builtin_memcpy(durable.data(), cache.data(), cache.size());
        return {};
    }
    ErrorOr<void> write_block(u32 block, ReadonlyBytes data)
    {
        VERIFY(data.size() == block_size && (block + 1) * block_size <= cache.size());
        __builtin_memcpy(cache.data() + block * block_size, data.data(), block_size);
        return finish(false);
    }
    ErrorOr<void> flush() { return finish(true); }
};

static ByteBuffer clean_journal(size_t size, u32 sequence)
{
    auto image = MUST(ByteBuffer::create_zeroed(32 * size));
    auto bytes = image.bytes();
    write_u32(bytes, 0, magic);
    write_u32(bytes, 4, 4);
    write_u32(bytes, 12, size);
    write_u32(bytes, 16, 32);
    write_u32(bytes, 20, 1);
    write_u32(bytes, 24, sequence);
    write_u32(bytes, 40, 0x10);
    for (size_t index = 0; index < 16; ++index)
        bytes[48 + index] = index;
    write_u32(bytes, 64, 1);
    bytes[80] = 4;
    seal_checksum(bytes.slice(0, 1024), 252, 0xffffffff);
    return image;
}

int main()
{
    size_t cuts = 0;
    for (size_t size : { 1024, 2048, 4096 }) {
        for (u32 sequence : { 1u, 0xffffffffu }) {
            auto journal = clean_journal(size, sequence);
            Array<u32, 32> mapping;
            HashTable<u32> all;
            for (size_t index = 0; index < mapping.size(); ++index) {
                mapping[index] = 100 + (index * 7) % 32;
                MUST(all.try_set(mapping[index]));
            }
            Vector<Write> writes;
            MUST(writes.try_append({ 50, MUST(ByteBuffer::create_zeroed(size)) }));
            MUST(writes.try_append({ 51, MUST(ByteBuffer::create_zeroed(size)) }));
            memset(writes[0].data.data(), 0x42, size);
            memset(writes[1].data.data(), 0x7b, size);
            write_u32(writes[1].data.bytes(), 0, magic); // escaped journal data
            auto uuid = journal.bytes().slice(48, 16);
            auto buffer = MUST(TransactionBuffer::create(size, 256, 2));
            MUST(buffer.stage(writes[0].block, writes[0].data.bytes()));
            MUST(buffer.stage(writes[1].block, writes[1].data.bytes()));
            auto bridged = MUST(PreparedCommit::from_buffer(journal.bytes(), 256, mapping.span(), all, uuid, buffer));
            auto fresh = MUST(PreparedCommit::create(journal.bytes(), 256, mapping.span(), all, uuid, writes.span()));
            CHECK(bridged.committed_journal() == fresh.committed_journal());
            buffer.abort();
            CHECK(PreparedCommit::from_buffer(journal.bytes(), 256, mapping.span(), all, uuid, buffer).error().code() == ECANCELED);
            auto plan = MUST(replay_plan(fresh.committed_journal(), 256, all, uuid));
            CHECK(plan.transactions.size() == 1 && plan.transactions[0] == sequence && plan.writes.size() == 2);
            CHECK(plan.writes[1].data == writes[1].data);
            // Include stale matching-sequence commit records in a clean journal.
            // Their slots must become durably zero before the new log is armed.
            auto stale = MUST(ByteBuffer::copy(fresh.committed_journal()));
            write_u32(stale.bytes(), 28, 0);
            seal_checksum(stale.bytes().slice(0, 1024), 252, 0xffffffff);
            auto baseline = MUST(ByteBuffer::create_zeroed(256 * size));
            memset(baseline.data() + 50 * size, 0x11, 2 * size);
            for (size_t index = 0; index < mapping.size(); ++index)
                memcpy(baseline.data() + mapping[index] * size, stale.data() + index * size, size);
            auto make_device = [&] { return Device { MUST(ByteBuffer::copy(baseline.bytes())), MUST(ByteBuffer::copy(baseline.bytes())), size }; };
            auto complete = MUST(PreparedCommit::create(stale.bytes(), 256, mapping.span(), all, uuid, writes.span()));
            auto device = make_device();
            auto allocations_before_execute = allocation_calls;
            allocation_budget = 0;
            CHECK(!complete.execute(device).is_error());
            allocation_budget = -1;
            CHECK(allocation_calls == allocations_before_execute);
            CHECK(complete.commit_durable() && complete.completed());
            size_t operations = device.calls;
            CHECK(complete.execute(device).error().code() == ECANCELED && device.calls == operations);
            auto retired_log = MUST(ByteBuffer::create_zeroed(32 * size));
            for (size_t index = 0; index < mapping.size(); ++index)
                memcpy(retired_log.data() + index * size, device.durable.data() + mapping[index] * size, size);
            CHECK(read_u32(retired_log.bytes(), 24) == sequence + 1 && read_u32(retired_log.bytes(), 28) == 0);
            auto next = MUST(PreparedCommit::create(retired_log.bytes(), 256, mapping.span(), all, uuid, writes.span()));
            auto next_plan = MUST(replay_plan(next.committed_journal(), 256, all, uuid));
            CHECK(next_plan.transactions[0] == sequence + 1);
            CHECK(!next.execute(device).is_error());
            for (size_t failure = 1; failure <= operations; ++failure) {
                for (bool persist : { false, true }) {
                    auto transaction = MUST(PreparedCommit::create(stale.bytes(), 256, mapping.span(), all, uuid, writes.span()));
                    auto failed = make_device();
                    failed.fail_at = failure;
                    failed.persist_on_failure = persist;
                    CHECK(transaction.execute(failed).error().code() == EIO);
                    CHECK(!transaction.completed());
                    CHECK(transaction.execute(failed).error().code() == ECANCELED && failed.calls == failure);
                    auto recovered_log = MUST(ByteBuffer::create_zeroed(32 * size));
                    for (size_t index = 0; index < mapping.size(); ++index)
                        memcpy(recovered_log.data() + index * size, failed.durable.data() + mapping[index] * size, size);
                    auto recovery = MUST(replay_plan(recovered_log.bytes(), 256, all, uuid));
                    for (auto const& write : recovery.writes)
                        memcpy(failed.durable.data() + write.block * size, write.data.data(), size);
                    auto first = failed.durable.bytes().slice(50 * size, size);
                    auto second = failed.durable.bytes().slice(51 * size, size);
                    bool old = first == baseline.bytes().slice(50 * size, size) && second == baseline.bytes().slice(51 * size, size);
                    bool updated = first == writes[0].data.bytes() && second == writes[1].data.bytes();
                    CHECK(old || updated);
                    if (transaction.commit_durable())
                        CHECK(updated);
                    ++cuts;
                }
            }
            auto invalid = writes[0].block;
            writes[0].block = mapping[1];
            CHECK(PreparedCommit::create(journal.bytes(), 256, mapping.span(), all, uuid, writes.span()).is_error());
            writes[0].block = invalid;
            auto bad_map = mapping;
            bad_map[1] = bad_map[0];
            CHECK(PreparedCommit::create(journal.bytes(), 256, bad_map.span(), all, uuid, writes.span()).is_error());
            auto moved = move(fresh);
            auto calls_before_moved_source = device.calls;
            CHECK(fresh.execute(device).error().code() == ECANCELED);
            CHECK(device.calls == calls_before_moved_source);
            CHECK(!moved.attempted());
            bool reached_success = false;
            for (int budget = 0; budget < 128; ++budget) {
                allocation_budget = budget;
                auto reservation = PreparedCommit::create(journal.bytes(), 256, mapping.span(), all, uuid, writes.span());
                allocation_budget = -1;
                if (!reservation.is_error()) {
                    reached_success = true;
                    break;
                }
                CHECK(reservation.error().code() == ENOMEM);
            }
            CHECK(reached_success);
        }
    }
    printf("Journal commit: %zu volatile-cache failure/recovery cases passed\n", cuts);
    return 0;
}
