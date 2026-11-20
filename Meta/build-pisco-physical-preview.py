#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Deterministic GPT/UEFI RAW preview, built only into NEW regular files."""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import time
import uuid
import zlib

REPO = Path(__file__).resolve().parent.parent
BASE = '6ae10be7e37aec1a68466ee2debef1634dd0ebd0'
V84_SHA = '827beda1336c73dad224146fbf4d859500770d8af6c41ea6fcf9dea68567e5dd'
MIB = 1024**2


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unpack(data, root):
    """Read newc from the pinned V8.4 image. Symlinks are installed last."""
    pos, links = 0, []
    while True:
        header = data[pos:pos+110]
        if header[:6] != b'070701':
            raise ValueError('Invalid newc header')
        fields = [int(header[6+i*8:14+i*8], 16) for i in range(13)]
        mode, size, namesize = fields[1], fields[6], fields[11]
        name = data[pos+110:pos+110+namesize-1].decode()
        pos = (pos + 110 + namesize + 3) & ~3
        content = data[pos:pos+size]
        pos = (pos + size + 3) & ~3
        if name == 'TRAILER!!!':
            break
        if name == '.':
            continue
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe cpio path')
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISDIR(mode):
            target.mkdir(exist_ok=True)
        elif stat.S_ISREG(mode):
            target.write_bytes(content)
            target.chmod(stat.S_IMODE(mode) & 0o777)
        elif stat.S_ISLNK(mode):
            links.append((target, content.decode()))
        else:
            raise ValueError('Special device in initramfs template')
    for path, value in links:
        path.symlink_to(value)


