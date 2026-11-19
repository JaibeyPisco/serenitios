#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause
"""Read-only regression of the shared CLI/GUI native account rules."""
import argparse
import importlib.machinery
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink() or Path('/dev') in args.output.absolute().resolve().parents:
        raise ValueError('New output outside /dev required')
    path = str(REPO / 'Meta/InstallerRuntimeV83/pisco-user-setup')
    spec = importlib.util.spec_from_loader('setup', importlib.machinery.SourceFileLoader('setup', path))
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    valid = setup.account_identity(args.payload, 'Valid User', 'pisco_test')
    assert valid == dict(name='pisco_test', full_name='Valid User', uid=1000, gid=1000)
    for name in ['anon', 'root', 'window', '../bad', 'Bad User', '-bad', '', 'x'*32]:
        try:
            setup.account_identity(args.payload, 'Valid User', name)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid identity accepted')
    for full_name in ['', 'x'*81, 'bad:name', 'bad\nname']:
        try:
            setup.account_identity(args.payload, full_name, 'pisco_test')
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid full name accepted')
    for password, again, expected in [('x'*12, 'x'*12, True), ('x'*128, 'x'*128, True),
            ('x'*11, 'x'*11, False), ('x'*129, 'x'*129, False), ('x'*12, 'y'*12, False),
            ('\0'+'x'*12, '\0'+'x'*12, False)]:
        assert setup.password_valid(password, again) == expected
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(passed=True, valid_identity=True, rejected_names=8,
        rejected_full_names=4, password_boundary_cases=6), indent=2))
    print('PASS shared CLI/GUI account validation and boundaries', flush=True)


if __name__ == '__main__':
    main()
