/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/HashMap.h>
#include <Kernel/FileSystem/Ext2FS/JournalInode.h>
#include <stdio.h>

#define CHECK(condition)                                      \
    do {                                                      \
        if (!(condition)) {                                   \
            fprintf(stderr, "Failed at line %d\n", __LINE__); \
            return 1;                                         \
        }                                                     \
    } while (0)

using namespace Kernel::Ext2Journal;

static int allocations_before_failure = -1;
extern "C" void* __real_malloc(size_t);
extern "C" void* __wrap_malloc(size_t size)
{
    if (allocations_before_failure == 0)
        return nullptr;
    if (allocations_before_failure > 0)
        --allocations_before_failure;
    return __real_malloc(size);
}

static void put_u32(Bytes bytes, size_t offset, u32 value)
{
    bytes[offset] = value;
    bytes[offset + 1] = value >> 8;
    bytes[offset + 2] = value >> 16;
    bytes[offset + 3] = value >> 24;
}

struct Fixture {
    ByteBuffer inode;
    HashMap<u32, ByteBuffer> indirect;
    Vector<u32> data;
    u32 next { 100 };
    size_t block_size;

    static Fixture create(size_t size, size_t count)
    {
        Fixture fixture { MUST(ByteBuffer::create_zeroed(128)), {}, {}, 100, size };
        fixture.inode[1] = 0x81;
        fixture.inode[26] = 1;
        put_u32(fixture.inode.bytes(), 4, size * count);
        for (size_t i = 0; i < min(count, 12uz); ++i) {
            auto block = fixture.next++;
            fixture.data.append(block);
            put_u32(fixture.inode.bytes(), 40 + i * 4, block);
        }
        auto tree = [&](auto&& self, unsigned depth, size_t needed) -> u32 {
            if (needed == 0)
                return 0;
            auto number = fixture.next++;
            auto pointers = MUST(ByteBuffer::create_zeroed(size));
            size_t capacity = 1;
            for (unsigned i = 1; i < depth; ++i)
                capacity *= size / 4;
            for (size_t index = 0; needed != 0; ++index) {
                auto child_count = min(needed, capacity);
                u32 child;
                if (depth == 1) {
                    child = fixture.next++;
                    fixture.data.append(child);
                } else {
                    child = self(self, depth - 1, child_count);
                }
                put_u32(pointers.bytes(), index * 4, child);
                needed -= child_count;
            }
            fixture.indirect.set(number, move(pointers));
            return number;
        };
        size_t remaining = count > 12 ? count - 12 : 0;
        size_t capacity = size / 4;
        for (unsigned depth = 1; depth <= 3; ++depth) {
            auto child_count = min(remaining, capacity);
            auto child = tree(tree, depth, child_count);
            put_u32(fixture.inode.bytes(), 40 + (11 + depth) * 4, child);
            remaining -= child_count;
            capacity *= size / 4;
        }
        put_u32(fixture.inode.bytes(), 28, (fixture.next - 100) * (size / 512));
        return fixture;
    }
};

