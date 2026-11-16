/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Timer.h>
#include <LibGUI/Application.h>
#include <LibGUI/BoxLayout.h>
#include <LibGUI/ConnectionToWindowServer.h>
#include <LibGUI/Desktop.h>
#include <LibGUI/Label.h>
#include <LibGUI/Menu.h>
#include <LibGUI/Notification.h>
#include <LibGUI/Window.h>
#include <LibGfx/DesktopGeometry.h>
#include <LibMain/Main.h>

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    auto app = TRY(GUI::Application::create(arguments));
    if (arguments.strings.size() > 1 && arguments.strings[1] != "notification"sv) {
        auto mode = arguments.strings[1];
        auto name = mode == "light"sv ? "Modern Light" : mode == "dark"sv ? "Modern Dark" : "Default";
        VERIFY(GUI::ConnectionToWindowServer::the().set_system_theme(ByteString::formatted("/res/themes/{}.ini", name), name, false, "Custom"sv));
        dbgln("DOCK THEME SET {}", name);
        return 0;
    }
    auto screen = GUI::Desktop::the().rects()[GUI::Desktop::the().main_screen_index()];
    auto usable = Gfx::DesktopGeometry::work_area(screen);
    VERIFY(usable.top() == screen.top() + 32);
    VERIFY(usable.bottom() == screen.bottom() - Gfx::DesktopGeometry::bottom_reserved_height());
    VERIFY(Gfx::DesktopGeometry::work_area({ 100, 50, 640, 480 }) == Gfx::IntRect(100, 82, 640, 448 - Gfx::DesktopGeometry::bottom_reserved_height()));
    if (arguments.strings.size() > 1 && arguments.strings[1] == "notification"sv) {
        auto notification = GUI::Notification::construct();
        notification->set_title("V7.2 notification test"_string);
        notification->set_text("Real notification below the top panel."_string);
        notification->show();
        auto timer = Core::Timer::create_single_shot(15000, [&] { app->quit(); });
        timer->start();
        return app->exec();
    }
    auto window = GUI::Window::construct();
    window->set_title("V7.2 Geometry Probe");
    auto menu = window->add_menu("&File"_string);
    window->resize(360, 200);
    window->move_to(screen.x() + 160, screen.y() + 110);
    auto root = window->set_main_widget<GUI::Widget>();
    root->set_min_size(180, 100);
    root->set_fill_with_background_color(true);
    root->set_layout<GUI::VerticalBoxLayout>();
    auto& label = root->add<GUI::Label>("Geometry checks: maximize, restore, fullscreen, minimize"_string);
    label.set_text_wrapping(Gfx::TextWrapping::Wrap);
    window->show();
    Gfx::IntRect original;
    int step = 0;
    auto timer = Core::Timer::create_repeating(1500, [&] {
        dbgln("DOCK STEP {} rect={} maximized={} fullscreen={} minimized={}", step, window->rect(), window->is_maximized(), window->is_fullscreen(), window->is_minimized());
        switch (step++) {
        case 0:
            original = window->rect();
            window->set_maximized(true);
            break;
        case 1:
            VERIFY(window->is_maximized());
            VERIFY(window->rect().top() >= usable.top());
            VERIFY(window->rect().bottom() <= usable.bottom() + 1);
            VERIFY(window->rect().width() >= screen.width() - 4);
            window->set_maximized(false);
            break;
        case 2:
            VERIFY(window->rect() == original);
            window->set_fullscreen(true);
            break;
        case 3:
            VERIFY(window->is_fullscreen());
            VERIFY(window->rect() == screen);
            window->set_fullscreen(false);
            break;
        case 4:
            VERIFY(window->rect() == original);
            window->set_minimized(true);
            break;
        case 5:
            VERIFY(window->is_minimized());
            window->set_minimized(false);
            break;
        case 6:
            VERIFY(!window->is_minimized());
            VERIFY(window->rect() == original);
            dbgln("DOCK GEOMETRY PASS screen={} usable={}", screen, usable);
            app->quit();
            break;
        }
    });
    timer->start();
    return app->exec();
}
