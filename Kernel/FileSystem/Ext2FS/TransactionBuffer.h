/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/ByteBuffer.h>
#include <AK/FixedArray.h>
#include <AK/Optional.h>

namespace Kernel::Ext2Journal {

// Standalone transaction storage only: no device I/O, commit protocol, cache
// integration or in-memory filesystem rollback. The caller supplies validated
// full metadata blocks and must exclude journal blocks before staging them.
// Single-owner, externally synchronized. Returned views expire on mutation,
// abort, move or destruction. Successful creation reserves all storage; staging
// and reading never allocate. Credits describe unique blocks, not write calls.
class TransactionBuffer {
public:
    static constexpr size_t maximum_credits = 1024;

    static ErrorOr<TransactionBuffer> create(size_t block_size, u32 filesystem_blocks, size_t credits)
    {
        if ((block_size != 1024 && block_size != 2048 && block_size != 4096) || filesystem_blocks == 0 || credits == 0)
            return Error::from_errno(EINVAL);
        if (credits > maximum_credits || credits > filesystem_blocks)
            return Error::from_errno(E2BIG);
        // Geometry/credit bounds above cap this multiplication at 4 MiB.
        auto blocks = TRY(FixedArray<u32>::create(credits));
        auto images = TRY(ByteBuffer::create_zeroed(credits * block_size));
        return TransactionBuffer(block_size, filesystem_blocks, move(blocks), move(images));
    }

    TransactionBuffer(TransactionBuffer const&) = delete;
    TransactionBuffer& operator=(TransactionBuffer const&) = delete;
    TransactionBuffer& operator=(TransactionBuffer&&) = delete;
    TransactionBuffer(TransactionBuffer&& other)
        : m_block_size(other.m_block_size)
        , m_filesystem_blocks(other.m_filesystem_blocks)
        , m_blocks(move(other.m_blocks))
        , m_images(move(other.m_images))
        , m_count(exchange(other.m_count, 0))
        , m_aborted(exchange(other.m_aborted, true))
    {
    }

    size_t block_size() const { return m_block_size; }
    u32 filesystem_blocks() const { return m_filesystem_blocks; }
    size_t capacity() const { return m_blocks.size(); }
    size_t size() const { return m_count; }
    bool is_aborted() const { return m_aborted; }

    struct Entry {
        u32 block;
        ReadonlyBytes image;
    };

    // Enumerate reserved images while preparing a commit, before device I/O.
    ErrorOr<Entry> entry(size_t index) const
    {
        if (m_aborted)
            return Error::from_errno(ECANCELED);
        if (index >= m_count)
            return Error::from_errno(ERANGE);
        return Entry { m_blocks[index], m_images.bytes().slice(index * m_block_size, m_block_size) };
    }

    ErrorOr<void> stage(u32 block, ReadonlyBytes image)
    {
        if (m_aborted)
            return Error::from_errno(ECANCELED);
        if (block >= m_filesystem_blocks || image.size() != m_block_size)
            return Error::from_errno(EINVAL);
        size_t slot = 0;
        for (; slot < m_count; ++slot) {
            if (m_blocks[slot] == block)
                break;
        }
        if (slot == m_count && m_count == capacity())
            return Error::from_errno(ENOSPC);
        // A caller may stage a view returned by read(), including this slot.
        __builtin_memmove(m_images.data() + slot * m_block_size, image.data(), m_block_size);
        if (slot == m_count) {
            m_blocks[slot] = block;
            ++m_count;
        }
        return {};
    }

    ErrorOr<Optional<ReadonlyBytes>> read(u32 block) const
    {
        if (m_aborted)
            return Error::from_errno(ECANCELED);
        if (block >= m_filesystem_blocks)
            return Error::from_errno(EINVAL);
        for (size_t slot = 0; slot < m_count; ++slot) {
            if (m_blocks[slot] == block)
                return Optional<ReadonlyBytes> { m_images.bytes().slice(slot * m_block_size, m_block_size) };
        }
        return Optional<ReadonlyBytes> {};
    }

    // Abort is terminal and idempotent. It discards staged images but cannot
    // undo any caller-owned filesystem state; this class has never written disk.
    void abort()
    {
        m_images.zero_fill();
        m_count = 0;
        m_aborted = true;
    }

private:
    TransactionBuffer(size_t block_size, u32 filesystem_blocks, FixedArray<u32> blocks, ByteBuffer images)
        : m_block_size(block_size)
        , m_filesystem_blocks(filesystem_blocks)
        , m_blocks(move(blocks))
        , m_images(move(images))
    {
    }

    size_t m_block_size;
    u32 m_filesystem_blocks;
    FixedArray<u32> m_blocks;
    ByteBuffer m_images;
    size_t m_count { 0 };
    bool m_aborted { false };
};

}
