#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Launch the installer or an existing native virtual target; never host disks."""
import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

REPO = Path(__file__).resolve().parent.parent


def eject_usb(path):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(30)
        connection.connect(str(path))
        stream = connection.makefile('rwb', buffering=0)
        stream.readline()
        for command in [dict(execute='qmp_capabilities'), dict(execute='device_del', arguments=dict(id='installer_usb'))]:
            stream.write((json.dumps(command) + '\n').encode())
            while True:
                reply = json.loads(stream.readline())
                if 'error' in reply:
                    raise RuntimeError('Virtual USB removal failed')
                if 'return' in reply:
                    break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True, help='Session directory: NEW for installation, existing with --boot')
    parser.add_argument('--image', type=Path, default=REPO / 'Build/pisco-installer-v84/build-5/pisco-installer-v84.img')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--boot', action='store_true', help='Boot an existing session target without the installer USB')
    args = parser.parse_args()
    work = args.session.absolute()
    image = None if args.boot else args.image.resolve(strict=True)
    if ((image is not None and not image.is_file()) or work.is_symlink()
            or Path('/dev') in work.resolve().parents or (not args.boot and work.exists())):
        raise ValueError('Regular USB image and NEW session outside /dev required')
    if any(',' in str(path) for path in [work, *([image] if image else [])]):
        raise ValueError('Comma in QEMU path')
    target = work / 'target.img'
    if args.boot:
        if not all(p.is_file() and not p.is_symlink() for p in [target, work / 'vars.fd']):
            raise ValueError('Regular target and private NVRAM required')
    else:
        work.mkdir(mode=0o700, parents=True)
        with target.open('xb') as f:
            f.truncate(3 * 1024**3)
        shutil.copyfile('/usr/share/OVMF/OVMF_VARS_4M.fd', work / 'vars.fd')
    command = ['qemu-system-x86_64', '-machine', 'q35', '-accel', 'tcg', '-cpu', 'max,-x2apic', '-smp', '1', '-m', '1G' if args.boot else '2G',
        '-display', 'none' if args.headless else 'gtk,gl=off,zoom-to-fit=off', '-vga', 'std', '-nic', 'none',
        '-drive', 'if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd',
        '-drive', 'if=pflash,format=raw,file=' + str(work / 'vars.fd'),
        '-device', 'ich9-ahci,id=ahci', '-drive', 'if=none,id=os,format=raw,file=' + str(target),
        '-device', 'ide-hd,drive=os,bus=ahci.0,serial=PISCO_V82_TARGET,bootindex=2']
    if not args.boot:
        command += ['-drive', 'if=none,id=usb,format=raw,readonly=on,file=' + str(image),
        '-device', 'qemu-xhci,id=xhci',
        '-device', 'usb-storage,drive=usb,bus=xhci.0,id=installer_usb,removable=on,serial=PISCO_V84_SOURCE,bootindex=1',
        '-device', 'usb-tablet,bus=xhci.0']
    command += ['-chardev', 'socket,id=console,path=' + str(work / 'console.sock') + ',server=on,wait=off,logfile=' + str(work / 'serial.log'),
        '-serial', 'chardev:console', '-qmp', 'unix:' + str(work / 'qmp.sock') + ',server=on,wait=off']
    (work / 'command.json').write_text(json.dumps(command, indent=2))
    with (work / 'qemu.log').open('w') as output:
        process = subprocess.Popen(command, stdout=output, stderr=output)
        try:
            while not (work / 'console.sock').exists():
                if process.poll() is not None:
                    raise RuntimeError('QEMU could not start; see session/qemu.log')
                time.sleep(.1)
            console = socket.socket(socket.AF_UNIX)
            console.connect(str(work / 'console.sock'))
            def drain():
                try:
                    while console.recv(65536):
                        pass
                except OSError:
                    pass
            threading.Thread(target=drain, daemon=True).start()
            removed = args.boot
            print('Starting native PISCO OS.' if args.boot else 'Use the graphical wizard. No installer-console commands are needed.', flush=True)
            while process.poll() is None:
                log = (work / 'serial.log').read_text(errors='replace') if (work / 'serial.log').exists() else ''
                if not removed and 'V84 PAGE 4' in log:
                    eject_usb(work / 'qmp.sock')
                    removed = True
                    (work / 'usb-removed.json').write_text(json.dumps(dict(after_verified_installation=True)))
                    print('Virtual USB removed after verification. Click Reiniciar in the wizard.', flush=True)
                time.sleep(.5)
            console.close()
        except KeyboardInterrupt:
            print('VM stopped by operator; do not use an unverified partial installation.', flush=True)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=20)


if __name__ == '__main__':
    main()
