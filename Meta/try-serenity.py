#!/usr/bin/env python3
# Copyright (c) 2026, the SerenityOS developers.
# SPDX-License-Identifier: BSD-2-Clause

"""Create (if necessary) and boot the UEFI installation with one command."""

import argparse
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = REPO / "Build/x86_64/installed-uefi.img"


def find_firmware():
    for directory, code, variables in (
        ("/usr/share/OVMF", "OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd"),
        ("/usr/share/OVMF", "OVMF_CODE.fd", "OVMF_VARS.fd"),
        ("/usr/share/edk2/x64", "OVMF_CODE.4m.fd", "OVMF_VARS.4m.fd"),
    ):
        code, variables = Path(directory) / code, Path(directory) / variables
        if code.is_file() and variables.is_file():
            return code, variables
    raise ValueError("Falta el firmware UEFI. En Ubuntu: sudo apt install ovmf")


def boot_command(image, code, variables):
    return ["qemu-system-x86_64", "-machine", "q35", "-m", "1G",
            "-drive", f"if=pflash,format=raw,readonly=on,file={code}",
            "-drive", f"if=pflash,format=raw,file={variables}",
            "-drive", f"if=none,id=os,format=raw,file={image},snapshot=on",
            "-device", "ich9-ahci,id=ahci", "-device", "ide-hd,drive=os,bus=ahci.0"]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Crea y abre la prueba UEFI de SerenityOS.")
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE,
                        help="Imagen instalada; por defecto Build/x86_64/installed-uefi.img")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar el plan sin escribir ni abrir QEMU")
    parser.add_argument("--repair-copy", action="store_true",
                        help="Reparar ext2 solo en la copia temporal al crear la imagen")
    parser.add_argument("--include-installer", action="store_true", help="Compilar e incluir el asistente nativo")
    parser.add_argument("--virtualbox", action="store_true", help="Exportar un VDI para VirtualBox en vez de abrir QEMU")
    args = parser.parse_args(argv)
    try:
        image = args.image.absolute()
        if args.include_installer and image == DEFAULT_IMAGE:
            image = DEFAULT_IMAGE.with_name("SerenityOS-Installer.img")
        # QEMU uses commas as option separators inside -drive values.
        if "," in str(image):
            raise ValueError("La ruta de la imagen no puede contener comas")
        if image.is_symlink() or (image.exists() and not image.is_file()):
            raise ValueError("La imagen debe ser un archivo regular, no un dispositivo ni enlace")
        code, variables = (None, None) if args.virtualbox else find_firmware()
        required = ["qemu-img"] if args.virtualbox else ["qemu-system-x86_64"]
        if not image.exists():
            required += ["e2fsck", "mkfs.fat", "mmd", "mcopy"]
            if args.include_installer:
                required += ["ninja", "debugfs"]
        missing = [tool for tool in required if not shutil.which(tool)]
        if missing:
            print("Faltan herramientas: " + ", ".join(missing), file=sys.stderr)
            print("Instálalas una vez: sudo apt install qemu-system-x86 qemu-utils ovmf e2fsprogs dosfstools mtools ninja-build",
                  file=sys.stderr)
            if not args.dry_run:
                return 1
        if not image.exists():
            print("Se creará la imagen instalada. Apaga primero la VM que use _disk_image.", flush=True)
            command = [sys.executable, str(REPO / "Meta/install-serenity.py"),
                       "--system-image", str(REPO / "Build/x86_64/_disk_image"),
                       "--kernel", str(REPO / "Build/x86_64/Kernel/EFIPrekernel/Kernel.efi"),
                       "--output", str(image)]
            if args.repair_copy:
                command.append("--repair-copy")
            if args.include_installer:
                build = ["ninja", "-C", str(REPO / "Build/x86_64"), "Installer", "kernel_x86-64", "generate_kernel_efi_image", "-j", "4"]
                if args.dry_run:
                    print(shlex.join(build))
                else:
                    subprocess.run(build, check=True)
                command += ["--installer-binary", str(REPO / "Build/x86_64/Userland/Applications/Installer/Installer")]
                payload = DEFAULT_IMAGE.with_name("SerenityOS-Payload.img")
                if not payload.exists():
                    payload_command = command[:command.index("--installer-binary")]
                    payload_command[payload_command.index("--output") + 1] = str(payload)
                    if args.dry_run:
                        print(shlex.join(payload_command))
                    else:
                        subprocess.run(payload_command, check=True)
                command += ["--payload-image", str(payload)]
            if args.dry_run:
                print(shlex.join(command))
            else:
                subprocess.run(command, check=True)
        else:
            print(f"Usando la imagen existente: {image}", flush=True)
        if args.virtualbox:
            output = image.with_suffix(".vdi")
            command = [sys.executable, str(REPO / "Meta/export-virtualbox.py"),
                       "--image", str(image), "--output", str(output)]
            if args.dry_run:
                print(shlex.join(command))
                return 0
            subprocess.run(command, check=True)
            return 0
        print("QEMU se abrirá en modo de prueba: los cambios se descartan al cerrar.", flush=True)
        if args.dry_run:
            print(shlex.join(boot_command(image, code, "<copia temporal de OVMF_VARS>")))
            return 0
        with tempfile.TemporaryDirectory(prefix="serenity-uefi-test-") as directory:
            private_vars = Path(directory) / "OVMF_VARS.fd"
            shutil.copyfile(variables, private_vars)
            child = subprocess.Popen(boot_command(image, code, private_vars))
            try:
                return child.wait()
            except KeyboardInterrupt:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                return 130
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"No se pudo abrir la prueba: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
