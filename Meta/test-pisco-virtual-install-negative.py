#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Mounted destination and interrupted-copy tests; NEW virtual disks only."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import time

spec = importlib.util.spec_from_file_location('install_test', Path(__file__).with_name('test-pisco-virtual-install.py'))
test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    image = args.image.resolve(strict=True)
    work = args.output.absolute()
    if not image.is_file() or work.exists() or work.is_symlink() or Path('/dev') in work.resolve().parents:
        raise ValueError('Regular source and new output outside /dev required')
    if any(',' in str(p) for p in [image, work]):
        raise ValueError('Comma in QEMU paths')
    work.mkdir(parents=True)
    before_source = test.old.sha(image)
    results = []
    def record(name):
        results.append(dict(case=name, passed=True))
        (work / 'results.json').write_text(json.dumps(results, indent=2))
        print('PASS', name, flush=True)
    mounted = work / 'mounted.img'
    with mounted.open('xb') as f:
        f.truncate(3 * 1024**3)
    subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext2', str(mounted)], check=True)
    before_mounted = test.old.sha(mounted)
    guest = test.Guest(work / 'mounted', mounted, image)
    try:
        guest.wait('V82 READY:')
        guest.wait('~ #')
        offset = len(guest.log())
        guest.send('mkdir /mnt; mount -t ext4 -o ro,noload /dev/sda /mnt; grep /mnt /proc/self/mountinfo; echo MOUNT_CHECK_DONE')
        output = guest.wait('\nMOUNT_CHECK_DONE\n', offset)
        assert ' /mnt ' in output, output
        offset = len(guest.log())
        guest.send('pisco-install /dev/sda')
        output = guest.wait('V82 REJECTED/FAILED:', offset)
        assert 'mounted target or partition' in output, output
        record('mounted-target-rejected-by-major-minor')
        guest.send('umount /mnt; poweroff -f')
        guest.proc.wait(timeout=25)
    finally:
        guest.stop()
    assert test.old.sha(mounted) == before_mounted
    record('mounted-filesystem-remains-byte-identical')
    target = work / 'interrupted.img'
    with target.open('xb') as f:
        f.truncate(3 * 1024**3)
    guest = test.Guest(work / 'interruption', target, image)
    try:
        guest.wait('V82 READY:')
        guest.wait('~ #')
        guest.send('pisco-install /dev/sda')
        guest.wait('Type exactly:')
        guest.send('INSTALL /dev/sda PISCO_V82_TARGET 3221225472')
        guest.wait('V82 WRITING PARTITIONS (GPT not published)', timeout=600)
        # Stop after actual partition bytes exist, while copy is in progress.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with target.open('rb') as f:
                f.seek(1048576)
                started = any(f.read(512))
            if started:
                break
            time.sleep(.01)
        assert started
        guest.monitor.execute('stop')
        assert 'V82 INSTALLED VERIFIED' not in guest.log()
    finally:
        guest.stop()
    with target.open('rb') as f:
        assert f.read(34 * 512) == bytes(34 * 512)
        f.seek(-33 * 512, 2)
        assert f.read() == bytes(33 * 512)
    record('interrupted-copy-has-no-published-GPT-or-success')
    interrupted_hash = test.old.sha(target)
    guest = test.Guest(work / 'retry', target, image)
    try:
        guest.wait('V82 READY:')
        guest.wait('~ #')
        guest.send('pisco-install /dev/sda')
        guest.wait('Type exactly:')
        guest.send('INSTALL /dev/sda PISCO_V82_TARGET 3221225472')
        text = guest.wait('V82 REJECTED/FAILED:', timeout=600)
        assert 'target not entirely zero' in text, text
        guest.send('poweroff -f')
        guest.proc.wait(timeout=25)
    finally:
        guest.stop()
    assert test.old.sha(target) == interrupted_hash and test.old.sha(image) == before_source
    record('partial-disk-rejected-without-additional-writes-source-unchanged')
    (work / 'complete.json').write_text(json.dumps(dict(passed=True, cases=len(results)), indent=2))


if __name__ == '__main__':
    main()
