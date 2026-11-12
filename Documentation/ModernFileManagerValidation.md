# Modern File Manager V4: implementation and validation

Date: 2026-11-12. Branch: `desktop/modern-file-manager-v4`.
Base: `eea00ffa4f3e39902feb53464d05ac92d7d4f236` (V3).

Status: the exercised V4 checks passed after the bounded cursor correction.
This is not a certification of general OS stability or every application.
The earlier blocked report and all eight pending files were preserved in
`Build/modern-file-manager-v4-20261112/pending-before-final-correction.tar`.
No V5 work or remote publishing is included.

## Visual implementation

- `FileManagerWindow.gml` and `main.cpp` provide one Modern navigation row,
  reusing Back, Forward, Parent, the editable breadcrumb and the three view
  actions. Edit/create/delete commands retain their menus and shortcuts.
  Default retains its Classic toolbars and presentation.
- The existing resizable folder tree gains a heading, consistent insets and
  full-row selection. Narrow Modern windows cap the pane's preferred width
  so that the file area remains usable. Deep paths keep horizontal scrolling.
- `DirectoryView.cpp/.h` opt into Modern presentation, remove nested content
  frames and apply local table row padding. The status bar uses existing counts
  and selection information, with a compact height.
- `IconView.cpp/.h` provide opt-in rounded selection tiles, matching hit/damage
  regions and wrapped UTF-8 labels. Windowed cells are 84 x 80; desktop cells
  remain 80 x 80. Other IconViews remain Classic by default.

`DesktopWidget.cpp` was inspected and left unchanged: the actual desktop icons
are painted by DirectoryView/IconView. Existing files, icon artwork, fonts and
positions were retained. No kernel, EXT2, driver, theme resource, window
manager/decoration, Run, FileOperation or ZIP implementation was modified.

## Confirmed crash cause and correction

The native multi-file move was reproduced both with the isolated V3 binaries
(Default) and the uncorrected V4 binaries (Modern Light). The first move could
pass; moving the files back with the cursor on the last row reproduced the
failure. Both symbolicated backtraces contain the same path:

```
SortingProxyModel::sort_mapping -> AbstractView::set_cursor
 -> IconView::did_change_cursor_index -> AbstractView::font_for_index
 -> SortingProxyModel::data -> FileSystemModel::data [assert at line 561]
```

This is a **preexisting V3 bug**, not a new V4 drawing or file-operation failure.
A shrinking model remaps the cursor before the view receives its final model
update. The old cursor still has structurally valid coordinates, but its row
can already be outside the model. IconView queried that obsolete row's font;
the proxy mapped it to an invalid source index and FileSystemModel correctly
asserted. `is_valid()` alone did not establish that the row still existed.

The correction is local to `IconView::did_change_cursor_index`: only recompute
the old item's rectangles/font if `model()->is_within_range(old_index)`.
The new cursor and normal model refresh still update the live items.
No assertion was removed, no model/proxy algorithm was restructured, and no
copy/move paths, mutations or operation counts were changed.

`Meta/test-icon-view-cursor-update.cpp` reproduces the shrinking-proxy transition
with a strict model that rejects invalid data queries. Eight iterations and
empty-model resets passed on the corrected library. The native V3/V4 crash
backtraces, rather than an assumption about shared source, establish that the
original fault predates V4.

## Build and reproducible guest fixtures

Used the existing cross toolchain and available Lagom installation. No packages
were installed and no full OS rebuild ran. Normal Ninja targets for libgui and
FileManager compiled; required libdesktop/libmaps dependencies were relinked.
The last correction required eight Ninja actions, including one C++ object.
The final incremental build reported no work.

```
ninja -C Build/x86_64 -j3 Userland/Libraries/LibGUI/libgui.so Userland/Applications/FileManager/FileManager
python3 Meta/build-file-manager-v4-tests.py --output /tmp/new-v4-fixtures
```

The output directory must be new. The builder does not install binaries, edit
images or start QEMU. Install its binaries only into an offline disposable
image. `TestIconViewCursorUpdate` runs the cursor regression;
`TestFileManagerV4 contracts` checks the opt-in grid and unchanged desktop cell
rectangles. Other FileManager fixture modes are read-only checks following
actual GUI operations on `/home/anon/V4-test`, not implementations of those
operations. The source defines the exact test names and bytes to prepare.
The builder was executed and both rebuilt binaries matched the tested binaries
byte for byte. These fixtures are target-specific diagnostics, not production
applications or host tests.

The x86_64 object size of IconView remained 1176 before and after; the new flag
uses available tail padding and adds no virtual slots. Required target
libraries were rebuilt and installed together. This does not certify another
architecture's ABI.

## Executed validation

All runs used copied disposable images, QEMU TCG, **one CPU**, 1024 x 768,
scale 1 and `snapshot=on`. No personal data or important disks were used.
Installed binary hashes and image commands are recorded in provenance files.

- **Cursor/model:** strict regression passed eight shrinking proxy updates and
  empty-model resets; grid/ABI/desktop-cell contracts passed.
