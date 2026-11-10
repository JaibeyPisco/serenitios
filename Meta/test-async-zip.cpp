/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include "../Userland/Applications/FileManager/ArchiveOperations.h"
#include <AK/HashTable.h>
#include <LibArchive/Zip.h>
#include <LibCompress/Deflate.h>
#include <LibCore/File.h>
#include <LibCore/Process.h>
#include <LibCore/System.h>
#include <LibCore/Timer.h>
#include <LibGUI/Application.h>
#include <LibGUI/BoxLayout.h>
#include <LibGUI/Button.h>
#include <LibGUI/Label.h>
#include <LibGUI/Window.h>
#include <LibMain/Main.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static void text_file(ByteString const& path, StringView text)
{
    auto file = MUST(Core::File::open(path, Core::File::OpenMode::Write | Core::File::OpenMode::Truncate));
    MUST(file->write_until_depleted(text.bytes()));
}

static ByteString read_text(ByteString const& path)
{
    auto file = MUST(Core::File::open(path, Core::File::OpenMode::Read));
    auto data = MUST(file->read_until_eof());
    return StringView(data).to_byte_string();
}

static u8 next_byte(u32& state)
{
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    return state & 255;
}

static void payload(ByteString const& path, size_t size, bool verify)
{
    auto file = MUST(Core::File::open(path, verify ? Core::File::OpenMode::Read : Core::File::OpenMode::Write | Core::File::OpenMode::Truncate));
    Array<u8, 16 * KiB> buffer;
    u32 state = 0x12345678;
    for (size_t offset = 0; offset < size;) {
        auto amount = min(buffer.size(), size - offset);
        if (verify) {
            MUST(file->read_until_filled(buffer.span().trim(amount)));
            for (size_t i = 0; i < amount; ++i)
                VERIFY(buffer[i] == next_byte(state));
        } else {
            for (size_t i = 0; i < amount; ++i)
                buffer[i] = next_byte(state);
            MUST(file->write_until_depleted(buffer.span().trim(amount)));
        }
        offset += amount;
    }
    if (verify) {
        auto st = MUST(Core::System::fstat(file->fd()));
        VERIFY(static_cast<size_t>(st.st_size) == size);
    }
}

struct Fixture {
    ByteString base;
    ByteString folder;
    ByteString archive;
    ByteString extracted;
    size_t size;

    Fixture(ByteString existing, size_t payload_size)
        : base(move(existing))
        , folder(ByteString::formatted("{}/folder", base))
        , archive(ByteString::formatted("{}/native.zip", base))
        , extracted(ByteString::formatted("{}/native", base))
        , size(payload_size)
    {
    }

    explicit Fixture(size_t payload_size)
        : size(payload_size)
    {
        char pattern[] = "/tmp/az-XXXXXX";
        base = MUST(Core::System::mkdtemp(pattern));
        folder = ByteString::formatted("{}/folder", base);
        archive = ByteString::formatted("{}/result.zip", base);
        extracted = ByteString::formatted("{}/extracted", base);
        MUST(Core::System::mkdir(folder, 0755));
        MUST(Core::System::mkdir(ByteString::formatted("{}/nested", folder), 0755));
        text_file(ByteString::formatted("{}/a.txt", folder), "archive test\n"sv);
        text_file(ByteString::formatted("{}/space file.txt", folder), "spaces preserved\n"sv);
        text_file(ByteString::formatted("{}/café.txt", folder), "UTF-8 filename\n"sv);
        payload(ByteString::formatted("{}/nested/bytes.bin", folder), size, false);
    }

