#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Real native LoginServer authentication on a NEW disposable installation."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import secrets
import string
import subprocess
import time

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('v82test', REPO / 'Meta/test-pisco-virtual-install.py')
v82 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v82)


class Monitor(v82.Monitor):
    def key(self, key):
        response = self.execute('human-monitor-command', {'command-line': f'sendkey {key} 50'})
        if response:
            raise ValueError('QEMU rejected keyboard injection')
        time.sleep(.15)

    def type(self, value):
        for char in value:
            if char in {'?': 'shift-slash', '&': 'shift-7', ';': 'semicolon'}:
                self.key({'?': 'shift-slash', '&': 'shift-7', ';': 'semicolon'}[char])
            else:
                super().type(char)


def account(guest, password, edit=False):
    guest.wait('V83 Full name:')
    offset = len(guest.log())
    guest.send('Pisco Test User')
    guest.wait('V83 Username:', offset)
    guest.send('piscotest')
    guest.wait('V83 Password:', offset)
    guest.send(password)
    guest.wait('V83 Repeat password:', offset)
    guest.send(password)
    guest.wait('V83 Accept account?', offset)
    guest.send('EDIT' if edit else 'YES')
    return offset


def extract_root(target, destination):
    with target.open('rb') as src, destination.open('xb') as dst:
        src.seek(129 * 1024**2)
        left = 1536 * 1024**2
        while left:
            data = src.read(min(left, 1024**2))
            if data.count(0) == len(data):
                dst.seek(len(data), 1)
            else:
                dst.write(data)
            left -= len(data)
        dst.truncate(1536 * 1024**2)


