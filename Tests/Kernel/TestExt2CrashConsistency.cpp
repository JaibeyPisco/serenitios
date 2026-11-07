/*
 * Copyright (c) 2026, the SerenityOS developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/ByteString.h>
#include <AK/ScopeGuard.h>
#include <LibTest/TestCase.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

TEST_CASE(ext2_crash_workload)
{
    auto* device = getenv("SERENITY_EXT2_CRASH_DEVICE");
    auto* operation = getenv("SERENITY_EXT2_CRASH_OPERATION");
    if (!device || !operation) {
        dbgln("Skipping crash workload: use Meta/test-ext2-crash-consistency.py");
        return;
    }
    VERIFY(strcmp(operation, "create") == 0 || strcmp(operation, "unlink") == 0 || strcmp(operation, "rename") == 0 || strcmp(operation, "truncate") == 0 || strcmp(operation, "verify-recovery") == 0);
    int device_fd = open(device, O_RDWR);
    VERIFY(device_fd >= 0);
    ScopeGuard close_device = [&] { close(device_fd); };
    constexpr char expected_label[16] = "SerenityCrash";
    char label[16];
    VERIFY(pread(device_fd, label, sizeof(label), 1024 + 120) == sizeof(label));
    VERIFY(memcmp(label, expected_label, sizeof(label)) == 0);
    struct stat device_stats;
    VERIFY(fstat(device_fd, &device_stats) == 0);
    VERIFY(S_ISBLK(device_stats.st_mode));
    constexpr off_t fixture_size = 64 * 1024 * 1024;
    u32 block_count;
    u32 block_size_shift;
    VERIFY(pread(device_fd, &block_count, sizeof(block_count), 1024 + 4) == sizeof(block_count));
    VERIFY(pread(device_fd, &block_size_shift, sizeof(block_size_shift), 1024 + 24) == sizeof(block_size_shift));
    VERIFY(block_count == fixture_size / 4096 && block_size_shift == 2);

    char mountpoint[] = "/tmp/ext2-crash-XXXXXX";
    VERIFY(mkdtemp(mountpoint));
    VERIFY(mount(device_fd, mountpoint, "Ext2FS", 0) == 0);
    int directory_fd = open(mountpoint, O_RDONLY | O_DIRECTORY);
    VERIFY(directory_fd >= 0);
    auto sentinel = ByteString::formatted("{}/sentinel", mountpoint);
    auto victim = ByteString::formatted("{}/victim", mountpoint);
    auto destination = ByteString::formatted("{}/destination", mountpoint);
    if (strcmp(operation, "verify-recovery") == 0) {
        // Read the acknowledged baseline before creating or rewriting anything.
        int fd = open(sentinel.characters(), O_RDONLY);
        VERIFY(fd >= 0);
        char contents[4096];
        VERIFY(read(fd, contents, sizeof(contents)) == sizeof(contents));
        for (auto byte : contents)
            VERIFY(byte == 0x7b);
        char extra;
        VERIFY(read(fd, &extra, 1) == 0);
        VERIFY(close(fd) == 0);
        VERIFY(close(directory_fd) == 0);
        VERIFY(umount(mountpoint) == 0);
        VERIFY(rmdir(mountpoint) == 0);
        dbgln("EXT2_RECOVERY_VERIFIED");
        return;
    }
    auto write_file = [](char const* path, size_t blocks, u8 value) {
        int fd = open(path, O_CREAT | O_TRUNC | O_RDWR, 0600);
        VERIFY(fd >= 0);
        char data[4096];
        memset(data, value, sizeof(data));
        for (size_t i = 0; i < blocks; ++i)
            VERIFY(write(fd, data, sizeof(data)) == sizeof(data));
        VERIFY(fsync(fd) == 0);
        VERIFY(close(fd) == 0);
    };
    write_file(sentinel.characters(), 1, 0x7b);
    if (strcmp(operation, "create") != 0)
        write_file(victim.characters(), 32, 0x41);
    if (strcmp(operation, "rename") == 0)
        write_file(destination.characters(), 32, 0x42);
    VERIFY(fsync(directory_fd) == 0);

    // The host arms its breakpoints at the first sync syscall. Reading the
    // known final sector then identifies the disposable AHCI port without
    // relying on kernel structure layouts or full debug information.
    sync();
    char probe[512];
    VERIFY(pread(device_fd, probe, sizeof(probe), fixture_size - sizeof(probe)) == sizeof(probe));

    if (strcmp(operation, "create") == 0) {
        write_file(victim.characters(), 32, 0x5a);
    } else if (strcmp(operation, "unlink") == 0) {
        VERIFY(unlink(victim.characters()) == 0);
    } else if (strcmp(operation, "rename") == 0) {
        VERIFY(rename(victim.characters(), destination.characters()) == 0);
    } else {
        int fd = open(victim.characters(), O_RDWR);
        VERIFY(fd >= 0);
        VERIFY(ftruncate(fd, 4096) == 0);
        VERIFY(fsync(fd) == 0);
        VERIFY(close(fd) == 0);
    }
    VERIFY(fsync(directory_fd) == 0);
    // The host counts completed writes through this second sync's return.
    sync();
    VERIFY(close(directory_fd) == 0);
    VERIFY(umount(mountpoint) == 0);
    VERIFY(rmdir(mountpoint) == 0);
}
