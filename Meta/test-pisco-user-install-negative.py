#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""V8.3 failure gates: mounted disk, failed account setup and interruption."""
import argparse
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import time

spec = importlib.util.spec_from_file_location('user_test', Path(__file__).with_name('test-pisco-user-install.py'))
test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--guards-only', action='store_true', help='Only mandatory-setup and short-password guards')
    args = parser.parse_args()
    image, work = args.image.resolve(strict=True), args.output.absolute()
    if not image.is_file() or work.exists() or work.is_symlink() or Path('/dev') in work.resolve().parents:
        raise ValueError('New output outside /dev and regular image required')
    if any(',' in str(p) for p in [image, work]):
        raise ValueError('Comma in QEMU paths')
    work.mkdir(parents=True)
    password = secrets.token_hex(12)
    original = test.v82.old.sha(image)
    results = []
    def record(name):
        results.append(dict(case=name, passed=True))
        (work / 'results.json').write_text(json.dumps(results, indent=2))
        print('PASS', name, flush=True)
    for case in ([] if args.guards_only else ['mounted', 'failed-setup', 'interrupted-setup']):
        disk = work / (case + '.img')
        with disk.open('xb') as f:
            f.truncate(3 * 1024**3)
        if case == 'mounted':
            subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext2', str(disk)], check=True)
        before = test.v82.old.sha(disk)
        guest = test.v82.Guest(work / case, disk, image, memory='2G')
        try:
            guest.wait('V83 READY:')
            guest.wait('~ #')
            if case == 'mounted':
                guest.send('mkdir /mnt; mount -t ext4 -o ro,noload /dev/sda /mnt; echo MOUNT_DONE')
                guest.wait('\nMOUNT_DONE\n')
                offset = len(guest.log())
                guest.send('pisco-install /dev/sda')
                guest.wait('V83 FAILED:', offset)
                guest.send('umount /mnt; poweroff -f')
                guest.proc.wait(timeout=30)
            else:
                if case == 'failed-setup':
                    guest.send('mv /sbin/e2fsck /sbin/e2fsck-disabled; echo FAULT_READY')
                    guest.wait('\nFAULT_READY\n')
                guest.send('pisco-install /dev/sda')
                guest.wait('V83 Full name:')
                offset = test.account(guest, password)
                guest.wait('Type exactly:', offset)
                guest.send('INSTALL /dev/sda PISCO_V82_TARGET 3221225472')
                guest.wait('V83 CONFIGURING USER (destination untouched)', offset, timeout=600)
                if case == 'failed-setup':
                    guest.wait('V83 FAILED:', offset, timeout=600)
                    assert 'V83 USER VERIFIED' not in guest.log()
                    guest.send('poweroff -f')
                    guest.proc.wait(timeout=30)
                else:
                    time.sleep(.5)
                    guest.monitor.execute('stop')
                assert 'V82 INSTALLED VERIFIED' not in guest.log()
                assert 'V83 USER VERIFIED' not in guest.log()
        finally:
            guest.stop()
        assert test.v82.old.sha(disk) == before
        assert password not in guest.log()
        record(case + '-no-success-and-entire-target-unchanged')
        if case == 'interrupted-setup':
            failed_boot = test.v82.Guest(work / 'incomplete-native-boot', disk)
            try:
                time.sleep(30)
                assert 'SerenityOS EFI Prekernel' not in failed_boot.log()
                assert 'Entering WindowServer main loop' not in failed_boot.log()
                test.v82.old.screenshot(failed_boot.monitor, failed_boot.work)
            finally:
                failed_boot.stop()
            record('incomplete-disk-cannot-start-native-kernel-or-desktop')
    disk = work / 'guards.img'
    with disk.open('xb') as f:
        f.truncate(3 * 1024**3)
    before = test.v82.old.sha(disk)
    guest = test.v82.Guest(work / 'guards', disk, image, memory='2G')
    short_password = secrets.token_hex(4)
    try:
        guest.wait('V83 READY:')
        guest.wait('~ #')
        offset = len(guest.log())
        guest.send('pisco-install-v82 /dev/sda')
        guest.wait('mandatory user setup missing', offset)
        record('legacy-entry-cannot-bypass-mandatory-account-setup')
        offset = len(guest.log())
        guest.send('pisco-install /dev/sda')
        guest.wait('V83 Full name:', offset)
        guest.send('Pisco Test User')
        guest.wait('V83 Username:', offset)
        guest.send('piscotest')
        guest.wait('V83 Password:', offset)
        guest.send(short_password)
        guest.wait('V83 Repeat password:', offset)
        guest.send(short_password)
        guest.wait('V83 INVALID password:', offset)
        assert 'V83 USER VERIFIED' not in guest.log() and 'V82 INSTALLED VERIFIED' not in guest.log()
    finally:
        guest.stop()
    assert test.v82.old.sha(disk) == before and short_password not in guest.log()
    record('short-password-rejected-with-no-echo-no-success-no-writes')
    assert test.v82.old.sha(image) == original
    record('USB-image-unchanged-and-no-password-in-transcripts')
    (work / 'complete.json').write_text(json.dumps(dict(passed=True, cases=len(results)), indent=2))


if __name__ == '__main__':
    main()
