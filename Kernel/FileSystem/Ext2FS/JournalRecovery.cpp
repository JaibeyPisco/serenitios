/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/ByteBuffer.h>
#include <AK/IntegralMath.h>
#include <Kernel/API/MajorNumberAllocation.h>
#include <Kernel/Devices/Storage/StorageDevice.h>
#include <Kernel/FileSystem/Ext2FS/FileSystem.h>
#include <Kernel/FileSystem/Ext2FS/JournalInode.h>
#include <Kernel/FileSystem/Ext2FS/JournalReplay.h>

namespace Kernel {

ErrorOr<void> Ext2FS::replay_journal_for_readonly_mount()
{
    // Called before constructing the ordinary block cache or publishing inodes.
    // VFS serializes filesystem creation; raw device access by other privileged
    // users is outside this opt-in, standalone-device recovery protocol.
    if (!file_description().is_readable() || !file_description().is_writable())
        return EBADF;
    auto* device = file_description().device();
    if (!device || device->major() != static_cast<unsigned>(MajorAllocation::BlockDeviceFamily::Storage))
        return ENOTSUP;
    auto const& storage = static_cast<StorageDevice const&>(*device);
    if (!storage.partitions().is_empty())
        return ENOTSUP;

    auto const block_size = static_cast<u32>(logical_block_size());
    auto const blocks = m_super_block.s_blocks_count;
    auto const inode_bytes = m_super_block.s_inode_size;
    // Keep recovery's trusted geometry deliberately small. Multiple block
    // groups, external journals and orphan cleanup require additional protocols.
    if (m_super_block.s_rev_level != EXT2_DYNAMIC_REV || blocks == 0
        || m_super_block.s_first_data_block != (block_size == 1024 ? 1u : 0u)
        || m_super_block.s_blocks_per_group == 0 || blocks > m_super_block.s_blocks_per_group
        || m_super_block.s_blocks_per_group > block_size * 8
        || m_super_block.s_inodes_count < 11 || m_super_block.s_inodes_count > m_super_block.s_inodes_per_group
        || m_super_block.s_inodes_per_group > block_size * 8
        || inode_bytes < 128 || inode_bytes > block_size || block_size % inode_bytes != 0
        || !AK::is_power_of_two(inode_bytes) || m_super_block.s_first_ino < 11 || m_super_block.s_first_ino > m_super_block.s_inodes_count
        || m_super_block.s_log_frag_size != static_cast<i32>(m_super_block.s_log_block_size)
        || m_super_block.s_journal_inum != 8 || m_super_block.s_journal_dev != 0
        || m_super_block.s_last_orphan != 0 || m_super_block.s_blocks_count_hi != 0)
        return ENOTSUP;
    for (auto byte : m_super_block.s_journal_uuid) {
        if (byte != 0)
            return ENOTSUP;
    }
    auto const device_bytes = storage.max_mathematical_addressable_block() * storage.block_size();
    if (static_cast<u64>(blocks) * block_size > device_bytes)
        return EINVAL;

    auto read_block = [&](u32 block, Bytes bytes) -> ErrorOr<void> {
        if (block >= blocks || bytes.size() != block_size)
            return EINVAL;
        auto buffer = UserOrKernelBuffer::for_kernel_buffer(bytes.data());
        auto count = TRY(file_description().read(buffer, static_cast<u64>(block) * block_size, block_size));
        if (count != block_size)
            return EIO;
        return {};
    };

    auto const descriptor_block = m_super_block.s_first_data_block + 1;
    auto const reserved_end = static_cast<u64>(descriptor_block) + 1 + m_super_block.s_reserved_gdt_blocks;
    if (reserved_end >= blocks)
        return EINVAL;
    auto descriptors = TRY(ByteBuffer::create_zeroed(block_size));
    TRY(read_block(descriptor_block, descriptors.bytes()));
    ext2_group_desc descriptor;
    __builtin_memcpy(&descriptor, descriptors.data(), sizeof(descriptor));
    auto const table_blocks = ceil_div(static_cast<u64>(m_super_block.s_inodes_per_group) * inode_bytes, static_cast<u64>(block_size));
    auto const table_end = static_cast<u64>(descriptor.bg_inode_table) + table_blocks;
    if (descriptor.bg_block_bitmap < reserved_end || descriptor.bg_inode_bitmap < reserved_end
        || descriptor.bg_inode_table < reserved_end || descriptor.bg_block_bitmap >= blocks
        || descriptor.bg_inode_bitmap >= blocks || table_end > blocks
        || descriptor.bg_block_bitmap == descriptor.bg_inode_bitmap
        || (descriptor.bg_block_bitmap >= descriptor.bg_inode_table && descriptor.bg_block_bitmap < table_end)
        || (descriptor.bg_inode_bitmap >= descriptor.bg_inode_table && descriptor.bg_inode_bitmap < table_end))
        return EINVAL;

    auto block_bitmap = TRY(ByteBuffer::create_zeroed(block_size));
    auto inode_bitmap = TRY(ByteBuffer::create_zeroed(block_size));
    TRY(read_block(descriptor.bg_block_bitmap, block_bitmap.bytes()));
    TRY(read_block(descriptor.bg_inode_bitmap, inode_bitmap.bytes()));
    auto allocated = [&](ReadonlyBytes bitmap, u32 index) { return (bitmap[index / 8] & (1u << (index % 8))) != 0; };
    if (!allocated(inode_bitmap.bytes(), 7))
        return EINVAL;
    auto is_metadata = [&](u32 block) {
        return block < reserved_end || block == descriptor.bg_block_bitmap || block == descriptor.bg_inode_bitmap
            || (block >= descriptor.bg_inode_table && block < table_end);
    };
    auto is_allocated_block = [&](ReadonlyBytes bitmap, u32 block) {
        return block >= m_super_block.s_first_data_block && allocated(bitmap, block - m_super_block.s_first_data_block);
    };
    for (u32 block = m_super_block.s_first_data_block; block < blocks; ++block) {
        if (is_metadata(block) && !is_allocated_block(block_bitmap.bytes(), block))
            return EINVAL;
    }

    auto const journal_inode_offset = 7u * inode_bytes;
    auto const journal_inode_block = descriptor.bg_inode_table + journal_inode_offset / block_size;
    auto const inode_offset_in_block = journal_inode_offset % block_size;
    auto journal_inode_table = TRY(ByteBuffer::create_zeroed(block_size));
    TRY(read_block(journal_inode_block, journal_inode_table.bytes()));
    auto journal_inode = journal_inode_table.bytes().slice(inode_offset_in_block, inode_bytes);
    auto validate_block = [&](u32 block) -> ErrorOr<void> {
        if (is_metadata(block) || !is_allocated_block(block_bitmap.bytes(), block))
            return EINVAL;
        return {};
    };
    auto journal_map = TRY(Ext2Journal::map_journal_inode(journal_inode, block_size, blocks, read_block, validate_block));
    auto journal = TRY(ByteBuffer::create_zeroed(journal_map.mapping.size() * block_size));
    for (size_t index = 0; index < journal_map.mapping.size(); ++index)
        TRY(read_block(journal_map.mapping[index], journal.bytes().slice(index * block_size, block_size)));
    auto uuid = ReadonlyBytes { m_super_block.s_uuid, sizeof(m_super_block.s_uuid) };
    auto plan = TRY(Ext2Journal::replay_plan(journal.bytes(), blocks, journal_map.all_blocks, uuid));
    auto superblock_image = TRY(ByteBuffer::create_zeroed(block_size));
    auto const superblock_block = block_size == 1024 ? 1u : 0u;
    auto const superblock_offset = block_size == 1024 ? 0u : 1024u;
    TRY(read_block(superblock_block, superblock_image.bytes()));

    // An interrupted replay must still expose the same journal inode, block
    // tree and filesystem geometry when restarted. Validate ALL after-images
    // before allowing the first write; only mutable allocation counters in the
    // primary superblock and descriptor, plus write time, are accepted by this
    // narrow path.
    for (auto const& write : plan.writes) {
        if (write.block < descriptor_block && write.block != superblock_block)
            return ENOTSUP;
        if (write.block == superblock_block) {
            auto candidate = TRY(ByteBuffer::copy(write.data.bytes()));
            auto offset = superblock_offset;
            auto const free_blocks = Ext2Journal::inode_u32(candidate.bytes(), offset + 12);
            auto const free_inodes = Ext2Journal::inode_u32(candidate.bytes(), offset + 16);
            if (free_blocks > blocks || free_inodes > m_super_block.s_inodes_count)
                return EINVAL;
            __builtin_memcpy(candidate.data() + offset + 12, superblock_image.data() + offset + 12, 8);
            __builtin_memcpy(candidate.data() + offset + 48, superblock_image.data() + offset + 48, 4);
            if (candidate.bytes() != superblock_image.bytes())
                return ENOTSUP;
        }
        if (write.block == descriptor_block) {
            auto candidate = TRY(ByteBuffer::copy(write.data.bytes()));
            auto read_u16 = [&](size_t offset) -> u32 { return static_cast<u32>(candidate[offset]) | (static_cast<u32>(candidate[offset + 1]) << 8); };
            if (read_u16(12) > blocks || read_u16(14) > m_super_block.s_inodes_count || read_u16(16) > m_super_block.s_inodes_count)
                return EINVAL;
            __builtin_memcpy(candidate.data() + 12, descriptors.data() + 12, 6);
            if (candidate.bytes() != descriptors.bytes())
                return ENOTSUP;
        }
        if (write.block == journal_inode_block
            && write.data.bytes().slice(inode_offset_in_block, inode_bytes) != journal_inode)
            return ENOTSUP;
        if (write.block == descriptor.bg_block_bitmap) {
            for (u32 block = m_super_block.s_first_data_block; block < blocks; ++block) {
                if ((is_metadata(block) || journal_map.all_blocks.contains(block)) && !is_allocated_block(write.data.bytes(), block))
                    return ENOTSUP;
            }
        }
        if (write.block == descriptor.bg_inode_bitmap) {
            for (u32 inode = 0; inode < min(m_super_block.s_first_ino - 1, m_super_block.s_inodes_count); ++inode) {
                if (allocated(write.data.bytes(), inode) != allocated(inode_bitmap.bytes(), inode))
                    return ENOTSUP;
            }
        }
        // Preserve reserved descriptor expansion space exactly.
        if (write.block > descriptor_block && write.block < reserved_end) {
            auto original = TRY(ByteBuffer::create_zeroed(block_size));
            TRY(read_block(write.block, original.bytes()));
            if (write.data.bytes() != original.bytes())
                return ENOTSUP;
        }
    }
    auto prepared = TRY(Ext2Journal::PreparedReplay::create(journal.bytes(), blocks, journal_map.mapping.span(), journal_map.all_blocks, uuid));
    struct ReplayDevice {
        OpenFileDescription& description;
        u32 block_size;
        ErrorOr<void> write_block(u32 block, ReadonlyBytes bytes)
        {
            auto buffer = UserOrKernelBuffer::for_kernel_buffer(const_cast<u8*>(bytes.data()));
            auto count = TRY(description.write(static_cast<u64>(block) * block_size, buffer, block_size));
            if (count != block_size)
                return EIO;
            return {};
        }
        ErrorOr<void> flush() { return description.sync(); }
    } replay_device { file_description(), block_size };
    TRY(prepared.execute(replay_device));

    // Reload the durable post-replay primary superblock before the normal
    // cache/descriptors/root inode are initialized by the caller.
    TRY(read_block(superblock_block, superblock_image.bytes()));
    __builtin_memcpy(&m_super_block, superblock_image.data() + superblock_offset, sizeof(m_super_block));
    m_super_block.s_feature_incompat &= ~EXT3_FEATURE_INCOMPAT_RECOVER;
    __builtin_memcpy(superblock_image.data() + superblock_offset, &m_super_block, sizeof(m_super_block));
    TRY(replay_device.write_block(superblock_block, superblock_image.bytes()));
    return replay_device.flush();
}

}
