/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

// Guest-only regression fixture. Link the actual FileUtils and progress widget
// objects, so GUI cases use the production helper and error handling.
#include "../Userland/Applications/FileManager/FileOperationProgressWidget.h"
#include "../Userland/Applications/FileManager/FileUtils.h"
#include <AK/StringBuilder.h>
#include <LibCore/File.h>
#include <LibCore/System.h>
#include <LibGUI/Application.h>
#include <LibGUI/BoxLayout.h>
#include <LibGUI/Button.h>
#include <LibGUI/Window.h>
#include <LibMain/Main.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static constexpr auto contents = "File operation regression data.\n"sv;

static void write_file(ByteString const& path)
{
    auto file = MUST(Core::File::open(path, Core::File::OpenMode::Write | Core::File::OpenMode::Truncate));
    MUST(file->write_until_depleted(contents.bytes()));
}

static bool exists(ByteString const& path)
{
    struct stat st;
    return lstat(path.characters(), &st) == 0;
}

static void verify_file(ByteString const& path)
{
    auto file = MUST(Core::File::open(path, Core::File::OpenMode::Read));
    auto bytes = MUST(file->read_until_eof());
    VERIFY(StringView(bytes) == contents);
}

struct Fixture {
    ByteString base;
    ByteString source;
    ByteString second;
    ByteString destination;
    ByteString blocked;
    ByteString invalid;
    ByteString unreadable;

    Fixture()
    {
        char pattern[] = "/tmp/fm-XXXXXX";
        auto* directory = mkdtemp(pattern);
        VERIFY(directory);
        base = directory;
        source = ByteString::formatted("{}/input file.txt", base);
        second = ByteString::formatted("{}/second.txt", base);
        destination = ByteString::formatted("{}/out", base);
        blocked = ByteString::formatted("{}/blocked", base);
        invalid = ByteString::formatted("{}/not-a-directory", base);
        unreadable = ByteString::formatted("{}/unreadable", base);
        VERIFY(mkdir(destination.characters(), 0755) == 0);
        VERIFY(mkdir(blocked.characters(), 0555) == 0);
        VERIFY(mkdir(unreadable.characters(), 0000) == 0);
        write_file(source);
        write_file(second);
        write_file(invalid);
        VERIFY(chmod(second.characters(), 0000) == 0);
    }

    ByteString copied() const { return ByteString::formatted("{}/input file.txt", destination); }
};

struct Result {
    int status;
    ByteString protocol;
};

static Result run_helper(StringView operation, Vector<ByteString> const& sources, ByteString const& destination = {})
{
    auto pipe = MUST(Core::System::pipe2(0));
    auto pid = MUST(Core::System::fork());
    if (pid == 0) {
        MUST(Core::System::close(pipe[0]));
        MUST(Core::System::dup2(pipe[1], STDOUT_FILENO));
        MUST(Core::System::close(pipe[1]));
        Vector<StringView> args { "/bin/FileOperation"sv, operation };
        for (auto const& source : sources)
            args.append(source);
        if (operation != "Delete"sv)
            args.append(destination);
        MUST(Core::System::exec(args.first(), args, Core::System::SearchInPath::No));
        _exit(127);
    }
    MUST(Core::System::close(pipe[1]));
    auto stream = MUST(Core::File::adopt_fd(pipe[0], Core::File::OpenMode::Read));
    auto data = MUST(stream->read_until_eof());
    int status;
    pid_t waited;
    do {
        waited = waitpid(pid, &status, 0);
    } while (waited < 0 && errno == EINTR);
    VERIFY(waited == pid);
    VERIFY(WIFEXITED(status));
    return { WEXITSTATUS(status), StringView(data).to_byte_string() };
}

static void verify_protocol(StringView name, Result const& result, bool success, StringView path = {}, StringView cause = {})
{
    size_t errors = 0;
    size_t finishes = 0;
    for (auto line : result.protocol.split_view('\n')) {
        errors += line.starts_with("ERROR "sv);
        finishes += line == "FINISH"sv;
        VERIFY(!line.starts_with("WARN "sv));
    }
    VERIFY(result.status == (success ? 0 : 1));
    VERIFY(errors == (success ? 0u : 1u));
    VERIFY(finishes == (success ? 1u : 0u));
    if (!success) {
        VERIFY(result.protocol.contains(path));
        VERIFY(result.protocol.contains(cause));
    }
    dbgln("FileOperation regression {} PASS: {}", name, result.protocol);
}

