#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Exercise the standalone commit protocol on an in-memory volatile device."""

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
    with tempfile.TemporaryDirectory(prefix="journal-commit-") as temporary:
        binary = Path(temporary) / "commit-test"
        subprocess.run([
            args.cxx, "-std=c++26", "-Wall", "-Wextra", "-Werror", "-Wno-literal-suffix",
            f"-I{root}", f"-I{root / 'Userland/Libraries'}", f"-I{build}",
            str(root / "Meta/test-journal-commit.cpp"), f"-L{build / 'lib'}",
            "-llagom-ak", f"-Wl,-rpath,{build / 'lib'}", "-Wl,--wrap=malloc", "-o", str(binary),
        ], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
