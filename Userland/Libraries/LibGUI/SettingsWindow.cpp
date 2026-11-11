/*
 * Copyright (c) 2020, Idan Horowitz <idan.horowitz@serenityos.org>
 * Copyright (c) 2021-2022, the SerenityOS developers.
 * Copyright (c) 2021, Andreas Kling <kling@serenityos.org>
 * Copyright (c) 2021-2022, Sam Atkins <atkinssj@serenityos.org>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibGUI/Application.h>
#include <LibGUI/BoxLayout.h>
#include <LibGUI/Button.h>
#include <LibGUI/MessageBox.h>
#include <LibGUI/SettingsWindow.h>
#include <LibGUI/Widget.h>
#include <LibGfx/DesignMetrics.h>
#include <LibGfx/Palette.h>

namespace GUI {

// Only the footer opts into standard density. Tab contents and global widget
// metrics remain compact, and switching back to Classic restores its height.
class SettingsButtonContainer final : public Widget {
    C_OBJECT(SettingsButtonContainer)
public:
    void update_density()
    {
        auto height = palette().window_theme_provider(Gfx::WindowThemeRole::WindowTheme) == Gfx::WindowThemeProvider::Modern ? Gfx::DesignMetrics::standard_control_height : Gfx::DesignMetrics::control_height;
        for_each_child_of_type<Button>([&](auto& button) {
            button.set_fixed_height(height);
            return IterationDecision::Continue;
        });
    }

private:
    SettingsButtonContainer() = default;
    virtual void theme_change_event(ThemeChangeEvent& event) override
    {
        Widget::theme_change_event(event);
        update_density();
    }
};

void SettingsWindow::set_modified(bool modified)
{
    Window::set_modified(modified);
    if (m_apply_button)
        m_apply_button->set_enabled(modified);
}

ErrorOr<NonnullRefPtr<SettingsWindow>> SettingsWindow::create(ByteString title, ShowDefaultsButton show_defaults_button)
{
    auto window = TRY(SettingsWindow::try_create());

    window->set_title(move(title));
    window->resize(Gfx::DesignMetrics::settings_width, Gfx::DesignMetrics::settings_height);
    window->set_resizable(false);
    window->set_minimizable(false);

    auto main_widget = window->set_main_widget<GUI::Widget>();
    main_widget->set_fill_with_background_color(true);
    main_widget->set_layout<GUI::VerticalBoxLayout>(Gfx::DesignMetrics::content_margin, Gfx::DesignMetrics::spacing);

    window->m_tab_widget = main_widget->add<GUI::TabWidget>();

    auto& button_container = main_widget->add<SettingsButtonContainer>();
    button_container.set_preferred_size({ SpecialDimension::Grow, SpecialDimension::Fit });
    button_container.set_layout<GUI::HorizontalBoxLayout>(GUI::Margins {}, Gfx::DesignMetrics::spacing);

    if (show_defaults_button == ShowDefaultsButton::Yes) {
        window->m_reset_button = button_container.add<GUI::DialogButton>("Defaults"_string);
        window->m_reset_button->on_click = [window = window->make_weak_ptr<SettingsWindow>()](auto) {
            window->reset_default_values();
        };
    }

    button_container.add_spacer();

    window->m_ok_button = button_container.add<GUI::DialogButton>("OK"_string);
    window->m_ok_button->on_click = [window = window->make_weak_ptr<SettingsWindow>()](auto) {
        window->apply_settings();
        GUI::Application::the()->quit();
    };

    window->m_cancel_button = button_container.add<GUI::DialogButton>("Cancel"_string);
    window->m_cancel_button->on_click = [window = window->make_weak_ptr<SettingsWindow>()](auto) {
        window->cancel_settings();
        GUI::Application::the()->quit();
    };

    window->m_apply_button = button_container.add<GUI::DialogButton>("Apply"_string);
    window->m_apply_button->set_enabled(false);
    button_container.update_density();
    window->m_apply_button->on_click = [window = window->make_weak_ptr<SettingsWindow>()](auto) {
        window->apply_settings();
    };

    window->on_close_request = [window = window->make_weak_ptr<SettingsWindow>()]() -> Window::CloseRequestDecision {
        if (!window->is_modified())
            return Window::CloseRequestDecision::Close;

        auto result = MessageBox::show(window, "Apply these settings before closing?"sv, "Unsaved Changes"sv, MessageBox::Type::Warning, MessageBox::InputType::YesNoCancel);
        switch (result) {
        case MessageBox::ExecResult::Yes:
            window->apply_settings();
            return Window::CloseRequestDecision::Close;
        case MessageBox::ExecResult::No:
            window->cancel_settings();
            return Window::CloseRequestDecision::Close;
        default:
            return Window::CloseRequestDecision::StayOpen;
        }
    };

    return window;
}

Optional<NonnullRefPtr<SettingsWindow::Tab>> SettingsWindow::get_tab(StringView id) const
{
    auto tab = m_tabs.find(id);
    if (tab == m_tabs.end())
        return {};
    return tab->value;
}

void SettingsWindow::set_active_tab(StringView id)
{
    if (auto tab = get_tab(id); tab.has_value())
        m_tab_widget->set_active_widget(tab.value());
}

void SettingsWindow::apply_settings()
{
    for (auto& [id, tab] : m_tabs)
        tab->apply_settings();
    set_modified(false);
}

void SettingsWindow::cancel_settings()
{
    for (auto& [id, tab] : m_tabs)
        tab->cancel_settings();
}

void SettingsWindow::reset_default_values()
{
    for (auto& [id, tab] : m_tabs) {
        tab->reset_default_values();
        tab->apply_settings();
    }
    set_modified(false);
}

}