static void run_cli_tests()
{
    {
        Fixture f;
        verify_protocol("copy"sv, run_helper("Copy"sv, { f.source }, f.destination), true);
        verify_file(f.source);
        verify_file(f.copied());
        verify_protocol("copy-deduplicate"sv, run_helper("Copy"sv, { f.source }, f.destination), true);
        verify_file(ByteString::formatted("{}/input file-1.txt", f.destination));
    }
    {
        Fixture f;
        verify_protocol("move"sv, run_helper("Move"sv, { f.source }, f.destination), true);
        VERIFY(!exists(f.source));
        verify_file(f.copied());
        verify_protocol("delete"sv, run_helper("Delete"sv, { f.copied() }), true);
        VERIFY(!exists(f.copied()));
    }
    {
        Fixture f;
        verify_protocol("copy-denied"sv, run_helper("Copy"sv, { f.source }, f.blocked), false, f.blocked, "Permission denied"sv);
        verify_file(f.source);
        auto missing = ByteString::formatted("{}/missing", f.base);
        verify_protocol("copy-missing"sv, run_helper("Copy"sv, { f.source }, missing), false, missing, "No such file"sv);
        verify_protocol("copy-invalid"sv, run_helper("Copy"sv, { f.source }, f.invalid), false, f.invalid, "Not a directory"sv);
        verify_protocol("move-denied"sv, run_helper("Move"sv, { f.source }, f.blocked), false, f.blocked, "Permission denied"sv);
        verify_file(f.source);
    }
    {
        Fixture f;
        verify_protocol("partial-copy"sv, run_helper("Copy"sv, { f.source, f.second }, f.destination), false, f.second, "Permission denied"sv);
        verify_file(f.copied());
        VERIFY(!exists(ByteString::formatted("{}/second.txt", f.destination)));
        verify_file(f.source);
        VERIFY(exists(f.second));
    }
    {
        Fixture f;
        verify_protocol("unreadable-directory"sv, run_helper("Copy"sv, { f.unreadable }, f.destination), false, f.unreadable, "Permission denied"sv);
        VERIFY(!exists(ByteString::formatted("{}/unreadable", f.destination)));
        auto locked = ByteString::formatted("{}/locked.txt", f.blocked);
        VERIFY(chmod(f.blocked.characters(), 0755) == 0);
        write_file(locked);
        VERIFY(chmod(f.blocked.characters(), 0555) == 0);
        verify_protocol("delete-denied"sv, run_helper("Delete"sv, { locked }), false, locked, "Permission denied"sv);
        verify_file(locked);
    }
    {
        Fixture f;
        auto unusual = ByteString::formatted("{}/line\nFINISH\nERROR forged", f.base);
        write_file(unusual);
        verify_protocol("newline-path"sv, run_helper("Copy"sv, { unusual }, f.destination), true);
        verify_file(ByteString::formatted("{}/line\nFINISH\nERROR forged", f.destination));
    }
    dbgln("FileOperation CLI cases passed: 12");
}

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    VERIFY(getuid() == 100); // Permissions must be tested as anon, never root.
    if (arguments.strings.size() == 2 && arguments.strings[1] == "--check"sv) {
        run_cli_tests();
        return 0;
    }
    VERIFY(arguments.strings.size() == 2);
    auto scenario = arguments.strings[1];
    auto app = TRY(GUI::Application::create(arguments));
    Fixture fixture;
    auto parent = GUI::Window::construct();
    parent->set_title("File Operation Regression");
    parent->resize(440, 80);
    parent->move_to(270, 140);
    auto widget = parent->set_main_widget<GUI::Widget>();
    widget->set_fill_with_background_color(true);
    widget->set_layout<GUI::VerticalBoxLayout>();
    auto& button = widget->add<GUI::Button>("Verify result and close test"_string);
    button.on_click = [&](auto) {
        if (scenario == "copy"sv || scenario == "partial"sv)
            verify_file(fixture.copied());
        if (scenario == "move"sv) {
            VERIFY(!exists(fixture.source));
            verify_file(fixture.copied());
        } else if (scenario == "delete"sv) {
            VERIFY(!exists(fixture.source));
        } else {
            verify_file(fixture.source);
        }
        if (scenario == "partial"sv)
            VERIFY(!exists(ByteString::formatted("{}/second.txt", fixture.destination)));
        dbgln("FileOperation GUI responsive and files verified: {}", scenario);
        app->quit();
    };
    parent->show();

    if (scenario == "disconnect"sv || scenario == "duplicate"sv) {
        auto pipe = TRY(Core::System::pipe2(0));
        auto writer = TRY(Core::File::adopt_fd(pipe[1], Core::File::OpenMode::Write));
        if (scenario == "duplicate"sv)
            TRY(writer->write_until_depleted("ERROR Could not copy \"/tmp/test\": Permission denied\nERROR Duplicate notification\nFINISH\n"sv.bytes()));
        writer->close();
        auto reader = TRY(Core::File::adopt_fd(pipe[0], Core::File::OpenMode::Read));
        auto buffered = TRY(Core::InputBufferedFile::create(move(reader)));
        auto progress = GUI::Window::construct();
        progress->set_title("Copying Files...");
        progress->resize(320, 190);
        (void)progress->set_main_widget<FileManager::FileOperationProgressWidget>(FileManager::FileOperation::Copy, move(buffered), pipe[0]);
        progress->center_within(*parent);
        progress->show();
        return app->exec();
    }

    auto operation = scenario == "move"sv ? FileManager::FileOperation::Move : scenario == "delete"sv ? FileManager::FileOperation::Delete
                                                                                                      : FileManager::FileOperation::Copy;
    auto destination = scenario == "denied"sv ? fixture.blocked : scenario == "invalid"sv ? fixture.invalid
        : scenario == "missing"sv                                                         ? ByteString::formatted("{}/missing", fixture.base)
                                                                                          : fixture.destination;
    Vector<ByteString> sources { fixture.source };
    if (scenario == "partial"sv)
        sources.append(fixture.second);
    TRY(FileManager::run_file_operation(operation, sources, destination, parent));
    return app->exec();
}
