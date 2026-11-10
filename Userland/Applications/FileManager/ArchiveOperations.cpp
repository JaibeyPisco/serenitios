/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include "ArchiveOperations.h"
#include <AK/LexicalPath.h>
#include <AK/ScopeGuard.h>
#include <LibCore/EventLoop.h>
#include <LibCore/File.h>
#include <LibCore/System.h>
#include <LibFileSystem/FileSystem.h>
#include <LibFileSystem/TempFile.h>
#include <LibGUI/BoxLayout.h>
#include <LibGUI/Label.h>
#include <LibGUI/MessageBox.h>
#include <LibGUI/Window.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

namespace FileManager {

struct ArchiveOperation {
    pid_t pid { 0 };
    bool extracting { false };
    ByteString destination;
    ByteString source;
    ByteString temporary_path;
    OwnPtr<FileSystem::TempFile> diagnostics;
    RefPtr<GUI::Window> progress;

    void finish(int status)
    {
        ByteString failure;
        if (WIFSIGNALED(status))
            failure = ByteString::formatted("The ZIP helper terminated by signal {}.", WTERMSIG(status));
        else if (!WIFEXITED(status))
            failure = "The ZIP helper did not exit normally.";
        else if (WEXITSTATUS(status) != 0)
            failure = ByteString::formatted("The ZIP helper exited with status {}.", WEXITSTATUS(status));

        if (failure.is_empty()) {
            // Extraction never merges into an existing directory. A failed or
            // overlapping extraction must not overwrite previously saved files.
            struct stat st;
            if (extracting && lstat(destination.characters(), &st) == 0) {
                failure = "The extraction destination already exists.";
            } else if (auto result = Core::System::rename(temporary_path, destination); result.is_error()) {
                failure = ByteString::formatted("Could not publish the result: {}", result.release_error());
            }
        }

        if (!failure.is_empty()) {
            ByteString detail;
            if (auto file = Core::File::open(diagnostics->path(), Core::File::OpenMode::Read); !file.is_error()) {
                Array<u8, 512> buffer;
                if (auto data = file.value()->read_some(buffer); !data.is_error())
                    detail = String::from_utf8_with_replacement_character(StringView(data.value())).to_byte_string().replace("\033[31;1m"sv, ""sv).replace("\033[0m"sv, ""sv);
            }
            dbgln("ZIP operation failed: pid={} destination={} reason={}", pid, destination, failure);
            GUI::MessageBox::show_error(progress, ByteString::formatted("{}\n{}\nSource: {}\nDestination: {}\n\nUnpublished output, if any, is at:\n{}\nExisting files have not been replaced.", failure, detail, source, destination, temporary_path));
        } else {
            dbgln("ZIP operation finished: pid={} destination={}", pid, destination);
        }
        progress->close();
    }
};

// Only ZIP operations in this File Manager process are tracked here. Other
// children are reaped as before, without creating a general job service.
static OwnPtr<ArchiveOperation> s_archive_operation;

void initialize_archive_operations()
{
    Core::EventLoop::register_signal(SIGCHLD, [](int) {
        for (;;) {
            int status = 0;
            auto pid = waitpid(-1, &status, WNOHANG);
            if (pid < 0 && errno == EINTR)
                continue;
            if (pid <= 0)
                break;
            if (s_archive_operation && s_archive_operation->pid == pid) {
                // Remove before entering a message box's nested event loop.
                auto operation = move(s_archive_operation);
                operation->finish(status);
            }
        }
    });
}

bool archive_operations_in_progress()
{
    return s_archive_operation;
}

static ErrorOr<void> start_archive_operation(bool extracting, Vector<ByteString> const& sources, ByteString const& destination, GUI::Window* owner)
{
    auto parent_directory = TRY(FileSystem::real_path(LexicalPath(destination).dirname()));
    auto output = LexicalPath::join(parent_directory, LexicalPath(destination).basename()).string();
    if (s_archive_operation)
        return Error::from_string_literal("A ZIP operation is already running in this File Manager; wait for it to finish");
    for (auto const& source : sources) {
        auto resolved_source = TRY(FileSystem::real_path(source));
        if (output == resolved_source || (!extracting && output.starts_with(ByteString::formatted("{}/", resolved_source))))
            return Error::from_string_literal("The archive destination must be outside the selected source");
    }
    if (extracting) {
        struct stat st;
        if (lstat(output.characters(), &st) == 0)
            return Error::from_string_literal("The extraction destination already exists; choose another location");
        if (errno != ENOENT)
            return Error::from_errno(errno);
    }

    auto operation = TRY(adopt_nonnull_own_or_enomem(new ArchiveOperation));
    operation->extracting = extracting;
    operation->destination = output;
    operation->source = sources.first();
    operation->diagnostics = TRY(FileSystem::TempFile::create_temp_file());
    auto pattern_string = ByteString::formatted("{}/.fm-zip-XXXXXX", parent_directory);
    auto pattern = TRY(ByteBuffer::copy(pattern_string.characters(), pattern_string.length() + 1));
    Span<char> pattern_chars { reinterpret_cast<char*>(pattern.data()), pattern.size() };
    if (extracting) {
        operation->temporary_path = TRY(Core::System::mkdtemp(pattern_chars));
    } else {
        auto fd = TRY(Core::System::mkstemp(pattern_chars));
        operation->temporary_path = reinterpret_cast<char const*>(pattern.data());
        TRY(Core::System::close(fd));
    }
    bool started = false;
    ScopeGuard cleanup = [&] {
        if (!started)
            (void)FileSystem::remove(operation->temporary_path, FileSystem::RecursionMode::Disallowed);
    };

    Vector<ByteString> arguments;
    ByteString working_directory;
    if (extracting) {
        arguments = { "/bin/unzip", "-d", operation->temporary_path, TRY(FileSystem::real_path(sources.first())) };
    } else {
        working_directory = TRY(FileSystem::real_path(LexicalPath(sources.first()).dirname()));
        arguments = { "/bin/zip", "-r", "-f", operation->temporary_path };
        for (auto const& source : sources)
            arguments.append(LexicalPath::relative_path(TRY(FileSystem::real_path(source)), working_directory));
    }
    Vector<StringView> argument_views;
    for (auto const& argument : arguments)
        TRY(argument_views.try_append(argument));
    auto diagnostics = TRY(Core::File::open(operation->diagnostics->path(), Core::File::OpenMode::Write));
    auto null_output = TRY(Core::File::open("/dev/null"sv, Core::File::OpenMode::Write));

    auto progress = GUI::Window::construct();
    progress->set_title(extracting ? "Extracting ZIP..." : "Creating ZIP...");
    progress->set_closeable(false);
    progress->set_resizable(false);
    progress->resize(420, 80);
    auto widget = progress->set_main_widget<GUI::Widget>();
    widget->set_fill_with_background_color(true);
    widget->set_layout<GUI::VerticalBoxLayout>(8);
    auto& label = widget->add<GUI::Label>("You can keep using File Manager.\nClosing its window does not cancel this operation."_string);
    label.set_text_alignment(Gfx::TextAlignment::CenterLeft);
    if (owner)
        progress->center_within(*owner);
    operation->progress = progress;

    auto pid = TRY(Core::System::fork());
    if (pid == 0) {
        auto execute = [&]() -> ErrorOr<void> {
            TRY(Core::System::dup2(diagnostics->fd(), STDERR_FILENO));
            TRY(Core::System::dup2(null_output->fd(), STDOUT_FILENO));
            if (!working_directory.is_empty())
                TRY(Core::System::chdir(working_directory));
            return Core::System::exec(argument_views.first(), argument_views, Core::System::SearchInPath::No);
        }();
        if (execute.is_error())
            warnln("Could not start ZIP helper: {}", execute.release_error());
        _exit(127);
    }
    operation->pid = pid;
    dbgln("ZIP operation started: pid={} destination={}", pid, output);
    started = true;
    s_archive_operation = move(operation);
    progress->show();
    return {};
}

ErrorOr<void> create_archive(Vector<ByteString> const& sources, ByteString const& destination, GUI::Window* owner)
{
    return start_archive_operation(false, sources, destination, owner);
}

ErrorOr<void> extract_archive(ByteString const& source, ByteString const& destination, GUI::Window* owner)
{
    return start_archive_operation(true, { source }, destination, owner);
}

}
