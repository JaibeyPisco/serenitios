/*
 * Copyright (c) 2020, Andreas Kling <kling@serenityos.org>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <Kernel/FileSystem/Inode.h>
#include <Kernel/Interrupts/InterruptDisabler.h>
#include <Kernel/Locking/Spinlock.h>
#include <Kernel/Memory/SharedInodeVMObject.h>

namespace Kernel::Memory {

ErrorOr<NonnullLockRefPtr<SharedInodeVMObject>> SharedInodeVMObject::try_create_with_inode(Inode& inode)
{
    if (inode.size() == 0)
        return EINVAL;
    return try_create_with_inode_and_range(inode, 0, inode.size());
}

ErrorOr<NonnullLockRefPtr<SharedInodeVMObject>> SharedInodeVMObject::try_create_with_inode_and_range(Inode& inode, u64 offset, size_t range_size)
{
    // Note: To ensure further allocation of a Region with this VMObject will not complain
    // on "smaller" VMObject than the requested Region, we simply take the max size between both values.
    auto size = max(inode.size(), (offset + range_size));
    VERIFY(size > 0);
    if (auto shared_vmobject = inode.shared_vmobject())
        return shared_vmobject.release_nonnull();
    auto new_physical_pages = TRY(VMObject::try_create_physical_pages(size));
    auto dirty_pages = TRY(Bitmap::create(new_physical_pages.size(), false));
    auto vmobject = TRY(adopt_nonnull_lock_ref_or_enomem(new (nothrow) SharedInodeVMObject(inode, move(new_physical_pages), move(dirty_pages))));
    TRY(vmobject->inode().set_shared_vmobject(*vmobject));
    return vmobject;
}

ErrorOr<NonnullLockRefPtr<VMObject>> SharedInodeVMObject::try_clone()
{
    auto new_physical_pages = TRY(this->try_clone_physical_pages());
    auto dirty_pages = TRY(Bitmap::create(new_physical_pages.size(), false));
    return adopt_nonnull_lock_ref_or_enomem<VMObject>(new (nothrow) SharedInodeVMObject(*this, move(new_physical_pages), move(dirty_pages)));
}

SharedInodeVMObject::SharedInodeVMObject(Inode& inode, FixedArray<RefPtr<PhysicalRAMPage>>&& new_physical_pages, Bitmap dirty_pages)
    : InodeVMObject(inode, move(new_physical_pages), move(dirty_pages))
{
}

SharedInodeVMObject::SharedInodeVMObject(SharedInodeVMObject const& other, FixedArray<RefPtr<PhysicalRAMPage>>&& new_physical_pages, Bitmap dirty_pages)
    : InodeVMObject(other, move(new_physical_pages), move(dirty_pages))
{
}

ErrorOr<void> SharedInodeVMObject::sync(off_t offset_in_pages, size_t pages)
{
    return TRY(sync_impl(offset_in_pages, pages, true));
}

ErrorOr<void> SharedInodeVMObject::sync_before_destroying()
{
    return TRY(sync_impl(0, page_count(), false));
}

ErrorOr<void> SharedInodeVMObject::sync_impl(off_t offset_in_pages, size_t pages, bool should_remap)
{
    MutexLocker sync_locker(m_sync_lock);
    SpinlockLocker locker(m_lock);

    size_t highest_page_to_flush = min(page_count(), offset_in_pages + pages);

    struct PageToFlush {
        size_t index;
        NonnullRefPtr<PhysicalRAMPage> page;
    };
    AK::Vector<PageToFlush> pages_to_flush;
    TRY(pages_to_flush.try_ensure_capacity(highest_page_to_flush - offset_in_pages));

    for (size_t page_index = offset_in_pages; page_index < highest_page_to_flush; ++page_index) {
        auto& physical_page = m_physical_pages[page_index];
        if (physical_page && is_page_dirty(page_index))
            TRY(pages_to_flush.try_append({ page_index, *physical_page }));
    }

    if (pages_to_flush.size() == 0)
        return {};

    // Mark pages as clean and remap regions before writing the pages to disk.
    // This makes the pages read-only. Once the spinlock is released, later
    // writes fault and mark them dirty again for a subsequent flush.
    if (should_remap) {
        for (auto it = pages_to_flush.begin(); it != pages_to_flush.end(); ++it)
            set_page_dirty(it->index, false);
        remap_regions_locked();
    }

    // Pin the pages, but release the VMObject spinlock before blocking I/O.
    // The mutex serializes flushes without tying them to a particular CPU.
    locker.unlock();
    for (auto it = pages_to_flush.begin(); it != pages_to_flush.end(); ++it) {
        u8 page_buffer[PAGE_SIZE];

        {
            // quickmap is CPU-local; only the short memory copy must be
            // non-preemptible. Re-enable interrupts before filesystem I/O.
            InterruptDisabler disabler;
            MM.copy_physical_page(*it->page, page_buffer);
        }
        auto result = m_inode->write_bytes(it->index * PAGE_SIZE, PAGE_SIZE, UserOrKernelBuffer::for_kernel_buffer(page_buffer), nullptr);
        if (result.is_error() || result.value() != PAGE_SIZE) {
            SpinlockLocker dirty_locker(m_lock);
            for (auto pending = it; pending != pages_to_flush.end(); ++pending) {
                if (pending->index < page_count() && m_physical_pages[pending->index].ptr() == pending->page.ptr())
                    set_page_dirty(pending->index, true);
            }
            if (result.is_error())
                return result.release_error();
            return EIO;
        }
    }

    return {};
}

}
