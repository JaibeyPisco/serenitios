#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Boot the actual recovery medium via OVMF and emulated USB, without any disks."""

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("launcher", REPO / "Meta/try-serenity.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media", type=Path, required=True)
    args = parser.parse_args()
    media = args.media.resolve(strict=True)
    work = Path(tempfile.mkdtemp(prefix="recovery-uefi-boot-", dir=REPO / "Build"))
    print(f"Artifacts: {work}", flush=True)
    code, variables = launcher.find_firmware()
    shutil.copyfile(variables, work / "OVMF_VARS.fd")
    cmd = ["qemu-system-x86_64", "-machine", "q35", "-accel", "tcg", "-cpu", "max", "-m", "768M",
           "-drive", f"if=pflash,format=raw,readonly=on,file={code}",
           "-drive", f"if=pflash,format=raw,file={work}/OVMF_VARS.fd",
           "-device", "qemu-xhci,id=usb", "-drive", f"if=none,id=media,format=raw,file={media},snapshot=on",
           "-device", "usb-storage,drive=media,bus=usb.0", "-display", "none", "-monitor", "none",
           "-nic", "none", "-serial", f"file:{work}/serial.log",
           "-qmp", f"unix:{work}/qmp.sock,server=on,wait=off"]
    (work / "command.json").write_text(json.dumps(cmd, indent=2) + "\n")
    with (work / "qemu.log").open("w") as log:
        guest = subprocess.Popen(cmd, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 120
            while True:
                output = (work / "serial.log").read_text(errors="replace") if (work / "serial.log").exists() else ""
                if "SERENITY_RECOVERY_READY" in output:
                    break
                if guest.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"Recovery USB did not reach its menu; see {work}")
                time.sleep(0.2)
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(15)
                connection.connect(str(work / "qmp.sock"))
                stream = connection.makefile("rb")
                json.loads(stream.readline())
                def execute(command, arguments=None):
                    request = dict(execute=command, id=command)
                    if arguments:
                        request["arguments"] = arguments
                    connection.sendall(json.dumps(request).encode() + b"\n")
                    while True:
                        reply = json.loads(stream.readline())
                        if reply.get("id") == command:
                            if "error" in reply:
                                raise RuntimeError(reply)
                            return reply["return"]
                execute("qmp_capabilities")
                time.sleep(1)
                execute("screendump", dict(filename=str(work / "menu.png"), format="png"))
            (work / "results.json").write_text(json.dumps(dict(uefi_usb_boot=True, rescue_menu_ready=True,
                                                              screenshot=str(work / "menu.png")), indent=2) + "\n")
            print("PASS: OVMF -> GPT/FAT USB -> GRUB -> Linux RAM rescue -> recovery menu", flush=True)
        finally:
            guest.terminate()
            try:
                guest.wait(timeout=10)
            except subprocess.TimeoutExpired:
                guest.kill()
                guest.wait()


if __name__ == "__main__":
    main()
