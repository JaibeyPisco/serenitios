/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/FixedArray.h>
#include <Kernel/FileSystem/Ext2FS/Journal.h>
#include <Kernel/FileSystem/Ext2FS/TransactionBuffer.h>

namespace Kernel::Ext2Journal {

inline void write_u32(Bytes bytes, size_t offset, u32 value)
{
    bytes[offset] = value >> 24;
    bytes[offset + 1] = value >> 16;
    bytes[offset + 2] = value >> 8;
    bytes[offset + 3] = value;
}

inline void seal_checksum(Bytes bytes, size_t offset, u32 seed)
{
    write_u32(bytes, offset, 0);
    write_u32(bytes, offset, crc32c(seed, bytes));
}

// A single-use, externally serialized transaction. Preparation never writes.
// No mounted filesystem uses this prototype. The caller must independently
// validate journal-inode mapping, ensure the old filesystem state is consistent,
// durably set the filesystem's needs-recovery feature before any journal
// transaction (this helper does not write the filesystem superblock), and write
// newly referenced data BEFORE execute(). Device::flush() must issue
// a device-only durability barrier, never filesystem writeback. Device writes
// must be complete-block writes or errors; short writes must become EIO.
// Any failed execute is indeterminate: prohibit further transactions until
// recovery. Reconstructing this object is NOT a substitute for recovery.
class PreparedCommit {
public:
    static ErrorOr<PreparedCommit> from_buffer(ReadonlyBytes clean_journal, u32 filesystem_blocks,
        Span<u32 const> journal_map, HashTable<u32> const& all_journal_blocks,
        ReadonlyBytes uuid, TransactionBuffer const& buffer)
    {
        if (clean_journal.size() < 1024 || buffer.filesystem_blocks() != filesystem_blocks || buffer.block_size() != read_u32(clean_journal, 12))
            return Error::from_errno(EINVAL);
        if (buffer.is_aborted())
            return Error::from_errno(ECANCELED);
        Vector<Write> writes;
        TRY(writes.try_ensure_capacity(buffer.size()));
        for (size_t index = 0; index < buffer.size(); ++index) {
            auto entry = TRY(buffer.entry(index));
            TRY(writes.try_append({ entry.block, TRY(ByteBuffer::copy(entry.image)) }));
        }
        return create(clean_journal, filesystem_blocks, journal_map, all_journal_blocks, uuid, writes.span());
    }

    static ErrorOr<PreparedCommit> create(ReadonlyBytes clean_journal, u32 filesystem_blocks,
        Span<u32 const> journal_map, HashTable<u32> const& all_journal_blocks,
        ReadonlyBytes uuid, Span<Write const> writes)
    {
        auto invalid = [] { return Error::from_errno(EINVAL); };
        // The parser validates the supported superblock and resource policy.
        auto previous = TRY(replay_plan(clean_journal, filesystem_blocks, all_journal_blocks, uuid));
        if (!previous.transactions.is_empty() || read_u32(clean_journal, 28) != 0 || writes.is_empty() || writes.size() > 1024)
            return invalid();
        auto block_size = read_u32(clean_journal, 12);
        auto length = read_u32(clean_journal, 16);
        // This deliberately narrow writer uses the first log block at index 1.
        if (read_u32(clean_journal, 20) != 1 || journal_map.size() != length || 2 * writes.size() + 3 > length)
            return Error::from_errno(ENOSPC);
        HashTable<u32> seen;
        auto mapping = TRY(FixedArray<u32>::create(length));
        for (size_t index = 0; index < journal_map.size(); ++index) {
            auto physical = journal_map[index];
            if (physical >= filesystem_blocks || !all_journal_blocks.contains(physical) || seen.contains(physical))
                return invalid();
            TRY(seen.try_set(physical));
            mapping[index] = physical;
        }
        seen.clear();
        Vector<Write> home_images;
        TRY(home_images.try_ensure_capacity(writes.size()));
        for (auto const& write : writes) {
            if (write.block >= filesystem_blocks || all_journal_blocks.contains(write.block) || write.data.size() != block_size || seen.contains(write.block))
                return invalid();
            TRY(seen.try_set(write.block));
            TRY(home_images.try_append({ write.block, TRY(ByteBuffer::copy(write.data.bytes())) }));
        }
        auto image = TRY(ByteBuffer::create_zeroed(clean_journal.size()));
        __builtin_memcpy(image.data(), clean_journal.data(), block_size);
        auto retired = TRY(ByteBuffer::copy(clean_journal.slice(0, block_size)));
        auto sequence = read_u32(clean_journal, 24);
        auto seed = crc32c(0xffffffff, uuid);
        Array<u8, 4> sequence_bytes {};
        write_u32(sequence_bytes.span(), 0, sequence);
        auto data_seed = crc32c(seed, sequence_bytes.span());
        for (size_t index = 0; index < home_images.size(); ++index) {
            auto descriptor = image.bytes().slice((1 + 2 * index) * block_size, block_size);
            auto payload = image.bytes().slice((2 + 2 * index) * block_size, block_size);
            __builtin_memcpy(payload.data(), home_images[index].data.data(), block_size);
            bool escaped = read_u32(payload, 0) == magic;
            if (escaped)
                write_u32(payload, 0, 0);
            write_u32(descriptor, 0, magic);
            write_u32(descriptor, 4, 1);
            write_u32(descriptor, 8, sequence);
            write_u32(descriptor, 12, home_images[index].block);
            write_u32(descriptor, 16, 8 | (escaped ? 1 : 0));
            write_u32(descriptor, 24, crc32c(data_seed, payload));
            __builtin_memcpy(descriptor.data() + 28, uuid.data(), 16);
            seal_checksum(descriptor, block_size - 4, seed);
        }
        auto commit_index = 1 + 2 * home_images.size();
        auto commit = image.bytes().slice(commit_index * block_size, block_size);
        write_u32(commit, 0, magic);
        write_u32(commit, 4, 2);
        write_u32(commit, 8, sequence);
        seal_checksum(commit, 16, seed);
        write_u32(image.bytes(), 28, 1);
        seal_checksum(image.bytes().slice(0, 1024), 252, 0xffffffff);
        write_u32(retired.bytes(), 24, sequence + 1);
        write_u32(retired.bytes(), 28, 0);
        seal_checksum(retired.bytes().slice(0, 1024), 252, 0xffffffff);
        auto zero = TRY(ByteBuffer::create_zeroed(block_size));
        return PreparedCommit(block_size, move(mapping), move(image), move(retired), move(zero), move(home_images));
    }

