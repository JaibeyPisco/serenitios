# File Manager asynchronous ZIP validation

Date: 2026-11-10. Scope: the existing Create Archive and Extract Here actions.
This is a bounded application change, not a general system stability certification.

## Implementation

`ArchiveOperations.cpp` tracks one ZIP child per File Manager process using the
existing event loop's SIGCHLD handler and nonblocking `waitpid`. Other children
are also reaped. No threads, kernel APIs, or global job service were added.
An independent, modeless progress window keeps the process alive if its main
window closes. Quit closes that main window while an archive operation is active.
File Manager retains the `sigaction` pledge because the event loop restores
signal handlers during shutdown.

The helper writes to a temporary sibling of the destination. Only a normal,
successful exit publishes the result using rename. Failed compression preserves
an existing archive. Extraction requires a new destination and never merges into
an existing directory. Failures show the source, destination, exit status or signal,
available diagnostics, and unpublished output location in one dialog. They do not
claim that a partial operation was rolled back.

The existing zip utility swallowed input and traversal errors. An unreadable
input reproduced exit status zero and an empty archive with the baseline binary;
the patched utility exits nonzero. Traversal errors now propagate, and nested
symbolic links fail explicitly instead of silently skipping remaining entries.

## Build and environment

The resumed workspace contained the ZIP changes on `main` at `b98c0878e2`, a
README-only child of `209b1845620`. The independent branch
`desktop/file-manager-async-zip` preserves that commit, Run's `5727103edca`, and
the stability checkpoint/tag. No existing changes were reverted.

Normal Ninja regeneration failed because the host Lagom installation was absent.
The modified File Manager translation units and zip were successfully recompiled
and linked with the existing compile database, Ninja link commands, and retained
toolchain/libraries. Logs are in `Build/async-zip-resume-20261110/`:
`build.log` records the infrastructure failure; `build-direct.log` and
`build-pledge-fix.log` record successful component builds. This does not claim a
fresh full-system build. clang-format 20.1.8 validation and `git diff --check` passed.

The retained `_disk_image` failed the initial read-only filesystem check. Only a
new disposable copy was repaired with e2fsck; the original image was preserved.
No EXT2 investigation or source change was made. Every VM used QEMU TCG, one CPU,
1024x768 graphics, no network, and a disposable disk with `snapshot=on`.
Candidate binaries installed in the test image were extracted and hash-checked.
Source and backing-image hashes remained unchanged after the tests.

The kernel was not modified or rebuilt. Its SHA-256 remained
`7093075d2a15e2079cc85509bd498683b3f453ca876b2915940cfc19e8d189ba`.

## Executed ZIP checks

`Meta/test-async-zip.py` builds a guest fixture linked to the actual archive
operations object. `Build/async-zip-validated-20261110/results.json` records all
six scenarios passing with the same runtime pledges as File Manager:

- Create a folder archive, extract it, and reject an overlapping second operation.
- Repeat compression over an existing archive with a shorter replacement file;
  verify the replacement contains no stale data.
- Extract an invalid ZIP: one readable error, no published destination.
- Fail compression on unreadable input: nonzero status and existing destination
  contents preserved.
- Compress and extract a 64 MiB incompressible payload while event-loop timers
  continue, a button responds, and the window is moved.
- Close the main window during compression: the independent progress window
  remains, the archive completes intact, and the application exits normally.

Every scenario checks that no children or zombies remain using `waitpid`/ECHILD.
Archive member names and every content byte are verified, including a nested
binary, a filename with spaces, and `café.txt`. Error/progress screenshots were
reviewed; the selected files are listed in `visual-review.json`.

The actual File Manager context menus were exercised separately in
`Build/async-zip-native-final-20261110/`. During 64 MiB compression its window was
moved and refreshed, Text Editor responded with its unsaved-document dialog,
and Alt+F4 closed File Manager's main window while the helper continued. The
helper completed and the process exited without a coredump. A subsequent Extract
Here completed; `TestAsyncZip verify-native` verified all names and bytes in the
archive and extracted files. A targeted `ps` check found neither File Manager
instance nor their ZIP children remaining. Screenshots, serial logs, command,
hashes, and `results.json` are retained there.

An earlier native run found a reproducible shutdown crash: the event loop's
signal-handler destructor called `signal` after File Manager dropped its
`sigaction` pledge. The application-only pledge correction above fixed it;
the failed run is preserved in `Build/async-zip-native-resume-20261110/`.
Earlier preparation and test attempts are retained and are not counted as passes.

## Regression evidence

`Build/async-zip-file-regression-20261110/` records the existing FileOperation
suite: 12 helper cases and nine GUI cases passed. These cover successful copy,
move and delete, permission failure, missing/invalid destination, partial copy,
helper disconnection and duplicate terminal notifications. Files and continued
GUI response were checked; result screenshots were reviewed.

`Build/async-zip-run-regression-v2-20261110/` records all seven Run outcomes
passing: exit zero, missing command, status 126, status 1, SIGTERM, successful
retry, and SIGABRT with Crash Reporter. Screenshots were reviewed; the signal
case retains editable input and the crash case has no duplicate Run error dialog.
The source and backing-image hashes remained unchanged.
The first attempt timed out because its fixed startup delay allowed input before
Run's window appeared. It is preserved as a failed automation attempt. The
artifact-only replay increases that startup delay from 5 to 12 seconds without
changing Run or its assertions.

## Limits

- One ZIP operation runs per File Manager process. There is no cancellation,
  percentage estimate, or coordination between separate instances.
- Closing the main window allows the operation to finish; it does not cancel it.
- Failed unpublished output is deliberately retained and can consume space.
  Recursive cleanup of large partial results is not performed on the GUI thread.
- Extraction into an existing directory is refused. This intentionally avoids
  overwriting an existing tree or exposing an apparently complete partial tree.
- Existing helper format/security limits, source files changing concurrently,
  external destination changes, and power-loss durability are not resolved here.
- Opening a ZIP directly as File Manager's initial path retains its separate
  preexisting temporary-directory launcher behavior. The modified actions are
  Create Archive and Extract Here; no broader launcher redesign is claimed.
- Generated images, binaries, screenshots and logs remain outside the commit
  under Build. No changes are published to a remote repository.
