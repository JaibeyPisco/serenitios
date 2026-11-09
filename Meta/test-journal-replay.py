#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Exercise prepared recovery with volatile-device failures, no disk images."""

import argparse
import importlib.util
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
    spec = importlib.util.spec_from_file_location("fixtures", root / "Meta/test-jbd2-reader.py")
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    with tempfile.TemporaryDirectory(prefix="journal-replay-") as directory:
        folder = Path(directory)
        binary = folder / "replay"
        subprocess.run([
            args.cxx, "-std=c++26", "-Wall", "-Wextra", "-Werror", "-Wno-literal-suffix",
            f"-I{root}", f"-I{root / 'Userland/Libraries'}", f"-I{build}",
            str(root / "Meta/test-journal-replay.cpp"), f"-L{build / 'lib'}",
            "-llagom-ak", f"-Wl,-rpath,{build / 'lib'}", "-o", str(binary),
        ], check=True)
        scenarios = {
            "committed": fixtures.fixture(),
            "uncommitted": fixtures.fixture([([(50, b"A" * 1024)], [], False)]),
            "revoked": fixtures.fixture([([(50, b"A" * 1024)], [], True), ([(51, b"B" * 1024)], [50], True)]),
            "wrapped": fixtures.fixture([([(50, b"A" * 1024)], [], True), ([(51, b"B" * 1024)], [], True)], start=12, sequence=0xFFFFFFFF),
            "same-target": fixtures.fixture([([(50, b"A" * 1024)], [], True), ([(50, b"B" * 1024)], [], True)]),
        }
        for name, (journal, _) in scenarios.items():
            path = folder / f"{name}.bin"
            path.write_bytes(journal)
            subprocess.run([str(binary), str(path)], check=True)
            print(f"PASS: {name}", flush=True)


if __name__ == "__main__":
    main()
