/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Timer.h>
#include <LibGUI/Application.h>
#include <LibGUI/Desktop.h>
#include <LibGUI/Window.h>
#include <LibMain/Main.h>
#include <Services/WindowServer/Window.h>

class DockObserver final : public GUI::Window {
    C_OBJECT(DockObserver);
private:
    DockObserver() = default;
    virtual void wm_event(GUI::WMEvent& event) override
    {
        if (event.type() == GUI::Event::WM_WindowStateChanged) {
            auto& state = static_cast<GUI::WMWindowStateChangedEvent&>(event);
            dbgln("DOCK STATE client={} window={} type={} active={} minimized={} workspace={},{} rect={} title={}", event.client_id(), event.window_id(), static_cast<int>(state.window_type()), state.is_active(), state.is_minimized(), state.workspace_row(), state.workspace_column(), state.rect(), state.title());
        } else if (event.type() == GUI::Event::WM_WindowRectChanged) {
            auto& rect = static_cast<GUI::WMWindowRectChangedEvent&>(event);
            dbgln("DOCK WINDOW RECT client={} window={} rect={}", event.client_id(), event.window_id(), rect.rect());
        } else if (event.type() == GUI::Event::WM_WindowRemoved) {
            dbgln("DOCK REMOVED client={} window={}", event.client_id(), event.window_id());
        } else if (event.type() == GUI::Event::WM_ApplicationIdentity) {
            auto& identity = static_cast<GUI::WMApplicationIdentityEvent&>(event);
            dbgln("DOCK IDENTITY client={} window={} executable={}", event.client_id(), event.window_id(), identity.executable());
        } else if (event.type() == GUI::Event::WM_WorkspaceChanged) {
            auto& workspace = static_cast<GUI::WMWorkspaceChangedEvent&>(event);
            dbgln("DOCK WORKSPACE {},{}", workspace.current_row(), workspace.current_column());
        }
    }
};

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    auto app = TRY(GUI::Application::create(arguments));
    Vector<NonnullRefPtr<GUI::Window>> windows;
    if (arguments.strings.size() > 1 && arguments.strings[1] == "overflow"sv) {
        for (int i = 0; i < 18; ++i) {
            auto window = GUI::Window::construct();
            window->set_title(ByteString::formatted("Unidentified window {}", i + 1));
            window->set_rect(120 + i * 6, 120 + i * 6, 240, 120);
            window->set_main_widget<GUI::Widget>()->set_fill_with_background_color(true);
            window->show();
            windows.append(move(window));
        }
    }
    auto observer = DockObserver::construct();
    observer->set_window_type(GUI::WindowType::Taskbar);
    auto screen = GUI::Desktop::the().rect();
    observer->set_rect(screen.right() - 5, 34, 4, 4);
    observer->show();
    observer->make_window_manager(WindowServer::WMEventMask::WindowStateChanges | WindowServer::WMEventMask::WindowRectChanges | WindowServer::WMEventMask::WindowRemovals | WindowServer::WMEventMask::WorkspaceChanges);
    dbgln("DOCK OBSERVER READY");
    auto timer = Core::Timer::create_single_shot(540000, [&] { app->quit(); });
    timer->start();
    return app->exec();
}
