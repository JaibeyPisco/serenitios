# Run: child process outcome validation

Date: 2026-11-09. Scope: the first desktop improvement, following
[the desktop audit](DesktopFunctionalAudit.md). This is not a general stability
certification. Kernel and File Manager sources were not changed.

## Recovery point

The 43 previously audited source, test, CI and documentation files were committed
locally as `a68feaa1968001b34ecc063fd468d85210943517`, on
`checkpoint/stability-before-desktop-20261109`, with annotated tag
`stability-before-desktop-20261109`. The working branch is
`desktop/run-process-status`.

The checkpoint contains no Build files, disk images or generated logs. Existing
research artifacts remain in Build. No push, fetch or remote modification was
performed. The previous patch, untracked-file archive and verified history
bundle remain in `Build/desktop-audit-20261109/`; checkpoint provenance is in
that directory's `checkpoint.json`.

## Change

`RunWindow.cpp` previously applied `WEXITSTATUS` to every successful `waitpid`
result. A shell terminated by a signal could therefore appear to exit with
status zero, causing Run to close and log success.

Run now checks `WIFSIGNALED` and `WIFEXITED` before interpreting an exit code.
Signal termination returns a separate outcome, logs the signal and redisplays
Run with a two-line message in its existing information area. The input remains
editable and selected for replacement. No additional Run modal dialog is
created for a signal, whether or not Crash Reporter appears.

Normal exit zero retains success behavior. Codes 126 and 127 retain failure
behavior with clearer messages. Other nonzero exit codes now also report failure
and display their status instead of logging success. Spawn/wait errors have
specific diagnostics; an interrupted wait is retried on `EINTR`. Each new attempt
clears the previous diagnostic.

Production changes are limited to:

- `Userland/Applications/Run/RunWindow.cpp`
- `Userland/Applications/Run/RunWindow.h`

The other additions are `Meta/test-run-process-status.py` and this document.
No GML redesign, shared library change or new application was needed.

## Build and regression

`ninja -C Build/x86_64 Run -j 4` passed. After the final input-selection
adjustment it rebuilt RunWindow and relinked Run. The final build log is
`Build/run-functional-20261109/build-final.log`.

The new Python regression tool copies an offline root image, installs the newly
built `/bin/Run`, verifies the installed binary's SHA-256, runs read-only fsck
before and after installation, then boots QEMU with one CPU and `snapshot=on`.
It drives the actual GUI with QMP, checks outcome-specific serial logs, captures
input/result PNGs and verifies both backing-image hashes after terminating the
disposable guest. No production image or disk device is modified.

Successful invocation:

```sh
python3 -B Meta/test-run-process-status.py \
    --root-image Build/ext2-phase2-20261107-b/root.ext2 \
    --output Build/run-status-regression-v2-20261109
```

All seven cases passed log assertions and their screenshots were visually
reviewed:

1. `exit 0`: Run closes normally and records success.
2. `serenity_run_missing_command_20261109`: the real shell returns 127;
   Run displays "Command not found" and permits correction.
3. `exit 126`: Run displays the execution/permission diagnostic. This tests
   status handling, not an injected filesystem permission failure.
4. `exit 1`: Run reports status 1 and does not record success.
5. `kill -TERM $$`: the child shell terminates by signal 15; Run remains visible
   with an inline message and the command selected. There is no Run error modal.
6. `exit 0`, replacing the preceding command in the same Run window: successful
   retry, with the desktop and terminal still responding.
7. `kill -ABRT $$`: the child shell terminates by signal 6; one Crash Reporter
   window appears, while Run displays its inline signal message behind it.
   Run neither records success nor opens another error modal.

Artifacts are in `Build/run-status-regression-v2-20261109/`: `results.json`,
`provenance.json`, `command.json`, serial/debug logs and per-case screenshots.
`Build/run-functional-20261109/regression-v2.log` contains the runner summary.
The kernel SHA-256 remained
`7093075d2a15e2079cc85509bd498683b3f453ca876b2915940cfc19e8d189ba`.

The first automated attempt, `Build/run-status-regression-20261109`, timed out
because its Super-key launch raced Taskbar startup and typed into Terminal.
The captured input frame confirms that Run was not open. The runner now starts
Run from the default terminal. That failed harness attempt and the earlier
manual-test artifacts were retained; they are not counted as passed regression
cases. Screenshot review remains explicitly separate from automatic log checks.

## Remaining limits

- The regression requires the normal anon desktop, its default terminal and a
  1024x768 display. It is a focused UI fixture, not general GUI automation.
- Userland comes from the existing offline test image; only Run was replaced.
  This does not certify every current desktop source or installed application.
- Run still waits synchronously for its child shell. Making long commands
  asynchronous is outside this change.
- These outcomes describe the directly waited-for shell. If it normalizes a
  descendant's signal into an ordinary nonzero exit status, Run reports that
  status rather than guessing a signal.
- The existing LaunchServer path still reports acceptance of an application
  launch; this change does not supervise every subsequently launched application.
- Spawn failure, wait failure and `EINTR` were not fault-injected in the GUI
  regression. Their handling was reviewed and compiled.
- The shell crash and incomplete reboot recorded in the desktop audit remain
  separate, unconfirmed issues. This change does not claim to fix either.

Next recommendation: improve File Manager's operation-error messages, using the
bounded scope and acceptance criteria already recorded in the desktop audit.
Neither that improvement nor asynchronous ZIP operations has been implemented.
