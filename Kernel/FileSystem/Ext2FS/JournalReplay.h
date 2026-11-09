/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <Kernel/FileSystem/Ext2FS/Journal.h>

namespace Kernel::Ext2Journal {

// Recovery executor only. Mapping must come from independently validated inode
// metadata, including every journal-owned indirect block in journal_blocks.
// The caller must exclude all other filesystem/cache I/O for the entire run.
// It must keep the filesystem needs_recovery flag until this protocol succeeds.
// No writable mount or cache integration is supplied by this component.
class PreparedReplay {
public:
    static ErrorOr<PreparedReplay> create(ReadonlyBytes journal, u32 filesystem_blocks, Span<u32 const> mapping, HashTable<u32> const& journal_blocks, ReadonlyBytes uuid)
    {
        auto plan = TRY(replay_plan(journal, filesystem_blocks, journal_blocks, uuid));
        auto block_size = read_u32(journal, 12);
        if (mapping.size() != journal.size() / block_size)
            return Error::from_errno(EINVAL);
        HashTable<u32> mapped;
        for (auto block : mapping) {
            if (block >= filesystem_blocks || !journal_blocks.contains(block) || mapped.contains(block))
                return Error::from_errno(EINVAL);
            TRY(mapped.try_set(block));
        }
        auto retired = TRY(ByteBuffer::copy(journal.slice(0, block_size)));
        auto next_sequence = plan.transactions.is_empty() ? read_u32(journal, 24) : plan.transactions.last() + 1;
        put_u32(retired.bytes(), 24, next_sequence);
        put_u32(retired.bytes(), 28, 0);
        put_u32(retired.bytes(), 252, 0);
        put_u32(retired.bytes(), 252, crc32c(0xffffffff, retired.bytes().slice(0, 1024)));
        return PreparedReplay(move(plan), move(retired), mapping[0]);
    }

    PreparedReplay(PreparedReplay const&) = delete;
    PreparedReplay& operator=(PreparedReplay const&) = delete;
    PreparedReplay& operator=(PreparedReplay&&) = delete;
    PreparedReplay(PreparedReplay&& other)
        : m_plan(move(other.m_plan))
        , m_retired(move(other.m_retired))
        , m_superblock(other.m_superblock)
        , m_attempted(exchange(other.m_attempted, true))
        , m_complete(exchange(other.m_complete, false))
    {
    }

    // Device callbacks must bypass ordinary filesystem caches, reject short
    // writes, and flush the actual device. All memory was allocated by create().
    // A failed write/flush may nevertheless have persisted: this object is
    // terminal after ANY attempt, and the caller must re-read/recover the disk.
    template<typename Device>
    ErrorOr<void> execute(Device& device)
    {
        if (m_attempted)
            return Error::from_errno(EALREADY);
        m_attempted = true;
        for (auto const& write : m_plan.writes)
            TRY(device.write_block(write.block, write.data.bytes()));
        // Home blocks must be durable before a clean journal can be published.
        TRY(device.flush());
        TRY(device.write_block(m_superblock, m_retired.bytes()));
        TRY(device.flush());
        m_complete = true;
        return {};
    }

    bool is_complete() const { return m_complete; }

private:
    static void put_u32(Bytes bytes, size_t offset, u32 value)
    {
        bytes[offset] = value >> 24;
        bytes[offset + 1] = value >> 16;
        bytes[offset + 2] = value >> 8;
        bytes[offset + 3] = value;
    }

    PreparedReplay(ReplayPlan plan, ByteBuffer retired, u32 superblock)
        : m_plan(move(plan))
        , m_retired(move(retired))
        , m_superblock(superblock)
    {
    }

    ReplayPlan m_plan;
    ByteBuffer m_retired;
    u32 m_superblock;
    bool m_attempted { false };
    bool m_complete { false };
};

}
