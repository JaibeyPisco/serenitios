/*
 * Copyright (c) 2021, Andreas Kling <kling@serenityos.org>
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/QuickSort.h>
#include <LibDesktop/AppFile.h>
#include <LibGUI/Model.h>

// These are launcher groups, not embedded settings pages or service availability.
enum class SettingsCategory { All,
    Appearance,
    Input,
    System,
    Applications };

inline StringView settings_category_name(SettingsCategory category)
{
    switch (category) {
    case SettingsCategory::All:
        return "All settings"sv;
    case SettingsCategory::Appearance:
        return "Appearance"sv;
    case SettingsCategory::Input:
        return "Input"sv;
    case SettingsCategory::System:
        return "System"sv;
    case SettingsCategory::Applications:
        return "Applications"sv;
    }
    VERIFY_NOT_REACHED();
}

inline SettingsCategory settings_category_for(StringView executable)
{
    if (executable == "/bin/DisplaySettings"sv)
        return SettingsCategory::Appearance;
    if (executable == "/bin/KeyboardSettings"sv || executable == "/bin/MouseSettings"sv)
        return SettingsCategory::Input;
    if (executable == "/bin/ClockSettings"sv || executable == "/bin/NetworkSettings"sv || executable == "/bin/CertificateSettings"sv || executable == "/bin/UsersSettings"sv)
        return SettingsCategory::System;
    // New Settings entries remain discoverable even without a dedicated mapping.
    return SettingsCategory::Applications;
}

class SettingsAppsModel final : public GUI::Model {
public:
    enum Column { Icon,
        Name,
        Description,
        ColumnCount };

    SettingsAppsModel()
    {
        Desktop::AppFile::for_each([&](Desktop::AppFile& app_file) {
            if (app_file.category() == "Settings")
                m_apps.append(app_file);
        });
        quick_sort(m_apps, [](auto& a, auto& b) { return a->name() < b->name(); });
        filter(SettingsCategory::All, {});
    }

    void filter(SettingsCategory category, StringView query)
    {
        m_visible_rows.clear();
        query = query.trim_whitespace();
        for (size_t i = 0; i < m_apps.size(); ++i) {
            auto& app = m_apps[i];
            if (category != SettingsCategory::All && settings_category_for(app->executable()) != category)
                continue;
            if (!query.is_empty() && !app->name().contains(query, CaseSensitivity::CaseInsensitive))
                continue;
            m_visible_rows.append(i);
        }
        did_update(GUI::Model::InvalidateAllIndices | GUI::Model::DontResizeColumns);
    }

    bool has_category(SettingsCategory category) const
    {
        return category == SettingsCategory::All || m_apps.first_matching([&](auto& app) { return settings_category_for(app->executable()) == category; }).has_value();
    }

    Desktop::AppFile const& app_at(GUI::ModelIndex const& index) const
    {
        VERIFY(index.model() == this && index.is_valid() && index.row() < row_count({}));
        return m_apps[m_visible_rows[index.row()]];
    }

    virtual int row_count(GUI::ModelIndex const&) const override { return m_visible_rows.size(); }
    virtual int column_count(GUI::ModelIndex const&) const override { return ColumnCount; }
    virtual GUI::ModelIndex index(int row, int column, GUI::ModelIndex const& parent = {}) const override
    {
        if (parent.is_valid() || row < 0 || row >= row_count({}) || column < 0 || column >= ColumnCount)
            return {};
        return create_index(row, column);
    }
    virtual ErrorOr<String> column_name(int column) const override
    {
        if (column == Name)
            return "Setting"_string;
        if (column == Description)
            return "Description"_string;
        return String {};
    }
    virtual bool is_column_sortable(int) const override { return false; }
    virtual GUI::Variant data(GUI::ModelIndex const& index, GUI::ModelRole role) const override
    {
        auto& app = app_at(index);
        if (role != GUI::ModelRole::Display)
            return {};
        if (index.column() == Icon)
            return app.icon();
        if (index.column() == Description)
            return app.description();
        if (app.name().ends_with(" Settings"sv))
            return app.name().substring(0, app.name().length() - " Settings"sv.length());
        return app.name();
    }

private:
    Vector<NonnullRefPtr<Desktop::AppFile>> m_apps;
    Vector<size_t> m_visible_rows;
};
