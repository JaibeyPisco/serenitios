/*
 * Copyright (c) 2021, Andreas Kling <kling@serenityos.org>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include "SettingsAppsModel.h"
#include <LibCore/System.h>
#include <LibDesktop/AppFile.h>
#include <LibGUI/Application.h>
#include <LibGUI/BoxLayout.h>
#include <LibGUI/Button.h>
#include <LibGUI/Icon.h>
#include <LibGUI/ItemListModel.h>
#include <LibGUI/Label.h>
#include <LibGUI/ListView.h>
#include <LibGUI/Menu.h>
#include <LibGUI/Model.h>
#include <LibGUI/Process.h>
#include <LibGUI/StackWidget.h>
#include <LibGUI/Statusbar.h>
#include <LibGUI/TableView.h>
#include <LibGUI/TextBox.h>
#include <LibGUI/Window.h>
#include <LibGfx/DesignMetrics.h>
#include <LibGfx/Palette.h>
#include <LibMain/Main.h>
#include <stdio.h>
#include <unistd.h>

// Keep responsive/theme-specific geometry local to the launcher.
class SettingsWidget final : public GUI::Widget {
    C_OBJECT(SettingsWidget)
public:
    Function<void()> on_presentation_change;

private:
    SettingsWidget() = default;
    virtual void resize_event(GUI::ResizeEvent& event) override
    {
        GUI::Widget::resize_event(event);
        if (on_presentation_change)
            on_presentation_change();
    }
    virtual void theme_change_event(GUI::ThemeChangeEvent& event) override
    {
        GUI::Widget::theme_change_event(event);
        if (on_presentation_change)
            on_presentation_change();
    }
};

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    TRY(Core::System::pledge("stdio thread recvfd sendfd rpath cpath wpath unix proc exec"));

    auto app = TRY(GUI::Application::create(arguments));

    TRY(Core::System::pledge("stdio thread recvfd sendfd rpath cpath wpath proc exec"));

    auto app_icon = GUI::Icon::default_icon("app-settings"sv);

    auto model = adopt_ref(*new SettingsAppsModel);
    Vector<SettingsCategory> categories;
    Vector<String> category_names;
    for (auto category : { SettingsCategory::All, SettingsCategory::Appearance, SettingsCategory::Input, SettingsCategory::System, SettingsCategory::Applications }) {
        if (model->has_category(category)) {
            categories.append(category);
            category_names.append(MUST(String::from_utf8(settings_category_name(category))));
        }
    }
    auto category_model = GUI::ItemListModel<String>::create(category_names);
    auto window = GUI::Window::construct();
    window->set_title("Settings");
    window->resize(680, 430);

    auto file_menu = window->add_menu("&File"_string);
    file_menu->add_action(GUI::CommonActions::make_quit_action([&](auto&) {
        app->quit();
    }));

    auto view_menu = window->add_menu("&View"_string);
    view_menu->add_action(GUI::CommonActions::make_fullscreen_action([&](auto&) {
        window->set_fullscreen(!window->is_fullscreen());
    }));

    auto help_menu = window->add_menu("&Help"_string);
    help_menu->add_action(GUI::CommonActions::make_command_palette_action(window));
    help_menu->add_action(GUI::CommonActions::make_about_action("Settings"_string, app_icon, window));

    auto main_widget = window->set_main_widget<SettingsWidget>();
    main_widget->set_min_size(420, 300);
    main_widget->set_fill_with_background_color(true);
    main_widget->set_layout<GUI::VerticalBoxLayout>(8, 8);

    auto& search_row = main_widget->add<GUI::Widget>();
    search_row.set_fixed_height(Gfx::DesignMetrics::control_height);
    search_row.set_layout<GUI::HorizontalBoxLayout>(0, 8);
    auto& title = search_row.add<GUI::Label>("Settings"_string);
    title.set_font(title.font().bold_variant());
    title.set_text_alignment(Gfx::TextAlignment::CenterLeft);
    auto& search = search_row.add<GUI::TextBox>();
    auto& clear = search_row.add<GUI::Button>("Clear"_string);
    clear.set_fixed_width(52);

    auto& body = main_widget->add<GUI::Widget>();
    body.set_layout<GUI::HorizontalBoxLayout>(0, 8);
    auto& sidebar = body.add<GUI::ListView>();
    sidebar.set_model(category_model);
    sidebar.set_alternating_row_colors(false);
    sidebar.set_background_role(Gfx::ColorRole::Window);
    sidebar.set_horizontal_padding(8);
    sidebar.set_should_hide_unnecessary_scrollbars(true);

    auto& content = body.add<GUI::Widget>();
    content.set_layout<GUI::VerticalBoxLayout>(8, 6);
    content.set_fill_with_background_color(true);
    content.set_background_role(Gfx::ColorRole::Base);
    auto& heading = content.add<GUI::Label>();
    heading.set_fixed_height(24);
    heading.set_font(heading.font().bold_variant());
    heading.set_text_alignment(Gfx::TextAlignment::CenterLeft);
    auto& subtitle = content.add<GUI::Label>("Open a configuration application"_string);
    subtitle.set_fixed_height(18);
    subtitle.set_text_alignment(Gfx::TextAlignment::CenterLeft);
    subtitle.set_foreground_role(Gfx::ColorRole::PlaceholderText);

    auto& stack = content.add<GUI::StackWidget>();
    auto& view = stack.add<GUI::TableView>();
    view.set_horizontal_padding(4);
    view.set_model(model);
    view.set_column_headers_visible(false);
    view.set_selection_behavior(GUI::AbstractView::SelectionBehavior::SelectRows);
    view.set_selection_mode(GUI::AbstractView::SelectionMode::SingleSelection);
    view.set_alternating_row_colors(false);
    view.set_highlight_key_column(false);
    view.set_should_hide_unnecessary_scrollbars(true);
    auto& empty = stack.add<GUI::Label>("No matching settings in this category."_string);
    empty.set_text_wrapping(Gfx::TextWrapping::Wrap);
    auto& statusbar = main_widget->add<GUI::Statusbar>();

    auto update_status = [&] {
        auto index = view.selection().first();
        String message;
        if (index.is_valid()) {
            auto& selected_app = model->app_at(index);
            message = MUST(String::formatted("{}{}", selected_app.description(), selected_app.requires_root() ? " (authentication required)" : ""));
        } else {
            auto count = model->row_count({});
            message = MUST(String::formatted("{} setting{}", count, count == 1 ? "" : "s"));
        }
        statusbar.set_text(message);
        statusbar.set_tooltip(message);
    };

    auto update_presentation = [&] {
        bool modern = main_widget->palette().window_theme_provider(Gfx::WindowThemeRole::WindowTheme) == Gfx::WindowThemeProvider::Modern;
        bool narrow = main_widget->width() < 580;
        int sidebar_width = narrow ? 112 : 144;
        sidebar.set_fixed_width(sidebar_width);
        title.set_fixed_width(sidebar_width);
        sidebar.set_item_height(32);
        sidebar.set_frame_style(modern ? Gfx::FrameStyle::NoFrame : Gfx::FrameStyle::SunkenContainer);
        view.set_frame_style(modern ? Gfx::FrameStyle::NoFrame : Gfx::FrameStyle::SunkenContainer);
        view.set_vertical_padding(max(0, 32 - view.font().pixel_size_rounded_up()));
        view.set_column_visible(SettingsAppsModel::Description, !narrow);
        view.set_column_width(SettingsAppsModel::Icon, 24);
        view.set_column_width(SettingsAppsModel::Name, narrow ? max(100, main_widget->width() - sidebar_width - 96 - view.frame_thickness() * 2) : 128);
        view.set_column_width(SettingsAppsModel::Description, max(120, main_widget->width() - sidebar_width - 232 - view.frame_thickness() * 2));
    };
    auto refresh = [&] {
        auto selected = sidebar.selection().first();
        if (!selected.is_valid())
            return;
        auto category = categories[selected.row()];
        model->filter(category, search.text());
        heading.set_text(MUST(String::from_utf8(settings_category_name(category))));
        search.set_placeholder(ByteString::formatted("Search in {}...", settings_category_name(category)));
        clear.set_enabled(!search.text().is_empty());
        stack.set_active_widget(model->row_count({}) ? static_cast<GUI::Widget*>(&view) : &empty);
        update_presentation();
        update_status();
    };
    sidebar.on_selection_change = [&] {
        if (!search.text().is_empty())
            search.set_text({});
        else
            refresh();
    };
    search.on_change = [&] { refresh(); };
    search.on_escape_pressed = [&] { search.set_text({}); };
    clear.on_click = [&](auto) {
        search.set_text({});
        search.set_focus(true);
    };
    view.on_selection_change = [&] { update_status(); };
    auto activate = [&](GUI::ModelIndex const& index) {
        if (!index.is_valid())
            return;
        auto& selected_app = model->app_at(index);
        auto launch_rect = view.to_widget_rect(view.content_rect(index)).translated(view.screen_relative_rect().location());
        setenv("__libgui_launch_origin_rect", ByteString::formatted("{},{},{},{}", launch_rect.x(), launch_rect.y(), launch_rect.width(), launch_rect.height()).characters(), 1);
        auto arguments = selected_app.arguments();
        if (selected_app.requires_root()) {
            arguments.insert(0, selected_app.executable());
            GUI::Process::spawn_or_show_error(window, "/bin/Escalator"sv, arguments, selected_app.working_directory());
        } else {
            GUI::Process::spawn_or_show_error(window, selected_app.executable(), arguments, selected_app.working_directory());
        }
    };
    view.on_activation = [&](auto& index) { activate(index); };
    search.on_down_pressed = [&] {
        if (model->row_count({})) {
            view.set_cursor(model->index(0, SettingsAppsModel::Name), GUI::AbstractView::SelectionUpdate::Set);
            view.set_focus(true);
        }
    };
    search.on_return_pressed = [&] {
        if (model->row_count({}))
            activate(model->index(0, SettingsAppsModel::Name));
    };
    view_menu->add_action(GUI::Action::create("&Find setting", { Mod_Ctrl, Key_F }, [&](auto&) {
        search.set_focus(true);
        search.select_all(); }, window));
    main_widget->on_presentation_change = [&] { update_presentation(); };
    sidebar.set_cursor(category_model->index(0, 0), GUI::AbstractView::SelectionUpdate::Set);
    update_presentation();
    search.set_focus(true);

    window->set_icon(app_icon.bitmap_for_size(16));

    window->show();
    return app->exec();
}
