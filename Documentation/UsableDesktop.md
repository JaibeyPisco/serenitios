# Usable desktop baseline

This update prioritizes the installed virtual desktop: boot, normal-user file
editing, terminal sessions, accessible windows and basic networking. The system
remains experimental; the [stability release gates](StabilityReleaseGates.md)
remain open.

## Changes

- Includes the kernel memory-pressure fixes validated in the phase-1 follow-up.
- `utmpupdate` now treats an initially empty session file as an empty registry,
  instead of failing JSON parsing whenever the first Terminal starts.
- Restoring saved window geometry constrains the window to the current desktop,
  keeping controls accessible after a resolution or monitor change.
- Adds a Terminal desktop shortcut and `Primeros-pasos.txt` in Spanish.
- The existing VM disk is copied, preserving its filesystem and user files.
  Display configuration remains at its existing 1440 × 900 resolution.
- VM 242 changes its virtual NIC from AMD PCnet to Intel 82540EM/E1000, matching
  a driver in this tree. NAT and the connected cable setting are retained.

## Focused validation

Artifacts are in `Build/usable-desktop-z8aexqff`, with `validation.json`, build
logs, checksums and screenshots. QEMU booted the new UEFI image into the desktop
twice. The session used UID 100 (`anon`). Display Settings opened; File Manager
opened Documents; Text Editor saved `usable.txt` through the file-permission
portal. The document remained intact after a clean shutdown and second boot.

The second boot used the updated session utility and GUI library. The terminal
session registry contained its UID/PID entry without the previous JSON error.
The browser restored fully inside the screen and rendered a local HTML page over
HTTP. DHCP and a gateway ping also passed. Both boots shut down cleanly, and the
final image export was compared sector-for-sector with its VDI conversion.
The Windows copy's SHA-256 matched and VM 242 subsequently reached its desktop
with the new disk and Intel NIC attached. `virtualbox-desktop.png` records that
boot. Gateway/HTTP checks above were completed in QEMU; the additional live
VirtualBox network check was not confirmed before handing control to the user.

This checks a basic desktop workflow. It does not establish modern website/TLS
compatibility, physical hardware support, audio behavior, update rollback after
power loss, or general filesystem crash consistency. The VM's previous system
disk remains the manual return path.

## Installed VM and return path

New disk: `C:\Users\Jaibey Pisco\VirtualBox VMs\242\SerenityOS-Usable-z8aexqff.vdi`.
The Windows copy is hash-checked before attachment. The previous disk,
`SerenityOS-System-Phase1-kvyvm0_5.vdi`, is retained unchanged. A pre-update VM
configuration copy is `242.before-usable-z8aexqff.vbox` in the same folder.

To return to the previous system, shut down VM 242 and attach the retained disk
at controller IDE, SATA port 0. Keep the new disk as well: files created after
this update belong to that disk and are not automatically copied backward.
