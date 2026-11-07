# Phase 1: memory and thread regression baseline

This bounded x86_64 regression run exercises the current local kernel build in
isolated QEMU guests. It does not establish general stability or physical-PC
compatibility. Existing VirtualBox disks are not used.

## Run

From the repository root, with QEMU, debugfs and e2fsck installed:

```sh
ninja -C Build/x86_64 kernel_x86-64 TestMprotect TestMunMap TestThreadSleep TestAnonymousMmap TestWXProtection -j 4
python3 Meta/test-kernel-stability.py
# Repeat the matrix to exercise scheduling-dependent failures:
python3 Meta/test-kernel-stability.py --repeat 3
```

The runner creates a new `Build/kernel-stability-*` directory, repairs only a
private copy of `_disk_image`, installs the current test binaries and verifies
their hashes. Each test then boots separately with `snapshot=on`, 2 GiB RAM and
one or two CPUs. `smp=on` explicitly enables kernel multiprocessing. Logs, QEMU
arguments and incremental `results.json` are retained for diagnosis.

Tests cover memory access permissions, partial unmapping, copy-on-write
isolation, shared anonymous mappings, write/execute restrictions, and repeated
concurrent thread sleep/wakeup. Some deliberately crash child processes with
SIGSEGV; that is expected. Because each test runs as init, even a successful test
ends with the kernel's "Init process quit unexpectedly" panic. Success requires
both the LibTest success summary and init exit code zero. Other panics, nonzero
exits and timeouts fail the run. The runner waits for the complete exit-code
line before terminating QEMU, and verifies that each requested CPU reached its
scheduler idle loop.

## Initial finding (2026-11-07)

With SMP explicitly enabled, `TestAnonymousMmap` triggered a kernel assertion in
`Region::remap_vmobject_page`: the VMObject's region list contained a region
whose page directory had not yet been assigned. The failing trace is retained
under `Build/kernel-stability-h0mru0xj/TestAnonymousMmap-2cpu/debug.log`.

Region construction registers the region before `map()` installs its page
directory. Another process faulting on the same shared VMObject can visit that
region during fork. Unmapping can also clear the directory while a remap waits
for its lock: an initial null check alone was insufficient, and a follow-up run
still failed inside `map_individual_page_impl`.

The remap operation now takes a strong directory reference, skips unmapped
regions, locks the retained directory, and rechecks that the region still belongs
to that directory before touching PTEs. A subsequent mapping uses the VMObject's
current physical page. Repeated SMP regressions are needed because the original
failure was dependent on scheduling.

## Repeated validation result

The retained-directory patch compiled successfully. The three-round matrix in
`Build/kernel-stability-k852r53i/results.json` passed 27 of 30 isolated guest
executions. All requested CPUs were confirmed active. `TestAnonymousMmap`
passed in all six executions after the patch; this is regression evidence, not
proof that all region lifetime races are eliminated.

`TestMunMap` failed three executions: one CPU in round 3 and two CPUs in rounds
2 and 3. All exceeded the 30-second bound. The SMP logs contain an assertion in
`RecursiveSpinlock::unlock` while exercising shared-file unmapping; the
single-CPU log stops before completion of the concurrent-neighbor-fault test.
The lock ownership assertion and hang require further diagnosis; they are not
explained away as expected protection-test child crashes.

This initial run did **not pass**. Its experimental kernel was not deployed to
the user's normal VirtualBox installation; the failures were investigated next.

## Follow-up diagnosis and correction

GDB captures in `Build/kernel-stability-0z24spb0` located the SMP assertion in
the address-space lock's destructor at the return from `sys$munmap`. The panic
handler then tried to acquire that same lock and stalled. Shared-file writeback
had performed blocking filesystem I/O while the CPU-owned spinlock was held.
The single-CPU neighbor-fault case also exposed why spinning on outstanding
faults while holding that lock cannot make reliable progress.

The follow-up change separates metadata changes from blocking cleanup:

- Unmap preflights outstanding faults before changing any mappings. Syscall
  callers release the address-space lock and yield before retrying.
- Removed regions leave the VMObject list and have their PTEs unmapped under
  metadata locks, but their destruction/writeback is deferred until after the
  address-space lock is released. Partial unmaps retain backing-object references
  for the same deferred writeback.
- `msync` records backing objects and exact range intersections under the
  address-space lock, then flushes outside it.
- Shared-inode flushing pins physical pages, releases its VMObject spinlock
  before I/O, and uses a sleeping mutex to serialize flushes. Failed or short
  writes leave the affected pages dirty for retry.
- A regression test checks `msync` over three adjacent shared regions created
  by a partial `mprotect`, including each region's correct file offset.

Use `--diagnose` with GDB installed to retain failed-guest stack traces. This
option uses the x86_64 debug symbols and the fixed kernel load offset with
`disable_kaslr`; it does not attach to the user's VirtualBox VM.

## Validation after the follow-up correction (2026-11-07)

- Focused matrix: 16/16 guest executions passed in
  `Build/kernel-stability-ol1mmzap` (four rounds, one/two CPUs, `TestMunMap` and
  `TestMprotect`, including the new split-region `msync` regression).
