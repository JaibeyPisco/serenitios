#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Boot the preview with disposable writable SATA/NVMe/USB guards; never use host block devices."""
import argparse
import binascii
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import zlib

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('v82test', REPO / 'Meta/test-pisco-virtual-install.py')
v82 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v82)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def screenshot(monitor, path):
    ppm = path.with_suffix('.ppm')
    monitor.execute('screendump', {'filename': str(ppm)})
    magic, geometry, maximum, pixels = ppm.read_bytes().split(b'\n', 3)
    assert magic == b'P6' and maximum == b'255'
    w, h = map(int, geometry.split())
    def chunk(kind, content):
        return struct.pack('>I', len(content)) + kind + content + struct.pack('>I', binascii.crc32(kind + content))
    rows = b''.join(b'\0' + pixels[y*w*3:(y+1)*w*3] for y in range(h))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
    return w, h


def click(monitor, w, h, x, y):
    monitor.execute('input-send-event', {'events': [
        {'type': 'abs', 'data': {'axis': 'x', 'value': int(x*32767/w)}},
        {'type': 'abs', 'data': {'axis': 'y', 'value': int(y*32767/h)}}]})
    for down in [True, False]:
        monitor.execute('input-send-event', {'events': [{'type': 'btn', 'data': {'down': down, 'button': 'left'}}]})
        time.sleep(.15)


