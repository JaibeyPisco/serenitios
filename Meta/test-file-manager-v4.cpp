/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

// Guest-only contracts and read-only checks of actual GUI file operations.
#include <LibCore/File.h>
#include <LibGUI/Application.h>
#include <LibGUI/IconView.h>
#include <LibGUI/ItemListModel.h>
#include <LibMain/Main.h>
#include <sys/stat.h>
#include <unistd.h>

static constexpr auto root = "/home/anon/V4-test"sv;
static ByteString path(StringView directory, StringView name)
{
    return ByteString::formatted("{}/{}/{}", root, directory, name);
}
static bool exists(ByteString const& name)
{
    struct stat st;
    return lstat(name.characters(), &st) == 0;
}
static void verify_file(ByteString const& name, StringView expected)
{
    auto file = MUST(Core::File::open(name, Core::File::OpenMode::Read));
    auto bytes = MUST(file->read_until_eof());
    VERIFY(StringView(bytes) == expected);
}
static void verify_group(StringView folder)
{
    verify_file(path(folder, "alpha.txt"sv), "Alpha payload for V4.\n"sv);
    verify_file(path(folder, "café report.txt"sv), "UTF-8 names and spaces survive.\n"sv);
    verify_file(path(folder, "Long filename with spaces and accents café for V4.txt"sv), "Long filename payload.\n"sv);
}
ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    VERIFY(arguments.strings.size() == 2);
    auto app = TRY(GUI::Application::create(arguments));
    auto mode = arguments.strings[1];
    if (mode == "contracts"sv) {
        static_assert(sizeof(GUI::IconView) == 1176);
        Vector<String> names { "one"_string, "two"_string, "three"_string, "four"_string, "five"_string, "six"_string };
        auto view = GUI::IconView::construct();
        view->set_frame_style(Gfx::FrameStyle::NoFrame);
        view->set_scrollbars_enabled(false);
        view->resize(320, 400);
        auto model = GUI::ItemListModel<String>::create(names);
        view->set_flow_direction(GUI::IconView::FlowDirection::TopToBottom);
        view->set_model(model);
        Vector<Gfx::IntRect> before;
        for (int i = 0; i < 6; ++i)
            before.append(view->content_rect(model->index(i, 0)));
        view->set_modern_presentation(true);
        VERIFY(view->effective_item_size() == Gfx::IntSize(80, 80));
        for (int i = 0; i < 6; ++i)
            VERIFY(view->content_rect(model->index(i, 0)) == before[i]);
        view->set_modern_presentation(false);
        view->set_flow_direction(GUI::IconView::FlowDirection::LeftToRight);
        view->set_modern_presentation(true);
        VERIFY(view->effective_item_size() == Gfx::IntSize(84, 80));
        view->set_modern_presentation(false);
        VERIFY(view->effective_item_size() == Gfx::IntSize(80, 80));
        dbgln("V4 CONTRACTS PASS: ABI, six desktop positions, opt-in and reversible grid");
        return 0;
    }
    if (mode != "dnd-moved"sv)
        verify_group("Source"sv);
    if (mode == "dnd-moved"sv || mode == "dnd-returned"sv) {
        auto empty_folder = mode == "dnd-moved"sv ? "Source"sv : "Drop"sv;
        for (auto name : { "alpha.txt"sv, "café report.txt"sv, "Long filename with spaces and accents café for V4.txt"sv })
            VERIFY(!exists(path(empty_folder, name)));
        if (mode == "dnd-moved"sv)
            verify_group("Drop"sv);
    } else if (mode == "copied"sv) {
        verify_file(path("Target"sv, "alpha.txt"sv), "Alpha payload for V4.\n"sv);
    } else if (mode == "moved"sv) {
        VERIFY(!exists(path("Target"sv, "alpha.txt"sv)));
        verify_file(path("Moved"sv, "alpha.txt"sv), "Alpha payload for V4.\n"sv);
    } else if (mode == "renamed"sv) {
        VERIFY(!exists(path("Moved"sv, "alpha.txt"sv)));
        verify_file(path("Moved"sv, "renamed file.txt"sv), "Alpha payload for V4.\n"sv);
    } else if (mode == "created"sv) {
        struct stat st;
        VERIFY(stat(path("Moved"sv, "New folder"sv).characters(), &st) == 0 && S_ISDIR(st.st_mode));
        verify_file(path("Moved"sv, "new file.txt"sv), ""sv);
    } else if (mode == "deleted"sv) {
        VERIFY(!exists(path("Moved"sv, "renamed file.txt"sv)));
    } else if (mode == "group"sv) {
        verify_group("Target"sv);
    } else if (mode == "dnd"sv) {
        verify_group("Drop"sv);
    } else {
        VERIFY(mode == "initial"sv);
    }
    verify_file("/home/anon/Desktop/V4 café.txt", "SerenityOS V4 desktop launch check.\n"sv);
    dbgln("V4 FILES PASS: {} (expected files and desktop payload intact)", mode);
    return 0;
}
