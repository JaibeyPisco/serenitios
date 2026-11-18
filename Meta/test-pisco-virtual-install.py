#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""V8.2 destructive tests confined to newly created, disposable QEMU files."""
import argparse
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

REPO = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


old = load('v81test', REPO / 'Meta/test-pisco-installer.py')
qmp = load('qmp', REPO / 'Meta/test-run-process-status.py')


class Monitor(qmp.Monitor):
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(30)
        self.socket.connect(str(path))
        self.stream = self.socket.makefile('rwb', buffering=0)
        self.stream.readline()
        self.execute('qmp_capabilities')

    def type(self, value):
        for char in value:
            if char == '>':
                self.key('shift-dot')
            else:
                super().type(char)


class Guest:
    def __init__(self, directory, target, image=None, guard=None, memory='1G'):
        self.work = directory
        directory.mkdir()
        shutil.copyfile('/usr/share/OVMF/OVMF_VARS_4M.fd', directory / 'vars.fd')
        cmd = ['qemu-system-x86_64', '-machine', 'q35', '-accel', 'tcg', '-cpu', 'max,-x2apic',
               '-m', memory, '-smp', '1', '-display', 'none', '-vga', 'std', '-nic', 'none',
               '-drive', 'if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd',
               '-drive', 'if=pflash,format=raw,file=' + str(directory / 'vars.fd'),
               '-device', 'ich9-ahci,id=ahci', '-drive', 'if=none,id=target,format=raw,file=' + str(target),
               '-device', 'ide-hd,drive=target,bus=ahci.0,serial=PISCO_V82_TARGET,bootindex=2',
               '-qmp', 'unix:' + str(directory / 'qmp.sock') + ',server=on,wait=off']
        if image:
            cmd += ['-drive', 'if=none,id=usb,format=raw,readonly=on,file=' + str(image),
                    '-device', 'qemu-xhci,id=xhci', '-device',
                    'usb-storage,drive=usb,bus=xhci.0,serial=PISCO_V82_SOURCE,bootindex=1',
                    '-chardev', 'socket,id=console,path=' + str(directory / 'console.sock')
                    + ',server=on,wait=off,logfile=' + str(directory / 'serial.log'), '-serial', 'chardev:console']
        else:
            cmd += ['-serial', 'file:' + str(directory / 'serial.log')]
        if guard:
            cmd += ['-drive', 'if=none,id=guard,format=raw,file=' + str(guard),
                    '-device', 'ide-hd,drive=guard,bus=ahci.1,serial=PISCO_V82_GUARD']
        (directory / 'command.json').write_text(json.dumps(cmd, indent=2))
        self.output = (directory / 'qemu.log').open('w')
        self.proc = subprocess.Popen(cmd, stdout=self.output, stderr=self.output)
        self.console = None
        for _ in range(150):
            if (directory / 'qmp.sock').exists():
                break
            time.sleep(.1)
        self.monitor = Monitor(directory / 'qmp.sock')
        if image:
            self.console = socket.socket(socket.AF_UNIX)
            self.console.connect(str(directory / 'console.sock'))
            threading.Thread(target=self.drain, daemon=True).start()

    def drain(self):
        try:
            while self.console.recv(65536):
                pass
        except OSError:
            pass

    def log(self):
        path = self.work / 'serial.log'
        return path.read_text(errors='replace') if path.exists() else ''

    def wait(self, marker, offset=0, timeout=240):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            text = self.log()[offset:]
            if marker in text:
                return text
            if 'V82 REJECTED/FAILED:' in text or 'V83 FAILED:' in text:
                raise RuntimeError(text[-3000:])
            if self.proc.poll() is not None or any(x in text for x in ['Kernel panic', 'KERNEL PANIC', 'CRASH: CPU']):
                raise RuntimeError(text[-3000:])
            time.sleep(.1)
        raise TimeoutError(marker + '\n' + self.log()[-4000:])

    def send(self, command):
        self.console.sendall((command + '\n').encode())

    def stop(self):
        if self.proc.poll() is None:
            self.monitor.execute('quit')
            self.proc.wait(timeout=20)
        self.monitor.close()
        if self.console:
            self.console.close()
        self.output.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    image = args.image.resolve(strict=True)
    work = args.output.absolute()
    if not image.is_file() or work.exists() or work.is_symlink() or Path('/dev') in work.resolve().parents:
        raise ValueError('Regular image and new output outside /dev required')
    if ',' in str(image) or ',' in str(work):
        raise ValueError('Comma in QEMU paths')
    work.mkdir(parents=True)
    results = []
    def record(name):
        results.append(dict(case=name, passed=True))
        (work / 'results.json').write_text(json.dumps(results, indent=2))
        print('PASS', name, flush=True)
    target = work / 'target.img'
    guard = work / 'guard.img'
    with target.open('xb') as f:
        f.truncate(3 * 1024**3)
    with guard.open('xb') as f:
        f.truncate(128 * 1024**2)
        f.write(b'V82 UNRELATED DATA MUST SURVIVE\n')
    original = {str(p): old.sha(p) for p in [image, guard]}
    blank_hash = old.sha(target)
    guest = Guest(work / 'installation', target, image, guard)
    try:
        guest.wait('V82 READY:')
        guest.wait('~ #')
        source = re.findall(r'SOURCE\|(/dev/\w+)', guest.log())[-1]
        rows = re.findall(r'DISK\|([^\r\n]+)', guest.log())
        disk = next(row.split('|')[0] for row in rows if 'PISCO_V82_TARGET' in row)
        other = next(row.split('|')[0] for row in rows if 'PISCO_V82_GUARD' in row)
        record('autonomous-USB-boot-and-distinct-disks')
        for device, reason in [(source, 'installer source'), (source + '1', 'only canonical'),
                               (other, 'unauthorized serial'), ('/dev/disk/by-id/not-authorized', 'only canonical')]:
            offset = len(guest.log())
            guest.send('pisco-install ' + device)
            text = guest.wait('V82 REJECTED/FAILED:', offset)
            assert reason in text, text
        record('reject-source-children-unauthorized-and-alias')
        offset = len(guest.log())
        guest.send('pisco-install ' + disk)
        guest.wait('Type exactly:', offset)
        guest.send('NO')
        guest.wait('confirmation cancelled', offset)
        assert old.sha(target) == blank_hash
        record('confirmation-required-no-writes-on-cancel')
        offset = len(guest.log())
        guest.send('pisco-install ' + disk)
        guest.wait('Type exactly:', offset)
        guest.send(f'INSTALL {disk} PISCO_V82_TARGET {3 * 1024**3}')
        text = guest.wait('V82 INSTALLED VERIFIED ', offset, timeout=600)
        report = json.loads(re.findall(r'V82 INSTALLED VERIFIED (\{[^\r\n]+)', text)[-1])
        (work / 'installed.json').write_text(json.dumps(report, indent=2))
        old.screenshot(guest.monitor, guest.work)
        record('confirmed-install-and-full-partition-readback')
        guest.send('poweroff -f')
        guest.proc.wait(timeout=25)
    finally:
        guest.stop()
    assert all(old.sha(Path(p)) == value for p, value in original.items())
    old.check_gpt(target)
    record('independent-GPT-CRCs-and-unrelated-disk-hashes')
    # Independent host-side checks before native kernel can change ext2.
    with target.open('rb') as f:
        for part in report['partitions']:
            f.seek(part['start'] * 512)
            import hashlib
            value = hashlib.sha256()
            left = part['bytes']
            while left:
                data = f.read(min(left, 1024**2))
                value.update(data)
                left -= len(data)
            assert value.hexdigest() == part['sha256']
        for start, end in [(34 * 512, 1048576),
                           ((report['partitions'][1]['start'] * 512 + report['partitions'][1]['bytes']),
                            target.stat().st_size - 33 * 512)]:
            f.seek(start)
            left = end - start
            while left:
                data = f.read(min(left, 1024**2))
                assert data and data.count(0) == len(data)
                left -= len(data)
    esp = str(target) + '@@1048576'
    for name in ['::/EFI/BOOT/grub.cfg', '::/cmdline.txt']:
        text = subprocess.check_output(['mtype', '-i', esp, name], text=True)
        assert report['root_partuuid'] in text and 'serial_debug' in text
    extracted_kernel = work / 'installed-Kernel.efi'
    subprocess.run(['mcopy', '-i', esp, '::/EFI/PISCO/Kernel.efi', str(extracted_kernel)], check=True)
    assert old.sha(extracted_kernel) == report['native_kernel_sha256']
    record('host-independent-partition-hashes-and-native-boot-config')
    guest = Guest(work / 'native-first', target)
    try:
        guest.wait('Entering WindowServer main loop', timeout=200)
        time.sleep(35)
        assert 'Linux version' not in guest.log() and 'SerenityOS EFI Prekernel' in guest.log()
        old.screenshot(guest.monitor, guest.work)
        shutil.copyfile(guest.work / 'screen.png', guest.work / 'desktop-first.png')
        record('USB-removed-fresh-NVRAM-GRUB-native-kernel-desktop')
        # Factory session starts a Terminal. Use its real native Shell.
        guest.monitor.type('echo PISCO-V82-PERSISTED > /home/anon/v82-persistence.txt')
        guest.monitor.key('ret')
        time.sleep(3)
        guest.monitor.type('sync')
        guest.monitor.key('ret')
        time.sleep(2)
        guest.monitor.type('reboot')
        offset = len(guest.log())
        guest.monitor.key('ret')
        guest.wait('Entering WindowServer main loop', offset, timeout=200)
        time.sleep(30)
        guest.monitor.type('cat /home/anon/v82-persistence.txt')
        guest.monitor.key('ret')
        time.sleep(3)
        old.screenshot(guest.monitor, guest.work)
        record('native-reboot-returned-to-desktop')
        guest.monitor.type('sync')
        guest.monitor.key('ret')
        time.sleep(2)
        guest.monitor.type('shutdown')
        guest.monitor.key('ret')
        guest.proc.wait(timeout=30)
    finally:
        guest.stop()
    # Inspect persisted file offline without altering the filesystem.
    root = work / 'installed-root.ext2'
    part = report['partitions'][1]
    with target.open('rb') as source, root.open('xb') as dest:
        source.seek(part['start'] * 512)
        remaining = part['bytes']
        while remaining:
            data = source.read(min(remaining, 1024**2))
            dest.write(data)
            remaining -= len(data)
    content = subprocess.check_output(['debugfs', '-R', 'cat /home/anon/v82-persistence.txt', str(root)], text=True)
    assert content.strip() == 'PISCO-V82-PERSISTED', content
    record('native-created-file-survives-reboot-offline-verified')
    with (work / 'fsck-after-shutdown.log').open('w') as output:
        subprocess.run(['e2fsck', '-f', '-n', str(root)], stdout=output, stderr=output, check=True)
    (work / 'complete.json').write_text(json.dumps(dict(passed=True, cases=len(results), usb_removed=True,
        native_kernel=True, linux_on_target=False, persistence_verified=True), indent=2))


if __name__ == '__main__':
    main()
