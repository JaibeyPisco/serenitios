#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Validate bounded journal-inode tree decoding with no device writes."""

import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=Path("Build/storage-foundation"))
    parser.add_argument("--cxx", default="g++-14")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    build = args.build.resolve()
    with tempfile.TemporaryDirectory(prefix="journal-inode-") as temporary:
        binary = Path(temporary) / "inode-test"
        subprocess.run([
            args.cxx, "-std=c++26", "-Wall", "-Wextra", "-Werror", "-Wno-literal-suffix",
            f"-I{root}", f"-I{root / 'Userland/Libraries'}", f"-I{build}",
            str(root / "Meta/test-journal-inode.cpp"), f"-L{build / 'lib'}",
            "-llagom-ak", f"-Wl,-rpath,{build / 'lib'}", "-Wl,--wrap=malloc", "-o", str(binary),
        ], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
