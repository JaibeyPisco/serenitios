#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Create a NEW derivative USB image; replace only the private initramfs."""
import argparse
import gzip
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parent.parent


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Existing graphical build directory')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--without-checker', action='store_true', help='Disposable negative-test image ONLY')
    args = parser.parse_args()
    source, output = args.source.resolve(strict=True), args.output.absolute()
    if output.exists() or output.is_symlink() or Path('/dev') in output.resolve().parents:
        raise ValueError('NEW output outside /dev required')
    image = source / 'pisco-installer-v84.img'
    if not image.is_file() or image.is_symlink():
        raise ValueError('Regular graphical image required')
    manifest = json.loads((source / 'build-manifest.json').read_text())
    if manifest.get('negative_test_missing_checker') and not args.without_checker:
        raise ValueError('A negative-test image cannot become a production image')
    if sha(image) != manifest['image_sha256']:
        raise ValueError('Source image checksum mismatch')
    output.mkdir(parents=True)
    root = output / 'initramfs-root'
    shutil.copytree(source / 'initramfs-root', root, symlinks=True)
    module = Path('/usr/lib/xorg/modules/drivers/modesetting_drv.so')
    shutil.copy2(module, root / str(module).lstrip('/'))
    for item in re.findall(r'/[^\s()]+', subprocess.check_output(['ldd', str(module)], text=True)):
        item = Path(item)
        if item.is_file():
            destination = root / str(item).lstrip('/')
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
    for folder in ['InstallerRuntimeV83', 'InstallerRuntimeV84']:
        for path in (REPO / 'Meta' / folder).iterdir():
            if path.is_file():
                target = root / 'bin' / path.name
                shutil.copyfile(path, target)
                target.chmod(0o755)
    shutil.copyfile(REPO / 'Meta/InstallerRuntimeV82/pisco-install', root / 'bin/pisco-install-v82')
    init = root / 'init'
    text = init.read_text()
    prefix = text.split('while :; do')[0].split('exec /bin/python3 /bin/pisco-start-gui')[0]
    init.write_text(prefix + 'while :; do\n /bin/python3 /bin/pisco-start-gui\n echo "V84 graphics stopped; retrying without disk operations"\n sleep 5\ndone\n')
    if args.without_checker:
        (root / 'sbin/e2fsck').unlink()
    listing = '\0'.join(['.', *sorted(str(p.relative_to(root)) for p in root.rglob('*'))]) + '\0'
    packed = subprocess.run(['cpio', '--null', '-o', '--format=newc', '--reproducible', '--owner=0:0'],
        cwd=root, input=listing.encode(), stdout=subprocess.PIPE, check=True).stdout
    initrd = output / 'initramfs.gz'
    initrd.write_bytes(gzip.compress(packed, mtime=0))
    target = output / image.name
    subprocess.run(['cp', '--sparse=always', str(image), str(target)], check=True)
    subprocess.run(['mcopy', '-o', '-i', str(target) + '@@1048576', str(initrd), '::/boot/initramfs.gz'], check=True)
    manifest.update(parent_image_sha256=manifest['image_sha256'], image_sha256=sha(target),
        initramfs_sha256=sha(initrd), negative_test_missing_checker=args.without_checker,
        v82_backend_sha256=sha(REPO / 'Meta/InstallerRuntimeV82/pisco-install'),
        v83_sources={p.name: sha(p) for p in (REPO / 'Meta/InstallerRuntimeV83').iterdir() if p.is_file()},
        v84_sources={p.name: sha(p) for p in (REPO / 'Meta/InstallerRuntimeV84').iterdir() if p.is_file()})
    (output / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(target, flush=True)


if __name__ == '__main__':
    main()
