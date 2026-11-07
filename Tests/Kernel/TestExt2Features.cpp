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

TEST_CASE(unsupported_ext2_features_are_rejected_before_mount)
{
    auto* prefix = getenv("SERENITY_EXT2_FEATURE_DEVICE_PREFIX");
    if (!prefix) {
        dbgln("Skipping feature workload: use Meta/test-ext2-features.py");
        return;
    }
    for (size_t index = 0; index < 3; ++index) {
        auto device = ByteString::formatted("{}{}", prefix, index);
        int fd = open(device.characters(), O_RDWR);
        VERIFY(fd >= 0);
        struct stat metadata;
        VERIFY(fstat(fd, &metadata) == 0 && S_ISBLK(metadata.st_mode));
        char label[16];
        constexpr char expected[16] = "SerenityFeat";
        VERIFY(pread(fd, label, sizeof(label), 1024 + 120) == sizeof(label));
        VERIFY(memcmp(label, expected, sizeof(label)) == 0);
        u32 blocks, shift, features;
        VERIFY(pread(fd, &blocks, sizeof(blocks), 1028) == sizeof(blocks));
        VERIFY(pread(fd, &shift, sizeof(shift), 1048) == sizeof(shift));
        VERIFY(blocks == 4096 && shift == 2);
        VERIFY(pread(fd, &features, sizeof(features), 1024 + 92 + 4 * index) == sizeof(features));
        constexpr u32 unsupported[] = { 0x4, 0x40, 0x400 }; // journal, extents, metadata checksums
        VERIFY(features & unsupported[index]);
        char mountpoint[] = "/tmp/ext2-features-XXXXXX";
        VERIFY(mkdtemp(mountpoint));
        VERIFY(mount(fd, mountpoint, "Ext2FS", 0) == -1);
        EXPECT_EQ(errno, ENOTSUP);
        VERIFY(rmdir(mountpoint) == 0);
        VERIFY(close(fd) == 0);
    }
    dbgln("EXT2_UNSUPPORTED_FEATURES_REJECTED");
}
