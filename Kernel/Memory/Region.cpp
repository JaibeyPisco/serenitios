/*
 * Copyright (c) 2018-2022, Andreas Kling <kling@serenityos.org>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/StringView.h>
#include <Kernel/Arch/PageDirectory.h>
#include <Kernel/Arch/PageFault.h>
#include <Kernel/Debug.h>
#include <Kernel/FileSystem/Inode.h>
#include <Kernel/Interrupts/InterruptDisabler.h>
#include <Kernel/Library/Panic.h>
#include <Kernel/Memory/AddressSpace.h>
#include <Kernel/Memory/AnonymousVMObject.h>
#include <Kernel/Memory/MMIOVMObject.h>
#include <Kernel/Memory/MemoryManager.h>
#include <Kernel/Memory/Region.h>
#include <Kernel/Memory/SharedInodeVMObject.h>
#include <Kernel/Tasks/Process.h>
#include <Kernel/Tasks/Scheduler.h>
#include <Kernel/Tasks/Thread.h>

namespace Kernel::Memory {

Region::Region()
    : m_range(VirtualRange({}, 0))
{
}

Region::Region(NonnullLockRefPtr<VMObject> vmobject, size_t offset_in_vmobject, OwnPtr<KString> name, Region::Access access, MemoryType memory_type, bool shared)
    : m_range(VirtualRange({}, 0))
    , m_offset_in_vmobject(offset_in_vmobject)
    , m_vmobject(move(vmobject))
    , m_name(move(name))
    , m_access(access)
    , m_shared(shared)
    , m_memory_type(memory_type)
{
    update_shadow_permission_bits(access);
    m_vmobject->add_region(*this);
}

Region::Region(VirtualRange const& range, NonnullLockRefPtr<VMObject> vmobject, size_t offset_in_vmobject, OwnPtr<KString> name, Region::Access access, MemoryType memory_type, bool shared)
    : m_range(range)
    , m_offset_in_vmobject(offset_in_vmobject)
    , m_vmobject(move(vmobject))
    , m_name(move(name))
    , m_access(access)
    , m_shared(shared)
    , m_memory_type(memory_type)
{
    VERIFY(m_range.base().is_page_aligned());
    VERIFY(m_range.size());
    VERIFY((m_range.size() % PAGE_SIZE) == 0);

    update_shadow_permission_bits(access);
    m_vmobject->add_region(*this);
}

Region::~Region()
{
    // Staged splits have not been published in the region tree or VMObject list.
    if (m_staged)
        return;

    if (is_writable() && vmobject().is_shared_inode())
        (void)static_cast<SharedInodeVMObject&>(vmobject()).sync_before_destroying();

    if (!m_detached_from_vmobject)
        m_vmobject->remove_region(*this);

    if (m_page_directory) {
        SpinlockLocker pd_locker(m_page_directory->get_lock());
        if (!is_readable() && !is_writable() && !is_executable()) {
            // If the region is "PROT_NONE", we didn't map it in the first place.
        } else {
            unmap_with_locks_held(ShouldFlushTLB::Yes, pd_locker);
            VERIFY(!m_page_directory);
        }
    }

    if (is_kernel())
        MM.unregister_kernel_region(*this);

    // Extend the lifetime of the region if there are any page faults in progress for this region's pages.
    // Both the removal of regions from the region trees and the fetching of the regions from the tree
    // during the start of page fault handling are serialized under the address space spinlock. This means
    // that once the region is removed no more page faults on this region can start, so this counter will
    // eventually reach 0. And similarly since we can only reach the region destructor once the region was
    // removed from the appropriate region tree, it is guaranteed that any page faults that are still being
    // handled have already increased this counter, and will be allowed to finish before deallocation.
    while (m_in_progress_page_faults)
        Processor::wait_check();
}

ErrorOr<NonnullOwnPtr<Region>> Region::create_unbacked()
{
    return adopt_nonnull_own_or_enomem(new (nothrow) Region);
}

void Region::detach_from_vmobject_for_unmap()
{
    VERIFY(!m_detached_from_vmobject);
    VERIFY(!m_in_progress_page_faults);
    m_vmobject->remove_region(*this);
    m_detached_from_vmobject = true;
}

ErrorOr<NonnullOwnPtr<Region>> Region::create_unplaced(NonnullLockRefPtr<VMObject> vmobject, size_t offset_in_vmobject, OwnPtr<KString> name, Region::Access access, MemoryType memory_type, bool shared)
{
    return adopt_nonnull_own_or_enomem(new (nothrow) Region(move(vmobject), offset_in_vmobject, move(name), access, memory_type, shared));
}

ErrorOr<NonnullOwnPtr<Region>> Region::try_clone()
{
    VERIFY(Process::has_current());

    if (m_shared) {
        VERIFY(!m_stack);
        if (vmobject().is_inode())
            VERIFY(vmobject().is_shared_inode());

        // Create a new region backed by the same VMObject.

        OwnPtr<KString> region_name;
        if (m_name)
            region_name = TRY(m_name->try_clone());

        auto region = TRY(Region::try_create_user_accessible(
            m_range, vmobject(), m_offset_in_vmobject, move(region_name), access(), m_memory_type, m_shared));
        region->set_mmap(m_mmap, m_mmapped_from_readable, m_mmapped_from_writable);
        region->set_shared(m_shared);
        region->set_syscall_region(is_syscall_region());
        return region;
    }

    if (vmobject().is_inode())
        VERIFY(vmobject().is_private_inode());

    auto vmobject_clone = TRY(vmobject().try_clone());

    // Set up a COW region. The parent (this) region becomes COW as well!
    if (is_writable())
        remap();

    OwnPtr<KString> clone_region_name;
    if (m_name)
        clone_region_name = TRY(m_name->try_clone());

    auto clone_region = TRY(Region::try_create_user_accessible(
        m_range, move(vmobject_clone), m_offset_in_vmobject, move(clone_region_name), access(), m_memory_type, m_shared));

    if (m_stack) {
        VERIFY(vmobject().is_anonymous());
        clone_region->set_stack(true);
    }
    clone_region->set_syscall_region(is_syscall_region());
    clone_region->set_mmap(m_mmap, m_mmapped_from_readable, m_mmapped_from_writable);
    return clone_region;
}

void Region::set_vmobject(NonnullLockRefPtr<VMObject>&& obj)
{
    if (m_vmobject.ptr() == obj.ptr())
        return;
    m_vmobject->remove_region(*this);
    m_vmobject = move(obj);
    m_vmobject->add_region(*this);
}

size_t Region::cow_pages() const
{
    if (!vmobject().is_anonymous())
        return 0;
    return static_cast<AnonymousVMObject const&>(vmobject()).cow_pages();
}

size_t Region::amount_dirty() const
{
    if (!vmobject().is_inode())
        return amount_resident();
    return static_cast<InodeVMObject const&>(vmobject()).amount_dirty();
}

size_t Region::amount_resident() const
{
    size_t bytes = 0;
    for (size_t i = 0; i < page_count(); ++i) {
        auto page = physical_page(i);
        if (page && !page->is_shared_zero_page() && !page->is_lazy_committed_page())
            bytes += PAGE_SIZE;
    }
    return bytes;
}

size_t Region::amount_shared() const
{
    size_t bytes = 0;
    for (size_t i = 0; i < page_count(); ++i) {
        auto page = physical_page(i);
        if (page && page->ref_count() > 1 && !page->is_shared_zero_page() && !page->is_lazy_committed_page())
            bytes += PAGE_SIZE;
    }
    return bytes;
}

ErrorOr<NonnullOwnPtr<Region>> Region::try_create_user_accessible(VirtualRange const& range, NonnullLockRefPtr<VMObject> vmobject, size_t offset_in_vmobject, OwnPtr<KString> name, Region::Access access, MemoryType memory_type, bool shared)
{
    return adopt_nonnull_own_or_enomem(new (nothrow) Region(range, move(vmobject), offset_in_vmobject, move(name), access, memory_type, shared));
}

bool Region::should_cow(size_t page_index) const
{
    if (!vmobject().is_anonymous())
        return false;
    return static_cast<AnonymousVMObject const&>(vmobject()).should_cow(first_page_index() + page_index, m_shared);
}

bool Region::should_dirty_on_write(size_t page_index, ShouldLockVMObject should_lock_vmobject) const
{
    if (!vmobject().is_inode())
        return false;

    auto is_not_dirty = [&](size_t page_index) -> bool {
        VERIFY(vmobject().m_lock.is_locked());
        return !static_cast<InodeVMObject const&>(vmobject()).is_page_dirty(first_page_index() + page_index);
    };

    if (should_lock_vmobject == ShouldLockVMObject::Yes) {
        SpinlockLocker locker(vmobject().m_lock);
        return is_not_dirty(page_index);
    }

    return is_not_dirty(page_index);
}

bool Region::map_individual_page_impl(size_t page_index, RefPtr<PhysicalRAMPage> page, ShouldLockVMObject should_lock_vmobject, bool readable, bool writeable)
{
    if (!page)
        return map_individual_page_impl(page_index, {}, false, false, should_lock_vmobject);
    return map_individual_page_impl(page_index, page->paddr(), readable, writeable && !page->is_shared_zero_page() && !page->is_lazy_committed_page(), should_lock_vmobject);
}

bool Region::map_individual_page_impl(size_t page_index, PhysicalAddress paddr)
{
    return map_individual_page_impl(page_index, paddr, is_readable(), is_writable(), ShouldLockVMObject::Yes);
}

bool Region::map_individual_page_impl(size_t page_index, PhysicalAddress paddr, bool readable, bool writable, ShouldLockVMObject should_lock_vmobject)
{
    VERIFY(m_page_directory->get_lock().is_locked_by_current_processor());

    auto page_vaddr = vaddr_from_page_index(page_index);

    bool user_allowed = page_vaddr.get() >= USER_RANGE_BASE && is_user_address(page_vaddr);
    if (is_mmap() && !user_allowed) {
        PANIC("About to map mmap'ed page at a kernel address");
    }

    auto* pte = MM.ensure_pte(*m_page_directory, page_vaddr);
    if (!pte)
        return false;

    if (!readable && !writable) {
        pte->clear();
        return true;
    }

    bool is_writable = writable && !(should_cow(page_index) || should_dirty_on_write(page_index, should_lock_vmobject));

    pte->set_memory_type(m_memory_type);
    pte->set_physical_page_base(paddr.get());
    pte->set_present(true);
    pte->set_writable(is_writable);
    if (Processor::current().has_nx())
        pte->set_execute_disabled(!is_executable());
    pte->set_user_allowed(user_allowed);

    return true;
}

bool Region::map_individual_page_impl(size_t page_index, ShouldLockVMObject should_lock_vmobject, bool readable, bool writeable)
{
    RefPtr<PhysicalRAMPage> page = nullptr;
    if (should_lock_vmobject == ShouldLockVMObject::Yes)
        page = physical_page(page_index);
    else
        page = physical_page_locked(page_index);
    return map_individual_page_impl(page_index, page, should_lock_vmobject, readable, writeable);
}

bool Region::remap_vmobject_page(size_t page_index, NonnullRefPtr<PhysicalRAMPage> physical_page, ShouldLockVMObject should_lock_vmobject)
{
    // Regions join the VMObject list during construction, before map() assigns
    // their page directory. A fault in another process sharing this VMObject
    // can visit such a region while fork() is still preparing its mappings.
    // There are no PTEs to update yet; map() will use the current physical page.
    auto page_directory = m_page_directory;
    if (!page_directory)
        return true;

    SpinlockLocker page_lock(page_directory->get_lock());
    // unmap() may have cleared the directory while we waited for its lock.
    // Keep the directory alive while locking, and only update a mapping that
    // still belongs to it. A later map() will populate any replacement mapping.
    if (m_page_directory != page_directory)
        return true;

    // NOTE: `page_index` is a VMObject page index, so first we convert it to a Region page index.
    if (!translate_vmobject_page(page_index))
        return false;

    bool success = map_individual_page_impl(page_index, physical_page, should_lock_vmobject, is_readable(), is_writable());
    MemoryManager::flush_tlb(m_page_directory, vaddr_from_page_index(page_index));
    return success;
}

void Region::unmap(ShouldFlushTLB should_flush_tlb)
{
    if (!m_page_directory)
        return;
    SpinlockLocker pd_locker(m_page_directory->get_lock());
    unmap_with_locks_held(should_flush_tlb, pd_locker);
}

void Region::unmap_with_locks_held(ShouldFlushTLB should_flush_tlb, SpinlockLocker<RecursiveSpinlock<LockRank::None>>&)
{
    if (!m_page_directory)
        return;
    size_t count = page_count();
    for (size_t i = 0; i < count; ++i) {
        auto vaddr = vaddr_from_page_index(i);
        MM.release_pte(*m_page_directory, vaddr, i == count - 1 ? MemoryManager::IsLastPTERelease::Yes : MemoryManager::IsLastPTERelease::No);
    }
    if (should_flush_tlb == ShouldFlushTLB::Yes)
        MemoryManager::flush_tlb(m_page_directory, vaddr(), page_count());
    m_page_directory = nullptr;
}

void Region::set_page_directory(PageDirectory& page_directory)
{
    VERIFY(!m_page_directory || m_page_directory == &page_directory);
    m_page_directory = page_directory;
}

ErrorOr<void> Region::map_impl(PageDirectory& page_directory, ShouldLockVMObject should_lock_vmobject, ShouldFlushTLB should_flush_tlb, bool readable, bool writeable)
{
    SpinlockLocker page_lock(page_directory.get_lock());

    // FIXME: Find a better place for this sanity check(?)
    if (is_user() && !is_shared()) {
        VERIFY(!vmobject().is_shared_inode());
    }

    set_page_directory(page_directory);
    size_t page_index = 0;
    while (page_index < page_count()) {
        if (!map_individual_page_impl(page_index, should_lock_vmobject, readable, writeable))
            break;
        ++page_index;
    }
    if (page_index > 0) {
        if (should_flush_tlb == ShouldFlushTLB::Yes)
            MemoryManager::flush_tlb(m_page_directory, vaddr(), page_index);
        if (page_index == page_count())
            return {};
    }
    return ENOMEM;
}

ErrorOr<void> Region::map(PageDirectory& page_directory, ShouldFlushTLB should_flush_tlb)
{
    // Flushing the data cache requires this region to be in the current address space,
    // or in the always mapped kernel address space.
    VERIFY(&page_directory == Memory::PageDirectory::find_current() || &page_directory == &MM.kernel_page_directory());

    // NOTE: The !Processor::current().has_self_snooping() check assumes that either all or none of
    //       the processors in an SMP system support self-snooping, since a context switch can happen
    //       after that check.
    if (m_memory_type != MemoryType::Normal && !Processor::current().has_self_snooping()) {
        // The physical pages used by this region could have previously been used by a cacheable region,
        // so some cache lines might still contain data for this region.
        // Because non-cacheable accesses bypass the data cache, those lines could later be written back
        // after the underlying physical memory has changed.
        // Since we don't track whether physical pages were previously mapped as cacheable,
        // always flush the data cache to remove any stale entries for this region.
        // If the processor supports self-snooping, the processor will snoop its own cache and handle
        // such memory type conflicts transparently.

        if (!is_writable()) {
            // Temporarily map it as writable, so we can flush the data cache.
            // In this case, we always need to flush the TLB to change the permissions,
            // both when making the mapping writable and when reverting to the original permissions.

            TRY(map_impl(page_directory, ShouldLockVMObject::Yes, ShouldFlushTLB::Yes, true, true));
            Processor::flush_data_cache(vaddr(), PAGE_SIZE * page_count());

            TRY(map_impl(page_directory, ShouldLockVMObject::Yes, ShouldFlushTLB::Yes, is_readable(), is_writable()));
        } else {
            TRY(map_impl(page_directory, ShouldLockVMObject::Yes, should_flush_tlb, is_readable(), is_writable()));
            Processor::flush_data_cache(vaddr(), PAGE_SIZE * page_count());
        }
        return {};
    }

    return map_impl(page_directory, ShouldLockVMObject::Yes, should_flush_tlb, is_readable(), is_writable());
}

ErrorOr<void> Region::map(PageDirectory& page_directory, PhysicalAddress paddr, ShouldFlushTLB should_flush_tlb)
{
    SpinlockLocker page_lock(page_directory.get_lock());
    set_page_directory(page_directory);
    size_t page_index = 0;
    while (page_index < page_count()) {
        if (!map_individual_page_impl(page_index, paddr))
            break;
        ++page_index;
        paddr = paddr.offset(PAGE_SIZE);
    }
    if (page_index > 0) {
        if (should_flush_tlb == ShouldFlushTLB::Yes)
            MemoryManager::flush_tlb(m_page_directory, vaddr(), page_index);
        if (page_index == page_count())
            return {};
    }
    return ENOMEM;
}

void Region::remap_impl(ShouldLockVMObject should_lock_vmobject)
{
    VERIFY(m_page_directory);
    ErrorOr<void> result;
    if (m_vmobject->is_mmio())
        result = map(*m_page_directory, static_cast<MMIOVMObject const&>(*m_vmobject).base_address());
    else
        result = map_impl(*m_page_directory, should_lock_vmobject, ShouldFlushTLB::Yes, is_readable(), is_writable());
    if (result.is_error())
        TODO();
}

void Region::remap_with_locked_vmobject()
{
    remap_impl(ShouldLockVMObject::No);
}

void Region::remap()
{
    remap_impl(ShouldLockVMObject::Yes);
}

ErrorOr<void> Region::try_set_access(Access access)
{
    VERIFY(m_page_directory);

    // The caller must hold the address space lock so this region cannot be unmapped.
    // Allocate bookkeeping before holding the page directory lock, since growing a
    // Vector can allocate kernel memory and re-enter memory management.
    Vector<NonnullRefPtr<PhysicalRAMPage>, 4> page_tables;
    size_t missing_tables;
    {
        SpinlockLocker page_lock(m_page_directory->get_lock());
        missing_tables = MM.missing_page_tables(*m_page_directory, range());
    }
    TRY(page_tables.try_ensure_capacity(missing_tables));

    SpinlockLocker page_lock(m_page_directory->get_lock());
    TRY(MM.prepare_page_tables(*m_page_directory, range(), page_tables));

    // From here on every PTE has a backing page table, so remapping cannot fail
    // to allocate one. Keep the page directory locked through the TLB flush.
    m_access = access;
    update_shadow_permission_bits(access);
    remap_impl(ShouldLockVMObject::Yes);
    return {};
}

ErrorOr<NonnullOwnPtr<Region>> Region::try_create_staged_split(VirtualRange const& subrange) const
{
    VERIFY(range().contains(subrange));
    OwnPtr<KString> name;
    if (m_name)
        name = TRY(m_name->try_clone());

    auto region = TRY(create_unbacked());
    region->m_staged = true;
    region->m_range = subrange;
    region->m_offset_in_vmobject = m_offset_in_vmobject + subrange.base().get() - vaddr().get();
    region->m_vmobject = m_vmobject;
    region->m_name = move(name);
    region->m_access = m_access;
    region->m_shared = m_shared;
    region->m_memory_type = m_memory_type;
    region->set_mmap(m_mmap, m_mmapped_from_readable, m_mmapped_from_writable);
    region->set_stack(m_stack);
    region->set_syscall_region(m_syscall_region);
    if (has_been_readable())
        region->m_has_been_readable.set();
    if (has_been_writable())
        region->m_has_been_writable.set();
    if (has_been_executable())
        region->m_has_been_executable.set();
    if (is_immutable())
        region->set_immutable();
    if (is_initially_loaded_executable_segment())
        region->set_initially_loaded_executable_segment();
    return region;
}

ErrorOr<void> Region::try_set_access_for_range(AddressSpace& space, VirtualRange const& protected_range, Access access)
{
    // The caller holds the address space lock throughout preparation and commit.
    VERIFY(range().contains(protected_range));
    VERIFY(m_page_directory);
    VERIFY(m_page_directory.ptr() == &space.page_directory());
    VERIFY(is_mmap());
    if (protected_range == range())
        return try_set_access(access);

    auto remaining_ranges = range().carve(protected_range);
    VERIFY(!remaining_ranges.is_empty());
    auto protected_region = TRY(try_create_staged_split(protected_range));
    OwnPtr<Region> other_region;
    if (remaining_ranges.size() == 2)
        other_region = TRY(try_create_staged_split(remaining_ranges[1]));

    Vector<NonnullRefPtr<PhysicalRAMPage>, 4> page_tables;
    size_t missing_tables;
    {
        SpinlockLocker page_lock(m_page_directory->get_lock());
        missing_tables = MM.missing_page_tables(*m_page_directory, protected_range);
    }
    TRY(page_tables.try_ensure_capacity(missing_tables));

    // The syscall preflight waits for faults outside the address-space lock.
    VERIFY(!m_in_progress_page_faults);

    {
        SpinlockLocker page_lock(m_page_directory->get_lock());
        TRY(MM.prepare_page_tables(*m_page_directory, protected_range, page_tables));
    }

    // All fallible work is complete. Keep the existing PTEs and change only the
    // requested subrange. The VMObject lock keeps reclaim and alias remapping
    // from observing the transfer of mapping ownership between regions.
    SpinlockLocker vmobject_lock(vmobject().m_lock);
    SpinlockLocker page_lock(m_page_directory->get_lock());
    auto old_base = vaddr();
    VERIFY(space.region_tree().remove(*this));
    // Removal leaves the node's old color and parent intact. Reset them before
    // reinserting the same Region at its new base address.
    m_tree_node.parent = nullptr;
    m_tree_node.color = AK::BaseRedBlackTree<FlatPtr>::Color::Red;
    m_range = remaining_ranges[0];
    m_offset_in_vmobject += vaddr().get() - old_base.get();
    space.region_tree().regions().insert(vaddr().get(), *this);

    auto publish = [&](Region& region) {
        region.set_page_directory(*m_page_directory);
        region.m_staged = false;
        vmobject().m_regions.append(region);
        // These disjoint ranges partition the old region, so placement cannot
        // fail and the intrusive tree needs no allocation here.
        space.region_tree().regions().insert(region.vaddr().get(), region);
    };
    publish(*protected_region);
    if (other_region) {
        publish(*other_region);
        (void)other_region.leak_ptr();
    }
    protected_region->m_access = access;
    protected_region->update_shadow_permission_bits(access);
    if (vmobject().is_mmio()) {
        auto paddr = static_cast<MMIOVMObject const&>(vmobject()).base_address().offset(protected_region->offset_in_vmobject());
        MUST(protected_region->map(*m_page_directory, paddr));
    } else {
        MUST(protected_region->map_impl(*m_page_directory, ShouldLockVMObject::No, ShouldFlushTLB::Yes, protected_region->is_readable(), protected_region->is_writable()));
    }
    (void)protected_region.leak_ptr();
    return {};
}

void Region::unmap_subrange(AddressSpace& space, VirtualRange const& removed_range, OwnPtr<Region> trailing_region)
{
    // The caller holds the address space lock and has prepared all metadata.
    VERIFY(range().contains(removed_range));
    VERIFY(m_page_directory.ptr() == &space.page_directory());
    VERIFY(is_mmap());
    auto remaining_ranges = range().carve(removed_range);
    VERIFY(!remaining_ranges.is_empty());
    VERIFY((remaining_ranges.size() == 2) == !!trailing_region);
    if (trailing_region) {
        VERIFY(trailing_region->m_staged);
        VERIFY(trailing_region->range() == remaining_ranges[1]);
    }

    // The caller preflights faults and defers shared-file writeback until after
    // releasing the address-space lock. Neither operation may block here.
    VERIFY(!m_in_progress_page_faults);

    {
        SpinlockLocker vmobject_lock(vmobject().m_lock);
        SpinlockLocker page_lock(m_page_directory->get_lock());
        auto old_base = vaddr();
        VERIFY(space.region_tree().remove(*this));
        m_tree_node.parent = nullptr;
        m_tree_node.color = AK::BaseRedBlackTree<FlatPtr>::Color::Red;
        m_range = remaining_ranges[0];
        m_offset_in_vmobject += vaddr().get() - old_base.get();
        space.region_tree().regions().insert(vaddr().get(), *this);
        if (trailing_region) {
            trailing_region->set_page_directory(*m_page_directory);
            trailing_region->m_staged = false;
            vmobject().m_regions.append(*trailing_region);
            space.region_tree().regions().insert(trailing_region->vaddr().get(), *trailing_region);
            (void)trailing_region.leak_ptr();
        }
    }

    // VMObject remapping now visits only the retained ranges. Drop its lock
    // before releasing PTEs, which can return page-table pages to the allocator.
    // Retained PTEs are never cleared or reallocated.
    SpinlockLocker page_lock(m_page_directory->get_lock());
    auto count = removed_range.size() / PAGE_SIZE;
    for (size_t i = 0; i < count; ++i) {
        MM.release_pte(*m_page_directory, removed_range.base().offset(i * PAGE_SIZE), i == count - 1 ? MemoryManager::IsLastPTERelease::Yes : MemoryManager::IsLastPTERelease::No);
    }
    MemoryManager::flush_tlb(m_page_directory, removed_range.base(), count);
}

void Region::clear_to_zero()
{
    VERIFY(vmobject().is_anonymous());
    SpinlockLocker locker(vmobject().m_lock);
    for (auto i = 0u; i < page_count(); ++i) {
        auto& page = physical_page_slot(i);
        VERIFY(page);
        if (page->is_shared_zero_page())
            continue;
        page = MM.shared_zero_page();
    }
}

PageFaultResponse Region::handle_fault(PageFault const& fault)
{
    auto page_index_in_region = page_index_from_address(fault.vaddr());

    if (fault.is_user() && !is_user()) {
        dbgln("User-mode page fault in non-user Region({})[{}] at {}", this, page_index_in_region, fault.vaddr());
        return PageFaultResponse::ShouldCrash;
    }

    if (fault.is_kernel() && is_user() && !fault.was_smap_disabled()) {
        dbgln("Kernel-mode page fault in user Region({})[{}] at {} (with SMAP enabled)", this, page_index_in_region, fault.vaddr());
        return PageFaultResponse::ShouldCrash;
    }

    // This check needs to be before the is_read() check, since both is_instruction_fetch() and is_read() is true for instruction fetch page faults.
    if (fault.is_instruction_fetch() && !is_executable()) {
        dbgln("Instruction fetch page fault in non-executable Region({})[{}] at {}", this, page_index_in_region, fault.vaddr());
        return PageFaultResponse::ShouldCrash;
    }

    if (fault.is_read() && !is_readable()) {
        dbgln("Read page fault in non-readable Region({})[{}] at {}", this, page_index_in_region, fault.vaddr());
        return PageFaultResponse::ShouldCrash;
    }

    if (fault.is_write() && !is_writable()) {
        dbgln("Write page fault in non-writable Region({})[{}] at {}", this, page_index_in_region, fault.vaddr());
        return PageFaultResponse::ShouldCrash;
    }

    if (fault.is_write()) {
        auto phys_page = physical_page(page_index_in_region);
        if (phys_page && (phys_page->is_shared_zero_page() || phys_page->is_lazy_committed_page())) {
            dbgln_if(PAGE_FAULT_DEBUG, "Zero page fault in Region({})[{}] at {}", this, page_index_in_region, fault.vaddr());
            return handle_zero_fault(page_index_in_region, *phys_page);
        }

        if (should_cow(page_index_in_region)) {
            dbgln_if(PAGE_FAULT_DEBUG, "CoW page fault in Region({})[{}] at {}", this, page_index_in_region, fault.vaddr());
            return handle_cow_fault(page_index_in_region);
        }

        if (vmobject().is_inode()) {
            dbgln_if(PAGE_FAULT_DEBUG, "Inode write page fault in Region({})[{}] at {}", this, page_index_in_region, fault.vaddr());
            return handle_inode_write_fault(page_index_in_region);
        }
    }

    if (fault.is_read() && vmobject().is_inode()) {
        dbgln_if(PAGE_FAULT_DEBUG, "Inode read page fault in Region({})[{}] at {}", this, page_index_in_region, fault.vaddr());
        return handle_inode_fault(page_index_in_region);
    }

    // None of the previous checks could find a reason for this page fault, this can mean two things:
    // a) Another thread already handled a page fault for this page before we could do so.
    //    In this case, we can simply return and execution should continue normally.
    // b) A bug (maybe we forgot a check here, the page tables are desynced from our state, or something else).

    // To be completely sure that this isn't a bug, look in the page table entry associated with the fault
    // address. If an address translation with this PTE would succeed, then this is case a).
    // Otherwise this is case b) and we should panic :^(.

    {
        SpinlockLocker locker(m_page_directory->get_lock());
        auto* page_table_entry = MM.pte(*m_page_directory, fault.vaddr());

        auto error = [&fault, page_table_entry](StringView message) {
            dbgln("BUG! Unhandled page fault at {}: {}", fault.vaddr(), message);

            if (page_table_entry) {
                dbgln("Associated page table entry:");
                dbgln("     - Physical address: {:p}", page_table_entry->physical_page_base());
                dbgln("     - Points to lazy committed page? {}", page_table_entry->physical_page_base() == MM.lazy_committed_page().paddr().get());
                dbgln("     - Points to shared zero page? {}", page_table_entry->physical_page_base() == MM.shared_zero_page().paddr().get());
            }

            PANIC("Unhandled page fault");
        };

        if (page_table_entry == nullptr)
            error("Page table not present"sv);

        if (!page_table_entry->is_present())
            error("Page table entry not present"sv);

        if (!page_table_entry->is_writable() && fault.is_write())
            error("Page table entry not writable"sv);

        if (page_table_entry->is_execute_disabled() && fault.is_instruction_fetch())
            error("Page table entry not executable"sv);

        if (!page_table_entry->is_user_allowed() && fault.is_user())
            error("Page table entry not user-accessible"sv);

        if (page_table_entry->is_user_allowed() && fault.is_kernel() && !fault.was_smap_disabled())
            error("Page table entry user-accessible and SMAP enabled"sv);
    }

    return PageFaultResponse::Continue;
}

PageFaultResponse Region::handle_zero_fault(size_t page_index_in_region, PhysicalRAMPage& page_in_slot_at_time_of_fault)
{
    VERIFY(vmobject().is_anonymous());

    auto& anonymous_vmobject = static_cast<AnonymousVMObject&>(vmobject());

    SpinlockLocker locker(anonymous_vmobject.m_lock);

    auto& page_slot = physical_page_slot(page_index_in_region);
    auto page_index_in_vmobject = translate_to_vmobject_page(page_index_in_region);

    bool already_handled = !page_slot.is_null() && !page_slot->is_shared_zero_page() && !page_slot->is_lazy_committed_page();

    if (already_handled) {
        if (!remap_vmobject_page(page_index_in_vmobject, *page_slot)) {
            dmesgln("MM: handle_zero_fault was unable to allocate a physical page");
            return PageFaultResponse::OutOfMemory;
        }
        return PageFaultResponse::Continue;
    }

    auto current_thread = Thread::current();
    if (current_thread != nullptr)
        current_thread->did_zero_fault();

    RefPtr<PhysicalRAMPage> new_physical_page;

    if (page_in_slot_at_time_of_fault.is_lazy_committed_page()) {
        new_physical_page = anonymous_vmobject.allocate_committed_page({});
        dbgln_if(PAGE_FAULT_DEBUG, "      >> ALLOCATED COMMITTED {}", new_physical_page->paddr());
    } else {
        auto page_or_error = MM.allocate_physical_page(MemoryManager::ShouldZeroFill::Yes);
        if (page_or_error.is_error()) {
            dmesgln("MM: handle_zero_fault was unable to allocate a physical page");
            return PageFaultResponse::OutOfMemory;
        }
        new_physical_page = page_or_error.release_value();
        dbgln_if(PAGE_FAULT_DEBUG, "      >> ALLOCATED {}", new_physical_page->paddr());
    }

    page_slot = new_physical_page;

    if (m_shared) {
        if (!anonymous_vmobject.remap_regions_one_page(page_index_in_vmobject, *new_physical_page)) {
            dmesgln("MM: handle_zero_fault was unable to allocate a physical page");
            return PageFaultResponse::OutOfMemory;
        }
    } else {
        if (!remap_vmobject_page(page_index_in_vmobject, *new_physical_page)) {
            dmesgln("MM: handle_zero_fault was unable to allocate a physical page");
            return PageFaultResponse::OutOfMemory;
        }
    }
    return PageFaultResponse::Continue;
}

PageFaultResponse Region::handle_cow_fault(size_t page_index_in_region)
{
    auto current_thread = Thread::current();
    if (current_thread)
        current_thread->did_cow_fault();

    if (!vmobject().is_anonymous())
        return PageFaultResponse::ShouldCrash;

    VERIFY(!m_shared);

    auto page_index_in_vmobject = translate_to_vmobject_page(page_index_in_region);

    // FIXME: Find a better way to ensure proper synchronization during copy-on-write fault handling that doesn't
    //        require taking a global lock. This lock currently causes us to never handle COW faults concurrently.
    //        Just taking the VMObject's lock isn't enough since each process that has access to the COW pages
    //        refers to it using a different VMObject (and Region) after a fork(). A fork() clones all regions
    //        and VMObjects for the child process, but keeps reusing the same physical pages inside the cloned VMObject.
    static Spinlock<LockRank::None> s_cow_lock;
    SpinlockLocker cow_locker(s_cow_lock);

    SpinlockLocker vmobject_locker(vmobject().m_lock);

    auto response = reinterpret_cast<AnonymousVMObject&>(vmobject()).handle_cow_fault(page_index_in_vmobject, vaddr().offset(page_index_in_region * PAGE_SIZE));
    if (!remap_vmobject_page(page_index_in_vmobject, *vmobject().physical_pages()[page_index_in_vmobject]))
        return PageFaultResponse::OutOfMemory;
    return response;
}

PageFaultResponse Region::handle_inode_fault(size_t page_index_in_region, bool mark_page_dirty)
{
    VERIFY(vmobject().is_inode());
    VERIFY(!g_scheduler_lock.is_locked_by_current_processor());

    auto& inode_vmobject = static_cast<InodeVMObject&>(vmobject());
    auto page_index_in_vmobject = translate_to_vmobject_page(page_index_in_region);
    auto& physical_page_slot = inode_vmobject.physical_pages()[page_index_in_vmobject];

    {
        // NOTE: The VMObject lock is required when manipulating the VMObject's physical page slot.
        SpinlockLocker locker(inode_vmobject.m_lock);

        if (!physical_page_slot.is_null()) {
            dbgln_if(PAGE_FAULT_DEBUG, "handle_inode_fault: Page faulted in by someone else before reading, remapping.");
            if (mark_page_dirty)
                inode_vmobject.set_page_dirty(page_index_in_vmobject, true);
            if (!remap_vmobject_page(page_index_in_vmobject, *physical_page_slot, ShouldLockVMObject::No))
                return PageFaultResponse::OutOfMemory;
            return PageFaultResponse::Continue;
        }
    }

    dbgln_if(PAGE_FAULT_DEBUG, "Inode fault in {} page index: {}", name(), page_index_in_region);

    auto current_thread = Thread::current();
    if (current_thread)
        current_thread->did_inode_fault();

    u8 page_buffer[PAGE_SIZE];
    auto& inode = inode_vmobject.inode();

    auto buffer = UserOrKernelBuffer::for_kernel_buffer(page_buffer);
    auto result = inode.read_bytes(page_index_in_vmobject * PAGE_SIZE, PAGE_SIZE, buffer, nullptr);

    if (result.is_error()) {
        dmesgln("handle_inode_fault: Error ({}) while reading from inode", result.error());
        return PageFaultResponse::ShouldCrash;
    }

    auto nread = result.value();
    // Note: If we received 0, it means we are at the end of file or after it,
    // which means we should return bus error.
    if (nread == 0)
        return PageFaultResponse::BusError;

    // If we read less than a page, zero out the rest to avoid leaking uninitialized data.
    if (nread < PAGE_SIZE)
        memset(page_buffer + nread, 0, PAGE_SIZE - nread);

    // Allocate a new physical page, and copy the read inode contents into it.
    auto new_physical_page_or_error = MM.allocate_physical_page(MemoryManager::ShouldZeroFill::No);
    if (new_physical_page_or_error.is_error()) {
        dmesgln("MM: handle_inode_fault was unable to allocate a physical page");
        return PageFaultResponse::OutOfMemory;
    }
    auto new_physical_page = new_physical_page_or_error.release_value();
    {
        InterruptDisabler disabler;
        u8* dest_ptr = MM.quickmap_page(*new_physical_page);
        memcpy(dest_ptr, page_buffer, PAGE_SIZE);

        if (is_executable()) {
            // Some architectures require an explicit synchronization operation after writing to memory that will be executed.
            // This is required even if no instructions were previously fetched from that (physical) memory location,
            // because some systems have an I-cache that is not coherent with the D-cache,
            // resulting in the I-cache being filled with old values if the contents of the D-cache aren't written back yet.
            Processor::flush_instruction_cache(VirtualAddress { dest_ptr }, PAGE_SIZE);
        }

        MM.unquickmap_page();
    }

    {
        SpinlockLocker locker(inode_vmobject.m_lock);

        // Someone else can assign a new page before we get here, so check if physical_page_slot is still null.
        if (physical_page_slot.is_null()) {
            physical_page_slot = new_physical_page;
            // Something went wrong if a newly loaded page is already marked dirty
            VERIFY(!inode_vmobject.is_page_dirty(page_index_in_vmobject));
        } else {
            dbgln_if(PAGE_FAULT_DEBUG, "handle_inode_fault: Page faulted in by someone else, remapping.");
        }

        if (mark_page_dirty)
            inode_vmobject.set_page_dirty(page_index_in_vmobject, true);
        if (!remap_vmobject_page(page_index_in_vmobject, *physical_page_slot, ShouldLockVMObject::No))
            return PageFaultResponse::OutOfMemory;
        return PageFaultResponse::Continue;
    }
}

PageFaultResponse Region::handle_inode_write_fault(size_t page_index_in_region)
{
    VERIFY(vmobject().is_inode());
    auto& inode_vmobject = static_cast<InodeVMObject&>(vmobject());
    auto page_index_in_vmobject = translate_to_vmobject_page(page_index_in_region);
    auto& physical_page_slot = inode_vmobject.physical_pages()[page_index_in_vmobject];

    {
        SpinlockLocker locker(inode_vmobject.m_lock);

        if (!physical_page_slot.is_null()) {
            dbgln_if(PAGE_FAULT_DEBUG, "handle_inode_write_fault: Marking page dirty and remapping.");
            inode_vmobject.set_page_dirty(page_index_in_vmobject, true);
            if (!remap_vmobject_page(page_index_in_vmobject, *physical_page_slot, ShouldLockVMObject::No))
                return PageFaultResponse::OutOfMemory;
            return PageFaultResponse::Continue;
        }
    }

    // This page was either not mapped yet or it was purged before we held the lock.
    // Handle this like a page-not-present fault, except we mark the page as dirty when we remap it
    dbgln_if(PAGE_FAULT_DEBUG, "handle_inode_write_fault: Page not present, calling handle_inode_fault to load the page and mark it dirty.");
    return handle_inode_fault(page_index_in_region, true);
}

RefPtr<PhysicalRAMPage> Region::physical_page_locked(size_t index) const
{
    VERIFY(vmobject().m_lock.is_locked());
    VERIFY(index < page_count());
    return vmobject().physical_pages()[first_page_index() + index];
}

RefPtr<PhysicalRAMPage> Region::physical_page(size_t index) const
{
    SpinlockLocker vmobject_locker(vmobject().m_lock);
    return physical_page_locked(index);
}

RefPtr<PhysicalRAMPage>& Region::physical_page_slot(size_t index)
{
    VERIFY(vmobject().m_lock.is_locked());
    VERIFY(index < page_count());
    return vmobject().physical_pages()[first_page_index() + index];
}

void Region::update_shadow_permission_bits(Access access)
{
    if (has_flag(access, Read))
        m_has_been_readable.set();
    if (has_flag(access, Write))
        m_has_been_writable.set();
    if (has_flag(access, Execute))
        m_has_been_executable.set();
}

}
