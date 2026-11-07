#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Convert an offline installed UEFI image into a verified VirtualBox VDI."""

import argparse
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent


def export(image, output):
    image = Path(image).resolve(strict=True)
    output = Path(output).absolute()
    if not image.is_file():
        raise ValueError("La entrada debe ser un archivo de imagen instalado.")
    if output.exists() or output.is_symlink():
        raise ValueError(f"El destino ya existe y no se sobrescribirá: {output}")
    with image.open("rb") as stream:
        stream.seek(512)
        if stream.read(8) != b"EFI PART":
            raise ValueError("Usa la imagen UEFI instalada, no _disk_image ni una ISO.")
    if not shutil.which("qemu-img"):
        raise ValueError("Falta qemu-img. En Ubuntu: sudo apt install qemu-utils")
    with tempfile.TemporaryDirectory(prefix=".serenity-vdi-", dir=output.parent) as directory:
        staged = Path(directory) / "SerenityOS.vdi"
        subprocess.run(["qemu-img", "convert", "-p", "-f", "raw", "-O", "vdi", str(image), str(staged)], check=True)
        subprocess.run(["qemu-img", "compare", "-f", "raw", "-F", "vdi", str(image), str(staged)], check=True)
        with staged.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(staged, output)
    print(f"Disco VirtualBox listo: {output}")
    print("Crea una VM de 64 bits con UEFI y conecta este VDI como disco SATA existente.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=REPO / "Build/x86_64/installed-uefi.img")
    parser.add_argument("--output", type=Path, default=REPO / "Build/x86_64/SerenityOS.vdi")
    args = parser.parse_args(argv)
    try:
        export(args.image, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"No se pudo exportar: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
