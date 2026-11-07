/*
 * Copyright (c) 2026, the SerenityOS developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/ScopeGuard.h>
#include <LibTest/TestCase.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

TEST_CASE(prot_none_across_page_tables)
{
    // This spans several page tables regardless of the mapping's alignment.
    constexpr size_t size = 8 * MiB;
    auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, size); };

    // Also exercise retries when running with allocation failures injected by a
    // debugger. The mapping must remain inaccessible after each failed attempt.
    int result;
    size_t failures = 0;
    while ((result = mprotect(mapping, size, PROT_READ | PROT_WRITE)) < 0) {
        VERIFY(errno == ENOMEM);
        VERIFY(++failures <= 3);
        auto pid = fork();
        VERIFY(pid >= 0);
        if (pid == 0) {
            *static_cast<volatile u8*>(mapping) = 99;
            _exit(1);
        }
        int status;
        VERIFY(waitpid(pid, &status, 0) == pid);
        EXPECT(WIFSIGNALED(status));
        EXPECT_EQ(WTERMSIG(status), SIGSEGV);
    }
    for (size_t offset = 0; offset < size; offset += PAGE_SIZE) {
        EXPECT_EQ(mapping[offset], 0);
        mapping[offset] = 42;
    }

    VERIFY(mprotect(mapping, size, PROT_NONE) == 0);
    VERIFY(mprotect(mapping, size, PROT_READ) == 0);
    for (size_t offset = 0; offset < size; offset += PAGE_SIZE)
        EXPECT_EQ(mapping[offset], 42);
}

TEST_CASE(mprotect_preserves_cow)
{
    auto* mapping = static_cast<u8*>(mmap(nullptr, PAGE_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, PAGE_SIZE); };
    mapping[0] = 42;

    auto pid = fork();
    VERIFY(pid >= 0);
    if (pid == 0) {
        if (mprotect(mapping, PAGE_SIZE, PROT_NONE) < 0 || mprotect(mapping, PAGE_SIZE, PROT_READ | PROT_WRITE) < 0)
            _exit(1);
        mapping[0] = 99;
        _exit(mapping[0] == 99 ? 0 : 1);
    }

    int status;
    VERIFY(waitpid(pid, &status, 0) == pid);
    EXPECT(WIFEXITED(status));
    EXPECT_EQ(WEXITSTATUS(status), 0);
    EXPECT_EQ(mapping[0], 42);
}

TEST_CASE(mprotect_revokes_write_access)
{
    auto* mapping = static_cast<u8*>(mmap(nullptr, PAGE_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, PAGE_SIZE); };
    mapping[0] = 42;
    VERIFY(mprotect(mapping, PAGE_SIZE, PROT_READ) == 0);

    auto pid = fork();
    VERIFY(pid >= 0);
    if (pid == 0) {
        *static_cast<volatile u8*>(mapping) = 99;
        _exit(1);
    }

    int status;
    VERIFY(waitpid(pid, &status, 0) == pid);
    EXPECT(WIFSIGNALED(status));
    EXPECT_EQ(WTERMSIG(status), SIGSEGV);
    EXPECT_EQ(mapping[0], 42);
}

TEST_CASE(mprotect_multiple_whole_regions)
{
    // Reserve the address range first so MAP_FIXED only replaces our own mappings.
    constexpr size_t size = 8 * MiB;
    auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, size); };
    VERIFY(mmap(mapping, size / 2, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0) == mapping);

    VERIFY(mprotect(mapping, size, PROT_READ | PROT_WRITE) == 0);
    mapping[0] = 11;
    mapping[size / 2] = 22;
    mapping[size - 1] = 33;
    VERIFY(mprotect(mapping + PAGE_SIZE, size - 2 * PAGE_SIZE, PROT_READ) == 0);
    mapping[0] = 11;
    mapping[size - 1] = 33;
    VERIFY(mprotect(mapping, size, PROT_READ) == 0);
    EXPECT_EQ(mapping[0], 11);
    EXPECT_EQ(mapping[size / 2], 22);
    EXPECT_EQ(mapping[size - 1], 33);
}

TEST_CASE(mprotect_shared_file_tracks_writes)
{
    char path[] = "/tmp/mprotect-XXXXXX";
    int fd = mkstemp(path);
    VERIFY(fd >= 0);
    ScopeGuard cleanup = [&] {
        close(fd);
        unlink(path);
    };
    VERIFY(ftruncate(fd, PAGE_SIZE) == 0);

    auto* mapping = static_cast<u8*>(mmap(nullptr, PAGE_SIZE, PROT_NONE, MAP_SHARED, fd, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, PAGE_SIZE); };
    VERIFY(mprotect(mapping, PAGE_SIZE, PROT_READ | PROT_WRITE) == 0);
    mapping[0] = 42;
    VERIFY(mprotect(mapping, PAGE_SIZE, PROT_READ) == 0);
    EXPECT_EQ(mapping[0], 42);
    VERIFY(mprotect(mapping, PAGE_SIZE, PROT_READ | PROT_WRITE) == 0);
    mapping[0] = 99;
    VERIFY(msync(mapping, PAGE_SIZE, MS_SYNC) == 0);
    u8 byte = 0;
    VERIFY(pread(fd, &byte, 1, 0) == 1);
    EXPECT_EQ(byte, 99);
}

TEST_CASE(partial_mprotect_preserves_neighbors_on_failure)
{
    constexpr size_t part_size = 4 * MiB;
    constexpr size_t size = 3 * part_size;
    auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, size); };
    mapping[0] = 11;
    mapping[part_size] = 22;
    mapping[2 * part_size] = 33;

    // A debugger can fail either staged Region allocation. A failed operation
    // must leave the original range, contents, and write permissions intact.
    size_t failures = 0;
    while (mprotect(mapping + part_size, part_size, PROT_READ) < 0) {
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
    EXPECT_EQ(mapping[part_size], 22);
    EXPECT_EQ(mapping[0], 44);
    EXPECT_EQ(mapping[2 * part_size], 55);
}

TEST_CASE(partial_mprotect_from_prot_none)
{
    constexpr size_t part_size = 8 * MiB;
    constexpr size_t size = 3 * part_size;
    auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, size); };

    size_t failures = 0;
    while (mprotect(mapping + part_size, part_size, PROT_READ | PROT_WRITE) < 0) {
        VERIFY(errno == ENOMEM);
        VERIFY(++failures <= 3);
    }
    for (size_t offset = part_size; offset < 2 * part_size; offset += PAGE_SIZE) {
        EXPECT_EQ(mapping[offset], 0);
        mapping[offset] = 42;
    }
    VERIFY(mprotect(mapping + part_size, part_size, PROT_READ) == 0);
    for (size_t offset = part_size; offset < 2 * part_size; offset += PAGE_SIZE)
        EXPECT_EQ(mapping[offset], 42);

    for (size_t offset : { 0uz, 2 * part_size }) {
        auto pid = fork();
        VERIFY(pid >= 0);
        if (pid == 0) {
            *static_cast<volatile u8*>(mapping + offset) = 99;
            _exit(1);
        }
        int status;
        VERIFY(waitpid(pid, &status, 0) == pid);
        EXPECT(WIFSIGNALED(status));
        EXPECT_EQ(WTERMSIG(status), SIGSEGV);
    }
}

TEST_CASE(partial_mprotect_preserves_cow)
{
    constexpr size_t size = 3 * PAGE_SIZE;
    auto* mapping = static_cast<u8*>(mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(mapping != MAP_FAILED);
    ScopeGuard unmap = [&] { munmap(mapping, size); };
    for (size_t i = 0; i < 3; ++i)
        mapping[i * PAGE_SIZE] = i + 1;

    auto pid = fork();
    VERIFY(pid >= 0);
    if (pid == 0) {
        if (mprotect(mapping + PAGE_SIZE, PAGE_SIZE, PROT_READ) < 0 || mprotect(mapping + PAGE_SIZE, PAGE_SIZE, PROT_READ | PROT_WRITE) < 0)
            _exit(1);
        for (size_t i = 0; i < 3; ++i)
            mapping[i * PAGE_SIZE] = 99;
        _exit(0);
    }
    int status;
    VERIFY(waitpid(pid, &status, 0) == pid);
    EXPECT(WIFEXITED(status));
    EXPECT_EQ(WEXITSTATUS(status), 0);
    for (size_t i = 0; i < 3; ++i)
        EXPECT_EQ(mapping[i * PAGE_SIZE], i + 1);

    // Splitting off prefixes reuses the original Region for a suffix, whose
    // offset in the shared VMObject must advance on each split.
    VERIFY(mprotect(mapping, PAGE_SIZE, PROT_READ) == 0);
    VERIFY(mprotect(mapping + PAGE_SIZE, PAGE_SIZE, PROT_READ) == 0);
    VERIFY(mprotect(mapping, size, PROT_READ | PROT_WRITE) == 0);
    for (size_t i = 0; i < 3; ++i)
        EXPECT_EQ(mapping[i * PAGE_SIZE], i + 1);
}

TEST_CASE(partial_mprotect_preserves_permission_history)
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
        VERIFY(mprotect(mapping + PAGE_SIZE, PAGE_SIZE, PROT_NONE) == 0);
        int forbidden_prot = initial_prot == PROT_WRITE ? PROT_EXEC : PROT_WRITE;
        for (size_t i = 0; i < 3; ++i) {
            EXPECT_EQ(mprotect(mapping + i * PAGE_SIZE, PAGE_SIZE, forbidden_prot), -1);
            EXPECT_EQ(errno, EINVAL);
        }
    }
}

TEST_CASE(partial_mprotect_preserves_shared_file_offsets)
{
    char path[] = "/tmp/mprotect-offsets-XXXXXX";
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

    VERIFY(mprotect(mapping + PAGE_SIZE, PAGE_SIZE, PROT_READ) == 0);
    EXPECT_EQ(mapping[PAGE_SIZE], 2);
    VERIFY(mprotect(mapping + PAGE_SIZE, PAGE_SIZE, PROT_READ | PROT_WRITE) == 0);
    mapping[PAGE_SIZE] = 99;
    VERIFY(msync(mapping, size, MS_SYNC) == 0);
    for (size_t i = 0; i < 3; ++i) {
        u8 byte = 0;
        VERIFY(pread(fd, &byte, 1, i * PAGE_SIZE) == 1);
        EXPECT_EQ(byte, i == 1 ? 99 : i + 1);
    }
}

TEST_CASE(msync_across_split_shared_regions)
{
    char path[] = "/tmp/msync-split-XXXXXX";
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

    // mprotect splits one mapping into three adjacent regions. Each msync
    // request must use only its intersection and the corresponding file offset.
    VERIFY(mprotect(mapping + PAGE_SIZE, PAGE_SIZE, PROT_READ) == 0);
    VERIFY(msync(mapping, size, MS_SYNC) == 0);
    for (size_t i = 0; i < 3; ++i) {
        u8 byte = 0;
        VERIFY(pread(fd, &byte, 1, i * PAGE_SIZE) == 1);
        EXPECT_EQ(byte, i + 1);
    }
}
