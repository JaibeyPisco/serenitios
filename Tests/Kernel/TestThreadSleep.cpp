/*
 * Copyright (c) 2026, the SerenityOS developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/Atomic.h>
#include <LibTest/TestCase.h>
#include <pthread.h>
#include <sched.h>
#include <time.h>

TEST_CASE(repeated_concurrent_sleep_timeouts)
{
    // On SMP, a timeout can wake a thread on another CPU before its callback
    // finishes. Immediately sleeping again must not reuse a still-queued timer.
    Atomic<bool> start { false };
    auto sleeper = [](void* context) -> void* {
        auto& ready = *static_cast<Atomic<bool>*>(context);
        while (!ready.load(AK::memory_order_acquire))
            sched_yield();

        for (size_t i = 0; i < 250; ++i) {
            timespec delay { 0, 1'000'000 };
            if (nanosleep(&delay, nullptr) != 0)
                return context;
        }
        return nullptr;
    };
    pthread_t threads[8];
    for (auto& thread : threads)
        VERIFY(pthread_create(&thread, nullptr, sleeper, &start) == 0);
    start.store(true, AK::memory_order_release);

    for (auto& thread : threads) {
        void* result;
        VERIFY(pthread_join(thread, &result) == 0);
        EXPECT_EQ(result, nullptr);
    }
}
