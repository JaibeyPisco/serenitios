/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include "ApplicationIdentity.h"
#include "QuickLaunchWidget.h"
#include "WindowList.h"
#include <AK/QuickSort.h>
#include <LibCore/MimeData.h>
#include <LibGUI/Action.h>
#include <LibGUI/BoxLayout.h>
#include <LibGUI/ConnectionToWindowManagerServer.h>
#include <LibGUI/Menu.h>
#include <LibGUI/MessageBox.h>
#include <LibGUI/Painter.h>

namespace Taskbar {

class DockButton final : public GUI::Button {
    C_OBJECT(DockButton);
public:
    size_t window_count { 0 };
    bool active { false };
    Optional<int> progress;
    Vector<WindowIdentifier> windows;
    Function<void(Gfx::IntPoint)> on_window_menu;
    Function<void(GUI::DropEvent&)> on_pin_drop;

private:
    DockButton() = default;
    virtual void drag_enter_event(GUI::DragEvent& event) override
    {
        if (event.mime_data().has_urls())
            event.accept();
    }
    virtual void drop_event(GUI::DropEvent& event) override
    {
        if (on_pin_drop)
            on_pin_drop(event);
    }
    virtual void context_menu_event(GUI::ContextMenuEvent& event) override
    {
        if (on_window_menu)
            on_window_menu(event.screen_position());
    }
    virtual void paint_event(GUI::PaintEvent& event) override
    {
        GUI::Painter painter(*this);
        painter.add_clip_rect(event.rect());
        if (active || is_being_pressed() || is_hovered())
            painter.fill_rect_with_rounded_corners(rect(), active || is_being_pressed() ? palette().inactive_selection() : palette().hover_highlight(), 7);
        if (icon()) {
            auto location = Gfx::IntRect { 0, 0, width(), 36 }.center() - icon()->rect().center();
            painter.blit(location, *icon(), icon()->rect());
        }
        auto dots = min(window_count, static_cast<size_t>(3));
        for (size_t i = 0; i < dots; ++i)
            painter.fill_rect_with_rounded_corners({ width() / 2 - static_cast<int>(dots * 3) + static_cast<int>(i * 6), 39, 4, 3 }, active ? palette().accent() : palette().window_text(), 1);
        if (progress.has_value())
            painter.fill_rect({ 4, 43, (width() - 8) * clamp(progress.value(), 0, 100) / 100, 1 }, palette().accent());
        if (is_focused())
            painter.draw_rect(rect().shrunken(2, 2), palette().focus_outline());
    }
};

// A view of QuickLaunch + WindowList, not an additional window/process model.
class DockWidget final : public GUI::Widget {
    C_OBJECT(DockWidget);
public:
    Function<void(int)> on_width_change;

