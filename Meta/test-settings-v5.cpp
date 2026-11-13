/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/ProcessStatisticsReader.h>
#include <LibGUI/Application.h>
#include <LibMain/Main.h>
#include <Userland/Applications/Settings/SettingsAppsModel.h>

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    auto app = TRY(GUI::Application::create(arguments));
    if (arguments.strings.size() > 1 && arguments.strings[1] == "root"sv) {
        auto processes = TRY(Core::ProcessStatisticsReader::get_all(false));
        bool found = false;
        for (auto& process : processes.processes) {
            if (process.name == "UsersSettings") {
                VERIFY(process.uid == 0);
                found = true;
                dbgln("SETTINGS V5 ROOT PASS: UsersSettings uid={} pid={}", process.uid, process.pid);
            }
        }
        VERIFY(found);
        return 0;
    }
    auto model = adopt_ref(*new SettingsAppsModel);
    VERIFY(model->row_count({}) == 13);
    struct GroupExpectation {
        SettingsCategory category;
        int count;
    };
    for (auto pair : { GroupExpectation { SettingsCategory::Appearance, 1 }, GroupExpectation { SettingsCategory::Input, 2 }, GroupExpectation { SettingsCategory::System, 4 }, GroupExpectation { SettingsCategory::Applications, 6 } }) {
        model->filter(pair.category, {});
        VERIFY(model->row_count({}) == pair.count);
        for (int row = 0; row < model->row_count({}); ++row) {
            auto& entry = model->app_at(model->index(row, SettingsAppsModel::Name));
            VERIFY(settings_category_for(entry.executable()) == pair.category);
        }
    }
    model->filter(SettingsCategory::All, " dIsP "sv);
    VERIFY(model->row_count({}) == 1);
    VERIFY(model->app_at(model->index(0, SettingsAppsModel::Name)).executable() == "/bin/DisplaySettings");
    VERIFY(model->data(model->index(0, SettingsAppsModel::Name), GUI::ModelRole::Display).to_byte_string() == "Display");
    model->filter(SettingsCategory::Input, "display"sv);
    VERIFY(model->row_count({}) == 0);
    model->filter(SettingsCategory::All, "settings"sv);
    VERIFY(model->row_count({}) == 13);
    model->filter(SettingsCategory::All, "   "sv);
    VERIFY(model->row_count({}) == 13);
    model->filter(SettingsCategory::All, "no-such-setting"sv);
    VERIFY(model->row_count({}) == 0);
    model->filter(SettingsCategory::All, "café"sv);
    VERIFY(model->row_count({}) == 0);
    model->filter(SettingsCategory::System, "users"sv);
    VERIFY(model->row_count({}) == 1);
    VERIFY(model->app_at(model->index(0, SettingsAppsModel::Name)).requires_root());
    model->filter(SettingsCategory::All, {});
    VERIFY(!model->index(-1, 0).is_valid());
    VERIFY(!model->index(13, 0).is_valid());
    VERIFY(!model->index(0, SettingsAppsModel::ColumnCount).is_valid());
    VERIFY(settings_category_for("/bin/FutureSettings"sv) == SettingsCategory::Applications);
    dbgln("SETTINGS V5 MODEL PASS: 13 apps, groups, scoped/case-insensitive filtering, empty reset, UTF-8, indices and privilege metadata");
    return 0;
}