    PreparedCommit(PreparedCommit const&) = delete;
    PreparedCommit& operator=(PreparedCommit const&) = delete;
    PreparedCommit(PreparedCommit&& other)
        : m_block_size(other.m_block_size)
        , m_mapping(move(other.m_mapping))
        , m_image(move(other.m_image))
        , m_retired(move(other.m_retired))
        , m_zero(move(other.m_zero))
        , m_home_images(move(other.m_home_images))
        , m_attempted(exchange(other.m_attempted, true))
        , m_commit_durable(other.m_commit_durable)
        , m_completed(other.m_completed)
    {
    }
    PreparedCommit& operator=(PreparedCommit&&) = delete;

    ReadonlyBytes committed_journal() const { return m_image.bytes(); }
    bool attempted() const { return m_attempted; }
    // False after a failed barrier does NOT establish that the commit is absent.
    // Failed I/O may still have persisted; recovery is mandatory on every error.
    bool commit_durable() const { return m_commit_durable; }
    bool completed() const { return m_completed; }

    template<typename Device>
    ErrorOr<void> execute(Device& device)
    {
        if (m_attempted)
            return Error::from_errno(ECANCELED);
        m_attempted = true;
        // Newly referenced caller data precedes all journal metadata.
        TRY(device.flush());
        auto commit_index = 1 + 2 * m_home_images.size();
        for (size_t index = 1; index < commit_index; ++index)
            TRY(device.write_block(m_mapping[index], m_image.bytes().slice(index * m_block_size, m_block_size)));
        // Supersede any old commit and terminating record before arming.
        TRY(device.write_block(m_mapping[commit_index], m_zero.bytes()));
        TRY(device.write_block(m_mapping[commit_index + 1], m_zero.bytes()));
        TRY(device.flush());
        TRY(device.write_block(m_mapping[0], m_image.bytes().slice(0, m_block_size)));
        TRY(device.flush());
        TRY(device.write_block(m_mapping[commit_index], m_image.bytes().slice(commit_index * m_block_size, m_block_size)));
        TRY(device.flush());
        m_commit_durable = true;
        for (auto const& write : m_home_images)
            TRY(device.write_block(write.block, write.data.bytes()));
        TRY(device.flush());
        TRY(device.write_block(m_mapping[0], m_retired.bytes()));
        TRY(device.flush());
        m_completed = true;
        return {};
    }

private:
    PreparedCommit(size_t block_size, FixedArray<u32> mapping, ByteBuffer image, ByteBuffer retired, ByteBuffer zero, Vector<Write> home_images)
        : m_block_size(block_size)
        , m_mapping(move(mapping))
        , m_image(move(image))
        , m_retired(move(retired))
        , m_zero(move(zero))
        , m_home_images(move(home_images))
    {
    }
    size_t m_block_size;
    FixedArray<u32> m_mapping;
    ByteBuffer m_image;
    ByteBuffer m_retired;
    ByteBuffer m_zero;
    Vector<Write> m_home_images;
    bool m_attempted { false };
    bool m_commit_durable { false };
    bool m_completed { false };
};

}
