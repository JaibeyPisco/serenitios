/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Process.h>
#include <LibCore/Timer.h>
#include <LibGUI/Application.h>
#include <LibGUI/ConnectionToWindowManagerServer.h>
#include <LibGUI/Window.h>
#include <LibMain/Main.h>
#include <Services/Taskbar/ApplicationIdentity.h>
#include <Services/Taskbar/WindowList.cpp>
#include <Services/WindowServer/Window.h>

class Observer final : public GUI::Window {
    C_OBJECT(Observer);
public:
    Vector<NonnullRefPtr<Desktop::AppFile>> apps;

private:
    Observer() = default;
    virtual void wm_event(GUI::WMEvent& event) override
    {
        WindowIdentifier id { event.client_id(), event.window_id() };
        auto& list = WindowList::the();
        switch (event.type()) {
        case GUI::Event::WM_WindowStateChanged: {
            auto& state = static_cast<GUI::WMWindowStateChangedEvent&>(event);
            if (state.window_type() != GUI::WindowType::Normal || state.is_frameless())
                break;
            auto& window = list.ensure_window(id);
            window.set_title(state.title());
            window.set_active(state.is_active());
            window.set_minimized(state.is_minimized());
            dbgln("IDENTITY STATE client={} window={} active={} minimized={} title={}", id.client_id(), id.window_id(), state.is_active(), state.is_minimized(), state.title());
            break;
        }
        case GUI::Event::WM_ApplicationIdentity: {
            if (auto* window = list.window(id)) {
                auto& identity = static_cast<GUI::WMApplicationIdentityEvent&>(event);
                window->set_application_file(Taskbar::application_file_for_executable(identity.executable(), apps));
                dbgln("IDENTITY client={} window={} executable={} app={}", id.client_id(), id.window_id(), identity.executable(), window->application_file());
            }
            break;
        }
        case GUI::Event::WM_WindowRemoved:
            list.remove_window(id);
            break;
        default:
            break;
        }
    }
};

static void activate(::Window const& window)
{
    auto id = window.identifier();
    GUI::ConnectionToWindowManagerServer::the().async_set_active_window(id.client_id(), id.window_id());
}

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    auto app = TRY(GUI::Application::create(arguments));
    auto first = GUI::Window::construct();
    first->set_title("Same title, different identities");
    first->set_rect(100, 150, 320, 160);
    first->set_main_widget<GUI::Widget>()->set_fill_with_background_color(true);
    first->show();
    if (arguments.strings.size() > 1) {
        auto timer = Core::Timer::create_single_shot(22000, [&] { app->quit(); });
        timer->start();
        return app->exec();
    }
    auto observer = Observer::construct();
    Desktop::AppFile::for_each([&](auto af) { observer->apps.append(move(af)); });
    VERIFY(Taskbar::application_file_for_executable("/bin/TestApplicationIdentity"sv, observer->apps) == "/res/apps/TestApplicationIdentity.af"sv);
    VERIFY(Taskbar::application_file_for_executable("/bin/UnknownApplicationIdentity"sv, observer->apps).is_empty());
    VERIFY(Taskbar::application_file_for_executable("Calculator"sv, observer->apps).is_empty());
    VERIFY(Taskbar::application_file_for_executable("/tmp/Calculator"sv, observer->apps).is_empty());
    auto ambiguous = observer->apps;
    ambiguous.append(Desktop::AppFile::open("/res/apps/TestApplicationIdentity.af"sv));
    VERIFY(Taskbar::application_file_for_executable("/bin/TestApplicationIdentity"sv, ambiguous).is_empty());
    VERIFY(Taskbar::application_file_for_executable("/bin/IdentityArguments"sv, observer->apps).is_empty());
    VERIFY(Taskbar::application_file_for_executable("/bin/IdentityTerminal"sv, observer->apps).is_empty());
    observer->set_window_type(GUI::WindowType::Taskbar);
    observer->set_rect(900, 100, 4, 4);
    observer->show();
    observer->make_window_manager(WindowServer::WMEventMask::WindowStateChanges | WindowServer::WMEventMask::WindowRemovals);
    auto second = GUI::Window::construct();
    second->set_title("Same title, different identities");
    second->set_rect(450, 150, 320, 160);
    second->set_main_widget<GUI::Widget>()->set_fill_with_background_color(true);
    second->show();
    int step = 0;
    int ticks = 0;
    WindowIdentifier removed;
    auto timer = Core::Timer::create_repeating(1500, [&] {
        VERIFY(++ticks < 25);
        auto& list = WindowList::the();
        Vector<::Window*> own;
        ::Window* calculator = nullptr;
        ::Window* unknown = nullptr;
        list.for_each_window([&](auto& window) {
            if (window.application_file() == "/res/apps/TestApplicationIdentity.af"sv)
                own.append(&window);
            if (window.application_file() == "/res/apps/Calculator.af"sv)
                calculator = &window;
            if (window.application_file().is_empty() && window.title() == "Same title, different identities"sv)
                unknown = &window;
        });
        dbgln("IDENTITY TEST step={} own={} calculator={} unknown={}", step, own.size(), calculator != nullptr, unknown != nullptr);
        auto own_window = [&](int window_id) -> ::Window& {
            for (auto* window : own) {
                if (window->identifier().window_id() == window_id)
                    return *window;
            }
            VERIFY_NOT_REACHED();
        };
        switch (step) {
        case 0: {
            VERIFY(own.size() == 2);
            size_t grouped = 0;
            list.for_each_window_in_application(*own[0], [&](auto&) { ++grouped; });
            VERIFY(grouped == 2);
            first->set_minimized(true);
            break;
        }
        case 1:
            VERIFY(own_window(first->window_id()).is_minimized());
            activate(own_window(first->window_id()));
            break;
        case 2:
            VERIFY(!own_window(first->window_id()).is_minimized());
            VERIFY(own_window(first->window_id()).is_active());
            activate(own_window(second->window_id()));
            break;
        case 3:
            VERIFY(own_window(second->window_id()).is_active());
            removed = own_window(second->window_id()).identifier();
            second->hide();
            break;
        case 4:
            VERIFY(!list.window(removed));
            VERIFY(own.size() == 1);
            second->show();
            break;
        case 5:
            VERIFY(own.size() == 2);
            MUST(Core::Process::spawn("/bin/Calculator"sv));
            MUST(Core::Process::spawn("/bin/TestApplicationIdentity"sv, Array { "helper"sv }));
            MUST(Core::Process::spawn("/bin/UnknownApplicationIdentity"sv, Array { "helper"sv }));
            break;
        case 6: {
            if (!calculator || !unknown || own.size() != 3)
                return;
            VERIFY(own.size() == 3);
            {
                size_t grouped = 0;
                list.for_each_window_in_application(*own[0], [&](auto&) { ++grouped; });
                VERIFY(grouped == 3);
            }
            size_t grouped = 0;
            list.for_each_window_in_application(*unknown, [&](auto&) { ++grouped; });
            VERIFY(grouped == 1);
            activate(*unknown);
            break;
        }
        case 7:
            VERIFY(unknown && unknown->is_active());
            VERIFY(calculator && calculator->application_file() != own[0]->application_file());
            dbgln("APPLICATION IDENTITY PASS: multi-window, distinct apps, unknown helper, minimize/restore, per-window activation, close/reopen, ambiguous metadata");
            break;
        case 18:
            app->quit();
            break;
        }
        ++step;
    });
    timer->start();
    return app->exec();
}
