/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/ByteBuffer.h>
#include <AK/HashTable.h>
#include <AK/Vector.h>

namespace Kernel::Ext2Journal {

struct JournalInodeMap {
    Vector<u32> mapping;
    HashTable<u32> all_blocks;
};

inline u32 inode_u32(ReadonlyBytes bytes, size_t offset)
{
    return bytes[offset] | (static_cast<u32>(bytes[offset + 1]) << 8) | (static_cast<u32>(bytes[offset + 2]) << 16) | (static_cast<u32>(bytes[offset + 3]) << 24);
}

// Decode a restricted internal journal's traditional ext2 block tree. The caller
// independently validates filesystem geometry, inode ownership and its inode
// table location. validate_block must reject unallocated blocks and filesystem
// metadata (superblocks, descriptors, bitmaps and inode tables). It is called
// before any pointer-block read. Both callbacks propagate allocation/I/O errors.
// There are no writes and no partially validated mapping is returned on error.
template<typename ReadBlock, typename ValidateBlock>
ErrorOr<JournalInodeMap> map_journal_inode(ReadonlyBytes inode, u32 block_size, u32 filesystem_blocks, ReadBlock& read_block, ValidateBlock& validate_block)
{
    if (inode.size() < 128 || (block_size != 1024 && block_size != 2048 && block_size != 4096) || filesystem_blocks == 0)
        return Error::from_errno(EINVAL);
    auto mode = static_cast<u16>(inode[0]) | (static_cast<u16>(inode[1]) << 8);
    auto links = static_cast<u16>(inode[26]) | (static_cast<u16>(inode[27]) << 8);
    // No extents, compression, ACL blocks, fragments or 64-bit sizes supported.
    if ((mode & 0xf000) != 0x8000 || links != 1 || inode_u32(inode, 20) != 0 || inode_u32(inode, 32) != 0 || inode_u32(inode, 104) != 0 || inode_u32(inode, 108) != 0 || inode_u32(inode, 112) != 0)
        return Error::from_errno(ENOTSUP);
    auto size = inode_u32(inode, 4);
    constexpr size_t maximum_bytes = 64 * 1024 * 1024;
    if (size < 2 * block_size || size > maximum_bytes || size % block_size != 0)
        return Error::from_errno(EINVAL);
    auto data_blocks = size / block_size;
    JournalInodeMap result;
    TRY(result.mapping.try_ensure_capacity(data_blocks));
    auto claim = [&](u32 block) -> ErrorOr<void> {
        if (block == 0 || block >= filesystem_blocks || result.all_blocks.contains(block))
            return Error::from_errno(EINVAL);
        TRY(validate_block(block));
        TRY(result.all_blocks.try_set(block));
        return {};
    };
    auto append_data = [&](u32 block) -> ErrorOr<void> {
        TRY(claim(block));
        TRY(result.mapping.try_append(block));
        return {};
    };
    for (size_t index = 0; index < 12; ++index) {
        auto block = inode_u32(inode, 40 + index * 4);
        if (index < data_blocks)
            TRY(append_data(block));
        else if (block != 0)
            return Error::from_errno(EINVAL);
    }
    auto pointers_per_block = block_size / 4;
    // At most three recursive levels; each owns one fallible heap block buffer,
    // avoiding multi-KiB stack arrays on the kernel's limited stack.
    auto visit = [&](auto&& self, u32 block, unsigned depth, size_t count) -> ErrorOr<void> {
        if (count == 0) {
            if (block != 0)
                return Error::from_errno(EINVAL);
            return {};
        }
        TRY(claim(block));
        auto pointers = TRY(ByteBuffer::create_zeroed(block_size));
        TRY(read_block(block, pointers.bytes()));
        size_t child_capacity = 1;
        for (unsigned level = 1; level < depth; ++level)
            child_capacity *= pointers_per_block;
        for (size_t index = 0; index < pointers_per_block; ++index) {
            auto child = inode_u32(pointers.bytes(), index * 4);
            auto child_count = min(count, child_capacity);
            if (depth == 1) {
                if (child_count != 0)
                    TRY(append_data(child));
                else if (child != 0)
                    return Error::from_errno(EINVAL);
            } else {
                TRY(self(self, child, depth - 1, child_count));
            }
            count -= child_count;
        }
        if (count != 0)
            return Error::from_errno(EINVAL);
        return {};
    };
    size_t remaining = data_blocks > 12 ? data_blocks - 12 : 0;
    size_t tree_capacity = pointers_per_block;
    for (unsigned depth = 1; depth <= 3; ++depth) {
        auto count = min(remaining, tree_capacity);
        TRY(visit(visit, inode_u32(inode, 40 + (11 + depth) * 4), depth, count));
        remaining -= count;
        tree_capacity *= pointers_per_block;
    }
    // Exact accounting rejects hidden blocks and unsupported auxiliary storage.
    if (remaining != 0 || result.mapping.size() != data_blocks || inode_u32(inode, 28) != result.all_blocks.size() * (block_size / 512))
        return Error::from_errno(EINVAL);
    return result;
}

}
