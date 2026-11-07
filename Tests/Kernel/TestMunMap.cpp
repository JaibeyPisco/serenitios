/*
 * Copyright (c) 2021, Brian Gianforcaro <bgianf@serenityos.org>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/Atomic.h>
#include <AK/ScopeGuard.h>
#include <LibTest/TestCase.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

static void expect_write_fault(u8* address)
{
    auto pid = fork();
    VERIFY(pid >= 0);
    if (pid == 0) {
        *static_cast<volatile u8*>(address) = 99;
        _exit(1);
    }
    int status;
    VERIFY(waitpid(pid, &status, 0) == pid);
    EXPECT(WIFSIGNALED(status));
    EXPECT_EQ(WTERMSIG(status), SIGSEGV);
}

TEST_CASE(munmap_zero_page)
{
    // munmap of the unmapped zero page should always "succeed".
    auto res = munmap(0x0, 0xF);
    EXPECT_EQ(res, 0);
}

TEST_CASE(partial_munmap_preserves_neighbors_on_failure)
{
    constexpr size_t part_size = 4 * MiB;
    constexpr size_t size = 3 * part_size;
    auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, size); };
    mapping[0] = 11;
    mapping[part_size] = 22;
    mapping[2 * part_size] = 33;

    // A debugger can fail the staged Region allocation. The original mapping
    // must remain accessible and writable until a later attempt succeeds.
    size_t failures = 0;
    while (munmap(mapping + part_size, part_size) < 0) {
        VERIFY(errno == ENOMEM);
        VERIFY(++failures <= 3);
        EXPECT_EQ(mapping[0], 11);
        EXPECT_EQ(mapping[part_size], 22);
        EXPECT_EQ(mapping[2 * part_size], 33);
        mapping[0] = 11;
        mapping[part_size] = 22;
        mapping[2 * part_size] = 33;
    }
    mapping[0] = 44;
    mapping[2 * part_size] = 55;
    expect_write_fault(mapping + part_size);
    expect_write_fault(mapping + 2 * part_size - 1);

    VERIFY(mmap(mapping + part_size, part_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0) == mapping + part_size);
    EXPECT_EQ(mapping[part_size], 0);
    EXPECT_EQ(mapping[0], 44);
    EXPECT_EQ(mapping[2 * part_size], 55);
}

TEST_CASE(partial_munmap_prefix_and_suffix)
{
    constexpr size_t size = 3 * PAGE_SIZE;
    for (size_t removed_offset : { 0uz, 2uz * PAGE_SIZE }) {
        auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
        VERIFY(mapping != MAP_FAILED);
        ScopeGuard unmap = [&] { munmap(mapping, size); };
        for (size_t i = 0; i < 3; ++i)
            mapping[i * PAGE_SIZE] = i + 1;
        VERIFY(munmap(mapping + removed_offset, PAGE_SIZE) == 0);
        expect_write_fault(mapping + removed_offset);
        for (size_t i = 0; i < 3; ++i) {
            if (i * PAGE_SIZE == removed_offset)
                continue;
            EXPECT_EQ(mapping[i * PAGE_SIZE], i + 1);
            // Force a remap to verify the retained Region's VMObject offset.
            VERIFY(mprotect(mapping + i * PAGE_SIZE, PAGE_SIZE, PROT_READ) == 0);
            VERIFY(mprotect(mapping + i * PAGE_SIZE, PAGE_SIZE, PROT_READ | PROT_WRITE) == 0);
            EXPECT_EQ(mapping[i * PAGE_SIZE], i + 1);
        }
    }
}

TEST_CASE(partial_munmap_prot_none)
{
    constexpr size_t size = 3 * PAGE_SIZE;
    auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, size); };
    VERIFY(munmap(mapping + PAGE_SIZE, PAGE_SIZE) == 0);
    VERIFY(mprotect(mapping, PAGE_SIZE, PROT_READ | PROT_WRITE) == 0);
    VERIFY(mprotect(mapping + 2 * PAGE_SIZE, PAGE_SIZE, PROT_READ | PROT_WRITE) == 0);
    mapping[0] = 11;
    mapping[2 * PAGE_SIZE] = 33;
    EXPECT_EQ(mprotect(mapping, size, PROT_READ), -1);
    EXPECT_EQ(errno, ENOMEM);
    expect_write_fault(mapping + PAGE_SIZE);
    EXPECT_EQ(mapping[0], 11);
    EXPECT_EQ(mapping[2 * PAGE_SIZE], 33);
}

TEST_CASE(multi_region_munmap_preserves_mappings_on_failure)
{
    constexpr size_t size = 8 * PAGE_SIZE;
    auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, size); };
    for (size_t i = 0; i < 8; ++i) {
        auto* address = mapping + i * PAGE_SIZE;
        VERIFY(mmap(address, PAGE_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0) == address);
        address[0] = i + 1;
    }

    // Collecting six regions grows the inline region list. A failed growth must
    // return before deleting even the first mapping in the requested range.
    size_t failures = 0;
    while (munmap(mapping + PAGE_SIZE, 6 * PAGE_SIZE) < 0) {
        VERIFY(errno == ENOMEM);
        VERIFY(++failures <= 3);
        for (size_t i = 0; i < 8; ++i) {
            EXPECT_EQ(mapping[i * PAGE_SIZE], i + 1);
            mapping[i * PAGE_SIZE] = i + 1;
        }
    }
    EXPECT_EQ(mapping[0], 1);
    EXPECT_EQ(mapping[7 * PAGE_SIZE], 8);
    expect_write_fault(mapping + PAGE_SIZE);
    expect_write_fault(mapping + 6 * PAGE_SIZE);
}

TEST_CASE(multi_region_munmap_partial_edges)
{
    constexpr size_t size = 8 * PAGE_SIZE;
    auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, size); };
    VERIFY(mmap(mapping, 4 * PAGE_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0) == mapping);
    VERIFY(mmap(mapping + 4 * PAGE_SIZE, 4 * PAGE_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0) == mapping + 4 * PAGE_SIZE);
    mapping[0] = 11;
    mapping[7 * PAGE_SIZE] = 88;
    VERIFY(munmap(mapping + PAGE_SIZE, 6 * PAGE_SIZE) == 0);
    EXPECT_EQ(mapping[0], 11);
    EXPECT_EQ(mapping[7 * PAGE_SIZE], 88);
    expect_write_fault(mapping + PAGE_SIZE);
    expect_write_fault(mapping + 6 * PAGE_SIZE);
    VERIFY(mprotect(mapping + 7 * PAGE_SIZE, PAGE_SIZE, PROT_READ) == 0);
    EXPECT_EQ(mapping[7 * PAGE_SIZE], 88);
}

TEST_CASE(multi_region_munmap_collects_all_intersections)
{
    constexpr size_t size = 4 * PAGE_SIZE;
    auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, size); };
    VERIFY(mmap(mapping, 3 * PAGE_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0) == mapping);
    VERIFY(mmap(mapping + 3 * PAGE_SIZE, PAGE_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0) == mapping + 3 * PAGE_SIZE);
    mapping[0] = 11;
    mapping[PAGE_SIZE] = 22;
    mapping[2 * PAGE_SIZE] = 33;
    mapping[3 * PAGE_SIZE] = 44;

    // The first region leaves two pages outside this two-page interval. That
    // excluded length must not make collection stop before the second region.
    VERIFY(munmap(mapping + 2 * PAGE_SIZE, 2 * PAGE_SIZE) == 0);
    expect_write_fault(mapping + 2 * PAGE_SIZE);
    expect_write_fault(mapping + 3 * PAGE_SIZE);
    EXPECT_EQ(mapping[0], 11);
    EXPECT_EQ(mapping[PAGE_SIZE], 22);
    VERIFY(mmap(mapping + 2 * PAGE_SIZE, 2 * PAGE_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0) == mapping + 2 * PAGE_SIZE);
    // An interval starting in an existing hole must still visit later mappings.
    VERIFY(munmap(mapping, 2 * PAGE_SIZE) == 0);
    VERIFY(munmap(mapping, size) == 0);
    expect_write_fault(mapping + 2 * PAGE_SIZE);
    expect_write_fault(mapping + 3 * PAGE_SIZE);
}

TEST_CASE(partial_munmap_preserves_cow)
{
    constexpr size_t size = 3 * PAGE_SIZE;
    auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, size); };
    mapping[0] = 11;
    mapping[PAGE_SIZE] = 22;
    mapping[2 * PAGE_SIZE] = 33;
    auto pid = fork();
    VERIFY(pid >= 0);
    if (pid == 0) {
        if (munmap(mapping + PAGE_SIZE, PAGE_SIZE) < 0)
            _exit(1);
        mapping[0] = 44;
        mapping[2 * PAGE_SIZE] = 55;
        _exit(0);
    }
    int status;
    VERIFY(waitpid(pid, &status, 0) == pid);
    EXPECT(WIFEXITED(status));
    EXPECT_EQ(WEXITSTATUS(status), 0);
    EXPECT_EQ(mapping[0], 11);
    EXPECT_EQ(mapping[PAGE_SIZE], 22);
    EXPECT_EQ(mapping[2 * PAGE_SIZE], 33);
}

TEST_CASE(partial_munmap_preserves_shared_file_offsets_and_writeback)
{
    char path[] = "/tmp/munmap-offsets-XXXXXX";
    int fd = mkstemp(path);
    VERIFY(fd >= 0);
    ScopeGuard cleanup = [&] {
        close(fd);
        unlink(path);
    };
    constexpr size_t size = 3 * PAGE_SIZE;
    VERIFY(ftruncate(fd, size) == 0);
    auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, size); };
    for (size_t i = 0; i < 3; ++i)
        mapping[i * PAGE_SIZE] = i + 1;
    VERIFY(munmap(mapping + PAGE_SIZE, PAGE_SIZE) == 0);
    for (size_t i = 0; i < 3; ++i) {
        u8 byte = 0;
        VERIFY(pread(fd, &byte, 1, i * PAGE_SIZE) == 1);
        EXPECT_EQ(byte, i + 1);
    }
    mapping[2 * PAGE_SIZE] = 99;
    VERIFY(msync(mapping + 2 * PAGE_SIZE, PAGE_SIZE, MS_SYNC) == 0);
    u8 byte = 0;
    VERIFY(pread(fd, &byte, 1, 2 * PAGE_SIZE) == 1);
    EXPECT_EQ(byte, 99);
}

TEST_CASE(partial_munmap_preserves_permission_history)
{
    int fd = open("/bin/SystemServer", O_RDONLY);
    VERIFY(fd >= 0);
    ScopeGuard close_file = [&] { close(fd); };
    constexpr size_t size = 3 * PAGE_SIZE;
    for (int initial_prot : { PROT_WRITE, PROT_EXEC }) {
        auto* mapping = static_cast<u8*>(mmap(nullptr, size, initial_prot, MAP_PRIVATE, fd, 0));
        VERIFY(mapping != MAP_FAILED);
        ScopeGuard unmap = [&] { munmap(mapping, size); };
        VERIFY(mprotect(mapping, size, PROT_READ) == 0);
        VERIFY(munmap(mapping + PAGE_SIZE, PAGE_SIZE) == 0);
        int forbidden_prot = initial_prot == PROT_WRITE ? PROT_EXEC : PROT_WRITE;
        for (size_t offset : { 0uz, 2uz * PAGE_SIZE }) {
            EXPECT_EQ(mprotect(mapping + offset, PAGE_SIZE, forbidden_prot), -1);
            EXPECT_EQ(errno, EINVAL);
        }
    }
}

TEST_CASE(partial_munmap_with_concurrent_neighbor_faults)
{
    constexpr size_t part_size = MiB;
    constexpr size_t size = 3 * part_size;
    for (size_t iteration = 0; iteration < 3; ++iteration) {
        auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
        VERIFY(mapping != MAP_FAILED);
        ScopeGuard unmap = [&] { munmap(mapping, size); };
        Atomic<bool> start { false };
        struct Worker {
            Atomic<bool>& start;
            u8* mapping;
        } workers[] { { start, mapping }, { start, mapping + 2 * part_size } };
        pthread_t threads[2];
        auto fault_pages = [](void* context) -> void* {
            auto& worker = *static_cast<Worker*>(context);
            while (!worker.start.load(AK::memory_order_acquire))
                sched_yield();
            for (size_t i = 0; i < part_size / PAGE_SIZE; ++i)
                worker.mapping[i * PAGE_SIZE] = 42;
            return nullptr;
        };
        for (size_t i = 0; i < 2; ++i)
            VERIFY(pthread_create(&threads[i], nullptr, fault_pages, &workers[i]) == 0);
        start.store(true, AK::memory_order_release);
        VERIFY(munmap(mapping + part_size, part_size) == 0);
        for (auto& thread : threads)
            VERIFY(pthread_join(thread, nullptr) == 0);
        for (auto& worker : workers) {
            for (size_t i = 0; i < part_size / PAGE_SIZE; ++i)
                EXPECT_EQ(worker.mapping[i * PAGE_SIZE], 42);
        }
    }
}
