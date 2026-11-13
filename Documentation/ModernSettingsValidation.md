# Settings V5: categorized application launcher

Date: 2026-11-13. Branch: `desktop/modern-settings-v5`.
Parent: `1b27c73bfed2c904276e9c3c0d0ea1e3681eac29`.
Status: the exercised V5 checks passed. No V6/V7 or general OS stability
certification is included.

## Implementation and scope

Settings remains a launcher for the existing configuration applications. It
loads their valid Settings-category `.af` entries once at startup and preserves
name ordering, icons, descriptions, executable identity and RequiresRoot data.
No individual settings application's implementation was changed or embedded.

The sidebar contains All settings, Appearance, Input, System and Applications.
Only groups with discovered entries are listed. Current grouping is:

- Appearance: Display.
- Input: Keyboard and Mouse.
- System: Certificate, Clock, Network and Users.
- Applications: Browser, Calendar, Games, Mail, Maps and Terminal.

Unmapped future Settings executables remain visible under Applications rather
than disappearing. These are navigation groups, not claims about hardware or
new services. No Wi-Fi/Bluetooth/update/notification pages were added.

The right panel presents existing app icons, short names and descriptions in
32-pixel rows. It opens an application on Enter or double-click. The full
selected description is also available in the status bar and its tooltip;
Users identifies that authentication is required.

Search matches application names within the selected category, ignoring ASCII
case and surrounding whitespace. Selecting a different category clears the
query. The placeholder identifies its scope; All settings provides global
search. Clear and Escape reset the query, Ctrl+F focuses/selects it, Down moves
into results and Enter opens the first result. An empty result has a real
message and Enter does nothing.

The launcher uses existing LibGUI widgets and V1-V3 palettes/painters. No shared
LibGUI/LibGfx changes, global font changes or resource generators were needed.
At widths below 580 pixels it reduces the sidebar and hides the description
column, retaining names, icons, search and scrolling. The widget minimum is
420 x 300. Default uses its existing Classic frames/control painter; the new
organization is available in all three themes.

Activation still uses GUI::Process::spawn_or_show_error. Metadata arguments and
working directory are forwarded; RequiresRoot prepends the executable to the
existing Escalator invocation. No password collection or privilege bypass was
added to Settings. File/View/Help, Quit, fullscreen, command palette and About
remain, with Find added to View.

Files: `Applications/Settings/main.cpp`, local `SettingsAppsModel.h`, the guest
model/privilege fixture and its build helper, the operational launcher profile
selection, and these documents. No kernel, EXT2, driver, File Manager, Taskbar
or individual settings backend code was modified.

## Executed validation

Used QEMU TCG, one CPU, 1024 x 768, scale 1 and a disposable disk. Baseline
captures came from the preserved V4 modern base. Candidate testing used a
separate image, with a test-only anon credential for authentication checks.
The final run-modern integration used the production V5 base with the original
credentials and an automatically discarded overlay.

- **Build:** normal `ninja -C Build/x86_64 Settings` compiled only the Settings
  object and executable. The guest fixture also built successfully. No full OS
  rebuild or dependency installation ran.
- **Model:** the guest fixture passed all 13 app entries and category counts
  1/2/4/6, mixed-case/trimmed queries, category scope, empty/whitespace reset,
  unmatched UTF-8 input, invalid indices, unknown-entry fallback and Users'
  privileged metadata.
- **Categories/search:** mouse and keyboard category changes, mixed-case Display
  lookup, no results, Enter with no results, Escape/Clear, scoped lookup and
  category-triggered query clearing were exercised. Display and Mouse opened
  from keyboard search/results.
- **Real destinations:** Browser, Calendar, Certificate, Clock, Display, Games,
  Keyboard, Mail, Maps, Mouse and Terminal settings windows opened through the
  new launcher and were closed. Network correctly reported **No network
  adapters found**, because this QEMU profile intentionally has no NIC. Its
  hardware configuration was not declared functional or replaced by a fake page.
- **Privilege:** Users displayed the existing Escalator prompt. Cancel returned
  without opening Users; a wrong test password was rejected. Correct test
  authentication opened UsersSettings, and the process-statistics fixture
  verified `uid=0`. The original base/overlay and production V5 image never
  received the temporary test credential. The original Users backend filters
  low system UIDs; no account mutation was needed to test launcher escalation.
- **Existing actions:** Browser's Show bookmarks checkbox was changed, Cancel
  was pressed, and reopening showed the original value. Appearance Apply/theme
  changes continued to work. This does not certify every control in every
  independent configuration application; their source remained unchanged.