def read(root, path):
    return subprocess.check_output(['debugfs', '-R', 'cat ' + path, str(root)], stderr=subprocess.DEVNULL, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    image, work = args.image.resolve(strict=True), args.output.absolute()
    if not image.is_file() or work.exists() or work.is_symlink() or Path('/dev') in work.resolve().parents:
        raise ValueError('Regular image and new output outside /dev required')
    if any(',' in str(path) for path in [image, work]):
        raise ValueError('Comma in QEMU paths')
    work.mkdir(parents=True)
    password = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(24))
    wrong = ''.join(secrets.choice(string.ascii_lowercase) for _ in range(24))
    results = []
    def record(case):
        results.append(dict(case=case, passed=True))
        (work / 'results.json').write_text(json.dumps(results, indent=2))
        print('PASS', case, flush=True)
    target, guard = work / 'target.img', work / 'guard.img'
    with target.open('xb') as f:
        f.truncate(3 * 1024**3)
    with guard.open('xb') as f:
        f.truncate(128 * 1024**2)
        f.write(b'V83 PROTECTED UNRELATED DATA\n')
    protected = {str(path): v82.old.sha(path) for path in [image, guard]}
    blank = v82.old.sha(target)
    guest = v82.Guest(work / 'installation', target, image, guard, memory='2G')
    try:
        guest.wait('V83 READY:')
        guest.wait('~ #')
        source = re.findall(r'SOURCE\|(/dev/\w+)', guest.log())[-1]
        for device in [source, source + '1', '/dev/sdb', '/dev/disk/by-id/unknown']:
            offset = len(guest.log())
            guest.send('pisco-install ' + device)
            guest.wait('V83 FAILED:', offset)
        record('V82-source-and-unauthorized-target-protections-preserved')
        offset = len(guest.log())
        guest.send('pisco-install /dev/sda')
        guest.wait('V83 Full name:', offset)
        for username in ['anon', '../invalid']:
            offset = len(guest.log())
            guest.send('Pisco Test User')
            guest.wait('V83 Username:', offset)
            guest.send(username)
            guest.wait('V83 INVALID or duplicate username', offset)
        offset = len(guest.log())
        guest.send('invalid:name')
        guest.wait('V83 INVALID full name', offset)
        record('duplicate-invalid-usernames-and-invalid-full-name-rejected')
        offset = len(guest.log())
        guest.send('Pisco Test User')
        guest.wait('V83 Username:', offset)
        guest.send('piscotest')
        guest.wait('V83 Password:', offset)
        guest.send(password)
        guest.wait('V83 Repeat password:', offset)
        guest.send(wrong)
        guest.wait('V83 INVALID password:', offset)
        record('password-confirmation-mismatch-rejected')
        offset = account(guest, password, edit=True)
        guest.wait('V83 Full name:', offset)
        offset = account(guest, password)
        guest.wait('Type exactly:', offset)
        guest.send('NO')
        guest.wait('V83 FAILED:', offset)
        assert v82.old.sha(target) == blank
        record('account-edit-and-destructive-cancellation-without-writes')
        offset = len(guest.log())
        guest.send('pisco-install /dev/sda')
        guest.wait('V83 Full name:', offset)
        offset = account(guest, password)
        guest.wait('Type exactly:', offset)
        guest.send('INSTALL /dev/sda PISCO_V82_TARGET 3221225472')
        text = guest.wait('V82 INSTALLED VERIFIED ', offset, timeout=900)
        report = json.loads(re.findall(r'V82 INSTALLED VERIFIED (\{[^\n]+)', text)[-1])
        assert report['user'] == dict(name='piscotest', uid=1000, gid=1000)
        (work / 'installed.json').write_text(json.dumps(report, indent=2))
        record('mandatory-account-verified-before-native-disk-completion')
        guest.send('poweroff -f')
        guest.proc.wait(timeout=30)
    finally:
        guest.stop()
    assert all(v82.old.sha(Path(path)) == value for path, value in protected.items())
    v82.old.check_gpt(target)
    root = work / 'root-before-login.ext2'
    extract_root(target, root)
    passwd, group, shadow = [read(root, '/etc/' + name) for name in ['passwd', 'group', 'shadow']]
    assert 'piscotest:x:1000:1000:Pisco Test User:/home/piscotest:/bin/Shell' in passwd
    assert 'wheel:x:1:anon' in group and 'phys:x:3:window,anon' in group
    for name in ['window', 'audio', 'users']:
        assert 'piscotest' in next(line for line in group.splitlines() if line.startswith(name + ':'))
    assert all(line.split(':')[1] == '!' for line in shadow.splitlines() if not line.startswith('piscotest:'))
    verifier = next(line.split(':')[1] for line in shadow.splitlines() if line.startswith('piscotest:'))
    import base64
    fields = verifier.split('$')
    assert fields[:3] == ['', 'p', '600000']
    assert fields[4] == base64.b64encode(hashlib.pbkdf2_hmac('sha256', password.encode(), fields[3].encode(), 600000, 24)).decode()
    assert '--auto-login' not in read(root, '/etc/SystemServer.ini')
    for path in ['/home/piscotest', '/home/piscotest/Desktop', '/home/piscotest/.config']:
        info = subprocess.check_output(['debugfs', '-R', 'stat ' + path, str(root)], stderr=subprocess.DEVNULL, text=True)
        assert re.search(r'User:\s+1000\s+Group:\s+1000', info) and '0700' in info
    record('native-account-hash-groups-home-permissions-and-no-autologin-offline-verified')
    guest = v82.Guest(work / 'native', target)
    # Reuse the connection with the extra punctuation needed by the native Shell.
    guest.monitor.__class__ = Monitor
    try:
        guest.wait('Entering WindowServer main loop', timeout=200)
        time.sleep(20)
        assert 'login with sid=' not in guest.log()
        v82.old.screenshot(guest.monitor, guest.work)
        (guest.work / 'login.png').write_bytes((guest.work / 'screen.png').read_bytes())
        guest.monitor.type('piscotest')
        guest.monitor.key('tab')
        guest.monitor.type(wrong)
        offset = len(guest.log())
        guest.monitor.key('ret')
        guest.wait('failed graphical login for user piscotest: invalid password', offset, timeout=360)
        record('real-LoginServer-rejects-incorrect-password')
        guest.monitor.type(password)
        offset = len(guest.log())
        guest.monitor.key('ret')
        guest.wait('login with sid=', offset, timeout=360)
        time.sleep(35)
        record('real-LoginServer-authenticates-created-user-without-USB-or-Linux')
        for command in ['id > /home/piscotest/identity.txt',
                        'echo PISCO-V83-PERSISTED > /home/piscotest/persist.txt',
                        'test -r /home/anon/.config/SystemServer.ini; echo $? > /home/piscotest/access-status.txt',
                        'test -r /etc/shadow; echo $? > /home/piscotest/shadow-status.txt', 'sync']:
            guest.monitor.type(command)
            guest.monitor.key('ret')
            time.sleep(1)
        v82.old.screenshot(guest.monitor, guest.work)
        (guest.work / 'desktop.png').write_bytes((guest.work / 'screen.png').read_bytes())
        offset = len(guest.log())
        guest.monitor.execute('system_reset')
        guest.wait('Entering WindowServer main loop', offset, timeout=200)
        time.sleep(20)
        guest.monitor.type('piscotest')
        guest.monitor.key('tab')
        guest.monitor.type(password)
        offset = len(guest.log())
        guest.monitor.key('ret')
        guest.wait('login with sid=', offset, timeout=360)
        time.sleep(35)
        guest.monitor.type('cat /home/piscotest/persist.txt')
        guest.monitor.key('ret')
        time.sleep(3)
        guest.monitor.type('sync')
        guest.monitor.key('ret')
        time.sleep(3)
        v82.old.screenshot(guest.monitor, guest.work)
        record('new-login-after-virtual-reboot-and-personal-file-persistence')
        assert 'Linux version' not in guest.log() and guest.log().count('SerenityOS EFI Prekernel') == 2
    finally:
        guest.stop()
    root = work / 'root-after-reboot.ext2'
    extract_root(target, root)
    assert read(root, '/home/piscotest/persist.txt').strip() == 'PISCO-V83-PERSISTED'
    identity = read(root, '/home/piscotest/identity.txt')
    assert 'uid=1000' in identity and 'gid=1000' in identity and 'wheel' not in identity and 'phys' not in identity
    for name in ['access-status', 'shadow-status']:
        assert read(root, '/home/piscotest/' + name + '.txt').strip() == '1'
    assert '[Terminal]' in read(root, '/home/piscotest/.config/SystemServer.ini')
    record('native-UID-restricted-access-and-personal-files-offline-verified')
    for path in work.rglob('*.log'):
        data = path.read_bytes()
        assert password.encode() not in data and wrong.encode() not in data
    record('ephemeral-test-passwords-absent-from-logs')
    (work / 'complete.json').write_text(json.dumps(dict(passed=True, cases=len(results), native_login=True,
        persistent_user=True, usb_removed=True, password_recorded=False), indent=2))


if __name__ == '__main__':
    main()
