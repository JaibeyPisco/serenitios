/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

// Guest-only rendering/interaction fixture, not a new desktop application.
#include <LibGUI/Application.h>
#include <LibGUI/BoxLayout.h>
#include <LibGUI/Button.h>
#include <LibGUI/CheckBox.h>
#include <LibGUI/ItemListModel.h>
#include <LibGUI/Label.h>
#include <LibGUI/ListView.h>
#include <LibGUI/TabWidget.h>
#include <LibGUI/TextBox.h>
#include <LibGUI/Window.h>
#include <LibGfx/Bitmap.h>
#include <LibGfx/DesignMetrics.h>
#include <LibGfx/Painter.h>
#include <LibGfx/Palette.h>
#include <LibGfx/SerenityStylePainter.h>
#include <LibMain/Main.h>

static void render_states(bool verify)
{
    for (auto path : { "/res/themes/Default.ini", "/res/themes/Modern Dark.ini", "/res/themes/Modern Light.ini" }) {
        auto palette = Gfx::Palette(Gfx::PaletteImpl::create_with_anonymous_buffer(MUST(Gfx::load_system_theme(path))));
        auto bitmap = MUST(Gfx::Bitmap::create(Gfx::BitmapFormat::BGRA8888, { 192, 240 }));
        Gfx::Painter painter(*bitmap);
        painter.fill_rect(bitmap->rect(), palette.window());
        Gfx::SerenityStylePainter style;
        for (int state = 0; state < 6; ++state) {
            bool enabled = state != 4;
            bool checked = state == 5;
            style.paint_button(painter, { 4, state * 38 + 4, 88, 28 }, palette, Gfx::ButtonStyle::Normal, state == 2, state == 1, checked, enabled, state == 3, false);
            style.paint_check_box(painter, { 100, state * 38 + 8, 18, 18 }, palette, enabled, checked || !enabled, state == 2);
            style.paint_tab_button(painter, { 124, state * 38 + 4, 64, 28 }, palette, checked, state == 1, enabled, Gfx::TabPosition::Top, true, true);
        }
        u64 hash = 14695981039346656037ull;
        for (int y = 0; y < bitmap->height(); ++y) {
            for (int x = 0; x < bitmap->width(); ++x) {
                auto color = bitmap->get_pixel(x, y);
                for (auto value : { color.red(), color.green(), color.blue(), color.alpha() }) {
                    hash ^= value;
                    hash *= 1099511628211ull;
                }
            }
        }
        dbgln("CONTROL RENDER: {} hash={}", path, hash);
        if (verify && palette.window_theme_provider(Gfx::WindowThemeRole::WindowTheme) == Gfx::WindowThemeProvider::Modern) {
            VERIFY(bitmap->get_pixel(4, 4) == palette.window());
            VERIFY(bitmap->get_pixel(4, 3 * 38 + 14) == palette.focus_outline());
            VERIFY(bitmap->get_pixel(4, 4 * 38 + 14) == palette.disabled_text_front());
        }
    }
    dbgln("CONTROL RENDER PASS");
}

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    auto app = TRY(GUI::Application::create(arguments));
    if (arguments.strings.size() > 1 && (arguments.strings[1] == "render"sv || arguments.strings[1] == "render-baseline"sv)) {
        render_states(arguments.strings[1] == "render"sv);
        return 0;
    }
    auto window = GUI::Window::construct();
    window->set_title("Modern controls: Café, configuración and long labels");
    window->resize(600, 440);
    window->move_to(180, 180);
    auto root = window->set_main_widget<GUI::Widget>();
    root->set_fill_with_background_color(true);
    root->set_layout<GUI::VerticalBoxLayout>(GUI::Margins { 12 }, 8);
    auto& tabs = root->add<GUI::TabWidget>();
    auto& page = tabs.add_tab<GUI::Widget>("Controls"_string);
    page.set_layout<GUI::VerticalBoxLayout>(GUI::Margins { 8 }, 8);
    auto& row = page.add<GUI::Widget>();
    row.set_fixed_height(Gfx::DesignMetrics::standard_control_height);
    row.set_layout<GUI::HorizontalBoxLayout>(GUI::Margins {}, 8);
    auto& normal = row.add<GUI::Button>();
    normal.set_text("Normal action"_string);
    auto& disabled = row.add<GUI::Button>();
    disabled.set_text("Disabled action"_string);
    disabled.set_enabled(false);
    disabled.on_click = [](auto) { VERIFY_NOT_REACHED(); };
    auto& compact = page.add<GUI::Button>();
    compact.set_text("Compact control (28 px)"_string);
    compact.set_fixed_height(Gfx::DesignMetrics::control_height);
    auto& checkbox = page.add<GUI::CheckBox>("Enable the real disabled action"_string);
    checkbox.set_fixed_height(24);
    auto& disabled_check = page.add<GUI::CheckBox>("Disabled and checked: Café, acción"_string);
    disabled_check.set_fixed_height(24);
    disabled_check.set_checked(true);
    disabled_check.set_enabled(false);
    auto& field = page.add<GUI::TextBox>();
    field.set_fixed_height(28);
    field.set_text("Café, configuración, filename with spaces.txt"sv);
    auto& disabled_field = page.add<GUI::TextBox>();
    disabled_field.set_fixed_height(28);
    disabled_field.set_text("Disabled input"sv);
    disabled_field.set_enabled(false);
    Vector<String> entries { "First item"_string, "Café / acción"_string, "A very long list item that must elide without changing the row height"_string };
    auto& list = page.add<GUI::ListView>();
    list.set_model(GUI::ItemListModel<String>::create(entries));
    auto& status = page.add<GUI::Label>("Ready"_string);
    status.set_fixed_height(22);
    int clicks = 0;
    auto action = [&](auto) {
        ++clicks;
        status.set_text(MUST(String::formatted("Action count: {}", clicks)));
        dbgln("CONTROL ACTION: count={}", clicks);
    };
    normal.on_click = [&](auto modifiers) { action(modifiers); };
    compact.on_click = [&](auto modifiers) { action(modifiers); };
    disabled.on_click = [&](auto modifiers) { VERIFY(disabled.is_enabled()); action(modifiers); };
    checkbox.on_checked = [&](bool checked) {
        disabled.set_enabled(checked);
        dbgln("CONTROL CHECK: {}", checked);
    };
    field.on_change = [&] { dbgln("CONTROL TEXT: {}", field.text()); };
    list.on_selection_change = [&] { dbgln("CONTROL LIST: selection changed"); };
    auto& long_page = tabs.add_tab<GUI::Widget>("Long title: configuración, navegación and more"_string);
    long_page.set_layout<GUI::VerticalBoxLayout>();
    long_page.add<GUI::Label>("Real second tab; use arrows or mouse to return."_string);
    window->on_close = [] { dbgln("CONTROL FIXTURE CLOSED"); };
    window->show();
    dbgln("CONTROL FIXTURE READY");
    return app->exec();
}