- **Themes/layout:** Dark, Light and Default before/after captures were reviewed.
  A category and query remained across theme changes. All themes worked in a
  narrow window; the 420 x 300 case scrolled to and selected the final Users
  entry, with full names and authentication status accessible. No horizontal
  scrollbar was needed for the launcher content in the exercised layouts.
- **Desktop smoke:** File Manager opened normally and Run completed `exit 0`.
  No V4 file/ZIP suite was rerun in V5; those binaries/libraries were not changed.
- **Published integration:** `./run-modern.sh --test --headless` booted the
  selected V5 base with one CPU, opened Settings and successfully filtered
  Display. The final singular/plural count copy was checked there as well.

Early prototypes exposed an empty/null-query filtering error and incorrect use
of Window::set_minimum_size while automatic widget sizing was enabled. Both
were fixed locally before the candidate checks: empty queries bypass matching,
and the minimum is set on the main widget. Diagnostic attempts with stale focus
coordinates also failed or probed Users after it had closed; these were not
counted as passes. Corrected input and a later live UID-0 probe passed. Logs and
prototype records remain retained. No assertion was removed or unrelated
subsystem investigated.

There were no Settings-process assertions after those prototype corrections.
The implementation adds no timers, indexing service, parallel processing or
polling. Responsiveness was observed during input, scrolling and theme changes;
this is not an instrumented compositor/performance certification.

## Reproduce and inspect

```
ninja -C Build/x86_64 Settings
python3 Meta/build-settings-v5-test.py --output /tmp/new-settings-v5-fixture
```

The output directory must be new. The helper only builds a guest executable;
it does not install or start a VM. `TestSettingsV5` expects the supplied 13-entry
inventory. Its optional `root` check requires an already authenticated, live
UsersSettings process and asserts its UID. Do not interpret a closed-target
probe failure as a product crash.

Local evidence: `Build/modern-settings-v5-20261113/` contains build logs,
provenance, fsck output, native input recipes and serial logs. `evidence/` holds
category/search/app/privilege/minimum-size captures, `validation-results.json`
and `integration.json`. Comparable scenes include `dark-before.png` versus
`dark-after.png`, and the corresponding Light/Default pairs, at the same
resolution/background and launcher upper-left placement. The new window is
larger to accommodate navigation and descriptions, and remains resizeable.

Other useful captures: `users-root-final.png`, `root-uid-proof.png`,
`authentication-rejected-verified.png`, `browser-cancel-restored.png`,
`minimum-window-last-setting.png` and `run-modern-search-integration.png`.

## Image selection, preserved data and rollback

V5 uses `Build/desktop-modern/modern-v5.ext2`, with a new writable
`desktop-v5.qcow2` created on the first normal launch. The base was flattened
from the cold V4 user overlay and updated only with the new Settings binary,
so existing guest data was carried forward. V4's `modern.ext2` and
`desktop.qcow2` were neither replaced nor modified; checksums were checked.
The pinned kernel is unchanged. The credential-modified test disk was removed
after QEMU stopped; screenshots, logs and results remain.

`Meta/run-modern.py` now reads a small optional `profile.json` containing only
base/overlay filenames inside its existing work directory. Missing profiles
retain the V4 defaults, and existing backing-file validation and single-run lock
still apply. This change is needed to expose V5 without rebasing or overwriting
a user's V4 overlay, not a new OS preference system.

To check V5 from Ubuntu/WSL:

```
./run-modern.sh
```

Then open Settings from the existing applications menu, or enter `Settings` in
Terminal/Run. Select categories, Ctrl+F and try Display, Mouse and Users. Select
Modern Light/Dark/Default through Display Settings -> Appearance. `--test`
discards that session's guest changes when QEMU exits.

The active generated profile is:

```
{"base": "modern-v5.ext2", "overlay": "desktop-v5.qcow2"}
```

For rollback, stop QEMU and change **only** the profile selection to:

```
{"base": "modern.ext2", "overlay": "desktop.qcow2"}
```

Never replace a backing file underneath an existing overlay. V4 and V5 guest
changes diverge after this upgrade; rollback does not merge subsequent files.
No images, credentials or bulky logs are committed or published remotely.

## Remaining limits

Search is a startup app-name/category filter, not filesystem or settings-content
indexing; reopen Settings to discover newly installed metadata. Backend pages
remain separate applications. Real network hardware, all backend mutations,
scale 2, other architectures, persistence across guest reboots and general
kernel/filesystem stability were not certified. No V6 or V7 implementation was
started.