def keyboard_pixels_changed(directory):
    """Exclude the entry caret and the mouse at x=300; require visible text pixels."""
    def read(name):
        magic, geometry, maximum, pixels = (directory/name).read_bytes().split(b'\n', 3)
        assert magic == b'P6' and maximum == b'255'
        return tuple(map(int, geometry.split())), pixels
    geometry, before = read('preview.ppm')
    other, after = read('keyboard.ppm')
    assert geometry == other
    w, h = geometry
    assert w >= 640 and h >= 600
    changed = sum(before[(y*w+x)*3:(y*w+x+1)*3] != after[(y*w+x)*3:(y*w+x+1)*3]
                  for y in range(530, 555) for x in range(45, 250))
    assert changed > 200, 'Keyboard text not visible; inspect keyboard.png and input routing'
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--controller', choices=['xhci', 'uhci'], required=True)
    parser.add_argument('--ambiguous-source', action='store_true', help='Attach a second identical source; expect fail-closed')
    parser.add_argument('--usb-size-mib', type=int, help='Copy to a NEW larger virtual USB, preserving the original GPT')
    parser.add_argument('--skip-payload', action='store_true', help='Boot/input test only; explicitly omit in-guest full payload hashing')
    args = parser.parse_args()
    image = args.image.resolve(strict=True)
    work = args.output.absolute()
    if not image.is_file() or work.exists() or Path('/dev') in work.resolve().parents or ',' in str(work) + str(image):
        raise ValueError('Regular image and NEW output outside /dev required')
    work.mkdir(parents=True)
    distribution_sha = sha(image)
    if args.usb_size_mib is not None:
        if args.usb_size_mib <= image.stat().st_size // 1024**2 or args.usb_size_mib > 8192:
            raise ValueError('Larger USB must exceed image size and be at most 8192 MiB')
        copy = work/'larger-usb.img'
        subprocess.run(['cp', '--sparse=always', str(image), str(copy)], check=True)
        with copy.open('r+b') as stream:
            stream.truncate(args.usb_size_mib*1024**2)
        image = copy
    source_before = sha(image) if args.usb_size_mib is not None else distribution_sha
    guards = []
    for name in ['sata', 'nvme', 'usb']:
        disk = work / (name + '.img')
        with disk.open('xb') as stream:
            stream.truncate(128*1024**2)
            stream.write(('V85A ' + name + ' unrelated data must survive\n').encode())
        guards.append(disk)
    before = {p.name: sha(p) for p in guards}
    shutil.copyfile('/usr/share/OVMF/OVMF_VARS_4M.fd', work / 'vars.fd')
    bus = 'usb.0'
    cmd = ['qemu-system-x86_64', '-machine', 'q35,i8042=off', '-accel', 'tcg', '-cpu', 'max,-x2apic',
           '-m', '2G', '-smp', '1', '-display', 'none', '-vga', 'std', '-nic', 'none',
           '-drive', 'if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd',
           '-drive', 'if=pflash,format=raw,file=' + str(work / 'vars.fd'),
           '-device', ('qemu-xhci' if args.controller == 'xhci' else 'piix3-usb-uhci') + ',id=usb',
           '-drive', 'if=none,id=source,format=raw,readonly=on,file=' + str(image),
           '-device', 'usb-storage,drive=source,bus=' + bus + ',serial=PISCO_V85A_SOURCE,bootindex=1',
           '-device', 'usb-tablet,bus=' + bus, '-device', 'usb-kbd,bus=' + bus,
           '-device', 'ich9-ahci,id=ahci',
           '-drive', 'if=none,id=sata,format=raw,file=' + str(guards[0]),
           '-device', 'ide-hd,drive=sata,bus=ahci.0,serial=PISCO_GUARD_SATA',
           '-device', 'ide-cd,bus=ahci.2,id=emptycd',
           '-drive', 'if=none,id=nvme,format=raw,file=' + str(guards[1]),
           '-device', 'nvme,drive=nvme,serial=PISCO_GUARD_NVME',
           '-drive', 'if=none,id=guardusb,format=raw,file=' + str(guards[2]),
           '-device', 'usb-storage,drive=guardusb,bus=' + bus + ',serial=PISCO_GUARD_USB',
           '-qmp', 'unix:' + str(work / 'qmp.sock') + ',server=on,wait=off',
           '-chardev', 'socket,id=console,path=' + str(work / 'console.sock') + ',server=on,wait=off,logfile=' + str(work / 'serial.log'),
           '-serial', 'chardev:console']
    (work / 'command.json').write_text(json.dumps(cmd, indent=2))
    if args.ambiguous_source:
        index = cmd.index('if=none,id=guardusb,format=raw,file=' + str(guards[2]))
        cmd[index] = 'if=none,id=guardusb,format=raw,readonly=on,file=' + str(image)
        (work / 'command.json').write_text(json.dumps(cmd, indent=2))
    output = (work / 'qemu.log').open('w')
    proc = subprocess.Popen(cmd, stdout=output, stderr=output)
    console = monitor = None
    def log():
        path = work / 'serial.log'
        return path.read_text(errors='replace') if path.exists() else ''
    def wait(marker, timeout=300, allow_safety_stop=False):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            text = log()
            if any(x in text for x in (['V85A SAFETY STOP'] if not allow_safety_stop else []) + ['V85A SAFETY FAILURE', 'Kernel panic', 'V85A PAYLOAD FAILED']):
                raise RuntimeError(text[-6000:])
            if marker in text:
                return
            if proc.poll() is not None:
                raise RuntimeError('QEMU exited: ' + (work / 'qemu.log').read_text())
            time.sleep(.2)
        raise TimeoutError(marker + '\n' + log()[-5000:])
    results = []
    try:
        for _ in range(300):
            if (work / 'console.sock').exists():
                break
            if proc.poll() is not None:
                raise RuntimeError((work / 'qemu.log').read_text())
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
        monitor = v82.Monitor(work / 'qmp.sock')
        if args.ambiguous_source:
            wait('installer medium missing or ambiguous', allow_safety_stop=True)
            time.sleep(3)
            assert 'V85A GUI READY' not in log()
            screenshot(monitor, work/'fail-closed.png')
            monitor.execute('quit')
            proc.wait(timeout=30)
            assert {p.name: sha(p) for p in guards} == before and sha(image) == source_before
            (work/'results.json').write_text(json.dumps(dict(passed=['ambiguous-medium-fail-closed-no-GUI', 'all-images-unchanged'], image_sha256=source_before), indent=2))
            return
        wait('V85A GUI READY')
        wait('V85A POLICY VERIFIED')
        # The marker may arrive before the UART has finished the JSON line.
        state = None
        deadline = time.monotonic() + 30
        while state is None and time.monotonic() < deadline:
            for candidate in re.findall(r'V85A POLICY VERIFIED (\{[^\r\n]+\})[\r\n]', log()):
                try:
                    value = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if all('device' in row and 'readonly' in row and 'sysfs_readonly' in row and 'role' in row for row in value.get('disks', [])) and len(value.get('disks', [])) in [4, 5]:
                    state = value
                    break
            if state is None:
                time.sleep(.2)
        assert state is not None, 'No complete policy record received'
        assert len(state['disks']) in [4, 5], state  # Empty optical capacity can be zero.
        assert sum(d['role'] == 'installer-source-excluded' for d in state['disks']) == 1, state
        assert all(d['readonly'] and d['sysfs_readonly'] for d in state['disks']), state
        assert any(d['device'].startswith('/dev/nvme') for d in state['disks']), state
        assert len([d for d in state['disks'] if not d['device'].startswith('/dev/sr')]) == 4, state
        (work / 'verified-policy.json').write_text(json.dumps(state, indent=2))
        results += ['autonomous-UEFI-USB-boot', 'GUI-ready', 'source-excluded', 'SATA-NVMe-USB-BLKROGET-and-sysfs-readonly']
        results.append('empty-optical-drive-readonly-without-blocking-boot')
        time.sleep(2)
        w, h = screenshot(monitor, work / 'preview.png')
        click(monitor, w, h, 300, 540)
        monitor.type('pisco usb keyboard 123')
        time.sleep(.5)
        screenshot(monitor, work / 'keyboard.png')
        keyboard_pixels = keyboard_pixels_changed(work)
        results.append('USB-keyboard-visible-text-and-pointer')
        click(monitor, w, h, 350, h-45)
        wait('V85A INSTALL BLOCK VERIFIED')
        results.append('installer-entry-blocked')
        if args.controller == 'xhci' and not args.skip_payload:
            click(monitor, w, h, 130, h-45)
            wait('V85A PAYLOAD VERIFIED', timeout=600)
            results.append('full-native-payload-SHA256-in-guest')
        screenshot(monitor, work / 'final.png')
        click(monitor, w, h, w-120, h-45)
        proc.wait(timeout=60)
        assert proc.returncode == 0
        results.append('GUI-poweroff')
    finally:
        if proc.poll() is None:
            if monitor:
                monitor.execute('quit')
            else:
                proc.terminate()
            proc.wait(timeout=30)
        if monitor:
            monitor.close()
        if console:
            console.close()
        output.close()
    after = {p.name: sha(p) for p in guards}
    assert before == after
    assert sha(image) == source_before
    results += ['all-writable-guard-disk-bytes-unchanged', 'source-image-unchanged']
    (work / 'results.json').write_text(json.dumps(dict(controller=args.controller, passed=results, guard_before=before,
                                                     guard_after=after, image_sha256=source_before, distribution_sha256=distribution_sha,
                                                     usb_bytes=image.stat().st_size, payload_check_skipped=args.skip_payload or args.controller != 'xhci',
                                                     keyboard_visible_pixels=keyboard_pixels), indent=2))
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