    void refresh(int available_width, unsigned row, unsigned column)
    {
        m_available_width = available_width;
        m_row = row;
        m_column = column;
        struct Entry {
            ByteString path;
            ByteString application;
            ByteString name;
            RefPtr<Gfx::Bitmap const> icon;
            Vector<WindowIdentifier> windows;
        };
        Vector<Entry> entries;
        for (auto const& pin : m_quick_launch.entries()) {
            auto path = pin->path();
            auto executable = path;
            if (path.ends_with(".af"sv)) {
                executable = {};
                for (auto const& app : m_apps) {
                    if (app->filename() == path) {
                        executable = app->executable();
                        break;
                    }
                }
            }
            auto application = application_file_for_executable(executable, m_apps);
            if (path.ends_with(".af"sv) && application != path)
                application = {};
            entries.append({ path, application, pin->name(), pin->icon().bitmap_for_size(32), {} });
        }
        Vector<::Window*> windows;
        WindowList::the().for_each_window([&](auto& window) {
            if (window.button() && window.workspace_row() == row && window.workspace_column() == column)
                windows.append(&window);
        });
        quick_sort(windows, [](auto* a, auto* b) {
            auto left = a->identifier();
            auto right = b->identifier();
            return left.client_id() < right.client_id() || (left.client_id() == right.client_id() && left.window_id() < right.window_id());
        });
        for (auto* window : windows) {
            Entry* matching = nullptr;
            if (!window->application_file().is_empty()) {
                for (auto& entry : entries) {
                    if (entry.application == window->application_file()) {
                        matching = &entry;
                        break;
                    }
                }
            }
            if (!matching) {
                RefPtr<Gfx::Bitmap const> icon;
                if (window->icon() && window->icon()->width() == 32 && window->icon()->height() == 32)
                    icon = window->icon();
                for (auto const& app : m_apps) {
                    if (app->filename() == window->application_file()) {
                        icon = app->icon().bitmap_for_size(32);
                        break;
                    }
                }
                entries.append({ {}, window->application_file(), window->title(), icon, {} });
                matching = &entries.last();
            }
            matching->windows.append(window->identifier());
        }

        auto maximum = max(164, available_width);
        auto capacity = max(1, (maximum - 60) / 48);
        bool overflow = entries.size() > static_cast<size_t>(capacity);
        if (overflow)
            capacity = max(1, (maximum - 116) / 48);
        auto pages = max(1, (static_cast<int>(entries.size()) + capacity - 1) / capacity);
        m_page = clamp(m_page, 0, pages - 1);
        auto begin = m_page * capacity;
        auto end = min(begin + capacity, static_cast<int>(entries.size()));
        m_buttons.clear();
        remove_all_children();
        if (overflow)
            add_page_button("<"_string, "Previous dock page"_string, m_page > 0, -1);
        for (int i = begin; i < end; ++i) {
            auto& entry = entries[i];
            auto& button = add<DockButton>();
            m_buttons.append(button);
            button.set_fixed_size(44, 44);
            button.set_icon(entry.icon ? entry.icon : m_fallback_icon);
            button.windows = entry.windows;
            button.window_count = entry.windows.size();
            auto tooltip = entry.name;
            if (entry.windows.size() > 1)
                tooltip = ByteString::formatted("{} ({} windows)", entry.name, entry.windows.size());
            button.set_tooltip(String::from_byte_string(tooltip).release_value_but_fixme_should_propagate_errors());
            for (auto id : entry.windows) {
                if (auto* window = WindowList::the().window(id)) {
                    button.active |= window->is_active();
                    if (window->progress().has_value())
                        button.progress = max(button.progress.value_or(0), window->progress().value());
                }
            }
            button.on_click = [this, ids = entry.windows, path = entry.path, &button](auto) {
                if (ids.is_empty())
                    launch(path);
                else if (ids.size() == 1)
                    toggle(ids[0]);
                else
                    show_windows(ids, path, button.screen_relative_rect().location(), false);
            };
            button.on_window_menu = [this, ids = entry.windows, path = entry.path](auto position) { show_windows(ids, path, position, true); };
            button.on_pin_drop = [this](auto& event) { m_quick_launch.dispatch_event(event); };
        }
        if (overflow)
            add_page_button(">"_string, "Next dock page"_string, m_page + 1 < pages, 1);
        auto& desktop = add<GUI::Button>();
        desktop.set_fixed_size(44, 44);
        desktop.set_icon(m_desktop_icon);
        desktop.set_tooltip("Show Desktop (Super+D)"_string);
        desktop.set_button_style(Gfx::ButtonStyle::Coolbar);
        desktop.on_click = [](auto) { GUI::ConnectionToWindowManagerServer::the().async_toggle_show_desktop(); };
        if (on_width_change)
            on_width_change(60 + (end - begin) * 48 + (overflow ? 56 : 0));
        do_layout();
    }

