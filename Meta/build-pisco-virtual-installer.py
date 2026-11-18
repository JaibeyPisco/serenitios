#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Build V8.2 in a NEW directory, reusing the accepted V8.1 payload/runtime."""
import argparse
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

REPO = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--v81', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--payload', type=Path, help='Verified derived payload; original V8.1 remains untouched')
    parser.add_argument('--user-setup', action='store_true', help='V8.3 mandatory native account setup')
    args = parser.parse_args()
    source = args.v81.resolve(strict=True)
    output = args.output.absolute()
    if output.exists() or output.is_symlink() or Path('/dev') in output.resolve().parents:
        raise ValueError('New output directory outside /dev required')
    v81 = load('v81', REPO / 'Meta/build-pisco-installer.py')
    gpt = load('gpt', REPO / 'Meta/install-serenity.py')
    payload = args.payload.resolve(strict=True) if args.payload else source / 'payload'
    release = json.loads((payload / 'release.json').read_text())
    if bool(release.get('requires_user_setup')) != args.user_setup:
        raise ValueError('Derived account payload requires mandatory V8.3 setup')
    if release['pisco_commit'] != v81.PISCO_COMMIT:
        raise ValueError('Wrong PISCO release')
    for name, key in [('root.ext2', 'root_sha256'), ('Kernel.efi', 'kernel_efi_sha256')]:
        if v81.sha(payload / name) != release[key]:
            raise ValueError('Corrupt payload')
    v81.run('e2fsck', '-f', '-n', payload / 'root.ext2')
    output.mkdir(parents=True)
    root = output / 'initramfs-root'
    shutil.copytree(source / 'initramfs-root', root, symlinks=True)
    for path in (REPO / 'Meta/InstallerRuntime').iterdir():
        if not path.is_file():
            continue
        target = root / 'init' if path.name == 'init' else root / 'bin' / path.name
        shutil.copyfile(path, target)
        target.chmod(0o755)
    shutil.copy2(REPO / 'Meta/InstallerRuntimeV82/pisco-install', root / 'bin/pisco-install')
    (root / 'bin/pisco-install').chmod(0o755)
    if args.user_setup:
        (root / 'bin/pisco-install').rename(root / 'bin/pisco-install-v82')
        for path in (REPO / 'Meta/InstallerRuntimeV83').iterdir():
            if path.is_file():
                shutil.copy2(path, root / 'bin' / path.name)
                (root / 'bin' / path.name).chmod(0o755)
    # Private runtime addition: V8.1 and its accepted image remain unchanged.
    init = root / 'init'
    init.write_text(init.read_text().replace('PISCO-V81', 'PISCO-V82')
        .replace('V8.1', 'V8.2').replace('V81 READY: disk identification and planning only; no disk writer installed.',
        'V82 READY: virtual-only installer; explicit confirmation required.')
        .replace('Commands: pisco-disks;', 'Commands: pisco-install /dev/DISK; pisco-disks;'))
    if args.user_setup:
        init.write_text(init.read_text().replace('PISCO-V82', 'PISCO-V83').replace('V8.2', 'V8.3')
            .replace('V82 READY:', 'V83 READY:'))
    included = {}
    def binary(path, destination=None):
        path = Path(path).resolve(strict=True)
        target = root / (destination or str(path).lstrip('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            target.unlink()
        shutil.copy2(path, target)
        included[str(path)] = v81.sha(path)
        for dependency in re.findall(r'/[^\s()]+', subprocess.check_output(['ldd', str(path)], text=True)):
            dep = Path(dependency)
            if dep.is_file():
                dest = root / str(dep).lstrip('/')
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dep, dest)
                included[str(dep)] = v81.sha(dep)
    binary('/usr/bin/python3.12', 'bin/python3')
    shutil.copytree('/usr/lib/python3.12', root / 'usr/lib/python3.12',
                    ignore=shutil.ignore_patterns('__pycache__', 'test', 'tests'))
    for extension in (root / 'usr/lib/python3.12/lib-dynload').glob('*.so'):
        binary(Path('/usr/lib/python3.12/lib-dynload') / extension.name)
    for name in ['mkfs.fat', 'mcopy', 'mmd', *(['debugfs', 'e2fsck'] if args.user_setup else [])]:
        binary(shutil.which(name), 'sbin/' + name)
    # mtools uses iconv even for the ASCII-only paths in the ESP. glibc loads
    # these conversion modules dynamically, so ldd cannot discover them.
    shutil.copytree('/usr/lib/x86_64-linux-gnu/gconv', root / 'usr/lib/x86_64-linux-gnu/gconv')
    (root / 'boot').mkdir(exist_ok=True)
    (output / 'native-grub.cfg').write_text('configfile $cmdpath/grub.cfg\n')
    v81.run('grub-mkstandalone', '-O', 'x86_64-efi',
            '--modules=part_gpt fat normal chain search search_fs_uuid configfile serial',
            '-o', root / 'boot/GRUB.EFI', 'boot/grub/grub.cfg=' + str(output / 'native-grub.cfg'))
    listing = '\0'.join(['.', *sorted(str(p.relative_to(root)) for p in root.rglob('*'))]) + '\0'
    cpio = subprocess.run(['cpio', '--null', '-o', '--format=newc', '--reproducible', '--owner=0:0'],
        input=listing.encode(), stdout=subprocess.PIPE, cwd=root, check=True).stdout
    (output / 'initramfs.gz').write_bytes(gzip.compress(cpio, mtime=0))
    shutil.copyfile(source / 'linux', output / 'linux')
    medium_uuid = str(uuid.uuid4())
    data = output / 'data-root'
    data.mkdir()
    (data / 'PISCO_INSTALLER_ID').write_text('PISCO-V83\n' if args.user_setup else 'PISCO-V82\n')
    (data / 'payload').mkdir()
    for name in ['root.ext2', 'Kernel.efi', 'root-manifest.json', 'release.json', 'SHA256SUMS']:
        os.link(payload / name, data / 'payload' / name)
    v81.run('mke2fs', '-q', '-F', '-t', 'ext2', '-b', '1024', '-U', medium_uuid,
            '-d', data, output / 'data.ext2', str(1792 * 1024))
    esp = output / 'esp.fat'
    with esp.open('wb') as stream:
        stream.truncate(128 * v81.MIB)
    v81.run('mkfs.fat', '--invariant', '-F', '32', '-n', 'PISCO_BOOT', esp)
    v81.run('mmd', '-i', esp, '::/EFI', '::/EFI/BOOT', '::/boot')
    config = output / 'grub.cfg'
    config.write_text('serial --unit=0 --speed=115200\nterminal_output console serial\n'
        'search --no-floppy --label --set=root PISCO_BOOT\n'
        'linux /boot/linux console=tty0 console=ttyS0,115200 pisco_media=' + medium_uuid
        + ' pisco_target_serial=PISCO_V82_TARGET\ninitrd /boot/initramfs.gz\nboot\n')
    v81.run('grub-mkstandalone', '-O', 'x86_64-efi', '--modules=part_gpt fat normal linux search search_label serial',
            '-o', output / 'BOOTX64.EFI', 'boot/grub/grub.cfg=' + str(config))
    for name, target in [('BOOTX64.EFI', '::/EFI/BOOT/BOOTX64.EFI'), ('linux', '::/boot/linux'),
                         ('initramfs.gz', '::/boot/initramfs.gz')]:
        v81.run('mcopy', '-i', esp, output / name, target)
    gpt.ESP_SECTORS = 128 * v81.MIB // 512
    gpt.ROOT_START = gpt.ESP_START + gpt.ESP_SECTORS
    sectors = ((gpt.ROOT_START + (output / 'data.ext2').stat().st_size // 512 + 33 + 2047) // 2048) * 2048
    plan = dict(disk_bytes=sectors * 512, disk_uuid=str(uuid.uuid4()), root_uuid=str(uuid.uuid4()),
                root_sectors=(output / 'data.ext2').stat().st_size // 512)
    image = output / ('pisco-installer-v83.img' if args.user_setup else 'pisco-installer-v82.img')
    with image.open('w+b') as stream:
        stream.truncate(plan['disk_bytes'])
        gpt.write_partition_table(stream, plan)
        gpt.copy_partition(esp, stream, gpt.ESP_START)
        gpt.copy_partition(output / 'data.ext2', stream, gpt.ROOT_START)
        stream.flush()
        os.fsync(stream.fileno())
    manifest = dict(payload=release, image_sha256=v81.sha(image), inherited_v81_manifest=v81.sha(source / 'build-manifest.json'),
                    runtime_inputs=included, scripts={p.name: v81.sha(p) for p in (REPO / 'Meta/InstallerRuntime').iterdir() if p.is_file()},
                    native_grub_sha256=v81.sha(root / 'boot/GRUB.EFI'), initramfs_sha256=v81.sha(output / 'initramfs.gz'))
    manifest['v82_backend_sha256'] = v81.sha(REPO / 'Meta/InstallerRuntimeV82/pisco-install')
    if args.user_setup:
        manifest['v83_sources'] = {p.name: v81.sha(p) for p in (REPO / 'Meta/InstallerRuntimeV83').iterdir() if p.is_file()}
    manifest['gconv_inputs'] = {str(p.relative_to(root)): v81.sha(p)
                              for p in (root / 'usr/lib/x86_64-linux-gnu/gconv').rglob('*') if p.is_file()}
    (output / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(image, flush=True)


if __name__ == '__main__':
    main()