- Complete matrix: 30/30 guest executions, containing 174 LibTest case
  executions, passed in `Build/kernel-stability-1bm9tnov` (three rounds of the
  five suites with one/two active CPUs). Kernel and test hashes are recorded in
  `provenance.json`; individual commands and logs are retained.
- The native installer integration also passed: reject the system disk,
  copy/verify the fixture and relocate/check GPT, then reject the occupied
  destination without changing it. Logs: `/tmp/serenity-native-install-test-kawxlfjf`.
- A fresh UEFI image and byte-compared VDI were generated in
  `Build/stability-phase1-20261107-kvyvm0_5` using the corrected kernel and the
  earlier DisplaySettings-corrected root filesystem. The VDI SHA-256 is
  `e2c3b3ab834e883f085a5d4e8baf18348164591e1b1414c72fe1fc24a27d2988`.
- VM `242` was switched to a Windows copy named
  `SerenityOS-System-Phase1-kvyvm0_5.vdi`. The copy's hash matched, and its UEFI
  boot reached the desktop and shell prompt. The previous VDI was retained.

These results pass the bounded phase-1 regression matrix. They do not certify
all kernel paths, process-exit writeback, long-duration stability or real
hardware. In particular, this work does not audit every other caller that may
perform shared-file writeback while holding unrelated locks.

## Limits and remaining work

- The allocation-failure retry paths in existing tests require fault injection;
  this runner does not inject allocation failures.
- This is not an out-of-memory, leak, long-duration or high-core-count test.
- Tests execute as root/init, not within a normal unprivileged desktop session.
- Disk crash recovery, network, USB, audio and physical hardware are outside
  this phase.
- Passing these checks is a baseline for subsequent stress and failure testing,
  not a release-readiness claim.

## Low-memory follow-up

`TestMemoryPressure` is explicitly opt-in through a marker installed only in the
runner's private root copy. Run it in a 128 or 256 MiB disposable guest:

```sh
ninja -C Build/x86_64 kernel_x86-64 TestMemoryPressure -j 4
python3 Meta/test-kernel-stability.py --tests TestMemoryPressure --memory-mib 128 --cpus 1 4 --stress-rounds 64 --repeat 2 --diagnose
python3 Meta/test-kernel-stability.py --tests TestMemoryPressure --memory-mib 256 --cpus 8 --stress-rounds 512 --timeout 300 --diagnose
```

The test reserves anonymous mappings until `ENOMEM`, releases them, and verifies
allocation resumes. It then faults an unreserved 512 MiB mapping in a child to
exhaust real physical pages, requires the OOM child's termination, checks a
parent sentinel, and repeatedly exits children with allocated memory. The normal
cleanup workload is scaled to headroom measured before pressure; a 128 MiB boot
can have less than 8 MiB of reservable memory. The runner additionally requires
the kernel's physical-exhaustion message and the test's completion markers;
an ordinary SIGSEGV, skipped test or init exit alone cannot pass.

The first run, `Build/kernel-stability-1w_0i05n`, timed out. Its GDB trace shows
`allocate_physical_page()` holding `m_global_data` while reclaiming clean inode
pages. Dropping a physical-page reference re-entered
`deallocate_physical_page()` and spun on that same non-recursive lock.

The allocator now limits the global lock to selecting a page, releases it before
VMObject reclamation, and retries under a fresh lock. A competing CPU or a pinned
page can prevent allocation even after reclamation, so this case no longer
asserts. Page zeroing retains explicit interrupt protection for the quickmap.
Process finalization also removes mappings before publishing the dead state and
waking waiters, so waiting parents do not race resource teardown after exit.

The first corrected test demanded an 8 MiB child allocation even in a guest with
only about 4 MiB of initial reservable headroom. That fixture failed; its records
are retained in `kernel-stability-t8exkwjd` and `kernel-stability-8monuet8`.
The scaled fixture passed all four 128 MiB guests (one/four CPUs, two rounds,
64 child exits each) in `Build/kernel-stability-uzmcjcez/results.json`.
The eight-CPU, 256 MiB guest also passed physical exhaustion and 512 child exits
in 61.7 seconds: `Build/kernel-stability-47m8oil9/results.json`. All requested
CPU idle-loop markers were observed. This is a bounded workload, not a
multi-hour soak.

The earlier expanded baseline in `Build/kernel-stability-ccy2fmgh` was
interrupted by a WSL restart after six passing guest results. It is incomplete;
the eight-CPU pressure attempt in `Build/kernel-stability-44_yemrh` was also
interrupted during preparation. Neither campaign is counted as a passing gate.
The replacement expanded baseline completed successfully:
`Build/kernel-stability-3mkivjq2/results.json` records 15/15 guests and 87 case
executions across the five original suites with one, four and eight active
CPUs. This exercises the changed allocator/finalization code without deliberate
memory pressure as well as the separate five passing pressure guests above.

These are genuine reservation and physical-pressure tests, not deterministic
failure injection into all kernel allocations. They do not measure exact leak
sizes, establish multi-hour reliability, audit every process-exit writeback lock
or test unprivileged desktop behavior. General release criteria are tracked in
[StabilityReleaseGates.md](../StabilityReleaseGates.md).