int main()
{
    size_t checks = 0;
    for (size_t size : { 1024, 2048, 4096 }) {
        for (size_t count : { 2uz, 12uz, 13uz, size / 4 + 12, size / 4 + 13, (64 * 1024 * 1024) / size }) {
            auto fixture = Fixture::create(size, count);
            size_t reads = 0;
            auto read = [&](u32 block, Bytes bytes) -> ErrorOr<void> {
                ++reads;
                auto it = fixture.indirect.find(block);
                if (it == fixture.indirect.end() || it->value.size() != bytes.size())
                    return Error::from_errno(EIO);
                __builtin_memcpy(bytes.data(), it->value.data(), bytes.size());
                return {};
            };
            auto validate = [](u32 block) -> ErrorOr<void> {
                if (block < 100)
                    return Error::from_errno(EINVAL);
                return {};
            };
            auto mapped = MUST(map_journal_inode(fixture.inode.bytes(), size, 200000, read, validate));
            CHECK(mapped.mapping == fixture.data);
            CHECK(mapped.all_blocks.size() == fixture.next - 100);
            CHECK(reads == fixture.indirect.size());
            for (auto block : fixture.data)
                CHECK(mapped.all_blocks.contains(block));
            for (auto const& item : fixture.indirect)
                CHECK(mapped.all_blocks.contains(item.key));
            ++checks;
        }
    }

    auto fixture = Fixture::create(1024, 13);
    auto read = [&](u32 block, Bytes bytes) -> ErrorOr<void> {
        auto it = fixture.indirect.find(block);
        if (it == fixture.indirect.end())
            return Error::from_errno(EIO);
        __builtin_memcpy(bytes.data(), it->value.data(), bytes.size());
        return {};
    };
    auto validate = [](u32 block) -> ErrorOr<void> {
        if (block < 100)
            return Error::from_errno(EINVAL);
        return {};
    };
    auto attempt = [&]() { return map_journal_inode(fixture.inode.bytes(), 1024, 200000, read, validate); };
    for (auto offset : { 4uz, 20uz, 28uz, 32uz, 104uz, 108uz, 112uz, 40uz, 40uz + 13 * 4, 40uz + 14 * 4 }) {
        auto original = inode_u32(fixture.inode.bytes(), offset);
        put_u32(fixture.inode.bytes(), offset, 1);
        CHECK(attempt().is_error());
        put_u32(fixture.inode.bytes(), offset, original);
        ++checks;
    }
    CHECK(map_journal_inode(fixture.inode.bytes().slice(0, 127), 1024, 200000, read, validate).is_error());
    CHECK(map_journal_inode(fixture.inode.bytes(), 512, 200000, read, validate).is_error());
    CHECK(map_journal_inode(fixture.inode.bytes(), 1024, 0, read, validate).is_error());
    fixture.inode[1] = 0x41;
    CHECK(attempt().is_error());
    fixture.inode[1] = 0x81;
    fixture.inode[26] = 2;
    CHECK(attempt().is_error());
    fixture.inode[26] = 1;
    auto direct = inode_u32(fixture.inode.bytes(), 40);
    put_u32(fixture.inode.bytes(), 44, direct);
    CHECK(attempt().is_error());
    put_u32(fixture.inode.bytes(), 44, direct + 1);
    auto pointer_block = inode_u32(fixture.inode.bytes(), 88);
    auto& pointers = fixture.indirect.find(pointer_block)->value;
    auto leaf = inode_u32(pointers.bytes(), 0);
    for (auto bad : { 0u, 200000u, direct, pointer_block }) {
        put_u32(pointers.bytes(), 0, bad);
        CHECK(attempt().is_error());
    }
    put_u32(pointers.bytes(), 0, leaf);
    put_u32(pointers.bytes(), 4, leaf + 1);
    CHECK(attempt().is_error());
    put_u32(pointers.bytes(), 4, 0);
    auto failed_read = [](u32, Bytes) -> ErrorOr<void> { return Error::from_errno(EIO); };
    auto failed_validate = [](u32) -> ErrorOr<void> { return Error::from_errno(EACCES); };
    CHECK(map_journal_inode(fixture.inode.bytes(), 1024, 200000, failed_read, validate).error().code() == EIO);
    CHECK(map_journal_inode(fixture.inode.bytes(), 1024, 200000, read, failed_validate).error().code() == EACCES);
    // Every header-level allocation failure must propagate instead of asserting.
    size_t injected = 0;
    for (int allocation = 0; allocation < 100; ++allocation) {
        allocations_before_failure = allocation;
        auto result = attempt();
        allocations_before_failure = -1;
        if (!result.is_error())
            break;
        CHECK(result.error().code() == ENOMEM);
        ++injected;
    }
    CHECK(injected > 1 && injected < 100);
    printf("Journal inode map: %zu valid/bounds checks, malformed tree checks and %zu allocation failures passed\n", checks, injected);
    return 0;
}
