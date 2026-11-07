/*
 * Copyright (c) 2026, the SerenityOS developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibTest/TestCase.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

TEST_CASE(exhaustion_and_process_exit_recover_memory)
{
    // Intentionally opt-in: exhaust only a disposable 128/256 MiB QEMU guest.
    auto* config = fopen("/tmp/serenity-memory-pressure-enabled", "r");
    if (!config) {
        dbgln("SKIP: memory pressure requires the isolated guest runner");
        return;
    }
    unsigned rounds = 0;
    VERIFY(fscanf(config, "%u", &rounds) == 1);
    fclose(config);
    VERIFY(rounds >= 16 && rounds <= 4096);
    constexpr size_t chunk_size = 1024 * 1024;
    void* reservations[512];
    size_t count = 0;
    for (; count < 512; ++count) {
        auto* mapping = mmap(nullptr, chunk_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (mapping == MAP_FAILED) {
            VERIFY(errno == ENOMEM);
            break;
        }
        reservations[count] = mapping;
    }
    VERIFY(count > 0 && count < 512);
    auto reserved_chunks = count;
    // Scale the cleanup workload to headroom measured before exhaustion. A
    // 128 MiB guest may have less than 8 MiB available even before this test.
    auto cleanup_chunks = reserved_chunks / 4;
    if (cleanup_chunks == 0)
        cleanup_chunks = 1;
    if (cleanup_chunks > 8)
        cleanup_chunks = 8;
    auto child_size = cleanup_chunks * chunk_size;
    while (count)
        VERIFY(munmap(reservations[--count], chunk_size) == 0);
    auto* recovered = static_cast<unsigned char*>(mmap(nullptr, chunk_size, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0));
    VERIFY(recovered != MAP_FAILED);
    memset(recovered, 0x5a, chunk_size);
    dbgln("MEMORY_RESERVATION_ENOMEM_RECOVERED chunks={}", reserved_chunks);

    // Touch unreserved memory exceeding the guest's RAM. The parent retains
    // committed memory and must survive physical exhaustion in the child.
    auto pid = fork();
    VERIFY(pid >= 0);
    if (pid == 0) {
        constexpr size_t pressure_size = 512 * chunk_size;
        auto* pressure = static_cast<volatile unsigned char*>(mmap(nullptr, pressure_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0));
        if (pressure == MAP_FAILED)
            _exit(90);
        for (size_t offset = 0; offset < pressure_size; offset += PAGE_SIZE)
            pressure[offset] = 0x7b;
        _exit(91); // Not enough pressure: this is not a passing OOM test.
    }
    int status = 0;
    VERIFY(waitpid(pid, &status, 0) == pid);
    VERIFY(WIFSIGNALED(status) && WTERMSIG(status) == SIGSEGV);
    for (size_t offset = 0; offset < chunk_size; ++offset)
        VERIFY(recovered[offset] == 0x5a);
    dbgln("MEMORY_OOM_CHILD_REAPED");

    // Leave each mapping allocated to exercise process-exit cleanup. Enough
    // leaked memory would prevent subsequent rounds from completing.
    for (unsigned round = 0; round < rounds; ++round) {
        pid = fork();
        VERIFY(pid >= 0);
        if (pid == 0) {
            auto* mapping = static_cast<unsigned char*>(mmap(nullptr, child_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
            if (mapping == MAP_FAILED)
                _exit(92);
            for (size_t offset = 0; offset < child_size; offset += PAGE_SIZE) {
                if (mapping[offset] != 0)
                    _exit(93);
                mapping[offset] = static_cast<unsigned char>(round);
            }
            _exit(0);
        }
        VERIFY(waitpid(pid, &status, 0) == pid);
        VERIFY(WIFEXITED(status) && WEXITSTATUS(status) == 0);
        if ((round + 1) % 16 == 0)
            dbgln("MEMORY_EXIT_ROUNDS_COMPLETED {}", round + 1);
    }
    VERIFY(munmap(recovered, chunk_size) == 0);
    auto* final_mapping = static_cast<unsigned char*>(mmap(nullptr, child_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    VERIFY(final_mapping != MAP_FAILED);
    for (size_t offset = 0; offset < child_size; offset += PAGE_SIZE) {
        VERIFY(final_mapping[offset] == 0);
        final_mapping[offset] = 0x3c;
    }
    VERIFY(munmap(final_mapping, child_size) == 0);
    dbgln("MEMORY_PRESSURE_RECOVERED rounds={} child_bytes={}", rounds, child_size);
}
