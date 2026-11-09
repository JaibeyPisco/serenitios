/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/Array.h>
#include <Kernel/FileSystem/Ext2FS/TransactionBuffer.h>
#include <stdio.h>

#define CHECK(condition)                                     \
    do {                                                     \
        if (!(condition)) {                                  \
            fprintf(stderr, "Failed at line %d\n", __LINE__); \
            return 1;                                        \
        }                                                    \
    } while (0)

using Kernel::Ext2Journal::TransactionBuffer;

// Instrument malloc calls emitted by these AK header implementations. Shared
// Lagom-library internals are not interposed by the linker's --wrap option.
static int allocations_before_failure = -1;
static size_t allocation_calls = 0;
extern "C" void* __real_malloc(size_t);
extern "C" void* __wrap_malloc(size_t size)
{
    ++allocation_calls;
    if (allocations_before_failure == 0)
        return nullptr;
    if (allocations_before_failure > 0)
        --allocations_before_failure;
    return __real_malloc(size);
}

int main()
{
    CHECK(TransactionBuffer::create(512, 100, 1).error().code() == EINVAL);
    CHECK(TransactionBuffer::create(8192, 100, 1).error().code() == EINVAL);
    CHECK(TransactionBuffer::create(1024, 0, 1).error().code() == EINVAL);
    CHECK(TransactionBuffer::create(1024, 100, 0).error().code() == EINVAL);
    CHECK(TransactionBuffer::create(1024, 100, 101).error().code() == E2BIG);
    CHECK(TransactionBuffer::create(4096, 0xffffffff, static_cast<size_t>(-1)).error().code() == E2BIG);
    for (int allocation = 0; allocation < 2; ++allocation) {
        allocations_before_failure = allocation;
        auto failed = TransactionBuffer::create(4096, 100, 2);
        allocations_before_failure = -1;
        CHECK(failed.is_error() && failed.error().code() == ENOMEM);
    }

    for (size_t block_size : { 1024, 2048, 4096 }) {
        auto transaction = MUST(TransactionBuffer::create(block_size, 100, 2));
        auto image = MUST(ByteBuffer::create_zeroed(block_size));
        image[0] = 0x42;
        image[block_size - 1] = 0x7b;
        CHECK(transaction.capacity() == 2 && transaction.size() == 0);
        CHECK(transaction.entry(0).error().code() == ERANGE);
        CHECK(!MUST(transaction.read(99)).has_value());
        CHECK(transaction.read(100).error().code() == EINVAL);
        CHECK(transaction.stage(100, image.bytes()).error().code() == EINVAL);
        CHECK(transaction.stage(0, image.bytes().slice(0, block_size - 1)).error().code() == EINVAL);
        CHECK(transaction.size() == 0);
        CHECK(!transaction.stage(0, image.bytes()).is_error());
        image[0] = 0x99;
        CHECK(MUST(transaction.read(0)).value()[0] == 0x42);
        CHECK(MUST(transaction.read(0)).value()[block_size - 1] == 0x7b);
        CHECK(!transaction.stage(99, image.bytes()).is_error());
        CHECK(transaction.size() == 2);
        CHECK(MUST(transaction.entry(0)).block == 0);
        CHECK(MUST(transaction.entry(1)).block == 99);
        CHECK(MUST(transaction.entry(0)).image[0] == 0x42);
        CHECK(transaction.entry(2).error().code() == ERANGE);
        CHECK(transaction.stage(1, image.bytes()).error().code() == ENOSPC);
        CHECK(!MUST(transaction.read(1)).has_value());
        CHECK(MUST(transaction.read(0)).value()[0] == 0x42);
        CHECK(!transaction.stage(0, image.bytes()).is_error());
        CHECK(transaction.size() == 2);
        CHECK(MUST(transaction.read(0)).value()[0] == 0x99);
        // Reads can be supplied as input, including a view of the target slot.
        CHECK(!transaction.stage(0, MUST(transaction.read(0)).value()).is_error());
        CHECK(!transaction.stage(0, MUST(transaction.read(99)).value()).is_error());
        CHECK(MUST(transaction.read(0)).value() == MUST(transaction.read(99)).value());
        auto moved = move(transaction);
        CHECK(transaction.is_aborted() && transaction.size() == 0);
        CHECK(transaction.stage(1, image.bytes()).error().code() == ECANCELED);
        CHECK(moved.size() == 2 && MUST(moved.read(0)).has_value());
        moved.abort();
        moved.abort();
        CHECK(moved.is_aborted() && moved.size() == 0);
        CHECK(moved.read(0).error().code() == ECANCELED);
        CHECK(moved.entry(0).error().code() == ECANCELED);
        CHECK(moved.stage(1, image.bytes()).error().code() == ECANCELED);
    }

    // Exercise all reserved slots at the maximum permitted geometry.
    auto full = MUST(TransactionBuffer::create(4096, 2048, TransactionBuffer::maximum_credits));
    Array<u8, 4096> image {};
    auto calls_before_staging = allocation_calls;
    allocations_before_failure = 0;
    for (u32 block = 0; block < TransactionBuffer::maximum_credits; ++block) {
        image[0] = block & 0xff;
        image[1] = block >> 8;
        CHECK(!full.stage(block, image.span()).is_error());
    }
    CHECK(full.stage(1024, image.span()).error().code() == ENOSPC);
    for (u32 block = 0; block < TransactionBuffer::maximum_credits; ++block) {
        auto staged = MUST(full.read(block)).value();
        CHECK(staged[0] == (block & 0xff) && staged[1] == (block >> 8));
    }
    full.abort();
    allocations_before_failure = -1;
    CHECK(full.size() == 0);
    CHECK(allocation_calls == calls_before_staging);
    puts("Transaction buffer: geometry, bounds, full-block copies, credits, aliasing, move and abort passed");
    return 0;
}
