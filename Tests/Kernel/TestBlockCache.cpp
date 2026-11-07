/*
 * Copyright (c) 2026, the SerenityOS developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/ByteBuffer.h>
#include <AK/ScopeGuard.h>
#include <LibTest/TestCase.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/statvfs.h>
#include <unistd.h>

struct TestFile {
    TestFile()
    {
        fd = mkstemp(path);
        VERIFY(fd >= 0);
    }

    ~TestFile()
    {
        close(fd);
        unlink(path);
    }

    size_t block_size() const
    {
        struct statvfs stats;
        VERIFY(fstatvfs(fd, &stats) == 0);
        return stats.f_bsize;
    }

    char path[32] = "/tmp/block-cache-XXXXXX";
    int fd { -1 };
};

static void write_all(int fd, ReadonlyBytes bytes)
{
    size_t done = 0;
    size_t failures = 0;
    while (done < bytes.size()) {
        auto written = pwrite(fd, bytes.data() + done, bytes.size() - done, done);
        if (written < 0) {
            VERIFY(errno == EIO);
            VERIFY(++failures <= 3);
            continue;
        }
        VERIFY(written > 0);
        done += written;
    }
}

static void read_all(int fd, Bytes bytes)
{
    size_t done = 0;
    size_t failures = 0;
    while (done < bytes.size()) {
        auto nread = pread(fd, bytes.data() + done, bytes.size() - done, done);
        if (nread < 0) {
            VERIFY(errno == EIO);
            VERIFY(++failures <= 3);
            continue;
        }
        VERIFY(nread > 0);
        done += nread;
    }
}

static void sync_with_retry(int fd)
{
    // A failed flush must report its error while preserving dirty data so that
    // a later fsync can retry the writeback.
    size_t failures = 0;
    while (fsync(fd) < 0) {
        auto error = errno;
        VERIFY(error == EIO || error == ENOSPC);
        VERIFY(++failures <= 4);
        dbgln("fsync reported writeback error: {}", error);
    }
    VERIFY(fsync(fd) == 0);
}

TEST_CASE(cached_writeback_retries)
{
    TestFile file;
    auto expected = MUST(ByteBuffer::create_uninitialized(4 * file.block_size()));
    memset(expected.data(), 0x5a, expected.size());
    write_all(file.fd, expected.bytes());
    sync_with_retry(file.fd);

    int direct_fd = open(file.path, O_RDONLY | O_DIRECT);
    VERIFY(direct_fd >= 0);
    ScopeGuard close_direct = [&] { close(direct_fd); };
    auto actual = MUST(ByteBuffer::create_uninitialized(expected.size()));
    read_all(direct_fd, actual.bytes());
    EXPECT_EQ(memcmp(actual.data(), expected.data(), actual.size()), 0);
}

TEST_CASE(direct_read_flushes_dirty_data)
{
    TestFile file;
    auto expected = MUST(ByteBuffer::create_uninitialized(file.block_size()));
    memset(expected.data(), 0x39, expected.size());
    write_all(file.fd, expected.bytes());
    int direct_fd = open(file.path, O_RDONLY | O_DIRECT);
    VERIFY(direct_fd >= 0);
    ScopeGuard close_direct = [&] { close(direct_fd); };
    auto actual = MUST(ByteBuffer::create_uninitialized(expected.size()));
    read_all(direct_fd, actual.bytes());
    EXPECT_EQ(memcmp(actual.data(), expected.data(), actual.size()), 0);
}

TEST_CASE(direct_write_invalidates_cached_data)
{
    TestFile file;
    auto expected = MUST(ByteBuffer::create_uninitialized(file.block_size()));
    memset(expected.data(), 0x41, expected.size());
    write_all(file.fd, expected.bytes());

    int direct_fd = open(file.path, O_RDWR | O_DIRECT);
    VERIFY(direct_fd >= 0);
    ScopeGuard close_direct = [&] { close(direct_fd); };
    u8 replacement = 0x42;
    size_t failures = 0;
    while (pwrite(direct_fd, &replacement, 1, 17) != 1) {
        VERIFY(errno == EIO);
        VERIFY(++failures <= 3);
    }
    expected[17] = replacement;

    auto actual = MUST(ByteBuffer::create_uninitialized(expected.size()));
    read_all(file.fd, actual.bytes());
    EXPECT_EQ(memcmp(actual.data(), expected.data(), actual.size()), 0);
    sync_with_retry(file.fd);
    read_all(direct_fd, actual.bytes());
    EXPECT_EQ(memcmp(actual.data(), expected.data(), actual.size()), 0);
}

TEST_CASE(cache_pressure_writeback)
{
    TestFile file;
    // One large write holds the inode lock while filling the block cache. This
    // exercises eviction/writeback instead of relying solely on periodic sync.
    constexpr size_t size = 48 * MiB;
    auto data = MUST(ByteBuffer::create_uninitialized(size));
    memset(data.data(), 0x6b, data.size());
    write_all(file.fd, data.bytes());
    sync_with_retry(file.fd);

    int direct_fd = open(file.path, O_RDONLY | O_DIRECT);
    VERIFY(direct_fd >= 0);
    ScopeGuard close_direct = [&] { close(direct_fd); };
    memset(data.data(), 0, data.size());
    read_all(direct_fd, data.bytes());
    auto expected_block = MUST(ByteBuffer::create_uninitialized(file.block_size()));
    memset(expected_block.data(), 0x6b, expected_block.size());
    for (size_t offset = 0; offset < size; offset += expected_block.size())
        EXPECT_EQ(memcmp(data.data() + offset, expected_block.data(), expected_block.size()), 0);
}