- **Actual File Manager operations:** copy, cut/paste move, F2 rename, new folder,
  new empty file, confirmed deletion and multi-file clipboard copy passed
  subsequent guest byte/existence checks. Names included spaces, UTF-8 accents
  and a long filename.
- **Drag-and-drop:** same-location drop was a no-op; three round trips (six
  native multi-file moves) and Ctrl-copy between two windows passed read-only
  checks after every operation. The cursor was deliberately placed on the last
  item, exercising the original trigger. Source/destination existence and exact
  payloads were checked. No crash or verification failure occurred in the final
  session.
- **Navigation/layout:** Back, Forward, Parent, editable paths, tree navigation,
  icon/table/column views, Ctrl+P, toolbar/location toggles, hidden-bars Ctrl+L,
  actual splitter resizing and narrow/wide window resizing were exercised.
  Modern Light table/icon and Modern Dark column/icon captures show the narrow
  layout with navigation and view buttons available.
- **Theme transitions:** the same window kept its Source path and three-file
  selection through Default, Dark, Light, Dark and Light; further Dark/Light
  narrow-window checks passed. Classic and Modern title controls remained the
  existing single sets.
- **Desktop:** six original icon bitmap crops matched their original coordinates
  in all three themes. Double-clicking `V4 café.txt` opened its expected text in
  Text Editor, which was closed without edits.
- **Run:** real UI tests passed missing-command exit 127, signal 15 and successful
  retry exit 0. Error messages remained readable.
- **FileOperation:** 12 CLI cases and six GUI scenarios passed: copy, move,
  delete, permission denied, invalid destination and controlled partial failure.
  The GUI remained responsive; error cases had one failure notification and no
  success message. Partial work was verified rather than claimed to roll back.
- **ZIP:** the existing production ArchiveOperations fixture passed valid
  compression/extraction and invalid-archive error handling. A 64 MiB job
  accepted interaction, its owner window was dragged and closed while running,
  and the archive's names and decompressed contents were verified afterward.
  It recorded 244 live event-loop ticks and `ECHILD` after completion, confirming
  no remaining children or zombies. ZIP production code was not changed.
- **Visual comparison:** before/after captures use the same background,
  applications and resolution. The final Default File Manager crop matched V3
  exactly: zero differing pixels out of 342608. This is a scene check, not
  exhaustive visual equivalence.

Input-script mistakes were corrected and not counted as passes: a reverse drop
initially hit an overlying Terminal; an early ZIP wait expected `1` rather than
the actual logged `true`; initial splitter coordinates hit the pane margin.
The later native attempts/captures verified the intended interactions. Earlier
blocked artifacts and failed attempts remain available.

## Basic performance and limitations

No visible interaction freeze or abnormal continuous repaint was observed in
the exercised window moves, menus, view changes and theme transitions. With
Terminal and Appearance open, ten-second scheduler samples reported WindowServer
at 11.08% for V3 and 12.15% for the final build; Taskbar was 2.06% and 2.10%.
Three successive Terminal title-area pixel samples were identical. These are
basic observations under TCG, not repaint counters, a speedup claim or proof of
performance equivalence. Existing clocks, graphs and cursors keep updating.

Existing directory byte totals can lag external moves until refresh (also
visible in the V3 run); the cursor correction does not alter that cache.
Actual filenames, contents and existence were verified independently of the
status text. Long labels are bounded/elided and deep trees require horizontal
scrolling in narrow windows. Scale 2, other architectures and every application
or state combination were not tested.

New icon artwork, unified Settings (V5), launcher (V6), top bar/dock (V7), blur
and other reference-image effects remain outside this implementation.

## Evidence and preservation

All local evidence is under `Build/modern-file-manager-v4-20261112/`:

- `baseline/`: V3 before images, commands, provenance and idle sample.
- `candidate/`: earlier blocked session, reconstructed disposable runtime image,
  original payload checks and diagnostic records.
- `diagnostic-v3/` and `diagnostic-v4/`: native reproductions, screenshots,
  serial logs and symbolicated `backtrace.txt` files.
- `final/`: corrected installed binary hashes, guest logs, result JSON,
  before/after comparisons, live themes, narrow views, file/ZIP/error captures,
  idle observations and preservation checks.
- `evidence-manifest.json`: hashes of retained screenshots, logs and recipes.

Comparable pairs include `baseline/dark-file-manager-before.png` against
`final/dark-file-manager-after.png`, and the corresponding Light pair.
Matching desktop, desktop-selection and filename scenes are also retained.
`final/zip-live-window-moved.png`, `zip-owner-closed.png`,
`desktop-file-open-settled.png` and `sidebar-resize-confirmed.png` capture the
additional interactions.

All earlier commits and the stability checkpoint remain ancestors. Prepared
baseline, candidate and final backing-image hashes were unchanged by the tests;
QEMU was stopped afterward. The kernel hash also remained unchanged.
Disk images, generated binaries and bulky logs are excluded from the commit.
clang-format 20 checks and git diff --check passed. The V4 source, fixtures and
this report are consolidated in an independent local commit on the named branch.
