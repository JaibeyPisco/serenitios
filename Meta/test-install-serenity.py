#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zlib

spec = importlib.util.spec_from_file_location("installer", Path(__file__).with_name("install-serenity.py"))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

launcher_spec = importlib.util.spec_from_file_location("launcher", Path(__file__).with_name("try-serenity.py"))
launcher = importlib.util.module_from_spec(launcher_spec)
launcher_spec.loader.exec_module(launcher)

export_spec = importlib.util.spec_from_file_location("exporter", Path(__file__).with_name("export-virtualbox.py"))
exporter = importlib.util.module_from_spec(export_spec)
export_spec.loader.exec_module(exporter)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.root = self.work / "root.ext2"
        self.kernel = self.work / "Kernel.efi"
        self.output = self.work / "installed.img"
        # Minimal headers for input validation. Integration uses a real ext2 FS.
        data = bytearray(4096)
        struct.pack_into("<I", data, 1028, 4)
        struct.pack_into("<H", data, 1080, 0xEF53)
        self.root.write_bytes(data)
        pe = bytearray(160)
        pe[:2] = b"MZ"
        struct.pack_into("<I", pe, 60, 64)
        pe[64:68] = b"PE\0\0"
        struct.pack_into("<H", pe, 68, 0x8664)
        struct.pack_into("<H", pe, 88, 0x20B)
        struct.pack_into("<H", pe, 156, 10)
        self.kernel.write_bytes(pe)

    def plan(self):
        return installer.make_plan(self.root, self.kernel, self.output)

    def test_plan_is_aligned_and_read_only(self):
        plan = self.plan()
        self.assertEqual(plan["root_start"] % 2048, 0)
        self.assertEqual(plan["disk_bytes"] % installer.MIB, 0)
        self.assertFalse(self.output.exists())

    def test_refuses_existing_output_and_dangling_symlink(self):
        self.output.write_text("preserve")
        with self.assertRaises(ValueError):
            self.plan()
        self.assertEqual(self.output.read_text(), "preserve")
        self.output.unlink()
        self.output.symlink_to(self.work / "missing")
        with self.assertRaises(ValueError):
            self.plan()

    def test_refuses_ext4_and_truncated_filesystems(self):
        data = bytearray(self.root.read_bytes())
        struct.pack_into("<I", data, 1120, 0x40)  # extents
        self.root.write_bytes(data)
        with self.assertRaises(ValueError):
            self.plan()
        struct.pack_into("<I", data, 1120, 0)
        struct.pack_into("<I", data, 1028, 4096)
        self.root.write_bytes(data)
        with self.assertRaises(ValueError):
            self.plan()

    def test_refuses_wrong_kernel_architecture(self):
        data = bytearray(self.kernel.read_bytes())
        struct.pack_into("<H", data, 68, 0xAA64)
        self.kernel.write_bytes(data)
        with self.assertRaises(ValueError):
            self.plan()

    def test_primary_and_backup_gpt_checksums_and_root_identity(self):
        plan = self.plan()
        # Sparse temporary file avoids allocating the complete disk in memory.
        with (self.work / "gpt.img").open("w+b") as disk:
            disk.truncate(plan["disk_bytes"])
            installer.write_partition_table(disk, plan)
            last = plan["disk_bytes"] // 512 - 1
            disk.seek(510)
            self.assertEqual(disk.read(2), b"\x55\xaa")
            entries = None
            for lba, alternate, table_lba in ((1, last, 2), (last, 1, last - 32)):
                disk.seek(lba * 512)
                header = bytearray(disk.read(512))
                self.assertEqual(header[:8], b"EFI PART")
                checksum, = struct.unpack_from("<I", header, 16)
                struct.pack_into("<I", header, 16, 0)
                self.assertEqual(zlib.crc32(header[:92]), checksum)
                self.assertEqual(struct.unpack_from("<QQ", header, 24), (lba, alternate))
                self.assertEqual(struct.unpack_from("<Q", header, 72)[0], table_lba)
                disk.seek(table_lba * 512)
                current = disk.read(16384)
                self.assertEqual(zlib.crc32(current), struct.unpack_from("<I", header, 88)[0])
                if entries is not None:
                    self.assertEqual(current, entries)
                entries = current
            self.assertEqual(uuid.UUID(bytes_le=entries[144:160]), uuid.UUID(plan["root_uuid"]))
            start, end = struct.unpack_from("<QQ", entries, 160)
            self.assertEqual(start, plan["root_start"])
            self.assertLess(end, last - 32)

    def test_failure_removes_intermediates_and_never_publishes(self):
        with patch.object(installer.shutil, "which", return_value="tool"), \
                patch.object(installer, "run", side_effect=subprocess.CalledProcessError(8, "e2fsck")):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.install(self.plan())
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.work.glob(".serenity-install-*")))

    @unittest.skipUnless(all(shutil.which(tool) for tool in
                             ("mke2fs", "e2fsck", "debugfs", "mkfs.fat", "mmd", "mcopy", "mtype")),
                         "Requires e2fsprogs, dosfstools and mtools")
    def test_real_copy_repair_and_no_overwrite_at_publication(self):
        with self.root.open("wb") as stream:
            stream.truncate(16 * installer.MIB)
        subprocess.run(["mke2fs", "-q", "-t", "ext2", "-F", str(self.root)], check=True)
        # Mark an unused block allocated, reproducing a block-bitmap mismatch.
        subprocess.run(["debugfs", "-w", "-R", "setb 3000", str(self.root)], check=True)
        original = self.root.read_bytes()
        plan = self.plan()
        with self.assertRaisesRegex(ValueError, "--repair-copy"):
            installer.install(plan)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.root.read_bytes(), original)
        installer.install(plan, repair_copy=True)
        self.assertEqual(self.root.read_bytes(), original)
        with self.output.open("rb") as disk:
            disk.seek(plan["root_start"] * 512)
            repaired = disk.read(len(original))
            self.assertNotEqual(repaired, original)
            check_copy = self.work / "repaired.ext2"
            check_copy.write_bytes(repaired)
            subprocess.run(["e2fsck", "-f", "-n", str(check_copy)], check=True)
            disk.seek(plan["esp_start"] * 512)
            esp = self.work / "extracted.fat"
            esp.write_bytes(disk.read(plan["esp_sectors"] * 512))
        cmdline = subprocess.check_output(["mtype", "-i", str(esp), "::/cmdline.txt"], text=True)
        self.assertEqual(cmdline.strip(), f"root=PARTUUID:{plan['root_uuid']}")
        subprocess.run(["mcopy", "-i", str(esp), "::/EFI/BOOT/BOOTX64.EFI", str(self.work / "boot.efi")], check=True)
        self.assertEqual((self.work / "boot.efi").read_bytes(), self.kernel.read_bytes())
        if shutil.which("qemu-img"):
            vdi = self.work / "installed.vdi"
            exporter.export(self.output, vdi)
            self.assertTrue(vdi.is_file())
            before = vdi.stat().st_size
            with self.assertRaises(ValueError):
                exporter.export(self.output, vdi)
            self.assertEqual(vdi.stat().st_size, before)
        # Simulate a destination created after planning: it must survive intact.
        self.assertEqual(self.output.stat().st_size, plan["disk_bytes"])
        self.output.unlink()
        original_link = installer.os.link

        def race(source, destination):
            destination.write_text("preserve racing output")
            original_link(source, destination)

        with patch.object(installer.os, "link", side_effect=race):
            with self.assertRaises(FileExistsError):
                installer.install(plan, repair_copy=True)
        self.assertEqual(self.output.read_text(), "preserve racing output")
        self.assertFalse(list(self.work.glob(".serenity-install-*")))


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.image = self.work / "installed.img"
        self.code = self.work / "OVMF_CODE.fd"
        self.variables = self.work / "OVMF_VARS.fd"
        self.code.write_bytes(b"firmware")
        self.variables.write_bytes(b"original variables")
        self.firmware = patch.object(launcher, "find_firmware", return_value=(self.code, self.variables))
        self.firmware.start()
        self.addCleanup(self.firmware.stop)

    def test_existing_image_only_needs_qemu_and_uses_private_firmware(self):
        self.image.write_bytes(b"existing installation")
        private_paths = []

        def boot(args):
            self.assertIn(f"if=none,id=os,format=raw,file={self.image},snapshot=on", args)
            value = next(arg for arg in args if arg.startswith("if=pflash,format=raw,file="))
            private = Path(value.split("file=", 1)[1])
            self.assertNotEqual(private, self.variables)
            self.assertEqual(private.read_bytes(), self.variables.read_bytes())
            private.write_bytes(b"guest writes")
            private_paths.append(private)
            from unittest.mock import Mock
            return Mock(wait=lambda: 0)

        with patch.object(launcher.shutil, "which", side_effect=lambda tool: "qemu" if tool == "qemu-system-x86_64" else None), \
                patch.object(launcher.subprocess, "Popen", side_effect=boot), \
                patch.object(launcher.subprocess, "run") as install:
            self.assertEqual(launcher.main(["--image", str(self.image)]), 0)
            install.assert_not_called()
        self.assertEqual(self.image.read_bytes(), b"existing installation")
        self.assertEqual(self.variables.read_bytes(), b"original variables")
        self.assertFalse(private_paths[0].exists())

    def test_missing_dependencies_do_not_create_an_image_or_start_qemu(self):
        with patch.object(launcher.shutil, "which", return_value=None), \
                patch.object(launcher.subprocess, "run") as install, \
                patch.object(launcher.subprocess, "Popen") as boot:
            self.assertEqual(launcher.main(["--image", str(self.image), "--repair-copy"]), 1)
            install.assert_not_called()
            boot.assert_not_called()
        self.assertFalse(self.image.exists())

    def test_failed_installation_never_starts_qemu(self):
        with patch.object(launcher.shutil, "which", return_value="tool"), \
                patch.object(launcher.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "installer")), \
                patch.object(launcher.subprocess, "Popen") as boot:
            self.assertEqual(launcher.main(["--image", str(self.image)]), 1)
            boot.assert_not_called()

    def test_dry_run_does_not_write_or_launch_processes(self):
        with patch.object(launcher.shutil, "which", return_value="tool"), \
                patch.object(launcher.subprocess, "run") as install, \
                patch.object(launcher.subprocess, "Popen") as boot:
            self.assertEqual(launcher.main(["--image", str(self.image), "--dry-run"]), 0)
            install.assert_not_called()
            boot.assert_not_called()
        self.assertFalse(self.image.exists())
        self.assertEqual(sorted(path.name for path in self.work.iterdir()), ["OVMF_CODE.fd", "OVMF_VARS.fd"])

    def test_copy_repair_option_reaches_installer(self):
        with patch.object(launcher.shutil, "which", return_value="tool"), \
                patch.object(launcher.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "installer")) as install, \
                patch.object(launcher.subprocess, "Popen") as boot:
            self.assertEqual(launcher.main(["--image", str(self.image), "--repair-copy"]), 1)
            self.assertIn("--repair-copy", install.call_args.args[0])
            boot.assert_not_called()

    def test_virtualbox_export_does_not_require_firmware_or_launch_qemu(self):
        self.image.write_bytes(b"existing installation")
        with patch.object(launcher, "find_firmware", side_effect=AssertionError("Not needed for export")), \
                patch.object(launcher.shutil, "which", return_value="tool"), \
                patch.object(launcher.subprocess, "run") as commands, \
                patch.object(launcher.subprocess, "Popen") as boot:
            self.assertEqual(launcher.main(["--image", str(self.image), "--virtualbox"]), 0)
            self.assertIn(str(self.image.with_suffix(".vdi")), commands.call_args.args[0])
            boot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
