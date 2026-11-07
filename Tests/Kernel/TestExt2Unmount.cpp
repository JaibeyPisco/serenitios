/*
 * Copyright (c) 2026, the SerenityOS developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/ByteString.h>
#include <AK/ScopeGuard.h>
#include <LibTest/TestCase.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

TEST_CASE(unmount_flushes_and_preserves_mount_on_error)
{
    // This opt-in test modifies a disposable ext2 filesystem. Require both an
    // explicit device path and the test volume label before mounting it.
    auto* device = getenv("SERENITY_EXT2_UNMOUNT_DEVICE");
    if (!device) {
        dbgln("Skipping ext2 unmount test: no disposable device specified");
        return;
    }
    int device_fd = open(device, O_RDWR);
    VERIFY(device_fd >= 0);
    ScopeGuard close_device = [&] { close(device_fd); };
    char label[16] {};
    VERIFY(pread(device_fd, label, sizeof(label), 1024 + 120) == sizeof(label));
    constexpr char expected_label[16] = "SerenityTest";
    VERIFY(memcmp(label, expected_label, sizeof(label)) == 0);
    u16 initial_state;
    VERIFY(pread(device_fd, &initial_state, sizeof(initial_state), 1024 + 58) == sizeof(initial_state));
    auto verify_unmounted_state = [&] {
        u16 state;
        VERIFY(pread(device_fd, &state, sizeof(state), 1024 + 58) == sizeof(state));
        EXPECT_EQ(state, initial_state == 1 ? 1 : 2);
    };

    char mountpoint[] = "/tmp/ext2-unmount-XXXXXX";
    VERIFY(mkdtemp(mountpoint));
    ScopeGuard remove_mountpoint = [&] { rmdir(mountpoint); };
    VERIFY(mount(device_fd, mountpoint, "Ext2FS", 0) == 0);
    auto path = ByteString::formatted("{}/durability-data", mountpoint);
    char expected[12288];
    memset(expected, 0x5a, sizeof(expected));
    int fd = open(path.characters(), O_CREAT | O_RDWR | O_TRUNC, 0600);
    VERIFY(fd >= 0);
    VERIFY(write(fd, expected, sizeof(expected)) == sizeof(expected));
    VERIFY(umount(mountpoint) == -1);
    VERIFY(errno == EBUSY);
    VERIFY(close(fd) == 0);

    int root_fd = open(mountpoint, O_RDONLY | O_DIRECTORY);
    VERIFY(root_fd >= 0);
    VERIFY(umount(mountpoint) == -1);
    VERIFY(errno == EBUSY);
    VERIFY(close(root_fd) == 0);

    size_t failures = 0;
    while (umount(mountpoint) < 0) {
        auto error = errno;
        VERIFY(error == EIO || error == ENOMEM);
        VERIFY(++failures <= 4);
        dbgln("unmount reported durability error: {}", error);
        u16 mounted_state;
        VERIFY(pread(device_fd, &mounted_state, sizeof(mounted_state), 1024 + 58) == sizeof(mounted_state));
        int writable_fd = open(path.characters(), O_WRONLY);
        if (writable_fd >= 0) {
            EXPECT_EQ(mounted_state, 2);
            VERIFY(close(writable_fd) == 0);
        } else {
            VERIFY(errno == EROFS);
            dbgln("filesystem made read-only after failed mounted-state recovery");
        }
        fd = open(path.characters(), O_RDONLY);
        VERIFY(fd >= 0);
        char actual[sizeof(expected)];
        VERIFY(read(fd, actual, sizeof(actual)) == sizeof(actual));
        VERIFY(memcmp(actual, expected, sizeof(actual)) == 0);
        VERIFY(close(fd) == 0);
    }

    verify_unmounted_state();
    VERIFY(mount(device_fd, mountpoint, "Ext2FS", 0) == 0);
    fd = open(path.characters(), O_RDONLY);
    VERIFY(fd >= 0);
    char actual[sizeof(expected)];
    VERIFY(read(fd, actual, sizeof(actual)) == sizeof(actual));
    EXPECT_EQ(memcmp(actual, expected, sizeof(actual)), 0);
    VERIFY(close(fd) == 0);
    VERIFY(umount(mountpoint) == 0);
    verify_unmounted_state();
}
