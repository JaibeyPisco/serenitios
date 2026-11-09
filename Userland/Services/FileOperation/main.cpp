/*
 * Copyright (c) 2021, Andreas Kling <kling@serenityos.org>
 * Copyright (c) 2021-2022, Sam Atkins <atkinssj@serenityos.org>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/ByteString.h>
#include <AK/Format.h>
#include <AK/LexicalPath.h>
#include <AK/String.h>
#include <AK/StringView.h>
#include <LibCore/ArgsParser.h>
#include <LibCore/DirIterator.h>
#include <LibCore/File.h>
#include <LibCore/System.h>
#include <LibMain/Main.h>
#include <sched.h>
#include <unistd.h>

struct WorkItem {
    enum class Type {
        CreateDirectory,
        DeleteDirectory,
        CopyFile,
        MoveFile,
        DeleteFile,
    };
    Type type;
    ByteString source;
    ByteString destination;
    off_t size;
    mode_t mode { 0 };
};

struct ErrorContext {
    StringView action { "preparing the operation"sv };
    ByteString path;

    void set(StringView next_action, ByteString const& next_path)
    {
        action = next_action;
        path = next_path;
    }
};

static ErrorOr<int> perform_copy(Vector<StringView> const& sources, ByteString const& destination, ErrorContext&);
static ErrorOr<int> perform_move(Vector<StringView> const& sources, ByteString const& destination, ErrorContext&);
static ErrorOr<int> perform_delete(Vector<StringView> const& sources, ErrorContext&);
static ErrorOr<int> execute_work_items(Vector<WorkItem> const& items, ErrorContext&);
static ErrorOr<NonnullOwnPtr<Core::File>> open_destination_file(ByteString const& destination, mode_t mode, ErrorContext&);
static ByteString deduplicate_destination_file_name(ByteString const& destination);

// Paths must not introduce new records into the line-based helper protocol.
static ByteString display_path(ByteString const& path)
{
    return String::from_utf8_with_replacement_character(path).to_byte_string().replace("\\"sv, "\\\\"sv).replace("\n"sv, "\\n"sv).replace("\r"sv, "\\r"sv).replace("\t"sv, "\\t"sv).replace("\""sv, "\\\""sv);
}

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    ByteString operation;
    Vector<StringView> paths;

    Core::ArgsParser args_parser;
    args_parser.add_positional_argument(operation, "Operation: either 'Copy', 'Move' or 'Delete'", "operation", Core::ArgsParser::Required::Yes);
    args_parser.add_positional_argument(paths, "Source paths, followed by a destination if applicable", "paths", Core::ArgsParser::Required::Yes);
    args_parser.parse(arguments);

    ErrorContext context;
    auto result = [&]() -> ErrorOr<int> {
        if (operation == "Delete")
            return perform_delete(paths, context);

        ByteString destination = paths.take_last();
        context.path = destination;
        if (paths.is_empty())
            return Error::from_string_literal("At least one source and destination are required");

        if (operation == "Copy")
            return perform_copy(paths, destination, context);
        if (operation == "Move")
            return perform_move(paths, destination, context);
        return Error::from_string_literal("Unknown operation");
    }();

    if (result.is_error()) {
        auto error = result.release_error();
        auto verb = operation == "Copy" ? "copy"sv : operation == "Move" ? "move"sv
            : operation == "Delete"                                      ? "delete"sv
                                                                         : "process"sv;
        auto cause = error.is_errno() ? ByteString { strerror(error.code()) } : error.string_literal().to_byte_string();
        outln("ERROR Could not {} \"{}\" while {}: {}", verb, display_path(context.path), context.action, cause);
        return 1;
    }

    outln("FINISH");
    return result.release_value();
}

static ErrorOr<int> collect_copy_work_items(ByteString const& source, ByteString const& destination, Vector<WorkItem>& items, ErrorContext& context)
{
    context.set("reading file information"sv, source);
    auto const st = TRY(Core::System::lstat(source));
    if (!S_ISDIR(st.st_mode)) {
        // It's a file.
        items.append(WorkItem {
            .type = WorkItem::Type::CopyFile,
            .source = source,
            .destination = LexicalPath::join(destination, LexicalPath::basename(source)).string(),
            .size = st.st_size,
            .mode = st.st_mode,
        });
        return 0;
    }

    // It's a directory.
    items.append(WorkItem {
        .type = WorkItem::Type::CreateDirectory,
        .source = {},
        .destination = LexicalPath::join(destination, LexicalPath::basename(source)).string(),
        .size = 0,
        .mode = st.st_mode,
    });

    Core::DirIterator dt(source, Core::DirIterator::SkipParentAndBaseDir);
    while (dt.has_next()) {
        auto name = dt.next_path();
        TRY(collect_copy_work_items(
            LexicalPath::join(source, name).string(),
            LexicalPath::join(destination, LexicalPath::basename(source)).string(),
            items, context));
    }

    context.set("reading the directory"sv, source);
    if (dt.has_error())
        return dt.error();

    return 0;
}

ErrorOr<int> perform_copy(Vector<StringView> const& sources, ByteString const& destination, ErrorContext& context)
{
    Vector<WorkItem> items;

    for (auto& source : sources) {
        TRY(collect_copy_work_items(source, destination, items, context));
    }

    return execute_work_items(items, context);
}

static ErrorOr<int> collect_move_work_items(ByteString const& source, ByteString const& destination, Vector<WorkItem>& items, ErrorContext& context)
{
    context.set("reading file information"sv, source);
    auto const st = TRY(Core::System::lstat(source));
    if (!S_ISDIR(st.st_mode)) {
        // It's a file.
        items.append(WorkItem {
            .type = WorkItem::Type::MoveFile,
            .source = source,
            .destination = LexicalPath::join(destination, LexicalPath::basename(source)).string(),
            .size = st.st_size,
            .mode = st.st_mode,
        });
        return 0;
    }

    // It's a directory.
    items.append(WorkItem {
        .type = WorkItem::Type::CreateDirectory,
        .source = {},
        .destination = LexicalPath::join(destination, LexicalPath::basename(source)).string(),
        .size = 0,
        .mode = st.st_mode,
    });

    Core::DirIterator dt(source, Core::DirIterator::SkipParentAndBaseDir);
    while (dt.has_next()) {
        auto name = dt.next_path();
        TRY(collect_move_work_items(
            LexicalPath::join(source, name).string(),
            LexicalPath::join(destination, LexicalPath::basename(source)).string(),
            items, context));
    }

    context.set("reading the directory"sv, source);
    if (dt.has_error())
        return dt.error();

    items.append(WorkItem {
        .type = WorkItem::Type::DeleteDirectory,
        .source = source,
        .destination = {},
        .size = 0,
    });

    return 0;
}

ErrorOr<int> perform_move(Vector<StringView> const& sources, ByteString const& destination, ErrorContext& context)
{
    Vector<WorkItem> items;

    for (auto& source : sources) {
        TRY(collect_move_work_items(source, destination, items, context));
    }

    return execute_work_items(items, context);
}

static ErrorOr<int> collect_delete_work_items(ByteString const& source, Vector<WorkItem>& items, ErrorContext& context)
{
    context.set("reading file information"sv, source);
    if (auto const st = TRY(Core::System::lstat(source)); !S_ISDIR(st.st_mode)) {
        // It's a file.
        items.append(WorkItem {
            .type = WorkItem::Type::DeleteFile,
            .source = source,
            .destination = {},
            .size = st.st_size,
        });
        return 0;
    }

    // It's a directory.
    Core::DirIterator dt(source, Core::DirIterator::SkipParentAndBaseDir);
    while (dt.has_next()) {
        auto name = dt.next_path();
        TRY(collect_delete_work_items(LexicalPath::join(source, name).string(), items, context));
    }

    context.set("reading the directory"sv, source);
    if (dt.has_error())
        return dt.error();

    items.append(WorkItem {
        .type = WorkItem::Type::DeleteDirectory,
        .source = source,
        .destination = {},
        .size = 0,
    });

    return 0;
}

ErrorOr<int> perform_delete(Vector<StringView> const& sources, ErrorContext& context)
{
    Vector<WorkItem> items;

    for (auto& source : sources) {
        TRY(collect_delete_work_items(source, items, context));
    }

    return execute_work_items(items, context);
}

ErrorOr<int> execute_work_items(Vector<WorkItem> const& items, ErrorContext& context)
{
    off_t total_work_bytes = 0;
    for (auto& item : items)
        total_work_bytes += item.size;

    off_t executed_work_bytes = 0;

    for (size_t i = 0; i < items.size(); ++i) {
        auto& item = items[i];
        off_t item_done = 0;
        auto print_progress = [&] {
            outln("PROGRESS {} {} {} {} {} {} {}", i, items.size(), executed_work_bytes, total_work_bytes, item_done, item.size, display_path(item.source));
        };

        auto copy_file = [&](ByteString const& source, ByteString const& destination, mode_t mode) -> ErrorOr<int> {
            context.set("opening the source"sv, source);
            auto source_file = TRY(Core::File::open(source, Core::File::OpenMode::Read));
            // FIXME: When the file already exists, let the user choose the next action instead of renaming it by default.
            auto destination_file = TRY(open_destination_file(destination, mode, context));
            auto actual_destination = context.path;
            context.set("allocating a copy buffer"sv, actual_destination);
            auto buffer = TRY(ByteBuffer::create_zeroed(64 * KiB));

            while (true) {
                print_progress();
                context.set("reading the source"sv, source);
                auto bytes_read = TRY(source_file->read_some(buffer.bytes()));
                if (bytes_read.is_empty())
                    break;
                context.set("writing the destination"sv, actual_destination);
                TRY(destination_file->write_until_depleted(bytes_read));
                item_done += bytes_read.size();
                executed_work_bytes += bytes_read.size();
                print_progress();
                // FIXME: Remove this once the kernel is smart enough to schedule other threads
                //        while we're doing heavy I/O. Right now, copying a large file will totally
                //        starve the rest of the system.
                sched_yield();
            }
            print_progress();
            return 0;
        };

        switch (item.type) {

        case WorkItem::Type::CreateDirectory: {
            outln("MKDIR {}", display_path(item.destination));
            context.set("creating the directory"sv, item.destination);
            // FIXME: Support deduplication like open_destination_file() when the directory already exists.
            auto old_mask = umask(0);
            auto maybe_error = Core::System::mkdir(item.destination, item.mode);
            umask(old_mask);
            if (maybe_error.is_error() && maybe_error.error().code() != EEXIST)
                return maybe_error.release_error();

            break;
        }

        case WorkItem::Type::DeleteDirectory: {
            context.set("removing the directory"sv, item.source);
            TRY(Core::System::rmdir(item.source));
            break;
        }

        case WorkItem::Type::CopyFile: {
            TRY(copy_file(item.source, item.destination, item.mode));
            break;
        }

        case WorkItem::Type::MoveFile: {
            ByteString destination = item.destination;
            while (true) {
                context.set("renaming the source"sv, destination);
                auto maybe_error = Core::System::rename(item.source, destination);
                if (!maybe_error.is_error()) {
                    item_done += item.size;
                    executed_work_bytes += item.size;
                    print_progress();
                    break;
                }
                auto error_code = maybe_error.release_error().code();

                if (error_code == EEXIST) {
                    destination = deduplicate_destination_file_name(destination);
                    continue;
                }

                if (error_code != EXDEV) {
                    return Error::from_errno(error_code);
                }

                // EXDEV means we have to copy the file data and then remove the original
                TRY(copy_file(item.source, item.destination, item.mode));
                context.set("removing the copied source"sv, item.source);
                TRY(Core::System::unlink(item.source));
                break;
            }

            break;
        }

        case WorkItem::Type::DeleteFile: {
            context.set("removing the file"sv, item.source);
            TRY(Core::System::unlink(item.source));

            item_done += item.size;
            executed_work_bytes += item.size;
            print_progress();

            break;
        }

        default:
            VERIFY_NOT_REACHED();
        }
    }

    return 0;
}

ErrorOr<NonnullOwnPtr<Core::File>> open_destination_file(ByteString const& destination, mode_t mode, ErrorContext& context)
{
    context.set("opening the destination"sv, destination);
    auto old_mask = umask(0);
    auto destination_file_or_error = Core::File::open(destination, (Core::File::OpenMode::Write | Core::File::OpenMode::Truncate | Core::File::OpenMode::MustBeNew), mode);
    umask(old_mask);
    if (destination_file_or_error.is_error() && destination_file_or_error.error().code() == EEXIST) {
        return open_destination_file(deduplicate_destination_file_name(destination), mode, context);
    }
    return destination_file_or_error;
}

ByteString deduplicate_destination_file_name(ByteString const& destination)
{
    LexicalPath destination_path(destination);
    auto title_without_counter = destination_path.title();
    size_t next_counter = 1;

    auto last_hyphen_index = title_without_counter.find_last('-');
    if (last_hyphen_index.has_value()) {
        auto counter_string = title_without_counter.substring_view(*last_hyphen_index + 1);
        auto last_counter = counter_string.to_number<unsigned>();
        if (last_counter.has_value()) {
            next_counter = *last_counter + 1;
            title_without_counter = title_without_counter.substring_view(0, *last_hyphen_index);
        }
    }

    StringBuilder basename;
    basename.appendff("{}-{}", title_without_counter, next_counter);
    if (!destination_path.extension().is_empty()) {
        basename.append('.');
        basename.append(destination_path.extension());
    }

    return LexicalPath::join(destination_path.dirname(), basename.to_byte_string()).string();
}
