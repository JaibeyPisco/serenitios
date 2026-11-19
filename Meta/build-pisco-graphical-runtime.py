#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Private Xorg/modesetting/evdev + Tk runtime; no desktop or network UI."""
import os
from pathlib import Path
import shutil
import subprocess
import re

REPO = Path(__file__).resolve().parent.parent


def add_graphics(root, binary, source, output, v81):
    for path in (REPO / 'Meta/InstallerRuntimeV84').iterdir():
        if path.is_file():
            shutil.copy2(path, root / 'bin' / path.name)
            (root / 'bin' / path.name).chmod(0o755)
    for name in ['/usr/lib/xorg/Xorg', '/usr/bin/xkbcomp']:
        binary(name)
    for name in ['drivers/modesetting_drv.so', 'input/evdev_drv.so', 'libfbdevhw.so', 'libshadow.so', 'libshadowfb.so', 'libvgahw.so']:
        binary('/usr/lib/xorg/modules/' + name)
    for name in ['usr/share/tcltk', 'usr/share/X11/xkb', 'usr/share/fonts/X11/misc',
                 'usr/share/fonts/truetype/dejavu', 'etc/fonts']:
        destination = root / name
        shutil.copytree('/' + name, destination, dirs_exist_ok=True)
    # Input modules use the same pinned installer kernel as V8.1, not the host kernel.
    version = json_version(source)
    modules = []
    for name in ['atkbd', 'usbhid', 'hid_generic']:
        dependencies = subprocess.check_output(['modprobe', '--show-depends', '-S', version, name], text=True)
        for path in re.findall(r'^insmod (\S+)', dependencies, re.M):
            if path not in modules:
                modules.append(path)
    for index, path in enumerate(modules):
        v81.decompress_module(Path(path), root / f'modules/9{index:02d}.ko')
    for name in ['tmp/.X11-unix', 'var/log', 'var/lib/xkb', 'etc/X11']:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / 'tmp').chmod(0o1777)
    init = root / 'init'
    init.write_text(init.read_text().split('while :; do')[0]
        .replace('PISCO-V83', 'PISCO-V84').replace('V8.3', 'V8.4').replace('V83 READY:', 'V84 READY:')
        + 'while :; do\n /bin/python3 /bin/pisco-start-gui\n echo "V84 graphics stopped; retrying without disk operations"\n sleep 5\ndone\n')


def json_version(source):
    import json
    return json.loads((source / 'build-manifest.json').read_text())['linux_version']
