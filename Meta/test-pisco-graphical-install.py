#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""QMP mouse/keyboard only after USB boot. No commands sent to installer console."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import secrets
import time
import subprocess

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('user_test', REPO / 'Meta/test-pisco-user-install.py')
user = importlib.util.module_from_spec(spec)
spec.loader.exec_module(user)


class UI:
    def __init__(self, guest):
        self.guest = guest
        self.monitor = guest.monitor
        self.monitor.__class__ = user.Monitor
        self.capture('welcome')
        data = (guest.work / 'screen.ppm').read_bytes().split(b'\n', 3)
        self.w, self.h = map(int, data[1].split())

    def capture(self, name):
        user.v82.old.screenshot(self.monitor, self.guest.work)
        (self.guest.work / (name + '.png')).write_bytes((self.guest.work / 'screen.png').read_bytes())

    def click(self, x, y):
        self.monitor.execute('input-send-event', {'events': [
            {'type': 'abs', 'data': {'axis': 'x', 'value': int(x * 32767 / self.w)}},
            {'type': 'abs', 'data': {'axis': 'y', 'value': int(y * 32767 / self.h)}}]})
        for down in [True, False]:
            self.monitor.execute('input-send-event', {'events': [{'type': 'btn', 'data': {'down': down, 'button': 'left'}}]})
            time.sleep(.15)
        time.sleep(.3)

    def next(self):
        self.click(self.w-130, self.h-50)

    def field(self, index, text):
        self.click(430, 239+70*index)
        self.monitor.key('home')
        self.monitor.key('shift-end')
        self.monitor.key('backspace')
        self.monitor.type(text)

    def select(self):
        offset = len(self.guest.log())
        self.click(100, 250)
        self.monitor.key('home')
        time.sleep(.3)
        self.capture('selected-disk')
        self.next()
        self.guest.wait('V84 PAGE 2', offset)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expect-failure', action='store_true', help='Missing-checker negative image')
    args = parser.parse_args()
    image, work = args.image.resolve(strict=True), args.output.absolute()
    if not image.is_file() or work.exists() or work.is_symlink() or Path('/dev') in work.resolve().parents:
        raise ValueError('Regular image and NEW output outside /dev required')
    if any(',' in str(p) for p in [image, work]):
        raise ValueError('Comma in QEMU paths')
    work.mkdir(parents=True, mode=0o700)
    target, guard = work / 'target.img', work / 'guard.img'
    with target.open('xb') as f:
        f.truncate(3 * 1024**3)
    with guard.open('xb') as f:
        f.truncate(128 * 1024**2)
        f.write(b'V84 UNRELATED DISK MUST SURVIVE\n')
    protected = {str(p): user.v82.old.sha(p) for p in [image, guard]}
    blank = user.v82.old.sha(target)
    password, wrong = secrets.token_hex(12), secrets.token_hex(12)
    results = []
    def record(name):
        results.append(dict(case=name, passed=True))
        (work / 'results.json').write_text(json.dumps(results, indent=2))
        print('PASS', name, flush=True)
    guest = user.v82.Guest(work / 'graphical', target, image, guard, memory='2G', tablet=True)
    try:
        guest.wait('V84 GUI READY', timeout=140)
        time.sleep(2)
        ui = UI(guest)
        offset = len(guest.log())
        ui.next()
        guest.wait('V84 PAGE 1', offset)
        ui.capture('disks')
        offset = len(guest.log())
        ui.next()
        assert 'V84 PAGE 2' not in guest.log()[offset:]
        record('autonomous-graphical-boot-no-default-disk-selection')
        ui.select()
        ui.click(ui.w-330, ui.h-50)
        guest.wait('V84 CANCELLED before writes')
        assert user.v82.old.sha(target) == blank
        record('GUI-cancellation-leaves-entire-target-unchanged')
        offset = len(guest.log())
        ui.next()
        guest.wait('V84 PAGE 1', offset)
        ui.select()
        for index, text in enumerate(['Pisco Graphical User', 'anon', password, wrong]):
            ui.field(index, text)
        offset = len(guest.log())
        ui.next()
        guest.wait('V84 VALIDATION password', offset)
        ui.capture('password-error')
        ui.field(3, password)
        for name in ['anon', '../invalid']:
            ui.field(1, name)
            offset = len(guest.log())
            ui.next()
            guest.wait('V84 VALIDATION identity', offset)
        record('GUI-rejects-inconsistent-password-duplicate-and-invalid-user')
        ui.field(1, 'piscogui')
        offset = len(guest.log())
        ui.next()
        guest.wait('V84 PAGE 3', offset)
        ui.capture('confirmation')
        offset = len(guest.log())
        ui.next()
        assert 'V84 STAGE' not in guest.log()[offset:]
        ui.click(67, 442)
        ui.next()
        guest.wait('V84 STAGE account', offset, timeout=600)
        ui.capture('progress')
        record('explicit-GUI-destructive-confirmation-and-real-progress')
        if args.expect_failure:
            guest.wait('V84 ERROR installation incomplete', offset, timeout=600)
            assert 'V82 INSTALLED VERIFIED' not in guest.log()
            ui.capture('failure')
            ui.click(130, ui.h-50)
            guest.wait('V84 PAGE 1', offset)
            ui.capture('failure-recovery')
            assert user.v82.old.sha(target) == blank
            record('failed-account-preparation-no-success-no-writes-return-to-disk-selection')
        else:
            text = guest.wait('V84 PAGE 4', offset, timeout=900)
            report = json.loads(re.findall(r'V82 INSTALLED VERIFIED (\{[^\n]+)', text)[-1])
            (work / 'installed.json').write_text(json.dumps(report, indent=2))
            ui.capture('complete')
            record('complete-installation-via-GUI-without-installer-console-commands')
            # Simulate physical removal, then click the real graphical restart.
            ui.next()
            assert guest.proc.poll() is None
            guest.monitor.execute('device_del', {'id': 'installer_usb'})
            time.sleep(4)
            offset = len(guest.log())
            ui.next()
            guest.wait('SerenityOS EFI Prekernel', offset, timeout=120)
            guest.wait('Entering WindowServer main loop', offset, timeout=240)
            time.sleep(20)
            ui.capture('first-login')
            record('graphical-restart-USB-removed-native-kernel-and-LoginServer')
            guest.monitor.type('piscogui')
            guest.monitor.key('tab')
            guest.monitor.type(password)
            offset = len(guest.log())
            guest.monitor.key('ret')
            guest.wait('login with sid=', offset, timeout=360)
            time.sleep(35)
            guest.monitor.type('echo PISCO-V84-PERSISTED > /home/piscogui/persist.txt')
            guest.monitor.key('ret')
            time.sleep(2)
            guest.monitor.type('sync')
            guest.monitor.key('ret')
            time.sleep(3)
            ui.capture('desktop')
            offset = len(guest.log())
            guest.monitor.execute('system_reset')
            guest.wait('Entering WindowServer main loop', offset, timeout=240)
            time.sleep(20)
            guest.monitor.type('piscogui')
            guest.monitor.key('tab')
            guest.monitor.type(password)
            offset = len(guest.log())
            guest.monitor.key('ret')
            guest.wait('login with sid=', offset, timeout=360)
            time.sleep(35)
            guest.monitor.type('cat /home/piscogui/persist.txt')
            guest.monitor.key('ret')
            time.sleep(3)
            guest.monitor.type('sync')
            guest.monitor.key('ret')
            time.sleep(3)
            ui.capture('persistence')
            record('native-account-login-files-and-second-login-after-reboot')
    finally:
        guest.stop()
    assert all(user.v82.old.sha(Path(p)) == value for p, value in protected.items())
    for path in work.rglob('*.log'):
        data = path.read_bytes()
        assert password.encode() not in data and wrong.encode() not in data
    record('source-and-unrelated-disk-unchanged-no-password-in-logs')
    if not args.expect_failure:
        root = work / 'installed-root.ext2'
        user.extract_root(target, root)
        assert user.read(root, '/home/piscogui/persist.txt').strip() == 'PISCO-V84-PERSISTED'
        with (work / 'fsck.log').open('w') as log:
            subprocess.run(['e2fsck', '-f', '-n', str(root)], stdout=log, stderr=log, check=True)
        user.v82.old.check_gpt(target)
        record('persistent-native-file-and-filesystem-integrity-offline-verified')
    (work / 'complete.json').write_text(json.dumps(dict(passed=True, cases=len(results),
        graphical_only_installation=True, installer_console_commands=0, negative=args.expect_failure), indent=2))


if __name__ == '__main__':
    main()
