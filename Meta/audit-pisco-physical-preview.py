#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Read-only audit of a completed preview RAW; extraction goes into a NEW directory."""
import argparse
import ast
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess

REPO = Path(__file__).resolve().parent.parent
MIB = 1024**2


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build, output = args.build.resolve(strict=True), args.output.absolute()
    if output.exists() or output.is_symlink() or Path('/dev') in output.resolve().parents:
        raise ValueError('NEW directory outside /dev required')
    image = build / 'pisco-v85a-preview-x86_64-uefi.img'
    if not image.is_file():
        raise ValueError('Regular RAW required')
    manifest = json.loads((build / 'build-manifest.json').read_text())
    before = sha(image)
    assert before == manifest['image_sha256']
    output.mkdir(parents=True)
    load('gptcheck', REPO / 'Meta/test-pisco-installer.py').check_gpt(image)
    assert image.stat().st_size == 1922*MIB
    for source, dest, key in [('/EFI/BOOT/BOOTX64.EFI', 'BOOTX64.EFI', 'grub_efi_sha256'),
                              ('/boot/linux', 'linux', 'linux_sha256'),
                              ('/boot/initramfs.gz', 'initramfs.gz', 'initramfs_sha256')]:
        subprocess.run(['mcopy', '-i', str(image)+'@@1048576', '::'+source, str(output/dest)], check=True)
        assert sha(output/dest) == manifest[key]
    pe = (output/'BOOTX64.EFI').read_bytes()
    offset = struct.unpack_from('<I', pe, 60)[0]
    assert pe[:2] == b'MZ' and pe[offset:offset+4] == b'PE\0\0'
    assert struct.unpack_from('<H', pe, offset+4)[0] == 0x8664
    assert struct.unpack_from('<H', pe, offset+24)[0] == 0x20b
    root = output/'initramfs-root'
    root.mkdir()
    load('builder', REPO/'Meta/build-pisco-physical-preview.py').unpack(gzip.decompress((output/'initramfs.gz').read_bytes()), root)
    config = json.loads((root/'etc/pisco-preview.json').read_text())
    assert config['mode'] == 'preview-only' and config['installation_enabled'] is False
    assert config['payload'] == manifest['payload']
    for name, digest in manifest['runtime_sources'].items():
        assert sha(root/'bin'/name) == digest
        if name.endswith('.py') or name in ['pisco-preview', 'pisco-preview-init']:
            ast.parse((root/'bin'/name).read_text(), filename=name)
    ast.parse((root/'bin/pisco-start-gui').read_text())
    assert 'QEMU USB Tablet' not in (root/'bin/pisco-start-gui').read_text()
    assert 'exit 64' in (root/'bin/pisco-install').read_text()
    assert 'exec /bin/python3 /bin/pisco-preview-init' in (root/'init').read_text()
    for name, digest in manifest['inactive_v84_sources'].items():
        path = root/'opt/pisco/v84-review'/name
        assert sha(path) == digest and path.stat().st_mode & 0o111 == 0
        if name != 'pisco-start-gui' and name != 'pisco-install':
            assert not (root/'bin'/name).exists()
    grub = (build/'grub.cfg').read_text()
    assert 'pisco_preview=1' in grub
    assert not any(token in grub for token in ['/home/', 'WSL', 'vda', 'sda', 'hostfwd', 'pisco_install='])
    data = output/'data.ext2'
    with image.open('rb') as src, data.open('xb') as dst:
        src.seek(129*MIB)
        for _ in range(1792):
            content = src.read(MIB)
            assert len(content) == MIB
            if content.count(0) == MIB:
                dst.seek(MIB, 1)
            else:
                dst.write(content)
        dst.truncate(1792*MIB)
    with (output/'e2fsck.log').open('w') as log:
        subprocess.run(['e2fsck', '-f', '-n', str(data)], check=True, stdout=log, stderr=log)
    extracted = {}
    for source, dest in [('/payload/root.ext2', 'root.ext2'), ('/payload/Kernel.efi', 'Kernel.efi'),
                         ('/PREVIEW.json', 'PREVIEW.json'), ('/licenses/PACKAGES.tsv', 'PACKAGES.tsv')]:
        subprocess.run(['debugfs', '-R', 'dump '+source+' '+str(output/dest), str(data)], check=True)
        assert (output/dest).is_file()
        extracted[dest] = sha(output/dest)
    assert extracted['root.ext2'] == config['payload']['root_sha256']
    assert extracted['Kernel.efi'] == config['payload']['kernel_efi_sha256']
    assert json.loads((output/'PREVIEW.json').read_text()) == config
    assert (output/'PACKAGES.tsv').read_text() == manifest['packages']
    assert sha(image) == before
    result = dict(passed=True, image_sha256=before, native_payload=extracted,
                  checks=['both-GPT-CRC-and-partition-bounds', 'x86_64-UEFI-fallback-PE', 'embedded-Linux-initrd-GRUB-hashes',
                          'preview-only-active-runtime', 'V84-original-code-archived-non-executable',
                          'native-payload-full-SHA256', 'ext2-readonly-fsck', 'package-provenance', 'input-image-unchanged'])
    (output/'results.json').write_text(json.dumps(result, indent=2))
    for scratch in [data, output/'root.ext2']:
        scratch.unlink()  # Only files extracted by this invocation, after verification.
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
