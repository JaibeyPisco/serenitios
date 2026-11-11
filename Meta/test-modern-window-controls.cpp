/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

// Guest-only fixture: geometry assertions and native WindowServer interactions.
#include <LibCore/Timer.h>
#include <LibGUI/Action.h>
#include <LibGUI/Application.h>
#include <LibGUI/BoxLayout.h>
#include <LibGUI/Button.h>
#include <LibGUI/Menu.h>
#include <LibGUI/MessageBox.h>
#include <LibGUI/Window.h>
#include <LibGfx/Bitmap.h>
#include <LibGfx/ClassicWindowTheme.h>
#include <LibGfx/Painter.h>
#include <LibGfx/Palette.h>
#include <LibMain/Main.h>

static Gfx::Palette load_palette(char const* path)
{
    return Gfx::Palette(Gfx::PaletteImpl::create_with_anonymous_buffer(MUST(Gfx::load_system_theme(path))));
}

static void geometry()
{
    static_assert(to_underlying(Gfx::WindowThemeProvider::Classic) == 0);
    static_assert(to_underlying(Gfx::WindowThemeProvider::RedmondGlass) == 1);
    static_assert(to_underlying(Gfx::WindowThemeProvider::RedmondPlastic) == 2);
    static_assert(sizeof(Gfx::SystemTheme) == 2756);
    Gfx::ClassicWindowTheme theme;
    auto standard = load_palette("/res/themes/Default.ini");
    VERIFY(!Gfx::ClassicWindowTheme::uses_modern_controls(standard));
    auto classic = theme.layout_buttons(Gfx::WindowTheme::WindowType::Normal, Gfx::WindowTheme::WindowMode::Other, { 0, 0, 640, 300 }, standard, 3);
    VERIFY(classic[0] == Gfx::IntRect(616, 5, 24, 22));
    VERIFY(classic[1] == Gfx::IntRect(592, 5, 24, 22));
    VERIFY(classic[2] == Gfx::IntRect(568, 5, 24, 22));

    size_t cases = 0;
    for (auto path : { "/res/themes/Modern Dark.ini", "/res/themes/Modern Light.ini" }) {
        auto palette = load_palette(path);
        VERIFY(Gfx::ClassicWindowTheme::uses_modern_controls(palette));
        for (int width : { 94, 108, 160, 640 }) {
            for (size_t count = 0; count <= 3; ++count) {
                for (auto mode : { Gfx::WindowTheme::WindowMode::Other, Gfx::WindowTheme::WindowMode::RenderAbove }) {
                    Gfx::IntRect window { 0, 0, width, 300 };
                    auto title = theme.titlebar_rect(Gfx::WindowTheme::WindowType::Normal, mode, window, palette);
                    auto buttons = theme.layout_buttons(Gfx::WindowTheme::WindowType::Normal, mode, window, palette, count);
                    Gfx::IntRect extent;
                    for (size_t i = 0; i < buttons.size(); ++i) {
                        VERIFY(title.contains(buttons[i]));
                        VERIFY(buttons[i].size() == Gfx::IntSize(24, 22));
                        if (i)
                            VERIFY(buttons[i - 1].right() <= buttons[i].left());
                        extent = extent.united(buttons[i]);
                    }
                    auto text = Gfx::ClassicWindowTheme::modern_title_text_rect(title, extent);
                    VERIFY(text.width() >= 0);
                    if (!text.is_empty() && !extent.is_empty())
                        VERIFY(text.left() > extent.right());
                    ++cases;
                }
            }
        }
        auto bitmap = MUST(Gfx::Bitmap::create(Gfx::BitmapFormat::BGRA8888, { 100, 40 }));
        Gfx::Painter painter(*bitmap);
        painter.clear_rect(bitmap->rect(), Gfx::Color::Transparent);
        for (int i = 0; i < 3; ++i) {
            Gfx::IntRect hit { i * 30, 0, 24, 22 };
            Gfx::ClassicWindowTheme::paint_modern_control(painter, hit, palette, static_cast<Gfx::ClassicWindowTheme::Control>(i), Gfx::WindowTheme::WindowState::Active);
            VERIFY(bitmap->get_pixel(hit.top_left()).alpha() == 0);
            VERIFY(bitmap->get_pixel(hit.center()).alpha() == 255);
        }
        VERIFY(bitmap->get_pixel(12, 11) == Gfx::Color(240, 86, 80));
        VERIFY(bitmap->get_pixel(42, 11) == Gfx::Color(242, 189, 65));
        VERIFY(bitmap->get_pixel(72, 11) == Gfx::Color(52, 195, 90));
    }
    dbgln("WINDOW GEOMETRY PASS: {} layouts; Classic golden layout, ABI, clipping, hit targets and colors", cases);
}

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    auto app = TRY(GUI::Application::create(arguments));
    if (arguments.strings.size() > 1 && arguments.strings[1] == "geometry"sv) {
        geometry();
        return 0;
    }
    auto window = GUI::Window::construct();
    window->set_title("Window controls - long title to verify safe elision in narrow windows");
    window->resize(500, 260);
    window->move_to(200, 200);
    auto widget = window->set_main_widget<GUI::Widget>();
    widget->set_fill_with_background_color(true);
    widget->set_layout<GUI::VerticalBoxLayout>();
    auto& modal = widget->add<GUI::Button>();
    modal.set_text("Open modal dialog"_string);
    modal.on_click = [&](auto) {
        dbgln("WINDOW MODAL OPEN");
        GUI::MessageBox::show(window, "Parent controls must remain blocked until this dialog closes."sv, "Modal controls"sv, GUI::MessageBox::Type::Information);
        dbgln("WINDOW MODAL CLOSED");
    };
    auto& narrow = widget->add<GUI::Button>();
    narrow.set_text("Narrow / normal"_string);
    narrow.on_click = [&](auto) { window->resize(window->rect().width() > 160 ? 108 : 500, 260); };
    auto& modified = widget->add<GUI::Button>();
    modified.set_text("Toggle modified marker"_string);
    modified.on_click = [&](auto) { window->set_modified(!window->is_modified()); };
    auto menu = window->add_menu("&View"_string);
    menu->add_action(GUI::CommonActions::make_fullscreen_action([&](auto&) { window->set_fullscreen(!window->is_fullscreen()); }, window));
    window->on_close_request = [] {
        dbgln("WINDOW CLOSE REQUEST");
        return GUI::Window::CloseRequestDecision::Close;
    };
    bool active = false;
    window->on_active_window_change = [&](bool value) { active = value; };
    ByteString previous;
    auto timer = Core::Timer::create_repeating(250, [&] {
        auto state = ByteString::formatted("rect={} maximized={} fullscreen={} active={}", window->rect(), window->is_maximized(), window->is_fullscreen(), active);
        if (state != previous) {
            dbgln("WINDOW STATE: {}", state);
            previous = move(state);
        }
    });
    timer->start();
    window->show();
    dbgln("WINDOW FIXTURE READY");
    return app->exec();
}
