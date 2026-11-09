# File Manager: operation error reporting

Date: 2026-11-09. This is the second bounded desktop improvement following
[the functional audit](DesktopFunctionalAudit.md) and
[the Run improvement](RunProcessStatusValidation.md). It is not a general
filesystem or desktop stability certification.

## Starting point and scope

Work started from clean commit `5727103edca2000eef788647442ad5273349a8cd` on a
new local branch, `desktop/file-operation-errors`. The Run commit and the
`stability-before-desktop-20261109` recovery tag were preserved. No remote was
changed or contacted.

Production changes are limited to:

- `Userland/Services/FileOperation/main.cpp`
- `Userland/Applications/FileManager/FileOperationProgressWidget.cpp`

The progress widget header, FileUtils, GML, Run, kernel and shared libraries were
not modified. No asynchronous ZIP work was implemented.

## Confirmed problems and correction

The helper returned many `TRY` errors directly to LibMain instead of writing an
`ERROR` record. The GUI therefore saw an unexplained end of its input and
displayed `Read from pipe returned null.`. Other paths wrote `WARN` separately,
so diagnostic behavior depended on which syscall failed.

The helper now tracks the current action and affected path and reports a single
terminal `ERROR` at the top level. It includes copy/move/delete, the affected
path, the action that failed and the available cause. Error results exit with
status 1; only completed operations emit `FINISH`. Directory iteration errors
are explicitly propagated: an unreadable source directory no longer produces
a success result after silently collecting no children. The original mkdir
error is retained instead of reconstructing it from a potentially changed errno.

The existing line protocol is retained. Displayed paths escape newlines,
carriage returns, tabs, backslashes and quotes, and replace invalid UTF-8 for
display only. Actual filesystem paths are unchanged. This also prevents a
filename from injecting an apparent `FINISH` or `ERROR` protocol record.

The GUI now:

- Displays the helper's diagnostic under `Copy Failed`, `Move Failed` or
  `Delete Failed`, rather than replacing it with a pipe error.
- Separately identifies a helper disconnect before a result: completion could
  not be confirmed. It does not infer success from EOF.
- Closes/disables the input before presenting the error, and ignores pending
  callbacks once it has closed. A later error or `FINISH` cannot generate a
  second notification or turn the failure into success.
- States that some files may already have changed and changes have not been
  undone. It does not promise rollback or remove partial output.
- Reads a complete diagnostic line instead of truncating it to 1 KiB, preserves
  spaces in the progress filename, and handles malformed progress without an
  assertion in the GUI.

## Build and repeatable tests

`ninja -C Build/x86_64 FileManager FileOperation -j 4` passed. The final production
build log is `Build/file-operation-errors-20261109/build-passed.log`.

Two regression files were added:

- `Meta/test-file-operation-errors.cpp`: guest fixture which runs the real
  helper as UID 100 and links the actual FileUtils/progress-widget objects.
- `Meta/test-file-operation-errors.py`: builds that fixture with the existing
  File Manager compile/link flags, installs the new binaries into a copy of an
  offline image, then runs QEMU with one CPU and `snapshot=on`.

The installed FileManager, FileOperation, fixture and already validated Run
binaries are checked by SHA-256 against the build outputs. The source image
and the installed backing image retain their hashes after testing. Read-only
fsck checks passed before and after installation. Test data is generated under
unique `/tmp/fm-*` directories inside the guest; no personal files or real disk
devices are used.

Successful command:

```sh
python3 -B Meta/test-file-operation-errors.py \
    --root-image Build/ext2-phase2-20261107-b/root.ext2 \
    --output Build/file-operation-errors-campaign-v3-20261109
```

All **12 helper cases** passed protocol, return-status and filesystem assertions:

1. Copy with exact source/destination contents.
2. Repeated copy preserving destination-name deduplication.
3. Move with source removal and exact destination contents.
4. Delete with the target absent afterward.
5. Copy into a directory without write permission.
6. Copy into a missing destination.
7. Copy using a regular file as the destination directory.
8. Move into a directory without write permission, retaining its source.
9. Partial batch copy: first file copied, second source unreadable. The first
   destination remains intact; the second is not created; sources remain.
10. Unreadable source directory: one error and no destination created.
11. Delete denied by the parent directory's permissions, retaining the file.
12. Filename containing newline/`FINISH`/`ERROR`: copied correctly without
    injecting terminal protocol records.

Every successful helper case had status zero, one `FINISH` and no `ERROR` or
`WARN`. Every failed case had status one, one `ERROR`, no `FINISH`, and the
expected path/cause.

All **nine GUI cases** passed: copy, move, delete, permission denied, missing
destination, invalid destination, partial copy, unexpected disconnect and
duplicate error records. The first seven invoke the real production helper
through FileUtils. The last two supply a deliberately closed pipe or two errors
followed by `FINISH` to the actual progress widget. Each case verifies a single
failure notification where appropriate, no success after failure, and a working
parent-window button after the result. That button rechecks files and closes
the test normally. This verifies the production consumer/event loop in a focused
test window; it is not a complete audit of File Manager's menus and shortcuts.

The nine result screenshots were visually reviewed. Permission, missing-path,
invalid-path and partial-copy messages showed operation, full test path, failed
action and cause. The partial-copy warning did not claim rollback. Disconnect
was visibly distinct from the reported permission error. The duplicate-record
test produced only the first error dialog.

Evidence is in `Build/file-operation-errors-campaign-v3-20261109/`:
`results.json`, `provenance.json`, `command.json`, fsck/build logs, serial/debug
logs and result/responsiveness PNGs for each case. The runner summary is
`Build/file-operation-errors-20261109/campaign-v3.log`. Failed fixture-build and
earlier harness attempts were retained. The first GUI run missed its test
button because of a wrong click coordinate and timed out; it is not counted as
a passed GUI run. The corrected campaign passed all checks.

## Limits and closure

The partial failure is between two files in a batch, not an injected mid-file
device-write failure. ENOSPC, hardware I/O errors, power loss, restart durability
and SMP were not tested or investigated in this stage. Existing collision,
overwrite, cancellation and ZIP behavior was not redesigned. This reporting
change is not a transaction or rollback implementation.

The GUI fixture requires the normal anon desktop and a 1024x768 display. Very
long-path dialog layout and malformed-protocol variants beyond the cases above
were not exhaustively tested. The source image contains existing installed
userland; only the listed binaries were replaced.

No critical blocker requiring another subsystem was found. The next previously
proposed improvement is asynchronous ZIP handling, but no work on it was started.
This stage stops after recording the validated change in its own local commit.
