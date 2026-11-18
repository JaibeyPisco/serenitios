#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Derive a NEW V8.3 seed from clean V7.3; replace only native auth libraries."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parent.parent


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(strict=True), args.output.absolute()
    if output.exists() or output.is_symlink() or Path('/dev') in output.resolve().parents:
        raise ValueError('New output directory outside /dev required')
    release = json.loads((source / 'release.json').read_text())
    if sha(source / 'root.ext2') != release['root_sha256']:
        raise ValueError('Invalid base payload')
    output.mkdir(parents=True)
    root = output / 'root.ext2'
    shutil.copyfile(source / 'root.ext2', root)
    patches = {}
    for library in ['LibCrypt', 'LibCore']:
        name = library.lower() + '.so.serenity'
        binary = REPO / 'Build/x86_64/Userland/Libraries' / library / name
        target = '/usr/lib/' + name
        for command in ['rm ' + target, f'write {binary} {target}', f'set_inode_field {target} mode 0100755']:
            subprocess.run(['debugfs', '-w', '-R', command, str(root)], check=True, stdout=subprocess.DEVNULL)
        extracted = output / name
        subprocess.run(['debugfs', '-R', f'dump {target} {extracted}', str(root)], check=True)
        if sha(extracted) != sha(binary):
            raise ValueError('Native library readback mismatch')
        patches[target] = sha(binary)
        extracted.unlink()
    subprocess.run(['e2fsck', '-f', '-n', str(root)], check=True)
    shutil.copyfile(source / 'Kernel.efi', output / 'Kernel.efi')
    manifest = dict(base_manifest_sha256=sha(source / 'root-manifest.json'), replaced_native_files=patches,
        source_files={name: sha(REPO / name) for name in ['Userland/Libraries/LibCrypt/crypt.cpp', 'Userland/Libraries/LibCore/Account.cpp']})
    (output / 'root-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    release.update(base_root_sha256=release['root_sha256'], root_sha256=sha(root),
        root_manifest_sha256=sha(output / 'root-manifest.json'), account_setup_implemented=True,
        factory_autologin=True, installed_autologin=False, requires_user_setup=True, native_auth_patches=patches)
    (output / 'release.json').write_text(json.dumps(release, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(''.join(sha(output / name) + '  ' + name + '\n'
        for name in ['root.ext2', 'Kernel.efi', 'root-manifest.json', 'release.json']))
    print(output, flush=True)


if __name__ == '__main__':
    main()
