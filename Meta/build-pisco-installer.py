#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Build the V8.1 read-only planning prototype, using regular files only.

No loop device, mount, physical device, existing image or persistent profile is
written. Inputs are a fresh CMake runtime install and a pinned Linux kernel.
"""
import argparse
import ctypes
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid

REPO = Path(__file__).resolve().parent.parent
PISCO_COMMIT = "dcd4feae31a1aee6748a342a7f92d996993e94d6"
MIB = 1024 * 1024


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_tree(source, target):
    shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)


def make_root(stage, output, epoch):
    """Run only under fakeroot; all metadata changes stay in the private stage."""
    image = output / "root.ext2"
    if (not os.environ.get("FAKEROOTKEY") or not stage.is_dir() or not output.is_dir()
            or image.exists() or image.is_symlink() or Path("/dev") in output.resolve().parents):
        raise ValueError("Private staging, fakeroot and a NEW regular image outside /dev required")
    for path in [stage, *sorted(stage.rglob("*"))]:
        os.chown(path, 0, 0, follow_symlinks=False)
        os.utime(path, (epoch, epoch), follow_symlinks=False)
    for path in [stage / "home/anon", *(stage / "home/anon").rglob("*")]:
        os.chown(path, 100, 100, follow_symlinks=False)
    os.chmod(stage / "home/anon", 0o700)
    os.chmod(stage / "root", 0o700)
    os.chmod(stage / "tmp", 0o1777)
    os.chmod(stage / "etc/shadow", 0o600)
    os.chown(stage / "etc/WindowServer.ini", 13, 13)
    os.chmod(stage / "etc/WindowServer.ini", 0o664)
    privileged = {"su": (1, 0o4750), "passwd": (1, 0o4755), "keymap": (3, 0o4750),
                  "shutdown": (3, 0o4750), "reboot": (3, 0o4750), "pls": (1, 0o4750),
                  "Escalator": (1, 0o4750), "utmpupdate": (5, 0o2755), "timezone": (3, 0o4750)}
    for name, (gid, mode) in privileged.items():
        if (stage / "bin" / name).is_file():
            os.chown(stage / "bin" / name, 0, gid)
            os.chmod(stage / "bin" / name, mode)
    os.chown(stage / "var/run/utmp", 0, 5)
    os.chmod(stage / "var/run/utmp", 0o664)
    manifest = []
    for path in sorted(stage.rglob("*")):
        metadata = path.lstat()
        item = dict(path="/" + str(path.relative_to(stage)), uid=metadata.st_uid,
                    gid=metadata.st_gid, mode=oct(stat.S_IMODE(metadata.st_mode)))
        if path.is_symlink():
            item["link"] = os.readlink(path)
        elif path.is_file():
            item["sha256"] = sha(path)
        manifest.append(item)
    (output / "root-manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    root_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, "pisco-v73-clean-root:" + sha(output / "root-manifest.json")))
    environment = {**os.environ, "E2FSPROGS_FAKE_TIME": str(epoch)}
    # Fixed geometry/UUID/hash seed and timestamps make the payload reproducible.
    run("mke2fs", "-q", "-F", "-t", "ext2", "-b", "1024", "-U", root_uuid,
        "-E", "hash_seed=" + root_uuid + ",lazy_itable_init=0", "-d", stage,
        output / "root.ext2", str(1536 * 1024), env=environment)
    normalize_root_times(stage, output / "root.ext2", epoch)
    run("e2fsck", "-f", "-n", output / "root.ext2")


def normalize_root_times(stage, image, epoch):
    # mke2fs -d imports host ctime/atime, including accesses made by hashing.
    # Normalize only this newly created image; never repair a source filesystem.
    commands = image.with_suffix(".times.debugfs")
    paths = ["/", "/lost+found", *["/" + str(path.relative_to(stage)) for path in sorted(stage.rglob("*"))]]
    def quoted(value):
        if "\n" in value or "\r" in value:
            raise ValueError("Newlines are unsupported in image paths")
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    commands.write_text("".join(f"set_inode_field {quoted(path)} {field} 0x{epoch:x}\n"
                               for path in paths for field in ["atime", "ctime", "mtime", "crtime"]))
    with image.with_suffix(".times.log").open("w") as log:
        run("debugfs", "-w", "-f", commands, image, stdout=log, stderr=log,
            env={**os.environ, "E2FSPROGS_FAKE_TIME": str(epoch)})


def prepare_payload(runtime, output, epoch):
    stage = output / "pisco-root"
    stage.mkdir()
    # Explicit runtime paths, never a persistent guest root or personal home.
    for name in ["bin", "etc", "res", "boot", "usr/lib", "usr/share"]:
        for base in [REPO / "Base", runtime]:
            if (base / name).is_dir():
                copy_tree(base / name, stage / name)
    for path in (stage / "usr/lib").rglob("objects"):
        if path.is_dir():
            shutil.rmtree(path)
    for name in ["gcc", "g++", "clang", "cc", "c++", "ld", "as"]:
        (stage / "bin" / name).unlink(missing_ok=True)
    for path in (stage / "bin").glob("Test*"):
        path.unlink()
    for path in (stage / "bin").glob("test-*"):
        if path.name != "test-bin":
            path.unlink()
    for name in ["run-tests", "test_env", "test262-runner", "update-cpp-test-results"]:
        (stage / "bin" / name).unlink(missing_ok=True)
    toolchain = REPO / "Toolchain/Local/x86_64/x86_64-serenity/lib"
    for path in sorted(toolchain.glob("*.so*")):
        target = stage / "usr/lib" / path.name
        target.unlink(missing_ok=True)
        if path.is_symlink():
            target.symlink_to(os.readlink(path))
        else:
            shutil.copyfile(path, target)
    for name in ["home/anon/Desktop", "home/anon/.config", "root", "tmp", "dev", "proc", "sys",
                 "mnt", "var/run", "usr/local", "usr/Ports", "usr/Tests", "usr/bin"]:
        (stage / name).mkdir(parents=True, exist_ok=True)
    # Whitelisted factory configuration only; no Base or development home copy.
    (stage / "home/anon/.config/SystemServer.ini").write_text("[Terminal]\nWorkingDirectory=/home/anon\n")
    # Let V7.3 seed its own current defaults; the legacy QuickLaunch group
    # would be migrated by appending it to those defaults.
    for label, destination in [("Home", "/home/anon"), ("Terminal", "/bin/Terminal"),
                               ("Text Editor", "/bin/TextEditor"), ("Help", "/bin/Help")]:
        (stage / "home/anon/Desktop" / label).symlink_to(destination)
    aliases = [("init", "/bin/init"), ("bin/sh", "Shell"), ("usr/bin/env", "/bin/env"),
               ("bin/test", "test-bin"), ("bin/install", "install-bin"),
               ("bin/[", "test"), ("bin/more", "less"), ("bin/pkg", "/bin/PackageManager"),
               ("bin/runc", "/bin/RunContainer")]
    aliases += [("bin/" + name, "checksum") for name in ["b2sum", "md5sum", "sha1sum", "sha256sum", "sha512sum"]]
    for path, target in aliases:
        (stage / path).unlink(missing_ok=True)
        (stage / path).symlink_to(target)
    (stage / "etc/shells").write_text("/bin/sh\n")
    (stage / "var/run/utmp").write_text("{}\n")
    (stage / "etc/PiscoRelease").write_text("PISCO OS V7.3 clean factory payload\nsource=" + PISCO_COMMIT + "\n")
    shadow = stage / "etc/shadow"
    shadow.write_text("\n".join(line.split(":")[0] + ":!:" + ":".join(line.split(":")[2:])
                                for line in shadow.read_text().splitlines()) + "\n")
    config = stage / "etc/WindowServer.ini"
    config.write_text(config.read_text().replace("Name=Default", "Name=Modern Dark"))
    # Assert the three consolidation binaries and current GUI/Gfx are installed.
    for guest, host in [("bin/Taskbar", "Services/Taskbar/Taskbar"),
                        ("bin/WindowServer", "Services/WindowServer/WindowServer"),
                        ("bin/Settings", "Applications/Settings/Settings"),
                        ("usr/lib/libgui.so.serenity", "Libraries/LibGUI/libgui.so.serenity"),
                        ("usr/lib/libgfx.so.serenity", "Libraries/LibGfx/libgfx.so.serenity")]:
        if sha(stage / guest) != sha(REPO / "Build/x86_64/Userland" / host):
            raise ValueError("Stale runtime install: " + guest)
    run("fakeroot", sys.executable, __file__, "--make-root", stage, output, epoch)
    shutil.copyfile(REPO / "Build/x86_64/Kernel/EFIPrekernel/Kernel.efi", output / "Kernel.efi")
    release = dict(pisco_commit=PISCO_COMMIT, epoch=epoch, factory_autologin=True,
                   account_setup_implemented=False, root_sha256=sha(output / "root.ext2"),
                   kernel_efi_sha256=sha(output / "Kernel.efi"), root_manifest_sha256=sha(output / "root-manifest.json"))
    (output / "release.json").write_text(json.dumps(release, indent=2) + "\n")
    (output / "SHA256SUMS").write_text("".join(sha(output / name) + "  " + name + "\n"
        for name in ["root.ext2", "Kernel.efi", "root-manifest.json", "release.json"]))


def decompress_module(source, target):
    if source.suffix != ".zst":
        shutil.copyfile(source, target)
        return
    lib = ctypes.CDLL("libzstd.so.1")
    lib.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    lib.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
    lib.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
    lib.ZSTD_decompress.restype = ctypes.c_size_t
    data = source.read_bytes()
    size = lib.ZSTD_getFrameContentSize(data, len(data))
    if size > 64 * MIB:
        raise ValueError("Invalid module frame size")
    result = ctypes.create_string_buffer(size)
    if lib.ZSTD_decompress(result, size, data, len(data)) != size:
        raise ValueError("Module decompression failed")
    target.write_bytes(result.raw)


def prepare_linux(kernel, version, output, epoch):
    root = output / "initramfs-root"
    for directory in ["bin", "sbin", "dev", "proc", "sys", "run", "tmp", "modules", "media/pisco"]:
        (root / directory).mkdir(parents=True, exist_ok=True)
    shutil.copyfile("/usr/bin/busybox", root / "bin/busybox")
    (root / "bin/busybox").chmod(0o755)
    if "statically linked" not in subprocess.check_output(["file", "/usr/bin/busybox"], text=True):
        raise ValueError("A static BusyBox is required")
    for applet in subprocess.check_output(["busybox", "--list"], text=True).splitlines():
        if applet != "busybox":
            (root / "bin" / applet).symlink_to("busybox")
    binaries = {}
    for name in ["blkid", "lsblk"]:
        binary = Path(shutil.which(name))
        binaries[name] = sha(binary)
        shutil.copyfile(binary, root / "sbin" / name)
        (root / "sbin" / name).chmod(0o755)
        for path in re.findall(r"/[^\s()]+", subprocess.check_output(["ldd", str(binary)], text=True)):
            path = Path(path)
            if path.is_file():
                target = root / str(path).lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    modules = []
    for name in ["ahci", "sd_mod", "xhci_pci", "xhci_hcd", "uhci_hcd", "ehci_pci", "usb_storage", "nvme", "virtio_blk"]:
        dependencies = subprocess.check_output(["modprobe", "--show-depends", "-S", version, name], text=True)
        for path in re.findall(r"^insmod (\S+)", dependencies, re.M):
            if path not in modules:
                modules.append(path)
    for index, path in enumerate(modules):
        decompress_module(Path(path), root / f"modules/{index:03d}.ko")
    for path in (REPO / "Meta/InstallerRuntime").iterdir():
        target = root / "init" if path.name == "init" else root / "bin" / path.name
        shutil.copyfile(path, target)
        target.chmod(0o755)
    for path in [root, *root.rglob("*")]:
        os.utime(path, (epoch, epoch), follow_symlinks=False)
    listing = "\0".join([".", *sorted(str(path.relative_to(root)) for path in root.rglob("*"))]) + "\0"
    cpio = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--reproducible", "--owner=0:0"],
                          input=listing.encode(), stdout=subprocess.PIPE, cwd=root, check=True).stdout
    (output / "initramfs.gz").write_bytes(gzip.compress(cpio, mtime=0))
    shutil.copyfile(kernel, output / "linux")
    inputs = {"linux": sha(kernel), "busybox": sha("/usr/bin/busybox"), **binaries}
    inputs.update({path: sha(path) for path in modules})
    return inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True, help="Fresh cmake --install --prefix staging tree")
    parser.add_argument("--linux-kernel", type=Path, required=True)
    parser.add_argument("--linux-version", required=True)
    parser.add_argument("--output", type=Path, required=True, help="NEW output directory")
    args = parser.parse_args()
    if not args.runtime.is_dir() or not args.linux_kernel.is_file():
        raise ValueError("Runtime directory and regular Linux kernel file required")
    output = args.output.absolute()
    if output.exists() or output.is_symlink() or Path("/dev") in output.resolve().parents:
        raise ValueError("Output must be a new directory outside /dev")
    if subprocess.check_output(["git", "diff", PISCO_COMMIT, "--", "Kernel", "Userland", "Base"], cwd=REPO):
        raise ValueError("PISCO source differs from validated V7.3")
    output.mkdir(parents=True)
    epoch = int(subprocess.check_output(["git", "show", "-s", "--format=%ct", PISCO_COMMIT], cwd=REPO))
    payload = output / "payload"
    payload.mkdir()
    prepare_payload(args.runtime.resolve(), payload, epoch)
    linux_inputs = prepare_linux(args.linux_kernel.resolve(), args.linux_version, output, epoch)
    medium_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, "pisco-v81-medium:" + sha(payload / "release.json")))
    data = output / "data-root"
    data.mkdir()
    (data / "PISCO_INSTALLER_ID").write_text("PISCO-V81\n")
    (data / "payload").mkdir()
    for name in ["root.ext2", "Kernel.efi", "root-manifest.json", "release.json", "SHA256SUMS"]:
        os.link(payload / name, data / "payload" / name)
    # Read-only data volume with room for the native filesystem payload.
    run("mke2fs", "-q", "-F", "-t", "ext2", "-b", "1024", "-L", "PISCO_V81_DATA", "-U", medium_uuid,
        "-d", data, output / "data.ext2", str(1792 * 1024))
    esp = output / "esp.fat"
    with esp.open("wb") as stream:
        stream.truncate(128 * MIB)
    run("mkfs.fat", "--invariant", "-F", "32", "-n", "PISCO_BOOT", esp)
    run("mmd", "-i", esp, "::/EFI", "::/EFI/BOOT", "::/boot")
    config = output / "grub.cfg"
    config.write_text('serial --unit=0 --speed=115200\nterminal_output console serial\nset timeout=0\nsearch --no-floppy --label --set=root PISCO_BOOT\nmenuentry "PISCO OS V8.1 installer prototype" {\n linux /boot/linux console=tty0 console=ttyS0,115200 pisco_media=' + medium_uuid + '\n initrd /boot/initramfs.gz\n}\n')
    run("grub-mkstandalone", "-O", "x86_64-efi", "--modules=part_gpt fat normal linux search search_label serial", "-o", output / "BOOTX64.EFI", "boot/grub/grub.cfg=" + str(config))
    for source, target in [(output / "BOOTX64.EFI", "::/EFI/BOOT/BOOTX64.EFI"),
                           (output / "linux", "::/boot/linux"), (output / "initramfs.gz", "::/boot/initramfs.gz")]:
        run("mcopy", "-i", esp, source, target)
    spec = importlib.util.spec_from_file_location("gpt", REPO / "Meta/install-serenity.py")
    gpt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gpt)
    # Reuse existing GPT/CRC assembly; ESP size is explicit for this Linux medium.
    gpt.ESP_SECTORS = 128 * MIB // 512
    gpt.ROOT_START = gpt.ESP_START + gpt.ESP_SECTORS
    sectors = ((gpt.ROOT_START + (output / "data.ext2").stat().st_size // 512 + 33 + 2047) // 2048) * 2048
    plan = dict(disk_bytes=sectors * 512, disk_uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, medium_uuid)),
                root_uuid=str(uuid.uuid5(uuid.NAMESPACE_URL, medium_uuid + ":partition")), root_sectors=(output / "data.ext2").stat().st_size // 512)
    image = output / "pisco-installer-v81.img"
    with image.open("w+b") as stream:
        stream.truncate(plan["disk_bytes"])
        gpt.write_partition_table(stream, plan)
        gpt.copy_partition(esp, stream, gpt.ESP_START)
        gpt.copy_partition(output / "data.ext2", stream, gpt.ROOT_START)
        stream.flush()
        os.fsync(stream.fileno())
    manifest = dict(pisco_commit=PISCO_COMMIT, linux_version=args.linux_version,
                    linux_inputs=linux_inputs, media_uuid=medium_uuid, payload=json.loads((payload / "release.json").read_text()),
                    image_sha256=sha(image), initramfs_sha256=sha(output / "initramfs.gz"),
                    grub_efi_sha256=sha(output / "BOOTX64.EFI"),
                    runtime_sources={path.name: sha(path) for path in sorted((REPO / "Meta/InstallerRuntime").iterdir())},
                    disk_writer_implemented=False)
    (output / "build-manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    (output / "SHA256SUMS").write_text(manifest["image_sha256"] + "  " + image.name + "\n")
    print(image, flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--make-root":
        make_root(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))
    else:
        main()
