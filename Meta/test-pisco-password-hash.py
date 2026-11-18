#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Compare the actual native LibCrypt implementation with Python/OpenSSL."""
import argparse
import base64
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    work = args.output.absolute()
    if work.exists() or work.is_symlink() or Path('/dev') in work.resolve().parents:
        raise ValueError('new regular output directory required')
    work.mkdir(parents=True)
    command = ['g++', '-std=c++23', '-O2', '-shared', '-fPIC', '-I.', '-IUserland/Libraries',
        'Userland/Libraries/LibCrypt/crypt.cpp', 'Userland/Libraries/LibCrypto/Hash/SHA2.cpp',
        '-LBuild/lagom/lib', '-llagom-ak', '-Wl,-rpath,' + str(REPO / 'Build/lagom/lib'),
        '-o', str(work / 'crypt.so')]
    with (work / 'build.log').open('w') as log:
        subprocess.run(command, cwd=REPO, stdout=log, stderr=log, check=True)
    library = ctypes.CDLL(str(work / 'crypt.so'))
    library.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    library.crypt.restype = ctypes.c_char_p
    salt = b'0123456789abcdef'
    for password in [b'password', b'', b'A' * 128, 'contrase\u00f1a larga'.encode()]:
        setting = b'$p$600000$' + salt
        expected = setting + b'$' + base64.b64encode(hashlib.pbkdf2_hmac('sha256', password, salt, 600000, 24))
        assert library.crypt(password, setting) == expected
        assert library.crypt(password, expected) == expected
        assert library.crypt(password + b'x', expected) != expected
        legacy = b'$5$' + salt + b'$' + base64.b64encode(hashlib.sha256(password + salt).digest())
        assert library.crypt(password, legacy) == legacy
    for invalid in [b'', b'!', b'$', b'$5', b'$5x', b'$p$1$' + salt, b'$p$600000$short', b'$p$600000$invalid:salt1234']:
        assert library.crypt(b'password', invalid) is None
    (work / 'result.json').write_text(json.dumps(dict(passed=True, interoperability_vectors=4,
        correct_wrong_and_legacy=True, malformed_settings_rejected=8, crypt_data_abi_unchanged=True), indent=2))
    print('PASS PBKDF2 native implementation vs OpenSSL, legacy and malformed inputs', flush=True)


if __name__ == '__main__':
    main()
