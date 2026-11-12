/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

// Guest regression: a sorting proxy moves the cursor after its old row is removed.
#include <LibGUI/Application.h>
#include <LibGUI/IconView.h>
#include <LibGUI/ItemListModel.h>
#include <LibGUI/SortingProxyModel.h>
#include <LibMain/Main.h>

class CheckedListModel final : public GUI::ItemListModel<String> {
public:
    using GUI::Model::did_update;
    static NonnullRefPtr<CheckedListModel> create(Vector<String> const& names)
    {
        return adopt_ref(*new CheckedListModel(names));
    }
    virtual GUI::Variant data(GUI::ModelIndex const& index, GUI::ModelRole role) const override
    {
        VERIFY(index.is_valid());
        VERIFY(is_within_range(index));
        return GUI::ItemListModel<String>::data(index, role);
    }

private:
    explicit CheckedListModel(Vector<String> const& names)
        : GUI::ItemListModel<String>(names)
    {
    }
};

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    auto app = TRY(GUI::Application::create(arguments));
    for (int iteration = 0; iteration < 8; ++iteration) {
        Vector<String> names { "alpha"_string, "bravo"_string, "charlie"_string };
        auto source = CheckedListModel::create(names);
        auto proxy = TRY(GUI::SortingProxyModel::create(*source));
        auto view = GUI::IconView::construct();
        view->resize(320, 240);
        view->set_model(proxy);
        proxy->sort(0, GUI::SortOrder::Ascending);
        view->set_selection_mode(GUI::AbstractView::SelectionMode::MultiSelection);
        view->set_cursor(proxy->index(2, 0, {}), GUI::AbstractView::SelectionUpdate::Set);
        view->select_all();
        names.remove(0);
        source->did_update(GUI::Model::DontInvalidateIndices);
        VERIFY(proxy->row_count() == 2);
        VERIFY(view->cursor_index().is_valid());
        VERIFY(proxy->is_within_range(view->cursor_index()));
        names.clear();
        source->did_update(GUI::Model::InvalidateAllIndices);
        VERIFY(view->selection().is_empty());
        VERIFY(!view->cursor_index().is_valid());
    }
    dbgln("ICON CURSOR PASS: eight shrinking proxy updates and empty-model resets");
    return 0;
}
