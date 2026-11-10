/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/ProcessStatisticsReader.h>
#include <LibGUI/Application.h>
#include <LibGUI/ConnectionToWindowServer.h>
#include <LibGfx/Palette.h>
#include <LibMain/Main.h>
#include <unistd.h>

static ErrorOr<Gfx::Palette> load_palette(ByteString const& path)
{
    return Gfx::Palette(Gfx::PaletteImpl::create_with_anonymous_buffer(TRY(Gfx::load_system_theme(path))));
}

static u64 process_time(Core::AllProcessesStatistics const& statistics, StringView name)
{
    u64 time = 0;
    for (auto const& process : statistics.processes) {
        if (process.name != name)
            continue;
        for (auto const& thread : process.threads)
            time += thread.time_user + thread.time_kernel;
    }
    return time;
}

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    VERIFY(arguments.strings.size() >= 2);
    auto mode = arguments.strings[1];
    if (mode == "idle"sv) {
        VERIFY(arguments.strings.size() == 3);
        auto before = TRY(Core::ProcessStatisticsReader::get_all(false));
        sleep(10);
        auto after = TRY(Core::ProcessStatisticsReader::get_all(false));
        dbgln("THEME IDLE: label={} server_ticks={} taskbar_ticks={} total_ticks={}", arguments.strings[2],
            process_time(after, "WindowServer"sv) - process_time(before, "WindowServer"sv),
            process_time(after, "Taskbar"sv) - process_time(before, "Taskbar"sv),
            after.total_time_scheduled - before.total_time_scheduled);
        return 0;
    }

    auto dark = TRY(load_palette("/res/themes/Modern Dark.ini"));
    auto standard = TRY(load_palette("/res/themes/Default.ini"));
    auto config = TRY(Core::ConfigFile::open("/res/themes/Modern Dark.ini"));
    size_t count = 0;
#define CHECK_COLOR(role)                                                           \
    do {                                                                            \
        auto parsed = Gfx::Color::from_string(config->read_entry("Colors", #role)); \
        VERIFY(parsed.has_value());                                                 \
        VERIFY(dark.color(Gfx::ColorRole::role) == parsed.value());                 \
        ++count;                                                                    \
    } while (0);
    ENUMERATE_COLOR_ROLES(CHECK_COLOR)
#undef CHECK_COLOR
#define CHECK_METRIC(role) VERIFY(dark.metric(Gfx::MetricRole::role) == standard.metric(Gfx::MetricRole::role));
    ENUMERATE_METRIC_ROLES(CHECK_METRIC)
#undef CHECK_METRIC
    VERIFY(dark.is_dark());
    VERIFY(!standard.is_dark());
    if (mode == "files"sv) {
        dbgln("THEME FILES PASS: {} explicit colors match the real loader; all metrics match Default", count);
        return 0;
    }

    VERIFY(mode == "dark"sv || mode == "default"sv);
    auto app = TRY(GUI::Application::create(arguments));
    auto expected = mode == "dark"sv ? dark : standard;
    auto theme_name = GUI::ConnectionToWindowServer::the().get_system_theme();
    // Older images can omit the preference and use the startup default.
    VERIFY(mode == "dark"sv ? theme_name == "Modern Dark"sv : theme_name.is_empty() || theme_name == "Default"sv);
    auto actual = app->palette();
#define CHECK_LIVE_COLOR(role) VERIFY(actual.color(Gfx::ColorRole::role) == expected.color(Gfx::ColorRole::role));
    ENUMERATE_COLOR_ROLES(CHECK_LIVE_COLOR)
#undef CHECK_LIVE_COLOR
#define CHECK_LIVE_METRIC(role) VERIFY(actual.metric(Gfx::MetricRole::role) == expected.metric(Gfx::MetricRole::role));
    ENUMERATE_METRIC_ROLES(CHECK_LIVE_METRIC)
#undef CHECK_LIVE_METRIC
    VERIFY(actual.is_dark() == expected.is_dark());
    dbgln("THEME LIVE PASS: {} all {} colors and metrics match WindowServer", mode, count);
    return 0;
}