    void verify_archive() const
    {
        auto file = MUST(Core::File::open(archive, Core::File::OpenMode::Read));
        auto bytes = MUST(file->read_until_eof());
        auto zip = Archive::Zip::try_create(bytes);
        VERIFY(zip.has_value());
        HashTable<ByteString> names;
        MUST(zip->for_each_member([&](auto const& member) -> ErrorOr<IterationDecision> {
            names.set(member.name.to_byte_string());
            if (member.name == "folder/nested/bytes.bin"sv)
                VERIFY(member.uncompressed_size == size);
            if (!member.is_directory) {
                auto data = member.compression_method == Archive::ZipCompressionMethod::Store
                    ? MUST(ByteBuffer::copy(member.compressed_data))
                    : MUST(Compress::DeflateDecompressor::decompress_all(member.compressed_data));
                if (member.name == "folder/nested/bytes.bin"sv) {
                    VERIFY(data.size() == size);
                    u32 state = 0x12345678;
                    for (auto byte : data.bytes())
                        VERIFY(byte == next_byte(state));
                } else if (member.name == "folder/a.txt"sv) {
                    VERIFY(StringView(data) == "archive test\n"sv);
                } else if (member.name == "folder/space file.txt"sv) {
                    VERIFY(StringView(data) == "spaces preserved\n"sv);
                } else if (member.name == "folder/café.txt"sv) {
                    VERIFY(StringView(data) == "UTF-8 filename\n"sv);
                }
            }
            return IterationDecision::Continue;
        }));
        VERIFY(names.size() == 6);
        for (auto name : { "folder/"sv, "folder/a.txt"sv, "folder/space file.txt"sv, "folder/café.txt"sv, "folder/nested/"sv, "folder/nested/bytes.bin"sv })
            VERIFY(names.contains(name));
    }

    void verify_extracted() const
    {
        VERIFY(read_text(ByteString::formatted("{}/folder/a.txt", extracted)) == "archive test\n"sv);
        VERIFY(read_text(ByteString::formatted("{}/folder/space file.txt", extracted)) == "spaces preserved\n"sv);
        VERIFY(read_text(ByteString::formatted("{}/folder/café.txt", extracted)) == "UTF-8 filename\n"sv);
        payload(ByteString::formatted("{}/folder/nested/bytes.bin", extracted), size, true);
    }
};

