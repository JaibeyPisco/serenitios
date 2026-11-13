#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Run the validated Modern desktop directly, without rebuilding the OS.

Normal runs keep guest changes in desktop.qcow2. --test uses a temporary
copy-on-write disk which is automatically removed when QEMU exits.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

REPO = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="Discard this session's guest changes on exit")
    parser.add_argument("--headless", action="store_true", help="Use QMP without opening the GTK window")
    args = parser.parse_args()
    work = REPO / "Build/desktop-modern"
    # A validated upgrade selects a new base/overlay pair without replacing V4.
    profile_path = work / "profile.json"
    try:
        profile = json.loads(profile_path.read_text()) if profile_path.exists() else {}
    except (OSError, ValueError) as error:
        parser.error(f"Cannot read desktop profile: {error}")
    if not isinstance(profile, dict):
        parser.error("Desktop profile must be a JSON object")
    base_name = profile.get("base", "modern.ext2")
    overlay_name = profile.get("overlay", "desktop.qcow2")
    if not all(isinstance(name, str) and bool(name) and Path(name).name == name for name in [base_name, overlay_name]) or base_name == overlay_name:
        parser.error("Desktop profile must name two distinct files inside Build/desktop-modern")
    base = work / base_name
    kernel = work / "Kernel"
    if not base.is_file() or not kernel.is_file():
        parser.error("Prepared desktop image/kernel missing; see Documentation/ModernDesktopLaunch.md")
    for command in ["qemu-system-x86_64", "qemu-img"]:
        if not shutil.which(command):
            parser.error(f"Missing {command}")
    with (work / "run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("The Modern desktop is already running")
        with tempfile.TemporaryDirectory(prefix="session-", dir=work) as temporary:
            temporary = Path(temporary)
            disk = temporary / "test.qcow2" if args.test else work / overlay_name
            if not disk.exists():
                subprocess.run(["qemu-img", "create", "-f", "qcow2", "-F", "raw", "-b", str(base), str(disk)], check=True)
            info = json.loads(subprocess.check_output(["qemu-img", "info", "--output=json", str(disk)], text=True))
            if info.get("format") != "qcow2" or Path(info.get("full-backing-filename", "")).resolve() != base.resolve():
                parser.error("Unexpected overlay/backing image; preserving the existing disk without running it")
            mode = "test" if args.test else "run"
            command = ["qemu-system-x86_64", "-machine", "q35", "-accel", "tcg", "-cpu", "max,-x2apic", "-m", "1G", "-smp", "1",
                       "-kernel", str(kernel), "-append", "root=ahci0:0:0 serial_debug disable_kaslr",
                       "-drive", f"file={disk},if=none,format=qcow2,id=root", "-device", "ich9-ahci,id=ahci",
                       "-device", "ide-hd,drive=root,bus=ahci.0", "-vga", "std", "-nic", "none",
                       "-display", "none" if args.headless else "gtk,gl=off,zoom-to-fit=off", "-name", "SerenityOS Modern Desktop",
                       "-qmp", f"unix:{work}/qmp.sock,server=on,wait=off", "-serial", f"file:{work}/last-{mode}.log"]
            (work / f"last-{mode}-command.json").write_text(json.dumps(command, indent=2) + "\n")
            print(f"Modern desktop: {disk} ({'temporary' if args.test else 'persistent'}), 1 CPU", flush=True)
            environment = os.environ.copy()
            environment["TMPDIR"] = str(temporary)
            return subprocess.call(command, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
