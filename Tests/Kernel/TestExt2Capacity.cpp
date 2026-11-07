/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/ByteString.h>
#include <LibTest/TestCase.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>

TEST_CASE(full_disk_preserves_synced_data_and_reclaims_space)
{
    auto* device = getenv("SERENITY_EXT2_CAPACITY_DEVICE");
    if (!device) {
        dbgln("Skipping capacity workload: use Meta/test-ext2-capacity.py");
        return;
    }
    int device_fd = open(device, O_RDWR);
    VERIFY(device_fd >= 0);
    struct stat metadata;
    VERIFY(fstat(device_fd, &metadata) == 0 && S_ISBLK(metadata.st_mode));
    char label[16];
    constexpr char expected_label[16] = "SerenitySpace";
    VERIFY(pread(device_fd, label, sizeof(label), 1024 + 120) == sizeof(label));
    VERIFY(memcmp(label, expected_label, sizeof(label)) == 0);
    u32 blocks, shift;
    VERIFY(pread(device_fd, &blocks, sizeof(blocks), 1028) == sizeof(blocks));
    VERIFY(pread(device_fd, &shift, sizeof(shift), 1048) == sizeof(shift));
    VERIFY(blocks == 4096 && shift == 2); // A disposable 16 MiB, 4 KiB-block fixture.
    dbgln("EXT2_CAPACITY_EXECUTED");

    char mountpoint[] = "/tmp/ext2-space-XXXXXX";
    VERIFY(mkdtemp(mountpoint));
    VERIFY(mount(device_fd, mountpoint, "Ext2FS", 0) == 0);
    auto sentinel = ByteString::formatted("{}/sentinel", mountpoint);
    auto filler = ByteString::formatted("{}/filler", mountpoint);
    auto reclaimed = ByteString::formatted("{}/reclaimed", mountpoint);
    char data[4096];
    memset(data, 0x7b, sizeof(data));
    int fd = open(sentinel.characters(), O_CREAT | O_RDWR, 0600);
    VERIFY(fd >= 0);
    VERIFY(write(fd, data, sizeof(data)) == sizeof(data));
    VERIFY(fsync(fd) == 0);
    VERIFY(close(fd) == 0);
    int directory_fd = open(mountpoint, O_RDONLY | O_DIRECTORY);
    VERIFY(directory_fd >= 0);
    VERIFY(fsync(directory_fd) == 0);

    fd = open(filler.characters(), O_CREAT | O_RDWR, 0600);
    VERIFY(fd >= 0);
    memset(data, 0x55, sizeof(data));
    bool full = false;
    for (size_t i = 0; i <= blocks; ++i) {
        auto written = write(fd, data, sizeof(data));
        if (written < 0) {
            VERIFY(errno == ENOSPC);
            full = true;
            break;
        }
        VERIFY(written > 0 && written <= static_cast<ssize_t>(sizeof(data)));
    }
    VERIFY(full);
    VERIFY(close(fd) == 0);
    fd = open(sentinel.characters(), O_RDONLY);
    VERIFY(fd >= 0);
    VERIFY(read(fd, data, sizeof(data)) == sizeof(data));
    for (auto byte : data)
        VERIFY(byte == 0x7b);
    VERIFY(close(fd) == 0);

    struct statvfs before_reclaim;
    VERIFY(fstatvfs(directory_fd, &before_reclaim) == 0);
    fd = open(filler.characters(), O_RDONLY);
    VERIFY(fd >= 0);
    VERIFY(unlink(filler.characters()) == 0);
    VERIFY(fstat(fd, &metadata) == 0 && metadata.st_nlink == 0);
    VERIFY(read(fd, data, sizeof(data)) == sizeof(data));
    for (auto byte : data)
        VERIFY(byte == 0x55);
    // An open unlinked file stays readable; its blocks must become available
    // as soon as its final description closes, without periodic cache eviction.
    VERIFY(close(fd) == 0);
    struct statvfs after_reclaim;
    VERIFY(fstatvfs(directory_fd, &after_reclaim) == 0);
    VERIFY(after_reclaim.f_bfree > before_reclaim.f_bfree);
    VERIFY(fsync(directory_fd) == 0);
    fd = open(reclaimed.characters(), O_CREAT | O_RDWR, 0600);
    VERIFY(fd >= 0);
    memset(data, 0x3c, sizeof(data));
    VERIFY(write(fd, data, sizeof(data)) == sizeof(data));
    VERIFY(fsync(fd) == 0);
    VERIFY(close(fd) == 0);
    VERIFY(fsync(directory_fd) == 0);
    VERIFY(close(directory_fd) == 0);
    VERIFY(umount(mountpoint) == 0);
    VERIFY(rmdir(mountpoint) == 0);
    VERIFY(close(device_fd) == 0);
}