static int invoke_zip(ByteString const& executable, ByteString const& destination, ByteString const& source)
{
    auto pid = MUST(Core::Process::spawn(executable, Vector<ByteString> { "-r", "-f", destination, source }, {}, Core::Process::KeepAsChild::Yes));
    int status;
    VERIFY(waitpid(pid, &status, 0) == pid);
    VERIFY(WIFEXITED(status));
    return WEXITSTATUS(status);
}

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    VERIFY(getuid() == 100);
    VERIFY(arguments.strings.size() >= 2);
    auto scenario = arguments.strings[1];
    if (scenario == "verify-native"sv) {
        VERIFY(arguments.strings.size() == 3);
        Fixture fixture(arguments.strings[2], 64 * MiB);
        fixture.verify_archive();
        fixture.verify_extracted();
        dbgln("ZIP NATIVE integrity verified: all names and contents");
        return 0;
    }
    VERIFY(arguments.strings.size() == 2);
    if (scenario == "probe"sv) {
        Fixture fixture(16);
        auto source = ByteString::formatted("{}/a.txt", fixture.folder);
        MUST(Core::System::chmod(source, 0000));
        VERIFY(invoke_zip("/bin/zip-before", fixture.archive, source) == 0);
        auto file = MUST(Core::File::open(fixture.archive, Core::File::OpenMode::Read));
        auto bytes = MUST(file->read_until_eof());
        auto zip = Archive::Zip::try_create(bytes);
        VERIFY(zip.has_value());
        VERIFY(MUST(zip->calculate_statistics()).member_count() == 0);
        dbgln("ZIP BASELINE reproduced: unreadable input, exit=0, archive members=0");
        VERIFY(invoke_zip("/bin/zip", ByteString::formatted("{}/corrected.zip", fixture.base), source) != 0);
        dbgln("ZIP correction verified: unreadable input exits nonzero");
        return 0;
    }
    if (scenario == "prepare"sv) {
        Fixture fixture(64 * MiB);
        dbgln("ZIP NATIVE fixture={}", fixture.base);
        return 0;
    }

    auto app = TRY(GUI::Application::create(arguments));
    FileManager::initialize_archive_operations();
    TRY(Core::System::pledge("stdio thread recvfd sendfd cpath rpath wpath fattr proc exec unix sigaction"));
    bool large = scenario == "long"sv || scenario == "close"sv;
    Fixture fixture(large ? 64 * MiB : 64 * KiB);
    auto window = GUI::Window::construct();
    window->set_title("Async ZIP Regression");
    window->resize(600, 320);
    window->move_to(170, 170);
    auto widget = window->set_main_widget<GUI::Widget>();
    widget->set_fill_with_background_color(true);
    widget->set_layout<GUI::VerticalBoxLayout>(8);
    auto& label = widget->add<GUI::Label>("ZIP starting..."_string);
    label.set_fixed_height(24);
    auto& button = widget->add<GUI::Button>("Interact while running / verify and close"_string);
    button.set_fixed_height(24);
    size_t ticks = 0;
    size_t live_ticks = 0;
    bool complete = false;
    int phase = 0;
    button.on_click = [&](auto) {
        if (!complete) {
            dbgln("ZIP TEST interaction: running={}", FileManager::archive_operations_in_progress());
            return;
        }
        app->quit();
    };
    window->show();

    if (scenario == "invalid"sv) {
        text_file(fixture.archive, "not a ZIP"sv);
        TRY(FileManager::extract_archive(fixture.archive, fixture.extracted, window));
    } else {
        if (scenario == "unreadable"sv) {
            text_file(fixture.archive, "existing output must survive\n"sv);
            MUST(Core::System::chmod(ByteString::formatted("{}/a.txt", fixture.folder), 0000));
        }
        TRY(FileManager::create_archive({ fixture.folder }, fixture.archive, window));
        VERIFY(FileManager::create_archive({ fixture.folder }, fixture.archive, window).is_error());
        dbgln("ZIP TEST duplicate start rejected");
    }
    auto timer = Core::Timer::create_repeating(100, [&] {
        ++ticks;
        if (FileManager::archive_operations_in_progress()) {
            ++live_ticks;
            label.set_text(MUST(String::formatted("ZIP running: {} event-loop ticks", live_ticks)));
            return;
        }
        if (complete)
            return;
        if (scenario == "close"sv)
            return;
        if (scenario == "invalid"sv || scenario == "unreadable"sv) {
            struct stat st;
            VERIFY(lstat(fixture.extracted.characters(), &st) < 0);
            if (scenario == "unreadable"sv)
                VERIFY(read_text(fixture.archive) == "existing output must survive\n"sv);
        } else if (phase == 0) {
            fixture.verify_archive();
            if (scenario == "repeat"sv) {
                fixture.size = 16;
                payload(ByteString::formatted("{}/nested/bytes.bin", fixture.folder), fixture.size, false);
                MUST(FileManager::create_archive({ fixture.folder }, fixture.archive, window));
                phase = 1;
                return;
            }
            MUST(FileManager::extract_archive(fixture.archive, fixture.extracted, window));
            phase = 2;
            return;
        } else if (phase == 1) {
            fixture.verify_archive();
            MUST(FileManager::extract_archive(fixture.archive, fixture.extracted, window));
            phase = 2;
            return;
        } else {
            fixture.verify_extracted();
            VERIFY(FileManager::extract_archive(fixture.archive, fixture.extracted, window).is_error());
        }
        if (large)
            VERIFY(live_ticks >= 3);
        complete = true;
        label.set_text("Names and contents verified. Ready to close."_string);
        dbgln("ZIP TEST verified: {} live_ticks={}", scenario, live_ticks);
    });
    timer->start();
    auto rc = app->exec();
    if (scenario == "close"sv) {
        fixture.verify_archive();
        VERIFY(live_ticks >= 3);
        dbgln("ZIP TEST close verified: archive intact, live_ticks={}", live_ticks);
    } else {
        VERIFY(complete);
    }
    VERIFY(!FileManager::archive_operations_in_progress());
    int status;
    VERIFY(waitpid(-1, &status, WNOHANG) == -1 && errno == ECHILD);
    dbgln("ZIP TEST PASS {}: no remaining children or zombies", scenario);
    return rc;
}
