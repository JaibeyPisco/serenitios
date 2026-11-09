/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

// Host-only binary adapter for the AK-only kernel parser. It never writes disks.
#include <Kernel/FileSystem/Ext2FS/Journal.h>
#include <stdio.h>

int main()
{
    Vector<u8> input;
    Array<u8, 4096> chunk;
    while (auto count = fread(chunk.data(), 1, chunk.size(), stdin)) {
        if (input.size() + count > Kernel::Ext2Journal::maximum_journal_bytes + 1024 * 1024)
            return 2;
        if (input.try_extend(chunk.span().slice(0, count)).is_error())
            return 2;
    }
    if (ferror(stdin) || input.size() < 12)
        return 2;
    auto bytes = input.span();
    auto filesystem_blocks = Kernel::Ext2Journal::read_u32(bytes, 0);
    auto map_count = Kernel::Ext2Journal::read_u32(bytes, 4);
    auto uuid_size = Kernel::Ext2Journal::read_u32(bytes, 8);
    if (map_count > (bytes.size() - 12) / 4)
        return 2;
    size_t offset = 12 + static_cast<size_t>(map_count) * 4;
    if (uuid_size > bytes.size() - offset)
        return 2;
    HashTable<u32> journal_blocks;
    for (size_t index = 0; index < map_count; ++index) {
        if (journal_blocks.try_set(Kernel::Ext2Journal::read_u32(bytes, 12 + index * 4)).is_error())
            return 2;
    }
    auto result = Kernel::Ext2Journal::replay_plan(bytes.slice(offset + uuid_size), filesystem_blocks, journal_blocks, bytes.slice(offset, uuid_size));
    if (result.is_error())
        return result.error().code() == E2BIG ? 3 : 1;
    auto plan = result.release_value();
    for (auto sequence : plan.transactions)
        printf("t %u\n", sequence);
    for (auto const& write : plan.writes) {
        printf("w %u ", write.block);
        for (auto byte : write.data.bytes())
            printf("%02x", byte);
        putchar('\n');
    }
    return ferror(stdout) ? 2 : 0;
}
