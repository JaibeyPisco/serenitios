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
#include <unistd.h>

TEST_CASE(ext2_basic_persistence)
{
    auto* device = getenv("SERENITY_PERSISTENCE_DEVICE");
    auto* phase_string = getenv("SERENITY_PERSISTENCE_PHASE");
    if (!device || !phase_string) {
        dbgln("Skipping opt-in persistent filesystem workload");
        return;
    }
    int phase = atoi(phase_string);
    VERIFY(phase >= 1 && phase <= 3);
    int disk = open(device, O_RDWR);
    VERIFY(disk >= 0);
    struct stat metadata;
    VERIFY(fstat(disk, &metadata) == 0 && S_ISBLK(metadata.st_mode));
    char label[16];
    constexpr char expected[16] = "SerenityPersist";
    VERIFY(pread(disk, label, sizeof(label), 1144) == sizeof(label));
    VERIFY(memcmp(label, expected, sizeof(label)) == 0);
    char mountpoint[] = "/tmp/ext2-persist-XXXXXX";
    VERIFY(mkdtemp(mountpoint));
    VERIFY(chmod(mountpoint, 0755) == 0);
    VERIFY(mount(disk, mountpoint, "Ext2FS", 0) == 0);
    auto folder = ByteString::formatted("{}/work", mountpoint);
    if (phase == 1) {
        VERIFY(mkdir(folder.characters(), 0700) == 0);
        VERIFY(chown(folder.characters(), 100, 100) == 0);
    }
    VERIFY(seteuid(100) == 0);
    VERIFY(geteuid() == 100);
    auto original = ByteString::formatted("{}/document", folder);
    auto renamed = ByteString::formatted("{}/renamed", folder);
    auto final = ByteString::formatted("{}/final", folder);
    auto deleted = ByteString::formatted("{}/deleted", folder);
    auto write = [&](ByteString const& path, char const* text, int flags) {
        int fd = open(path.characters(), O_WRONLY | flags, 0600);
        VERIFY(fd >= 0);
        auto size = strlen(text);
        VERIFY(::write(fd, text, size) == static_cast<ssize_t>(size));
        VERIFY(fsync(fd) == 0);
        VERIFY(close(fd) == 0);
    };
    auto check = [&](ByteString const& path, char const* expected_text) {
        int fd = open(path.characters(), O_RDONLY);
        VERIFY(fd >= 0);
        char buffer[64];
        auto count = read(fd, buffer, sizeof(buffer));
        VERIFY(count == static_cast<ssize_t>(strlen(expected_text)));
        VERIFY(memcmp(buffer, expected_text, count) == 0);
        VERIFY(read(fd, buffer, 1) == 0);
        VERIFY(close(fd) == 0);
    };
    if (phase == 1) {
        write(original, "before", O_CREAT | O_EXCL);
        check(original, "before");
        write(original, "after", O_APPEND);
        check(original, "beforeafter");
        VERIFY(rename(original.characters(), renamed.characters()) == 0);
        write(deleted, "remove", O_CREAT | O_EXCL);
        VERIFY(unlink(deleted.characters()) == 0);
    } else if (phase == 2) {
        check(renamed, "beforeafter");
        write(renamed, "again", O_APPEND);
        VERIFY(rename(renamed.characters(), final.characters()) == 0);
        check(final, "beforeafteragain");
    } else {
        check(final, "beforeafteragain");
        VERIFY(access(renamed.characters(), F_OK) == -1 && errno == ENOENT);
    }
    VERIFY(access(original.characters(), F_OK) == -1 && errno == ENOENT);
    VERIFY(access(deleted.characters(), F_OK) == -1 && errno == ENOENT);
    int directory = open(folder.characters(), O_RDONLY | O_DIRECTORY);
    VERIFY(directory >= 0 && fsync(directory) == 0);
    VERIFY(close(directory) == 0);
    VERIFY(seteuid(0) == 0);
    VERIFY(umount(mountpoint) == 0);
    VERIFY(rmdir(mountpoint) == 0);
    VERIFY(close(disk) == 0);
    dbgln("EXT2_PERSISTENCE_EXECUTED phase={}", phase);
}
