# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Preview policy: no installer, no writable block descriptor, no automount."""
import fcntl
import json
import os
from pathlib import Path
import struct

BLKROSET, BLKROGET = 0x125d, 0x125e


def protect():
    rows = []
    for node in sorted(Path('/sys/class/block').iterdir(), key=lambda p: ((p / 'partition').exists(), p.name)):
        if int((node / 'size').read_text()) == 0:
            continue
        device = '/dev/' + node.name
        # O_NONBLOCK also permits inspecting/protecting an empty optical drive.
        # Without it, Linux can reject open() with ENOMEDIUM before BLKROSET.
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fcntl.ioctl(fd, BLKROSET, struct.pack('i', 1))
            readonly = struct.unpack('i', fcntl.ioctl(fd, BLKROGET, bytes(4)))[0] == 1
        finally:
            os.close(fd)
        # Linux can permit opening a read-only block device with O_WRONLY;
        # BLKROSET rejects writes at submission, not necessarily at open().
        # Verify both kernel interfaces without submitting any write request.
        sysfs_readonly = (node / 'ro').read_text().strip() == '1'
        if not readonly or not sysfs_readonly:
            raise RuntimeError('block protection not verified')
        rows.append(dict(device=device, readonly=True, sysfs_readonly=True,
                         partition=(node / 'partition').exists(), bytes=int((node / 'size').read_text()) * 512))
    return rows


def inventory(source):
    rows = protect()
    result = []
    for row in rows:
        if row['partition']:
            continue
        node = Path('/sys/class/block') / Path(row['device']).name
        model = node / 'device/model'
        serial = node / 'device/serial'
        row.update(role='installer-source-excluded' if row['device'] == source else 'protected-not-authorized',
                   model=model.read_text().strip() if model.exists() else 'Almacenamiento',
                   serial=serial.read_text().strip() if serial.exists() else '',
                   transport='USB' if 'usb' in str(node.resolve()) else ('NVMe' if node.name.startswith('nvme') else 'Disco'))
        result.append(row)
    return result


def save_state(source):
    rows = inventory(source)
    path = Path('/run/pisco/preview-state.json')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(mode='preview-only', installation_enabled=False, source=source, disks=rows)))
    temporary.replace(path)
    return rows
