/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <LibCore/StandardPaths.h>
#include <LibDesktop/AppFile.h>
#include <LibGUI/BoxLayout.h>
#include <LibGUI/Button.h>
#include <LibGUI/Desktop.h>
#include <LibGUI/Frame.h>
#include <LibGUI/IconView.h>
#include <LibGUI/ItemListModel.h>
#include <LibGUI/Label.h>
#include <LibGUI/ListView.h>
#include <LibGUI/Menu.h>
#include <LibGUI/MessageBox.h>
#include <LibGUI/Process.h>
#include <LibGUI/StackWidget.h>
#include <LibGUI/TextBox.h>
#include <LibGUI/Window.h>
#include <LibGfx/Palette.h>
#include <errno.h>
#include <string.h>
#include <unistd.h>

// A filtered view of Taskbar's existing discovery results, not a second registry.
class LauncherAppsModel final : public GUI::Model {
public:
    explicit LauncherAppsModel(Vector<NonnullRefPtr<Desktop::AppFile>> const& apps)
        : m_apps(apps)
    {
        filter({}, {});
    }
    void filter(StringView category, StringView query)
    {
        m_rows.clear();
        query = query.trim_whitespace();
        for (size_t i = 0; i < m_apps.size(); ++i) {
            auto& app = m_apps[i];
            if (!category.is_empty() && app->category() != category)
                continue;
            if (!query.is_empty() && !app->name().contains(query, CaseSensitivity::CaseInsensitive))
                continue;
            m_rows.append(i);
        }
        did_update(InvalidateAllIndices);
    }
    Desktop::AppFile const& app_at(GUI::ModelIndex const& index) const
    {
        VERIFY(index.model() == this && index.row() >= 0 && index.row() < row_count({}));
        return m_apps[m_rows[index.row()]];
    }
    virtual int row_count(GUI::ModelIndex const& parent) const override { return parent.is_valid() ? 0 : m_rows.size(); }
    virtual int column_count(GUI::ModelIndex const&) const override { return 1; }
    virtual GUI::ModelIndex index(int row, int column, GUI::ModelIndex const& parent = {}) const override
    {
        if (parent.is_valid() || row < 0 || row >= row_count({}) || column != 0)
            return {};
        return create_index(row, column);
    }
    virtual ErrorOr<String> column_name(int) const override { return "Applications"_string; }
    virtual GUI::Variant data(GUI::ModelIndex const& index, GUI::ModelRole role) const override
    {
        auto& app = app_at(index);
        if (role == GUI::ModelRole::Icon)
            return app.icon();
        if (role == GUI::ModelRole::Display)
            return app.name();
        return {};
    }

private:
    Vector<NonnullRefPtr<Desktop::AppFile>> const& m_apps;
    Vector<size_t> m_rows;
};

