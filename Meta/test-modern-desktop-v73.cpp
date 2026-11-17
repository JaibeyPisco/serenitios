/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibGUI/Application.h>
#include <LibGUI/ConnectionToWindowServer.h>
#include <LibGUI/Desktop.h>
#include <LibGUI/Window.h>
#include <LibCore/Timer.h>
#include <LibGfx/DesktopGeometry.h>
#include <LibGfx/Palette.h>
#include <LibMain/Main.h>

// Guest-only integration controls. No test executable is shipped in release.
ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    auto app = TRY(GUI::Application::create(arguments));
    if (arguments.strings.size() < 2)
        return Error::from_string_literal("Expected theme or check-frame");
    auto& server = GUI::ConnectionToWindowServer::the();
    auto mode = arguments.strings[1];
    if (mode == "applet"sv) {
        auto window = GUI::Window::construct();
        window->set_window_type(GUI::WindowType::Applet);
        window->set_title("V73 layout probe");
        window->resize(16, 16);
        window->set_main_widget<GUI::Widget>()->set_fill_with_background_color(true);
        window->show();
        auto screen = GUI::Desktop::the().rect();
        int step = 0;
        auto timer = Core::Timer::create_repeating(1000, [&] {
            ++step;
            if (step == 3)
                window->resize(32, 16);
            if (step == 6)
                window->resize(screen.width(), 16);
            if (step == 9)
                window->resize(16, 16);
            if (step == 2 || step == 5 || step == 8 || step == 11) {
                auto rect = server.get_applet_rect_on_screen(window->window_id());
                dbgln("V73 APPLET step={} rect={} screen={}", step, rect, screen);
                if (step == 8) {
                    VERIFY(rect.left() > screen.right());
                } else {
                    VERIFY(rect.left() >= screen.left() + 148);
                    VERIFY(rect.right() <= screen.right() - 84);
                    VERIFY(rect.top() >= screen.top() && rect.bottom() <= screen.top() + 32);
                }
            }
            if (step == 12) {
                dbgln("V73 APPLET LAYOUT PASS");
                app->quit();
            }
        });
        timer->start();
        return app->exec();
    }
    if (mode == "theme"sv && arguments.strings.size() == 3) {
        auto theme = arguments.strings[2];
        VERIFY(theme == "dark"sv || theme == "light"sv || theme == "default"sv);
        auto name = theme == "dark"sv ? "Modern Dark" : theme == "light"sv ? "Modern Light" : "Default";
        VERIFY(server.set_system_theme(ByteString::formatted("/res/themes/{}.ini", name), name, false, "Custom"sv));
        dbgln("V73 THEME PASS {}", name);
        return 0;
    }
    if (mode == "check-frame"sv && arguments.strings.size() == 4) {
        auto client = arguments.strings[2].to_number<int>();
        auto window = arguments.strings[3].to_number<int>();
        VERIFY(client.has_value() && window.has_value());
        auto rect = server.get_window_rect_from_client(client.value(), window.value());
        auto const& palette = app->palette();
        auto frame = palette.window_theme().frame_rect_for_window(Gfx::WindowTheme::WindowType::Normal, Gfx::WindowTheme::WindowMode::Other, rect, palette, 1);
        auto usable = Gfx::DesktopGeometry::work_area(GUI::Desktop::the().rects()[GUI::Desktop::the().main_screen_index()]);
        dbgln("V73 FRAME client={} frame={} usable={}", rect, frame, usable);
        VERIFY(!rect.is_empty() && usable.contains(frame));
        dbgln("V73 FRAME PASS");
        return 0;
    }
    return Error::from_string_literal("Invalid V7.3 test arguments");
}