def normalize_fat(image, epoch):
    """Normalize short-entry FAT32 timestamps, preserving all cluster fields."""
    stamp = time.gmtime(epoch)
    date = ((stamp.tm_year-1980)<<9) | (stamp.tm_mon<<5) | stamp.tm_mday
    clock = (stamp.tm_hour<<11) | (stamp.tm_min<<5) | (stamp.tm_sec//2)
    with image.open('r+b') as f:
        boot = f.read(512)
        sector, cluster_sectors, reserved, fats = struct.unpack_from('<HBHB', boot, 11)
        fat_sectors = struct.unpack_from('<I', boot, 36)[0]
        first_cluster = struct.unpack_from('<I', boot, 44)[0]
        fat_start = reserved * sector
        data_start = (reserved + fats * fat_sectors) * sector
        cluster_bytes = sector * cluster_sectors
        seen = set()
        def directory(first):
            current = first
            while current < 0x0ffffff8:
                if current in seen:
                    raise ValueError('Cyclic FAT directory')
                seen.add(current)
                offset = data_start + (current-2) * cluster_bytes
                f.seek(offset)
                block = bytearray(f.read(cluster_bytes))
                children = []
                for pos in range(0, cluster_bytes, 32):
                    entry = block[pos:pos+32]
                    if entry[0] == 0:
                        break
                    if entry[0] == 0xe5 or entry[11] == 0x0f:
                        continue
                    block[pos+13] = 0
                    struct.pack_into('<HHH', block, pos+14, clock, date, date)
                    struct.pack_into('<HH', block, pos+22, clock, date)
                    if entry[11] & 0x10 and entry[0] != ord('.'):
                        children.append((struct.unpack_from('<H', entry, 20)[0]<<16) | struct.unpack_from('<H', entry, 26)[0])
                f.seek(offset)
                f.write(block)
                for child in children:
                    directory(child)
                f.seek(fat_start+current*4)
                current = struct.unpack('<I', f.read(4))[0] & 0x0fffffff
        directory(first_cluster)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--v84', type=Path, required=True)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, payload, output = args.v84.resolve(strict=True), args.payload.resolve(strict=True), args.output.absolute()
    if output.exists() or output.is_symlink() or Path('/dev') in output.resolve().parents:
        raise ValueError('NEW output directory outside /dev required')
    image = source / 'pisco-installer-v84.img'
    if not image.is_file() or sha(image) != V84_SHA:
        raise ValueError('Input must be the exact validated V8.4 image')
    release = json.loads((payload / 'release.json').read_text())
    for name, key in [('root.ext2','root_sha256'), ('Kernel.efi','kernel_efi_sha256')]:
        if sha(payload/name) != release[key]:
            raise ValueError('Invalid native seed')
    v84 = json.loads((source/'build-manifest.json').read_text())
    if release != v84['payload']:
        raise ValueError('Payload differs from validated V8.4')
    output.mkdir(parents=True)
    epoch = int(subprocess.check_output(['git','show','-s','--format=%ct',BASE],cwd=REPO))
    env = {**os.environ, 'SOURCE_DATE_EPOCH':str(epoch), 'E2FSPROGS_FAKE_TIME':str(epoch), 'TZ':'UTC', 'LC_ALL':'C'}
    run('mcopy','-i',str(image)+'@@1048576','::/boot/linux',output/'linux')
    run('mcopy','-i',str(image)+'@@1048576','::/boot/initramfs.gz',output/'v84-initramfs.gz')
    if sha(output/'v84-initramfs.gz') != v84['initramfs_sha256']:
        raise ValueError('V8.4 embedded initramfs mismatch')
    # Audit the actual native files inside V8.4, not just its sidecar manifest.
    original_data=output/'v84-data-audit.ext2'
    with image.open('rb') as src, original_data.open('xb') as dst:
        src.seek(129*MIB)
        left=1792*MIB
        while left:
            chunk=src.read(min(left,MIB))
            if chunk.count(0)==len(chunk):dst.seek(len(chunk),1)
            else:dst.write(chunk)
            left-=len(chunk)
        dst.truncate(1792*MIB)
    audit={}
    for name,key in [('root.ext2','root_sha256'),('Kernel.efi','kernel_efi_sha256')]:
        extracted=output/('audited-'+name)
        run('debugfs','-R',f'dump /payload/{name} {extracted}',original_data)
        audit[name]=sha(extracted)
        if audit[name]!=release[key]:raise ValueError('Actual V8.4 payload mismatch')
        extracted.unlink()  # Scratch created above, never an input or previous release.
    original_data.unlink()
    root = output/'initramfs-root'
    root.mkdir()
    unpack(gzip.decompress((output/'v84-initramfs.gz').read_bytes()), root)
    archive = root/'opt/pisco/v84-review'
    archive.mkdir(parents=True)
    archive_hashes = {}
    for name in ['pisco-install','pisco-install-v82','pisco-user-setup','pisco-wizard','pisco-start-gui','pisco-plan','pisco-disks','pisco-verify']:
        path = root/'bin'/name
        if path.exists():
            archive_hashes[name] = sha(path)
            path.rename(archive/name)
            (archive/name).chmod(0o444)
    expected={'pisco-install-v82':v84['v82_backend_sha256'],**v84['v83_sources'],**v84['v84_sources']}
    for name,value in expected.items():
        if archive_hashes.get(name)!=value:raise ValueError('Archived V8.4 installer mismatch')
    for folder in ['sbin','bin']:
        for path in (root/folder).iterdir():
            if path.name in ['debugfs','e2fsck','mkfs.fat','mcopy','mmd','dd','fdisk','sfdisk','mkswap','swapon','losetup','blkdiscard'] or path.name.startswith('mkfs.'):
                path.unlink()
    (root/'bin/pisco-install').write_text('#!/bin/sh\necho "V85A BLOCKED: preview-only; installation is not available."\nexit 64\n')
    (root/'bin/pisco-install').chmod(0o755)
    sources = {}
    for path in sorted((REPO/'Meta/InstallerRuntimeV85A').iterdir()):
        if path.is_file():
            shutil.copyfile(path,root/'bin'/path.name)
            (root/'bin'/path.name).chmod(0o755)
            sources[path.name] = sha(path)
    # Start the existing Xorg/Tk environment with capability-based input discovery.
    start = (archive/'pisco-start-gui').read_text()
    start = start.replace("if name == 'AT Translated Set 2 keyboard':", "if has_keys(entry, [28, 30, 44]):")
    start = start.replace("if name in ['QEMU USB Tablet', 'QEMU QEMU USB Tablet']:", "if has_keys(entry, [272]) or has_keys(entry, [330]):")
    start = start.replace("inputs = {}", "def has_keys(entry, keys):\n    parts=(entry/'device/capabilities/key').read_text().split()\n    bitmap=sum(int(word,16) << (64*i) for i,word in enumerate(reversed(parts)))\n    return all(bitmap & (1 << key) for key in keys)\n\ninputs = {}")
    start = start.replace("if len(inputs) != 2:", "if 'keyboard' not in inputs:")
    start = start.replace('Option "AllowMouseOpenFail" "false"', 'Option "AllowMouseOpenFail" "true"')
    start = start.replace(' InputDevice "pointer" "CorePointer"', '{POINTER}')
    start = start.replace("Path('/run/pisco/xorg.conf').write_text(config)", "config=config.replace('{POINTER}', ' InputDevice \\\"pointer\\\" \\\"CorePointer\\\"' if 'pointer' in inputs else '')\nPath('/run/pisco/xorg.conf').write_text(config)")
    start = start.replace('/bin/pisco-wizard','/bin/pisco-preview')
    start = start.replace('V84','V85A').replace('required keyboard/tablet','keyboard input')
    (root/'bin/pisco-start-gui').write_text(start)
    (root/'bin/pisco-start-gui').chmod(0o755)
    # No interactive shell, swap, internal mounts, or configurable installation path.
    (root/'init').write_text('#!/bin/sh\nexport PATH=/bin:/sbin\nmount -t devtmpfs devtmpfs /dev\nmount -t proc proc /proc\nmount -t sysfs sysfs /sys\nmount -t tmpfs tmpfs /run\nmount -t tmpfs tmpfs /tmp\nmkdir -p /run/pisco /media/pisco\nfor module in /modules/*.ko; do insmod "$module" || echo "Module unavailable"; done\nexec /bin/python3 /bin/pisco-preview-init\n')
    data_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL,'pisco-v85a-preview-v1:'+release['root_sha256']))
    config = dict(version='V8.5A', mode='preview-only', installation_enabled=False, base_validated_commit=BASE,
        data_uuid=data_uuid,payload=release,runtime_sources=sources)
    (root/'etc/pisco-preview.json').write_text(json.dumps(config,sort_keys=True,indent=2)+'\n')
    for path in [root,*root.rglob('*')]:
        os.utime(path,(epoch,epoch),follow_symlinks=False)
    listing='\0'.join(['.',*sorted(str(p.relative_to(root)) for p in root.rglob('*'))])+'\0'
    packed=subprocess.run(['cpio','--null','-o','--format=newc','--reproducible','--owner=0:0'],cwd=root,input=listing.encode(),stdout=subprocess.PIPE,check=True).stdout
    (output/'initramfs.gz').write_bytes(gzip.compress(packed,mtime=0))
    data=output/'data-root';(data/'payload').mkdir(parents=True)
    for name in ['root.ext2','Kernel.efi','root-manifest.json','release.json','SHA256SUMS']:
        run('cp','--sparse=always',payload/name,data/'payload'/name)
    (data/'PISCO_PREVIEW_ID').write_text('PISCO-V85A\n')
    (data/'PREVIEW.json').write_text(json.dumps(config,sort_keys=True,indent=2)+'\n')
    notices=data/'licenses';notices.mkdir()
    packages=['busybox-static','grub-efi-amd64-bin','grub-common','libc6','python3.12','python3-tk','libtk8.6','libtcl8.6','xserver-xorg-core','xserver-xorg-input-evdev','fonts-dejavu-core','xfonts-base','linux-image-unsigned-6.8.0-149-generic']
    package_info=subprocess.check_output(['dpkg-query','-W','-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n',*packages],text=True)
    (notices/'PACKAGES.tsv').write_text(package_info)
    for package in packages:
        copyright_file=Path('/usr/share/doc')/package/'copyright'
        if copyright_file.is_file():shutil.copyfile(copyright_file,notices/(package+'.copyright'))
    shutil.copyfile(REPO/'LICENSE',notices/'SerenityOS-LICENSE')
    for path in [data,*data.rglob('*')]:os.utime(path,(epoch,epoch),follow_symlinks=False)
    run('mke2fs','-q','-F','-t','ext2','-b','1024','-U',data_uuid,'-L','PISCO85A_DATA','-E','hash_seed='+data_uuid+',lazy_itable_init=0','-d',data,output/'data.ext2',str(1792*1024),env=env)
    v81=load('v81',REPO/'Meta/build-pisco-installer.py')
    v81.normalize_root_times(data,output/'data.ext2',epoch)
    commands=output/'owners.debugfs'
    commands.write_text(''.join('set_inode_field '+name+' '+field+' 0\n' for name in ['/', '/lost+found', *['/'+str(p.relative_to(data)) for p in sorted(data.rglob('*'))]] for field in ['uid','gid']))
    with (output/'owners.log').open('w') as log:run('debugfs','-w','-f',commands,output/'data.ext2',env=env,stdout=log,stderr=log)
    run('e2fsck','-f','-n',output/'data.ext2')
    esp=output/'esp.fat'
    with esp.open('wb') as f:f.truncate(128*MIB)
    run('mkfs.fat','--invariant','-F','32','-i','853a0001','-n','P85ABOOT',esp,env=env)
    run('mmd','-i',esp,'::/EFI','::/EFI/BOOT','::/boot',env=env)
    grub=output/'grub.cfg'
    grub.write_text('serial --unit=0 --speed=115200\nterminal_output console serial\nsearch --no-floppy --label --set=root P85ABOOT\n'
        'linux /boot/linux console=tty0 console=ttyS0,115200 pisco_preview=1\ninitrd /boot/initramfs.gz\nboot\n')
    os.utime(grub,(epoch,epoch))
    run('grub-mkstandalone','-O','x86_64-efi','--modules=part_gpt fat normal linux search search_label serial','-o',output/'BOOTX64.EFI','boot/grub/grub.cfg='+str(grub),env=env)
    for name,dest in [('BOOTX64.EFI','::/EFI/BOOT/BOOTX64.EFI'),('linux','::/boot/linux'),('initramfs.gz','::/boot/initramfs.gz')]:
        os.utime(output/name,(epoch,epoch))
        run('mcopy','-m','-i',esp,output/name,dest,env=env)
    normalize_fat(esp,epoch)
    gpt=load('gpt',REPO/'Meta/install-serenity.py')
    sectors=1922*MIB//512;last=sectors-1;entries=bytearray(16384)
    for index,(kind,start,count,label) in enumerate([(gpt.ESP_TYPE,2048,128*MIB//512,'PISCO Preview EFI'),(gpt.LINUX_TYPE,129*MIB//512,1792*MIB//512,'PISCO Preview Data')]):
        unique=uuid.uuid5(uuid.NAMESPACE_URL,data_uuid+':'+str(index))
        struct.pack_into('<16s16sQQQ72s',entries,index*128,kind.bytes_le,unique.bytes_le,start,start+count-1,0,label.encode('utf-16le'))
    disk_id=str(uuid.uuid5(uuid.NAMESPACE_URL,data_uuid+':disk'));crc=zlib.crc32(entries)
    mbr=bytearray(512);struct.pack_into('<B3sB3sII',mbr,446,0,b'\0\x02\0',0xee,b'\xff\xff\xff',1,last);mbr[510:]=b'\x55\xaa'
    target=output/'pisco-v85a-preview-x86_64-uefi.img'
    with target.open('xb') as f:
        f.truncate(sectors*512)
        for offset,content in [(0,mbr),(512,gpt.gpt_header(1,last,2,last,disk_id,crc)),(1024,entries),((last-32)*512,entries),(last*512,gpt.gpt_header(last,1,last-32,last,disk_id,crc))]:f.seek(offset);f.write(content)
        gpt.copy_partition(esp,f,2048);gpt.copy_partition(output/'data.ext2',f,129*MIB//512)
        f.flush();os.fsync(f.fileno())
    manifest=dict(config,epoch=epoch,source_v84_sha256=V84_SHA,image_sha256=sha(target),linux_sha256=sha(output/'linux'),
        initramfs_sha256=sha(output/'initramfs.gz'),grub_efi_sha256=sha(output/'BOOTX64.EFI'),inactive_v84_sources=archive_hashes,
        builder_sha256=sha(Path(__file__)),mke2fs_conf_sha256=sha(Path('/etc/mke2fs.conf')),
        actual_v84_payload_audit=audit,packages=package_info,
        tool_versions={name:subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True).stdout.strip() for name,args in [('grub',['grub-mkstandalone','--version']),('mke2fs',['mke2fs','-V']),('mtools',['mcopy','-V'])]})
    (output/'build-manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    (output/'SHA256SUMS').write_text(manifest['image_sha256']+'  '+target.name+'\n')
    # Remove only our newly created large intermediates; keep the final RAW and provenance.
    for scratch in [output/'data.ext2', output/'esp.fat', data/'payload/root.ext2']:
        scratch.unlink()
    print(target,flush=True)


if __name__=='__main__':main()
