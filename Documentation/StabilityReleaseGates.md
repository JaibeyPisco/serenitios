# Stability release gates

The project remains experimental. A persistent desktop, successful installation
and passing individual suites are useful evidence, not a stable release. No
gate below is complete merely because a tool or design exists. Preserve failed
runs as well as passing runs, and associate evidence with the tested binaries.

## Kernel and memory

Existing baseline: the five-suite, one/two-CPU regression matrix passed 30 guest
executions after the unmap/writeback corrections. See
[phase 1](Kernel/StabilityPhase1.md). This does not cover every allocation path.

Required before a release candidate:

- Low-RAM boot, reservation failure, physical exhaustion and recovery, plus
  deterministic allocation-failure injection at selected kernel call sites.
- Process exit, fork/COW, shared mappings, concurrent unmap/writeback and
  scheduler tests across one, two, four and eight active CPUs where supported.
- Multi-hour workloads in a single running system, with memory/accounting
  trends, bounded resource growth, watchdogs and usable failure diagnostics.
- No unresolved kernel panic, deadlock, corrupted accounting or unexplained
  memory growth in the declared supported configuration.

`TestMemoryPressure` and the isolated runner add opt-in low-RAM coverage. They
do not inject failures into every allocator or prove the absence of small leaks.
Repeated fresh guests are not a long-duration workload in one system.
The 128 MiB one/four-CPU pressure matrix passed four guests; the 256 MiB
eight-CPU case passed with 512 child exits. These results corrected a concrete
reclaim-lock deadlock but do not close the remaining kernel criteria above.

## Data protection

Five ext2 crash-consistency failures are still blockers. The independent host
journal reader is a prototype, not kernel journaling. See
[transaction requirements](Kernel/Ext2Transactions.md) and
[crash testing](Kernel/Ext2CrashTesting.md).

Require recovery across enumerated write cuts, disk-full conditions, failed and
short writes, failed flushes and interrupted recovery. Verify arbitrary affected
file contents and metadata, not just one sentinel. Test emulated failures first
and real storage power-loss behavior on disposable media within the declared
hardware scope. Never advertise durability on the basis of cached write success.

## Installation, recovery and updates

The [UEFI rescue image](RecoveryMedia.md) has virtual-machine boot and recovery
evidence. Physical USB boot and hardware recovery remain unverified. Require
repeatable installation, diagnostic export, repair without destroying the
source, and an update mechanism with verified rollback after interrupted
updates. Preserve the previous bootable version until the new one is validated.
An offline clone/recovery tool does not by itself provide update rollback.

## Declared hardware support

Choose named PC models and record firmware, CPU, RAM and exact storage, graphics,
USB, network and audio identifiers. Validate each boot path and device with
stress, unplug/replug where applicable, restart, shutdown and recovery tests.
Mark unsupported devices explicitly. VirtualBox and QEMU results do not certify
physical hardware; a stable release can initially support a narrow hardware set.

## Applications and security

Require a recorded desktop/application crash baseline, fixes with regression
tests, unprivileged-session testing, permission and isolation checks, and an
inventory of listening services with their intended exposure. Track security
issues separately from functional failures. Include installer and recovery
privilege boundaries; do not assume root/init tests cover normal-user behavior.

## Continuous maintenance

`.github/workflows/stability-host.yml` defines host-side installer, recovery and
journal tests for pushes and pull requests. A local pass is not evidence that
the remote workflow has run. Kernel build/guest regression CI, sustained test
machines, release versioning, support periods and security-update delivery are
still required. Add every confirmed regression to an appropriate automated gate.
The local host safeguard suites passed 57 tests (installer 13, offline recovery
15, recovery-media safeguards 8 and independent journal reader 21). Journal
filesystem integration has separate evidence in the transaction document.
Retained logs and per-suite exit codes are in `Build/stability-host-ixk2ao1_`.
The replacement kernel baseline passed 15 guests / 87 case executions with
one/four/eight CPUs (`Build/kernel-stability-3mkivjq2`); pressure tests have
separate evidence in the phase-1 document.

Release candidates need a frozen supported configuration, reproducible artifacts,
checksums, known issues, archived passing/failing evidence and a tested rollback
path. Assign support ownership and duration before calling a release supported.
