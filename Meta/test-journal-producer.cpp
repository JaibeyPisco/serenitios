/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

// Host-only adapter: reads a framed request and emits a journal image on stdout.
// It never opens or writes filesystem images, devices or existing files.
#include <Kernel/FileSystem/Ext2FS/JournalCommit.h>
#include <stdio.h>

using namespace Kernel::Ext2Journal;

int main()
{
    Vector<u8> input;
    Array<u8, 4096> chunk;
    while (auto count = fread(chunk.data(), 1, chunk.size(), stdin)) {
        if (input.size() + count > maximum_journal_bytes + 8 * 1024 * 1024)
            return 2;
        if (input.try_extend(chunk.span().slice(0, count)).is_error())
            return 2;
    }
    if (ferror(stdin) || input.size() < 28)
        return 2;
    auto bytes = input.span();
    auto filesystem_blocks = read_u32(bytes, 0);
    auto block_size = read_u32(bytes, 4);
    auto logical_count = read_u32(bytes, 8);
    auto physical_count = read_u32(bytes, 12);
    auto uuid_size = read_u32(bytes, 16);
    auto write_count = read_u32(bytes, 20);
    auto committed = read_u32(bytes, 24);
    if ((block_size != 1024 && block_size != 2048 && block_size != 4096)
        || logical_count > maximum_journal_bytes / block_size || physical_count > maximum_journal_bytes / block_size + 1024
        || uuid_size != 16 || write_count > 1024 || committed > 1)
        return 2;
    size_t offset = 28;
    if (static_cast<size_t>(logical_count) + physical_count > (bytes.size() - offset) / 4)
        return 2;
    Vector<u32> mapping;
    HashTable<u32> physical;
    for (u32 index = 0; index < logical_count; ++index) {
        if (mapping.try_append(read_u32(bytes, offset)).is_error())
            return 2;
        offset += 4;
    }
    for (u32 index = 0; index < physical_count; ++index) {
        if (physical.try_set(read_u32(bytes, offset)).is_error())
            return 2;
        offset += 4;
    }
    if (bytes.size() - offset < uuid_size + 4)
        return 2;
    auto uuid = bytes.slice(offset, uuid_size);
    offset += uuid_size;
    auto journal_size = read_u32(bytes, offset);
    offset += 4;
    if (journal_size > bytes.size() - offset)
        return 2;
    auto journal = bytes.slice(offset, journal_size);
    offset += journal_size;
    if (bytes.size() - offset != static_cast<size_t>(write_count) * (block_size + 4))
        return 2;
    Vector<Write> writes;
    for (u32 index = 0; index < write_count; ++index) {
        auto block = read_u32(bytes, offset);
        auto data = ByteBuffer::copy(bytes.slice(offset + 4, block_size));
        if (data.is_error() || writes.try_append({ block, data.release_value() }).is_error())
            return 2;
        offset += block_size + 4;
    }
    auto result = PreparedCommit::create(journal, filesystem_blocks, mapping.span(), physical, uuid, writes.span());
    if (result.is_error()) {
        fprintf(stderr, "PreparedCommit::create failed: %d\n", result.error().code());
        return 1;
    }
    auto prepared = result.release_value();
    auto image = ByteBuffer::copy(prepared.committed_journal());
    if (image.is_error())
        return 2;
    auto output = image.release_value();
    if (!committed) {
        auto commit_offset = (1 + 2 * writes.size()) * block_size;
        if (commit_offset + block_size > output.size())
            return 2;
        __builtin_memset(output.data() + commit_offset, 0, block_size);
    }
    return fwrite(output.data(), 1, output.size(), stdout) == output.size() && !ferror(stdout) ? 0 : 2;
}
