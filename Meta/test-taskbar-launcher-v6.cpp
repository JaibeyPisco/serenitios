/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/ProcessStatisticsReader.h>
#include <LibMain/Main.h>
#include <Userland/Services/Taskbar/ApplicationLauncher.h>
#include <unistd.h>

ErrorOr<int> serenity_main(Main::Arguments)
{
    Vector<NonnullRefPtr<Desktop::AppFile>> apps;
    Desktop::AppFile::for_each([&](auto app) {
        if (!app->exclude_from_system_menu() && access(app->executable().characters(), X_OK) == 0)
            apps.append(move(app));
    });
    auto model = adopt_ref(*new LauncherAppsModel(apps));
    auto count = model->row_count({});
    VERIFY(count > 0);
    model->filter({}, " cAlCuLaToR "sv);
    VERIFY(model->row_count({}) == 1);
    VERIFY(model->app_at(model->index(0, 0)).executable() == "/bin/Calculator");
    model->filter("&Games"sv, "calculator"sv);
    VERIFY(model->row_count({}) == 0);
    model->filter({}, "no-such-application-v6"sv);
    VERIFY(!model->index(0, 0).is_valid());
    model->filter({}, "   "sv);
    VERIFY(model->row_count({}) == count);
    VERIFY(!model->index(-1, 0).is_valid());
    VERIFY(!model->index(count, 0).is_valid());
    VERIFY(!model->index(0, 1).is_valid());
    VERIFY(!model->index(0, 0, model->index(0, 0)).is_valid());
    dbgln("LAUNCHER V6 MODEL PASS: {} discovered apps, scoped filtering, reset and index boundaries", count);
    for (int sample = 0; sample < 2; ++sample) {
        auto processes = TRY(Core::ProcessStatisticsReader::get_all(false));
        for (auto& process : processes.processes) {
            if (process.name != "Taskbar")
                continue;
            u64 user = 0, kernel = 0, calls = 0, writes = 0;
            for (auto& thread : process.threads) {
                user += thread.time_user;
                kernel += thread.time_kernel;
                calls += thread.syscall_count;
                writes += thread.unix_socket_write_bytes;
            }
            dbgln("LAUNCHER V6 SAMPLE {}: pid={} user={} kernel={} syscalls={} socket_writes={}", sample, process.pid, user, kernel, calls, writes);
        }
        if (sample == 0)
            sleep(10);
    }
    return 0;
}
