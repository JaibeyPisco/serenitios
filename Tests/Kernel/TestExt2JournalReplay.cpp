/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/ByteString.h>
#include <Kernel/API/FileSystem/MountSpecificFlags.h>
#include <LibCore/System.h>
#include <LibTest/TestCase.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>

TEST_CASE(ext2_journal_recovery_mount)
{
    auto* device = getenv("SERENITY_JOURNAL_DEVICE");
    auto* operation = getenv("SERENITY_JOURNAL_OPERATION");
    auto* outcome = getenv("SERENITY_JOURNAL_OUTCOME");
    if (!device || !operation || !outcome) {
        dbgln("Skipping journal recovery workload: use Meta/test-kernel-journal-replay.py");
        return;
    }
    bool readonly_fd = strcmp(outcome, "readonly-fd") == 0;
    int fd = open(device, readonly_fd ? O_RDONLY : O_RDWR);
    VERIFY(fd >= 0);
    struct stat metadata;
    VERIFY(fstat(fd, &metadata) == 0 && S_ISBLK(metadata.st_mode));
    constexpr char expected_label[16] = "SerenityJournal";
    char label[16];
    VERIFY(pread(fd, label, sizeof(label), 1144) == sizeof(label));
    VERIFY(memcmp(label, expected_label, sizeof(label)) == 0);
    u32 blocks, shift;
    VERIFY(pread(fd, &blocks, sizeof(blocks), 1028) == sizeof(blocks));
    VERIFY(pread(fd, &shift, sizeof(shift), 1048) == sizeof(shift));
    VERIFY(blocks == 8192 && shift == 2);
    char mountpoint[] = "/tmp/ext2-journal-XXXXXX";
    VERIFY(mkdtemp(mountpoint));

    // Opt-in is mandatory even when the journal is clean.
    VERIFY(mount(fd, mountpoint, "Ext2FS", 0) == -1);
    VERIFY(errno == ENOTSUP);
    if (strcmp(outcome, "default") == 0) {
        VERIFY(close(fd) == 0);
        VERIFY(rmdir(mountpoint) == 0);
        dbgln("EXT2_JOURNAL_REPLAY_EXECUTED");
        return;
    }
    int mount_fd = fsopen("Ext2FS", 0);
    VERIFY(mount_fd >= 0);
    constexpr char key[] = "journal_replay";
    u64 enabled = 1;
    MountSpecificFlag flag {
        .key_string_length = sizeof(key) - 1,
        .value_length = sizeof(enabled),
        .value_type = MountSpecificFlag::ValueType::Boolean,
        .key_string_addr = reinterpret_cast<unsigned char const*>(key),
        .value_addr = &enabled,
    };
    VERIFY(ioctl(mount_fd, MOUNT_IOCTL_SET_MOUNT_SPECIFIC_FLAG, &flag) == 0);
    if (strcmp(outcome, "reject") == 0 || readonly_fd) {
        VERIFY(fsmount(-1, mount_fd, fd, mountpoint) == -1);
        VERIFY(errno == EINVAL || errno == EIO || errno == ENOTSUP || errno == EROFS || errno == EACCES || errno == EBADF);
        VERIFY(close(mount_fd) == 0);
        VERIFY(close(fd) == 0);
        VERIFY(rmdir(mountpoint) == 0);
        dbgln("EXT2_JOURNAL_REPLAY_EXECUTED");
        return;
    }
    auto result = fsmount(-1, mount_fd, fd, mountpoint);
    if (result < 0)
        dbgln("Journal replay mount failed: {} ({})", strerror(errno), errno);
    VERIFY(result == 0);
    char second_mountpoint[] = "/tmp/ext2-journal-reuse-XXXXXX";
    VERIFY(mkdtemp(second_mountpoint));
    VERIFY(fsmount(-1, mount_fd, fd, second_mountpoint) == -1 && errno == EBUSY);
    VERIFY(rmdir(second_mountpoint) == 0);
    VERIFY(close(mount_fd) == 0);
    bool new_state = strcmp(outcome, "new") == 0;
    auto check_file = [&](char const* name, bool present, size_t size, u8 value) {
        auto path = ByteString::formatted("{}/{}", mountpoint, name);
        int file = open(path.characters(), O_RDONLY);
        if (!present) {
            VERIFY(file == -1 && errno == ENOENT);
            return;
        }
        VERIFY(file >= 0);
        struct stat file_metadata;
        VERIFY(fstat(file, &file_metadata) == 0 && static_cast<size_t>(file_metadata.st_size) == size);
        char contents[4096];
        for (size_t offset = 0; offset < size; offset += sizeof(contents)) {
            VERIFY(read(file, contents, sizeof(contents)) == sizeof(contents));
            for (auto byte : contents)
                VERIFY(static_cast<u8>(byte) == value);
        }
        char extra;
        VERIFY(read(file, &extra, 1) == 0);
        VERIFY(close(file) == 0);
    };
    check_file("sentinel", true, 4096, 0x7b);
    if (strcmp(operation, "create") == 0) {
        check_file("victim", new_state, 32 * 4096, 0x5a);
        check_file("destination", false, 0, 0);
    } else if (strcmp(operation, "unlink") == 0) {
        check_file("victim", !new_state, 32 * 4096, 0x41);
        check_file("destination", false, 0, 0);
    } else if (strcmp(operation, "rename") == 0) {
        check_file("victim", !new_state, 32 * 4096, 0x41);
        check_file("destination", true, 32 * 4096, new_state ? 0x41 : 0x42);
    } else {
        VERIFY(strcmp(operation, "truncate") == 0);
        check_file("victim", true, new_state ? 4096 : 32 * 4096, 0x41);
        check_file("destination", false, 0, 0);
    }
    auto denied = ByteString::formatted("{}/must-not-exist", mountpoint);
    VERIFY(open(denied.characters(), O_CREAT | O_WRONLY, 0600) == -1 && errno == EROFS);
    auto sentinel_path = ByteString::formatted("{}/sentinel", mountpoint);
    VERIFY(open(sentinel_path.characters(), O_WRONLY) == -1 && errno == EROFS);
    int held_file = open(sentinel_path.characters(), O_RDONLY);
    VERIFY(held_file >= 0);
    VERIFY(umount(mountpoint) == -1 && errno == EBUSY);
    VERIFY(close(held_file) == 0);
    VERIFY(!Core::System::remount({}, StringView { mountpoint, strlen(mountpoint) }, 0).is_error());
    VERIFY(open(sentinel_path.characters(), O_WRONLY) == -1 && errno == EROFS);
    sync();
    VERIFY(umount(mountpoint) == 0);
    VERIFY(rmdir(mountpoint) == 0);
    VERIFY(close(fd) == 0);
    dbgln("EXT2_JOURNAL_REPLAY_EXECUTED");
}