    void update_taskbar_rects()
    {
        WindowList::the().for_each_window([&](auto& window) {
            auto id = window.identifier();
            auto rect = window.workspace_row() == m_row && window.workspace_column() == m_column ? screen_relative_rect() : Gfx::IntRect {};
            GUI::ConnectionToWindowManagerServer::the().async_set_window_taskbar_rect(id.client_id(), id.window_id(), rect);
        });
        for (auto const& button : m_buttons) {
            for (auto id : button->windows)
                GUI::ConnectionToWindowManagerServer::the().async_set_window_taskbar_rect(id.client_id(), id.window_id(), button->screen_relative_rect());
        }
    }

private:
    virtual void drag_enter_event(GUI::DragEvent& event) override { m_quick_launch.dispatch_event(event); }
    virtual void drop_event(GUI::DropEvent& event) override { m_quick_launch.dispatch_event(event); }
    DockWidget(QuickLaunchWidget& quick_launch, Vector<NonnullRefPtr<Desktop::AppFile>> const& apps)
        : m_quick_launch(quick_launch)
        , m_apps(apps)
        , m_fallback_icon(GUI::Icon::default_icon("window"sv).bitmap_for_size(32))
        , m_desktop_icon(GUI::Icon::default_icon("desktop"sv).bitmap_for_size(32))
    {
        set_layout<GUI::HorizontalBoxLayout>(GUI::Margins { 6, 8 }, 4);
    }
    void add_page_button(String text, String tooltip, bool enabled, int delta)
    {
        auto& button = add<GUI::Button>(move(text));
        button.set_fixed_size(24, 44);
        button.set_tooltip(move(tooltip));
        button.set_enabled(enabled);
        button.set_button_style(Gfx::ButtonStyle::Coolbar);
        button.on_click = [this, delta](auto) {
            m_page += delta;
            // Button callbacks must finish before replacing the current page.
            deferred_invoke([this] { refresh(m_available_width, m_row, m_column); });
        };
    }
    void launch(ByteString const& path)
    {
        auto entry = QuickLaunchEntry::create_from_path(path);
        if (!entry) {
            GUI::MessageBox::show_error(window(), "Pinned application is unavailable."sv);
            return;
        }
        if (auto result = entry->launch(); result.is_error())
            GUI::MessageBox::show_error(window(), ByteString::formatted("Failed to launch: {}", result.error()));
    }
    static void toggle(WindowIdentifier id)
    {
        auto* window = WindowList::the().window(id);
        if (!window)
            return;
        if (window->is_minimized() || !window->is_active())
            GUI::ConnectionToWindowManagerServer::the().async_set_active_window(id.client_id(), id.window_id());
        else if (!window->is_blocked())
            GUI::ConnectionToWindowManagerServer::the().async_set_window_minimized(id.client_id(), id.window_id(), true);
    }
    void show_windows(Vector<WindowIdentifier> const& ids, ByteString const& path, Gfx::IntPoint position, bool context)
    {
        m_menu = GUI::Menu::construct();
        for (auto id : ids) {
            auto* window = WindowList::the().window(id);
            if (!window)
                continue;
            auto title = String::from_byte_string(window->title()).release_value_but_fixme_should_propagate_errors();
            auto activate = [id](auto&) {
                if (WindowList::the().window(id))
                    GUI::ConnectionToWindowManagerServer::the().async_set_active_window(id.client_id(), id.window_id());
            };
            if (!context) {
                m_menu->add_action(GUI::Action::create(window->title(), move(activate)));
                continue;
            }
            auto submenu = m_menu->add_submenu(move(title));
            submenu->add_action(GUI::Action::create("Activate", move(activate)));
            submenu->add_action(GUI::Action::create(window->is_minimized() ? "Restore" : "Minimize", [id](auto&) {
                if (auto* window = WindowList::the().window(id))
                    GUI::ConnectionToWindowManagerServer::the().async_set_window_minimized(id.client_id(), id.window_id(), !window->is_minimized());
            }));
            submenu->add_action(GUI::Action::create("Window menu", [id, position](auto&) {
                if (WindowList::the().window(id))
                    GUI::ConnectionToWindowManagerServer::the().async_popup_window_menu(id.client_id(), id.window_id(), position);
            }));
        }
        if (!path.is_empty()) {
            if (!ids.is_empty())
                m_menu->add_separator();
            m_menu->add_action(GUI::Action::create("New window", [this, path](auto&) { launch(path); }));
            if (context)
                m_menu->add_action(GUI::Action::create("Unpin", [this, path](auto&) { m_quick_launch.remove_pinned_path(path); }));
            if (context) {
                m_menu->add_action(GUI::Action::create("Move left", [this, path](auto&) { m_quick_launch.move_pinned_path(path, -1); }));
                m_menu->add_action(GUI::Action::create("Move right", [this, path](auto&) { m_quick_launch.move_pinned_path(path, 1); }));
            }
        }
        m_menu->popup(position);
    }
    QuickLaunchWidget& m_quick_launch;
    Vector<NonnullRefPtr<Desktop::AppFile>> const& m_apps;
    RefPtr<Gfx::Bitmap> m_fallback_icon;
    RefPtr<Gfx::Bitmap> m_desktop_icon;
    RefPtr<GUI::Menu> m_menu;
    Vector<NonnullRefPtr<DockButton>> m_buttons;
    int m_available_width { 640 };
    int m_page { 0 };
    unsigned m_row { 0 };
    unsigned m_column { 0 };
};

}
