#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Prepare a disposable V7.1 image and exercise the WM identity contract.

Requires built Taskbar, WindowServer and the guest identity fixture. The retained
image/profile are never edited. QEMU runs with one CPU and discarded writes.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import time

REPO = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--theme", default="Modern Dark")
    parser.add_argument("--linger", type=int, default=0)
    parser.add_argument("--desktop-fixtures", type=Path)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=768)
    args = parser.parse_args()
    work = args.output.resolve()
    work.mkdir(parents=True, exist_ok=False)
    image = work / "root.ext2"
    source = args.source.resolve(strict=True)
    digest = lambda p: hashlib.file_digest(p.open("rb"), "sha256").hexdigest()
    original = digest(source)
    subprocess.run(["cp", "--reflink=auto", "--sparse=always", source, image], check=True)

    def debug(command, writable=False):
        result = subprocess.run(["debugfs", *(["-w"] if writable else []), "-R", command, str(image)], text=True, capture_output=True, check=True)
        with (work / "install.log").open("a") as log:
            log.write(result.stdout + result.stderr)
        return result

    hashes = {}

    def install(host, guest, replace=True, mode="0100755"):
        import re
        owner = None
        if replace:
            stat = debug(f"stat {guest}").stdout
            owner = re.search(r"User:\s+(\d+)\s+Group:\s+(\d+)", stat)
        if replace:
            debug(f"rm {guest}", True)
        debug(f"write {host} {guest}", True)
        debug(f"set_inode_field {guest} mode {mode}", True)
        if owner:
            debug(f"set_inode_field {guest} uid {owner[1]}", True)
            debug(f"set_inode_field {guest} gid {owner[2]}", True)
        extracted = work / f"installed-{len(hashes)}"
        debug(f"dump {guest} {extracted}")
        assert digest(host) == digest(extracted), guest
        hashes[guest] = digest(host)

    build = REPO / "Build/x86_64"
    for host, guest in [
        (build / "Userland/Services/Taskbar/Taskbar", "/bin/Taskbar"),
        (build / "Userland/Services/WindowServer/WindowServer", "/bin/WindowServer"),
        (build / "Userland/Libraries/LibGUI/libgui.so.serenity", "/usr/lib/libgui.so.serenity"),
        (build / "Userland/Libraries/LibDesktop/libdesktop.so.serenity", "/usr/lib/libdesktop.so.serenity"),
        (build / "Userland/Applications/Run/Run", "/bin/Run"),
    ]:
        install(host, guest)
    fixture = args.fixture.resolve(strict=True)
    install(fixture, "/bin/TestApplicationIdentity", False)
    install(fixture, "/bin/UnknownApplicationIdentity", False)
    if args.desktop_fixtures:
        for name in ["TestModernDockObserver", "TestModernDockGeometry", "TestFileOperationErrors", "TestAsyncZip"]:
            host = args.desktop_fixtures.resolve() / name
            if host.exists():
                install(host, f"/bin/{name}", False)
    for name, executable, extra in [
        ("TestApplicationIdentity", "/bin/TestApplicationIdentity", ""),
        ("IdentityArguments", "/bin/IdentityArguments", "Arguments=profile\n"),
        ("IdentityTerminal", "/bin/IdentityTerminal", "RunInTerminal=true\n"),
    ]:
        af = work / f"{name}.af"
        af.write_text(f"[App]\nName={name}\nExecutable={executable}\n{extra}")
        install(af, f"/res/apps/{name}.af", False, "0100644")
    for guest in ["/etc/WindowServer.ini", "/home/anon/.config/WindowServer.ini"]:
        host = work / ("global-window.ini" if guest.startswith("/etc") else "user-window.ini")
        debug(f"dump {guest} {host}")
        if host.exists():
            import re
            text = host.read_text()
            text = re.sub(r"(?m)^Width=\d+", f"Width={args.width}", text)
            text = re.sub(r"(?m)^Height=\d+", f"Height={args.height}", text)
            text = re.sub(r"(?m)^Name=.*$", f"Name={args.theme}", text)
            host.write_text(text)
            install(host, guest, mode="0100644")
    service = work / "SystemServer.ini"
    debug(f"dump /home/anon/.config/SystemServer.ini {service}")
    executable = "TestModernDockObserver" if args.desktop_fixtures else "TestApplicationIdentity"
    service.write_text(service.read_text() + f"\n[IdentityProbe]\nExecutable=/bin/{executable}\nKeepAlive=false\n")
    install(service, "/home/anon/.config/SystemServer.ini", mode="0100644")
    with (work / "fsck.log").open("w") as log:
        subprocess.run(["e2fsck", "-f", "-n", str(image)], stdout=log, stderr=subprocess.STDOUT, check=True)
    command = ["qemu-system-x86_64", "-machine", "q35", "-accel", "tcg", "-cpu", "max,-x2apic", "-m", "1G", "-smp", "1",
               "-kernel", str(REPO / "Build/desktop-modern/Kernel"), "-append", "root=ahci0:0:0 serial_debug disable_kaslr",
               "-drive", f"file={image},if=none,format=raw,id=root,snapshot=on", "-device", "ich9-ahci,id=ahci", "-device", "ide-hd,drive=root,bus=ahci.0",
               "-vga", "std", "-display", "none", "-nic", "none", "-qmp", f"unix:{work}/qmp.sock,server=on,wait=off", "-serial", f"file:{work}/serial.log"]
    (work / "command.json").write_text(json.dumps(command, indent=2))
    spec = importlib.util.spec_from_file_location("monitor", REPO / "Meta/test-run-process-status.py")
    monitor_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monitor_module)
    with (work / "qemu.log").open("w") as log:
        guest = subprocess.Popen(command, stdout=log, stderr=log)
        monitor = None
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                serial = (work / "serial.log").read_text(errors="replace") if (work / "serial.log").exists() else ""
                if any(marker in serial for marker in ["VERIFICATION FAILED", "ASSERTION FAILED", "KERNEL PANIC"]):
                    raise RuntimeError("Guest assertion; inspect serial.log")
                if guest.poll() is not None:
                    raise RuntimeError("Guest exited before identity checks completed")
                if ("DOCK OBSERVER READY" if args.desktop_fixtures else "APPLICATION IDENTITY PASS") in serial:
                    break
                time.sleep(0.2)
            else:
                raise TimeoutError("No identity pass marker")
            monitor = monitor_module.Monitor(work / "qmp.sock")
            def screenshot(path):
                import struct, zlib, binascii
                ppm = path.with_suffix(".ppm")
                monitor.execute("screendump", {"filename": str(ppm)})
                magic, dimensions, maximum, pixels = ppm.read_bytes().split(b"\n", 3)
                width, height = map(int, dimensions.split())
                assert magic == b"P6" and maximum == b"255"
                def chunk(kind, data):
                    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data))
                scan = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
                path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(scan)) + chunk(b"IEND", b""))
            screenshot(work / ("desktop-ready.png" if args.desktop_fixtures else "identity-pass.png"))
            (work / "ready-for-classic-input").touch()
            if args.desktop_fixtures:
                monitor.close()
                monitor = None
                deadline = time.monotonic() + args.linger
                while guest.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.5)
                if guest.poll() is None:
                    monitor = monitor_module.Monitor(work / "qmp.sock")
                    monitor.execute("quit")
                    guest.wait(timeout=10)
                assert digest(source) == original
                (work / "desktop-provenance.json").write_text(json.dumps(dict(theme=args.theme, source_sha256=original, installed=hashes), indent=2))
                return
            offset = len((work / "serial.log").read_text(errors="replace"))
            for _ in range(3):
                monitor.key("meta_l-2")
                time.sleep(1)
            trace = (work / "serial.log").read_text(errors="replace")[offset:]
            import re
            states = re.findall(r"IDENTITY STATE client=(\d+) window=(\d+) active=(true|false) minimized=(true|false)", trace)
            restored_after_minimize = any(
                minimized == "true" and any(
                    later_client == client and later_window == window and active == "true" and later_minimized == "false"
                    for later_client, later_window, active, later_minimized in states[index + 1:]
                ) for index, (client, window, _, minimized) in enumerate(states)
            )
            assert restored_after_minimize, "Classic Super+2 did not minimize and then restore a window"
            monitor.screenshot(work / "classic-final.png")
            time.sleep(args.linger)
            monitor.execute("quit")
            guest.wait(timeout=10)
        finally:
            if monitor:
                monitor.close()
            if guest.poll() is None:
                guest.terminate()
                guest.wait(timeout=10)
    assert digest(source) == original
    (work / "results.json").write_text(json.dumps(dict(contract="pass", theme=args.theme, source_sha256=original, installed=hashes, classic_shortcut="Super+2 activation/minimize/restore passed", classic_states=states), indent=2))
    print(f"Identity contract PASS: {work}", flush=True)


if __name__ == "__main__":
    main()