class ApplicationLauncher final : public GUI::Window {
    C_OBJECT(ApplicationLauncher);

public:
    void toggle(Gfx::IntRect const& anchor)
    {
        if (is_visible()) {
            hide();
            return;
        }
        auto screen = GUI::Desktop::the().rect();
        for (auto const& rect : GUI::Desktop::the().rects()) {
            if (rect.contains(anchor.center())) {
                screen = rect;
                break;
            }
        }
        auto size = Gfx::IntSize { min(640, screen.width() - 16), min(480, screen.height() - Gfx::DesktopGeometry::top_panel_height - Gfx::DesktopGeometry::bottom_panel_height - 8) };
        resize(size);
        move_to(clamp(anchor.x(), screen.left() + 8, screen.right() - size.width() - 7), clamp(anchor.bottom() + 5, screen.top() + Gfx::DesktopGeometry::top_panel_height + 4, screen.bottom() - Gfx::DesktopGeometry::bottom_panel_height - size.height()));
        m_sidebar->set_fixed_width(size.width() < 540 ? 120 : 156);
        m_view->set_modern_presentation(main_widget()->palette().window_theme_provider(Gfx::WindowThemeRole::WindowTheme) == Gfx::WindowThemeProvider::Modern);
        m_search->set_text({});
        m_sidebar->set_cursor(m_category_model->index(0, 0), GUI::AbstractView::SelectionUpdate::Set);
        refresh();
        show();
        m_search->set_focus(true);
    }

private:
    ApplicationLauncher(Vector<NonnullRefPtr<Desktop::AppFile>> const& apps, Vector<ByteString> const& categories, GUI::Menu& system_menu)
        : m_model(adopt_ref(*new LauncherAppsModel(apps)))
    {
        set_window_type(GUI::WindowType::Popup);
        set_forced_shadow(true);
        set_title("Applications");
        on_close_request = [this] { hide(); return CloseRequestDecision::StayOpen; };
        m_categories.append({});
        m_category_names.append("All applications"_string);
        for (auto const& category : categories) {
            m_categories.append(category);
            m_category_names.append(MUST(String::from_byte_string(category.replace("&"sv, ""sv))));
        }
        m_category_model = GUI::ItemListModel<String>::create(m_category_names);
        auto root = set_main_widget<GUI::Frame>();
        root->set_frame_style(Gfx::FrameStyle::Window);
        root->set_fill_with_background_color(true);
        root->set_layout<GUI::VerticalBoxLayout>(12, 8);
        auto& top = root->add<GUI::Widget>();
        top.set_fixed_height(28);
        top.set_layout<GUI::HorizontalBoxLayout>(0, 8);
        auto& title = top.add<GUI::Label>("Applications"_string);
        title.set_fixed_width(108);
        title.set_font(title.font().bold_variant());
        title.set_text_alignment(Gfx::TextAlignment::CenterLeft);
        m_search = top.add<GUI::TextBox>();
        m_search->set_placeholder("Search applications..."sv);
        auto& body = root->add<GUI::Widget>();
        body.set_layout<GUI::HorizontalBoxLayout>(0, 8);
        m_sidebar = body.add<GUI::ListView>();
        m_sidebar->set_model(m_category_model);
        m_sidebar->set_item_height(28);
        m_sidebar->set_horizontal_padding(6);
        m_sidebar->set_alternating_row_colors(false);
        m_sidebar->set_background_role(Gfx::ColorRole::Window);
        m_sidebar->set_should_hide_unnecessary_scrollbars(true);
        m_stack = body.add<GUI::StackWidget>();
        m_view = m_stack->add<GUI::IconView>();
        m_view->set_model(m_model);
        m_view->set_selection_mode(GUI::AbstractView::SelectionMode::SingleSelection);
        m_view->set_should_hide_unnecessary_scrollbars(true);
        m_view->set_always_wrap_item_labels(true);
        m_empty = m_stack->add<GUI::Label>("No matching applications."_string);
        m_empty->set_text_wrapping(Gfx::TextWrapping::Wrap);
        m_status = root->add<GUI::Label>();
        m_status->set_fixed_height(18);
        m_status->set_text_alignment(Gfx::TextAlignment::CenterLeft);
        auto& bottom = root->add<GUI::Widget>();
        bottom.set_fixed_height(28);
        bottom.set_layout<GUI::HorizontalBoxLayout>(0, 6);
        auto shortcut = [&](String const& label, StringView executable) {
            auto& button = bottom.add<GUI::Button>(label);
            button.on_click = [this, executable](auto) {
                hide();
                GUI::Process::spawn_or_show_error(this, executable, ReadonlySpan<StringView> {}, Core::StandardPaths::home_directory());
            };
        };
        shortcut("Settings"_string, "/bin/Settings"sv);
        shortcut("Files"_string, "/bin/FileManager"sv);
        shortcut("Run..."_string, "/bin/Run"sv);
        auto& system = bottom.add<GUI::Button>("System"_string);
        system.set_menu(&system_menu);
        system.set_tooltip("Themes, help and power actions"_string);
        m_search->on_change = [this] { refresh(); };
        m_sidebar->on_selection_change = [this] {
            if (!m_search->text().is_empty())
                m_search->set_text({});
            else
                refresh();
        };
        auto select_result = [this] {
            if (!m_model->row_count({}))
                return;
            m_view->set_cursor(m_model->index(0, 0), GUI::AbstractView::SelectionUpdate::Set);
            m_view->set_focus(true);
        };
        m_search->on_down_pressed = move(select_result);
        m_search->on_return_pressed = [this] { activate(m_model->index(0, 0)); };
        m_view->on_activation = [this](auto& index) { activate(index); };
        m_view->on_selection_change = [this] {
            auto index = m_view->selection().first();
            if (index.is_valid()) {
                auto& app = m_model->app_at(index);
                auto text = MUST(String::formatted("{} — {}", app.name(), app.description()));
                m_status->set_text(text);
                m_status->set_tooltip(text);
            }
        };
        m_close_action = GUI::Action::create("Close launcher", { Key_Escape }, [this](auto&) { hide(); }, this);
        m_find_action = GUI::Action::create("Find application", { Mod_Ctrl, Key_F }, [this](auto&) { m_search->set_focus(true); m_search->select_all(); }, this);
    }
    void refresh()
    {
        auto selected = m_sidebar->selection().first();
        if (!selected.is_valid())
            return;
        m_model->filter(m_categories[selected.row()], m_search->text());
        auto count = m_model->row_count({});
        m_stack->set_active_widget(count ? static_cast<GUI::Widget*>(m_view.ptr()) : m_empty.ptr());
        m_status->set_tooltip({});
        m_status->set_text(MUST(String::formatted("{} application{}", count, count == 1 ? "" : "s")));
    }
    void activate(GUI::ModelIndex const& index)
    {
        if (!index.is_valid())
            return;
        // AppFile preserves arguments, working directory, terminal and escalation.
        auto& app = m_model->app_at(index);
        auto executable = app.executable();
        // Serenity's spawn may return before exec failure is reported. Catch a
        // removed/non-executable entry here without changing the process API.
        if (access(executable.characters(), X_OK) < 0) {
            auto saved_errno = errno;
            hide();
            GUI::MessageBox::show_error(nullptr, ByteString::formatted("Cannot open {} ({}): {}", app.name(), executable, strerror(saved_errno)));
            return;
        }
        hide();
        if (auto result = app.spawn_with_escalation(); result.is_error())
            GUI::MessageBox::show_error(nullptr, ByteString::formatted("Cannot open {} ({}): {}", app.name(), executable, result.error()));
    }
    RefPtr<GUI::Action> m_close_action;
    RefPtr<GUI::Action> m_find_action;
    NonnullRefPtr<LauncherAppsModel> m_model;
    Vector<ByteString> m_categories;
    Vector<String> m_category_names;
    RefPtr<GUI::ItemListModel<String>> m_category_model;
    RefPtr<GUI::ListView> m_sidebar;
    RefPtr<GUI::TextBox> m_search;
    RefPtr<GUI::IconView> m_view;
    RefPtr<GUI::StackWidget> m_stack;
    RefPtr<GUI::Label> m_empty;
    RefPtr<GUI::Label> m_status;
};
