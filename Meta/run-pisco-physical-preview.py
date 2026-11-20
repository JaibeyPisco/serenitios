#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Run the V8.5A preview with one CPU and no writable data disk attached."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--session', type=Path, required=True, help='NEW directory')
    parser.add_argument('--controller', choices=['xhci', 'uhci'], default='xhci')
    parser.add_argument('--display', choices=['gtk', 'none'], default='gtk')
    args = parser.parse_args()
    image = args.image.resolve(strict=True)
    session = args.session.absolute()
    if not image.is_file() or session.exists() or session.is_symlink() or Path('/dev') in session.resolve().parents:
        raise ValueError('Regular image and NEW session outside /dev required')
    if ',' in str(image) + str(session):
        raise ValueError('Comma in QEMU path')
    manifest = json.loads((image.parent/'build-manifest.json').read_text())
    with image.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if manifest.get('mode') != 'preview-only' or manifest.get('installation_enabled') is not False or digest != manifest['image_sha256']:
        raise ValueError('Verified preview-only artifact required')
    session.mkdir(parents=True)
    shutil.copyfile('/usr/share/OVMF/OVMF_VARS_4M.fd', session/'vars.fd')
    cmd = ['qemu-system-x86_64', '-machine', 'q35,i8042=off', '-accel', 'tcg', '-cpu', 'max,-x2apic', '-smp', '1', '-m', '2G',
           '-display', args.display, '-vga', 'std', '-nic', 'none',
           '-drive', 'if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd',
           '-drive', 'if=pflash,format=raw,file='+str(session/'vars.fd'),
           '-device', ('qemu-xhci' if args.controller == 'xhci' else 'piix3-usb-uhci')+',id=usb',
           '-drive', 'if=none,id=source,format=raw,readonly=on,file='+str(image),
           '-device', 'usb-storage,drive=source,bus=usb.0,bootindex=1', '-device', 'usb-tablet,bus=usb.0',
           '-device', 'usb-kbd,bus=usb.0', '-serial', 'file:'+str(session/'serial.log')]
    (session/'command.json').write_text(json.dumps(cmd, indent=2))
    subprocess.run(cmd, check=True)


if __name__ == '__main__':
    main()
