#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Exercise the desktop consolidation in an already prepared disposable guest.

Requires the V7.3, V7.2 geometry and observer fixtures and an initial Terminal.
Does not prepare a disk, install software, or control the persistent desktop.
"""

import argparse
import binascii
import importlib.util
import json
from pathlib import Path
import re
import struct
import time
import zlib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--close-cycles", type=int, default=20)
    parser.add_argument("--width", type=int, choices=[640, 1024], default=640)
    args = parser.parse_args()
    work = args.session.resolve(strict=True)
    spec = importlib.util.spec_from_file_location("monitor", Path(__file__).with_name("test-run-process-status.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monitor = module.Monitor(work / "qmp.sock")
    results = []
    width, height = args.width, 480 if args.width == 640 else 768

    def log():
        return (work / "serial.log").read_text(errors="replace")

    def wait(marker, offset=0, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            recent = log()[offset:]
            if any(value in recent for value in ["ASSERTION FAILED", "VERIFICATION FAILED", "KERNEL PANIC", "CRASH: CPU"]):
                raise RuntimeError(recent[-3000:])
            if marker in recent:
                return
            time.sleep(0.1)
        raise TimeoutError(marker)

    def shot(name):
        ppm = work / f"{name}.ppm"
        monitor.execute("screendump", {"filename": str(ppm)})
        magic, dimensions, maximum, pixels = ppm.read_bytes().split(b"\n", 3)
        actual_width, actual_height = map(int, dimensions.split())
        assert magic == b"P6" and maximum == b"255"
        assert (actual_width, actual_height) == (width, height)

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data))

        scan = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
        ppm.with_suffix(".png").write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(scan)) + chunk(b"IEND", b""))

    def terminal(command):
        monitor.key("esc")
        time.sleep(0.1)
        states = re.findall(r"DOCK STATE client=(\d+) window=(\d+) type=\d+ active=(true|false) minimized=(true|false).*?title=([^\r\n]*)", log())
        states = [state for state in states if "anon@" in state[4]]
        assert states
        state = states[-1]
        if state[2] != "true" or state[3] == "true":
            offset = len(log())
            monitor.key("meta_l-1")
            wait(f"client={state[0]} window={state[1]} type=1 active=true minimized=false", offset)
            time.sleep(0.2)
        monitor.key("ctrl-u")
        if command.endswith(" &"):
            monitor.type(command[:-2])
            monitor.key("spc")
            monitor.key("shift-7")
        else:
            monitor.type(command)
        monitor.key("ret")

    def record(case, **values):
        results.append(dict(case=case, passed=True, **values))
        (work / "matrix-results.json").write_text(json.dumps(results, indent=2))
        print(case, "PASS", flush=True)

    try:
        wait("title=anon@")
        for cycle in range(args.close_cycles):
            monitor.key("meta_l")
            time.sleep(0.4)
            monitor.type("Calculator")
            offset = len(log())
            monitor.key("down")
            monitor.key("ret")
            wait("title=Calculator", offset)
            client, window = re.findall(r"DOCK STATE client=(\d+) window=(\d+).*title=Calculator[\r\n]", log()[offset:])[-1]
            time.sleep(0.4)
            monitor.key("alt-f4")
            wait(f"DOCK REMOVED client={client} window={window}", offset)
            record(f"menu-lifetime-close-{cycle + 1}")
        for width, height in [(width, height)]:
            for theme, name in [("dark", "Modern Dark"), ("light", "Modern Light"), ("default", "Default")]:
                case = f"{width}-{theme}"
                offset = len(log())
                terminal(f"TestDesktopV73 theme {theme} &")
                wait(f"V73 THEME PASS {name}", offset)
                time.sleep(0.6)
                shot(case + "-desktop")

                offset = len(log())
                terminal("TestModernDockGeometry &")
                wait("DOCK STEP 0", offset)
                time.sleep(0.4)
                shot(case + "-maximized")
                wait("DOCK STEP 3", offset)
                shot(case + "-fullscreen")
                wait("DOCK GEOMETRY PASS", offset)
                record(case + "-geometry")

                offset = len(log())
                terminal("Settings &")
                wait("title=Settings", offset)
                time.sleep(0.6)
                client, window = re.findall(r"DOCK STATE client=(\d+) window=(\d+).*title=Settings[\r\n]", log()[offset:])[-1]
                shot(case + "-settings")
                offset = len(log())
                terminal(f"TestDesktopV73 check-frame {client} {window} &")
                wait("V73 FRAME PASS", offset)
                offset = len(log())
                monitor.key("meta_l-2")
                wait(f"client={client} window={window} type=1 active=true minimized=false", offset)
                monitor.key("ctrl-f")
                monitor.type("Mouse")
                time.sleep(0.4)
                shot(case + "-settings-filter")
                monitor.key("alt-f4")
                wait(f"DOCK REMOVED client={client} window={window}", offset)
                record(case + "-settings-frame-and-keyboard")

                monitor.key("meta_l")
                time.sleep(0.4)
                monitor.type("Calculator")
                time.sleep(0.3)
                shot(case + "-launcher")
                offset = len(log())
                monitor.key("down")
                monitor.key("ret")
                wait("title=Calculator", offset)
                calc_client, calc_window = re.findall(r"DOCK STATE client=(\d+) window=(\d+).*title=Calculator[\r\n]", log()[offset:])[-1]
                time.sleep(0.4)
                shot(case + "-calculator")
                monitor.key("alt-f4")
                wait(f"DOCK REMOVED client={calc_client} window={calc_window}", offset)
                record(case + "-launcher-keyboard-and-app-close")
        (work / "matrix-complete.json").write_text(json.dumps(dict(passed=True, cases=len(results)), indent=2))
    finally:
        monitor.close()


if __name__ == "__main__":
    main()
