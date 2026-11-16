#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Build the guest-only V7.2 dock geometry/notification fixture using the configured Settings flags.

Does not install binaries, change images, or launch QEMU. The output directory
must be new. Run the fixtures in a disposable SerenityOS guest, not on the host.
"""

import argparse
import json
from pathlib import Path
import shlex
import subprocess

REPO = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=REPO / "Build/x86_64")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build = args.build.resolve(strict=True)
    database = json.loads((build / "compile_commands.json").read_text())
    entry = next(item for item in database if item["file"] == str(REPO / "Userland/Applications/Settings/main.cpp"))
    commands = subprocess.check_output(["ninja", "-C", str(build), "-t", "commands", "Settings"], text=True)
    segments = commands.splitlines()[-1].split("&&")
    link_template = next(shlex.split(segment) for segment in segments if "-o " in segment)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for source, name in [("test-modern-dock-geometry.cpp", "TestModernDockGeometry"), ("test-modern-dock-observer.cpp", "TestModernDockObserver")]:
        obj = output / (name + ".o")
        compile_command = shlex.split(entry["command"])
        for option in ("-MF", "-MT"):
            if option in compile_command:
                offset = compile_command.index(option)
                del compile_command[offset:offset + 2]
        if "-MD" in compile_command:
            compile_command.remove("-MD")
        compile_command[compile_command.index("-o") + 1] = str(obj)
        compile_command[compile_command.index(entry["file"])] = str(REPO / "Meta" / source)
        link_command = [word for word in link_template if not word.endswith(".o")]
        link_command[link_command.index("-o") + 1] = str(output / name)
        link_command.insert(link_command.index("-o"), str(obj))
        link_command.append(str(build / "Userland/Libraries/LibIPC/libipc.so"))
        with (output / (name + ".log")).open("w") as log:
            subprocess.run(compile_command, cwd=entry["directory"], stdout=log, stderr=subprocess.STDOUT, check=True)
            subprocess.run(link_command, cwd=build, stdout=log, stderr=subprocess.STDOUT, check=True)
        print(output / name)


if __name__ == "__main__":
    main()
