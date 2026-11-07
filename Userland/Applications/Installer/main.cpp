/*
 * Copyright (c) 2026, the SerenityOS developers.
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/Array.h>
#include <AK/ByteBuffer.h>
#include <Kernel/API/MajorNumberAllocation.h>
#include <LibCore/ArgsParser.h>
#include <LibCore/DirIterator.h>
#include <LibCore/File.h>
#include <LibCore/System.h>
#include <LibCore/Timer.h>
#include <LibCrypto/Checksum/CRC32.h>
#include <LibGUI/Application.h>
#include <LibGUI/BoxLayout.h>
#include <LibGUI/Button.h>
#include <LibGUI/ComboBox.h>
#include <LibGUI/ItemListModel.h>
#include <LibGUI/Icon.h>
#include <LibGUI/Label.h>
#include <LibGUI/MessageBox.h>
#include <LibGUI/Progressbar.h>
#include <LibGUI/TextBox.h>
#include <LibGUI/Window.h>
#include <LibMain/Main.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

// This initial backend accepts only our fixed 512-byte-sector, 128-entry GPT
// image layout and an entirely zero-filled whole storage device.
static u64 get64(u8 const* bytes)
{
    u64 value;
    memcpy(&value, bytes, sizeof(value));
    return value;
}

static u32 get32(u8 const* bytes)
{
    u32 value;
    memcpy(&value, bytes, sizeof(value));
    return value;
}

static void put64(u8* bytes, u64 value) { memcpy(bytes, &value, sizeof(value)); }
static void put32(u8* bytes, u32 value) { memcpy(bytes, &value, sizeof(value)); }

static ErrorOr<void> read_at(Core::File& file, Bytes bytes, u64 offset)
{
    TRY(file.seek(offset, SeekMode::SetPosition));
    TRY(file.read_until_filled(bytes));
    return {};
}

static ErrorOr<void> write_at(Core::File& file, ReadonlyBytes bytes, u64 offset)
{
    TRY(file.seek(offset, SeekMode::SetPosition));
    TRY(file.write_until_depleted(bytes));
    return {};
}

struct Installation {
    OwnPtr<Core::File> source;
    OwnPtr<Core::File> target;
    Array<u8, 512> header {};
    Array<u8, 16384> entries {};
    ByteBuffer buffer;
    ByteBuffer comparison;
    u64 source_size { 0 };
    u64 target_size { 0 };
    u64 offset { 0 };
    enum class Phase { Idle, CheckEmpty, Copy, Verify, Complete } phase { Phase::Idle };

    ErrorOr<void> prepare(StringView image, StringView disk)
    {
        auto input = TRY(Core::File::open(image, Core::File::OpenMode::Read));
        auto metadata = TRY(Core::System::fstat(input->fd()));
        if (!S_ISREG(metadata.st_mode) || metadata.st_size < 2 * 1024 * 1024 || metadata.st_size % 512)
            return Error::from_string_literal("Choose a regular, prepared UEFI disk image (not an ext2 image or a live disk).");
        source_size = metadata.st_size;
        TRY(read_at(*input, header, 512));
        auto checksum = get32(header.data() + 16);
        put32(header.data() + 16, 0);
        if (memcmp(header.data(), "EFI PART", 8) || get32(header.data() + 8) != 0x10000
            || get32(header.data() + 12) != 92 || get64(header.data() + 24) != 1
            || get64(header.data() + 32) != source_size / 512 - 1
            || get64(header.data() + 72) != 2 || get32(header.data() + 80) != 128
            || get32(header.data() + 84) != 128
            || Crypto::Checksum::CRC32(header.span().slice(0, 92)).digest() != checksum)
            return Error::from_string_literal("Invalid or unsupported GPT image. Use Meta/install-serenity.py to prepare it.");
        put32(header.data() + 16, checksum);
        TRY(read_at(*input, entries, 1024));
        if (Crypto::Checksum::CRC32(entries).digest() != get32(header.data() + 88))
            return Error::from_string_literal("The image partition table checksum is invalid.");
        if (get64(header.data() + 40) < 34 || get64(header.data() + 48) != source_size / 512 - 34)
            return Error::from_string_literal("Invalid GPT usable range.");
        for (size_t index = 0; index < 128; ++index) {
            auto* entry = entries.data() + index * 128;
            if (get64(entry) == 0 && get64(entry + 8) == 0)
                continue;
            auto first = get64(entry + 32);
            auto last = get64(entry + 40);
            if (first < get64(header.data() + 40) || first > last || last > get64(header.data() + 48))
                return Error::from_string_literal("A source partition lies outside the image.");
            for (size_t previous = 0; previous < index; ++previous) {
                auto* other = entries.data() + previous * 128;
                if ((get64(other) || get64(other + 8)) && first <= get64(other + 40) && last >= get64(other + 32))
                    return Error::from_string_literal("Source partitions overlap.");
            }
        }
        Array<u8, 512> backup;
        TRY(read_at(*input, backup, source_size - 512));
        auto expected_backup = header;
        put64(expected_backup.data() + 24, source_size / 512 - 1);
        put64(expected_backup.data() + 32, 1);
        put64(expected_backup.data() + 72, source_size / 512 - 33);
        put32(expected_backup.data() + 16, 0);
        put32(expected_backup.data() + 16, Crypto::Checksum::CRC32(expected_backup.span().slice(0, 92)).digest());
        if (backup != expected_backup)
            return Error::from_string_literal("The image backup GPT is invalid.");
        Array<u8, 16384> backup_entries;
        TRY(read_at(*input, backup_entries, source_size - 33 * 512));
        if (backup_entries != entries)
            return Error::from_string_literal("The image GPT copies do not match.");
        auto output = TRY(Core::File::open(disk, Core::File::OpenMode::ReadWrite | Core::File::OpenMode::DontCreate));
        auto target_metadata = TRY(Core::System::fstat(output->fd()));
        if (!S_ISBLK(target_metadata.st_mode)
            || major(target_metadata.st_rdev) != static_cast<unsigned>(Kernel::MajorAllocation::BlockDeviceFamily::Storage))
            return Error::from_string_literal("The destination must be a whole storage disk, not a partition or file.");
        u64 block_size = 0;
        TRY(Core::System::ioctl(output->fd(), STORAGE_DEVICE_GET_BLOCK_SIZE, &block_size));
        TRY(Core::System::ioctl(output->fd(), STORAGE_DEVICE_GET_SIZE, &target_size));
        if (block_size != 512 || target_size < source_size || target_size % 512)
            return Error::from_string_literal("The destination needs 512-byte sectors and must be at least as large as the image.");
        // Fault in destination pages in this process before raw DMA completions
        // copy into them from the kernel's I/O worker context.
        buffer = TRY(ByteBuffer::create_zeroed(1024 * 1024));
        comparison = TRY(ByteBuffer::create_zeroed(buffer.size()));
        source = move(input);
        target = move(output);
        offset = 0;
        phase = Phase::CheckEmpty;
        return {};
    }

    ErrorOr<void> finalize_gpt()
    {
        auto last = target_size / 512 - 1;
        // The root filesystem keeps its original size. Only relocate the backup
        // GPT; unused capacity remains available for future partition tooling.
        put64(header.data() + 32, last);
        put64(header.data() + 48, last - 33);
        put32(header.data() + 16, 0);
        put32(header.data() + 16, Crypto::Checksum::CRC32(header.span().slice(0, 92)).digest());
        auto backup = header;
        put64(backup.data() + 24, last);
        put64(backup.data() + 32, 1);
        put64(backup.data() + 72, last - 32);
        put32(backup.data() + 16, 0);
        put32(backup.data() + 16, Crypto::Checksum::CRC32(backup.span().slice(0, 92)).digest());
        TRY(write_at(*target, entries, (last - 32) * 512));
        TRY(write_at(*target, backup, last * 512));
        TRY(write_at(*target, header, 512));
        Array<u8, 512> mbr;
        TRY(read_at(*source, mbr, 0));
        put32(mbr.data() + 458, min(last, static_cast<u64>(0xffffffff)));
        TRY(write_at(*target, mbr, 0));
        TRY(Core::System::fsync(target->fd()));
        // Read back the metadata changed after the byte-for-byte verification.
        Array<u8, 512> check {};
        TRY(read_at(*target, check, 512));
        if (check != header)
            return Error::from_string_literal("Primary GPT read-back failed.");
        TRY(read_at(*target, check, last * 512));
        if (check != backup)
            return Error::from_string_literal("Backup GPT read-back failed.");
        Array<u8, 16384> check_entries {};
        TRY(read_at(*target, check_entries, (last - 32) * 512));
        if (check_entries != entries)
            return Error::from_string_literal("Backup GPT entries read-back failed.");
        return {};
    }

    ErrorOr<void> step()
    {
        auto limit = phase == Phase::CheckEmpty ? target_size : source_size;
        auto bytes = buffer.bytes().slice(0, min(static_cast<u64>(buffer.size()), limit - offset));
        if (phase == Phase::CheckEmpty) {
            TRY(read_at(*target, bytes, offset));
            for (auto byte : bytes) {
                if (byte != 0)
                    return Error::from_string_literal("This disk contains data. Nothing was written. Select a new, empty virtual disk.");
            }
        } else if (phase == Phase::Copy) {
            TRY(read_at(*source, bytes, offset));
            TRY(write_at(*target, bytes, offset));
        } else {
            TRY(read_at(*source, bytes, offset));
            auto check = comparison.bytes().slice(0, bytes.size());
            TRY(read_at(*target, check, offset));
            if (memcmp(bytes.data(), check.data(), bytes.size()))
                return Error::from_string_literal("Disk verification failed. Do not boot this destination.");
        }
        offset += bytes.size();
        if (offset == limit) {
            offset = 0;
            if (phase == Phase::CheckEmpty)
                phase = Phase::Copy;
            else if (phase == Phase::Copy) {
                TRY(Core::System::fsync(target->fd()));
                phase = Phase::Verify;
            } else {
                TRY(finalize_gpt());
                phase = Phase::Complete;
                source = nullptr;
                target = nullptr;
            }
        }
        return {};
    }
};

ErrorOr<int> serenity_main(Main::Arguments arguments)
{
    TRY(Core::System::pledge("stdio rpath wpath recvfd sendfd unix"));
    StringView source_path;
    StringView target_path;
    bool confirm_empty_disk = false;
    Core::ArgsParser parser;
    parser.add_option(source_path, "Prepared UEFI image", "image", 0, "path");
    parser.add_option(target_path, "Whole empty destination disk", "target", 0, "device");
    parser.add_option(confirm_empty_disk, "Run without a GUI; destination must be entirely zero-filled", "confirm-empty-disk", 0);
    parser.parse(arguments);
    if (confirm_empty_disk) {
        if (source_path.is_empty() || target_path.is_empty() || getuid() != 0)
            return Error::from_string_literal("Command-line installation needs root, --image and --target.");
        Installation job;
        TRY(job.prepare(source_path, target_path));
        while (job.phase != Installation::Phase::Complete)
            TRY(job.step());
        outln("Installation completed and verified.");
        return 0;
    }
    if (!target_path.is_empty())
        return Error::from_string_literal("Command-line --target requires --confirm-empty-disk.");
    auto app = TRY(GUI::Application::create(arguments));
    auto window = GUI::Window::construct();
    window->set_title("Install SerenityOS");
    window->resize(600, 340);
    window->center_on_screen();
    window->set_icon(TRY(GUI::Icon::try_create_default_icon("app-partition-editor"sv)).bitmap_for_size(16));
    if (getuid() != 0) {
        GUI::MessageBox::show_error(window, "Open Install SerenityOS from the menu with administrator privileges."sv);
        return 1;
    }
    auto main_widget = window->set_main_widget<GUI::Widget>();
    auto& widget = *main_widget;
    widget.set_fill_with_background_color(true);
    widget.set_layout<GUI::VerticalBoxLayout>(GUI::Margins { 12 }, 8);
    auto& heading = widget.add<GUI::Label>("Install on an empty disk"_string);
    heading.set_fixed_height(28);
    auto& instructions = widget.add<GUI::Label>("Choose a prepared UEFI .img file and a new empty virtual disk.\nThe current system and disks containing data are not eligible."_string);
    instructions.set_fixed_height(44);
    auto& source_label = widget.add<GUI::Label>("Prepared image path:"_string);
    source_label.set_fixed_height(20);
    auto& image = widget.add<GUI::TextBox>();
    image.set_text(source_path.is_empty() ? "/home/anon/installed-uefi.img"_string : TRY(String::from_utf8(source_path)));
    image.set_fixed_height(24);
    auto& disk_label = widget.add<GUI::Label>("Destination disk:"_string);
    disk_label.set_fixed_height(20);
    auto& disks = widget.add<GUI::ComboBox>();
    Vector<ByteString> paths;
    Core::DirIterator iterator("/dev"sv, Core::DirIterator::SkipParentAndBaseDir);
    while (iterator.has_next()) {
        auto path = ByteString::formatted("/dev/{}", iterator.next_path());
        auto metadata = Core::System::stat(path);
        if (!metadata.is_error() && S_ISBLK(metadata.value().st_mode)
            && major(metadata.value().st_rdev) == static_cast<unsigned>(Kernel::MajorAllocation::BlockDeviceFamily::Storage))
            paths.append(move(path));
    }
    disks.set_model(GUI::ItemListModel<ByteString>::create(paths));
    disks.set_only_allow_values_from_model(true);
    disks.set_fixed_height(24);
    if (!paths.is_empty())
        disks.set_selected_index(0);
    auto& status = widget.add<GUI::Label>("1. Select the image and disk, then start installation."_string);
    status.set_fixed_height(36);
    auto& progress = widget.add<GUI::Progressbar>();
    progress.set_fixed_height(22);
    auto& start = widget.add<GUI::Button>("Install"_string);
    start.set_fixed_height(28);
    start.set_enabled(!paths.is_empty());
    Installation job;
    auto timer = Core::Timer::create();
    bool active = false;
    timer->on_timeout = [&] {
        auto result = job.step();
        if (result.is_error()) {
            timer->stop();
            active = false;
            job.source = nullptr;
            job.target = nullptr;
            status.set_text("Installation stopped. See the error before retrying."_string);
            GUI::MessageBox::show_error(window, ByteString::formatted("{}", result.error()));
            image.set_enabled(true);
            disks.set_enabled(true);
            start.set_enabled(true);
            return;
        }
        if (job.phase == Installation::Phase::Complete) {
            timer->stop();
            active = false;
            progress.set_value(100);
            status.set_text("Installed and verified. Shut down, remove the source disk,\nand boot the destination using UEFI."_string);
            start.set_text("Close"_string);
            start.set_enabled(true);
            return;
        }
        auto phase = job.phase == Installation::Phase::CheckEmpty ? 0 : job.phase == Installation::Phase::Copy ? 1 : 2;
        auto limit = phase == 0 ? job.target_size : job.source_size;
        progress.set_value(phase * 33 + job.offset * 33 / limit);
        status.set_text(phase == 0 ? "2. Checking that the entire disk is empty..."_string
                                  : phase == 1 ? "3. Installing the system and boot files..."_string
                                               : "4. Verifying the installed disk..."_string);
    };
    start.on_click = [&](auto) {
        if (job.phase == Installation::Phase::Complete) {
            app->quit();
            return;
        }
        auto target = disks.text();
        auto question = ByteString::formatted("Install onto {}?\nOnly a completely empty disk will be accepted.\nDo not interrupt after copying starts.", target);
        if (GUI::MessageBox::show(window, question, "Confirm installation"sv, GUI::MessageBox::Type::Warning, GUI::MessageBox::InputType::YesNo) != GUI::Dialog::ExecResult::Yes)
            return;
        auto result = job.prepare(image.text(), target);
        if (result.is_error()) {
            GUI::MessageBox::show_error(window, ByteString::formatted("{}", result.error()));
            return;
        }
        active = true;
        image.set_enabled(false);
        disks.set_enabled(false);
        start.set_enabled(false);
        progress.set_value(0);
        timer->start(10);
    };
    window->on_close_request = [&] { return active ? GUI::Window::CloseRequestDecision::StayOpen : GUI::Window::CloseRequestDecision::Close; };
    window->show();
    return app->exec();
}
