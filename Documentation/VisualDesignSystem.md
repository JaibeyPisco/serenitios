# Visual design system proposal

Status: proposed, not implemented. Date: 2026-11-10.
Companion documents: [architecture audit](VisualModernizationAudit.md) and
[implementation plan](VisualModernizationPlan.md).

## Reference interpretation

The supplied dark image uses navy surfaces, bright blue selection, soft outer
corners, a top status strip, a centered dock, an application launcher, and layered
Settings/File Manager windows. The light image keeps that hierarchy while making
content surfaces pale and text dark. Both rely strongly on translucent surfaces
and a photographic purple/blue mountain background.

Adopt the hierarchy, color relationship and spacing rather than reproducing
every pictured feature. The initial implementation uses opaque fills. Decorative
blur, duplicate window-control sets, branded application icons, Windows-style
drive letters, and unsupported control-center switches are excluded. Keep
Serenity's existing three functional title buttons at the right, with explicit
glyphs. Application menus remain in their windows. The reference's Spanish
labels do not introduce a localization project; repository UI language policy
continues to apply.

All following values are proposed design choices, not measured samples from the
images or claims about the current desktop.

## Color tokens

| Semantic token | Light | Dark | Existing role or consumer |
| --- | --- | --- | --- |
| Desktop fallback | `#E8EDF8` | `#10182C` | DesktopBackground |
| Window/panel | `#F3F5FA` | `#18243C` | Window, MenuStripe, Tray |
| Content/input | `#FFFFFF` | `#101A2E` | Base, MenuBase |
| Raised control | `#FFFFFF` | `#223250` | Button |
| Primary text | `#182338` | `#F4F7FD` | WindowText, BaseText, ButtonText, MenuBaseText |
| Secondary text | `#52627A` | `#BAC7DC` | PlaceholderText and contextual labels |
| Structural stroke | `#7586A0` | `#6E829F` | ThreedShadow1, gutter/ruler borders |
| Accent/focus | `#005FCC` | `#63A8FF` | Accent, FocusOutline, TextCursor |
| Selected background | `#0B5ED7` | `#1D4ED8` | Selection, MenuSelection |
| Selected text | `#FFFFFF` | `#FFFFFF` | SelectionText, MenuSelectionText |
| Hover background | `#E3EDFF` | `#2B4164` | HoverHighlight |
| Pressed/inactive selection | `#D7E4FA` | `#324D75` | InactiveSelection |
| Active title surface | `#DCE7FA` | `#22385D` | ActiveWindowBorder1/2 |
| Inactive title surface | `#EFF2F7` | `#19253B` | InactiveWindowBorder1/2 |
| Active title text | `#182338` | `#F4F7FD` | ActiveWindowTitle |
| Inactive title text | `#52627A` | `#BAC7DC` | InactiveWindowTitle |
| Error text | `#B42318` | `#FFB4AB` | Proposed semantic use; not a new INI role |
| Warning text | `#875300` | `#F5D17A` | Proposed semantic use; not a new INI role |
| Success text | `#146C43` | `#7EDFA2` | Proposed semantic use; not a new INI role |

Complete theme files must additionally define disabled text, tooltips, overlays,
moving/highlighted window colors, syntax roles and all remaining applicable
palette fields. Do not set unrecognized semantic token names as INI keys.
Terminal ANSI colors remain a separately selected existing color scheme during
the first milestone; its background is not evidence of a full dark UI theme.

Acceptance targets are at least 4.5:1 for ordinary text and 3:1 for essential
control boundaries/focus indicators against adjacent surfaces. Check the actual
rendered combinations, not only primary text on one background. Color is never
the only indication of error, selection or focus. Disabled controls need visible
labels and a disabled behavior; they are not made to resemble active controls.

The opaque-color calculation for the proposed primary/secondary/selected text
pairs ranges from 5.68:1 to 16.18:1; focus pairs are 5.98:1 (light) and 5.23:1
(dark). The selected structural strokes measure 3.39:1 against the light panel
and 3.27:1 against the dark raised control. A lighter draft stroke failed the
3:1 target and was revised. Calculation evidence is retained in
`Build/visual-plan-audit-20261110/proposed-contrast.json`. These are calculated
proposal colors, not a rendered-UI accessibility certification.

## Typography

Retain the installed Katica 10 regular UI and Katica 10 bold window titles in
the first milestone. Use Katica 12 bold for later settings section headings;
Katica 12 regular is an optional large-density mode only after clipping tests.
Keep Csilla 10 for paths/terminal/code where fixed-width text is appropriate.
Do not globally upscale bitmap fonts or replace fonts to imitate the reference.

Hierarchy: body 10 regular; window/title and group labels 10 bold; section
headings 12 bold; secondary text body size with secondary color. Elide long
titles/paths with access to the full value. Preserve keyboard mnemonics and
readable focus indicators. Review accented names, long paths and narrow dialogs.

## Geometry and spacing

All dimensions are logical pixels at scale 1, not hardcoded physical pixels.
These are eventual targets; the first theme milestone retains current metrics.

| Component | Proposed target | Current anchor / condition |
| --- | --- | --- |
| Spacing scale | 4, 8, 12, 16, 24 | Existing base spacing 8 and margin 12 |
| Standard control | Height 32, horizontal padding 12 | Existing shared height 28; migrate forms individually |
| Compact control / toolbar icon | Height 28 / icon 16 or 24 | Keep compact mode for constrained layouts |
| Control radius | 5 | Existing painter metric |
| Window outer radius | 8 | Existing Default metric; verify clipping/input |
| Panel radius | 10 | Only where the selected painter supports it |
| Window title | Height 32, button target at least 28x28 | Existing title 28, button 24x22; code/input tests required |
| Border / keyboard focus | 1 / 2 | Preserve resize hit regions independently |
| Toolbar / list row | Height 40 / 28 | Check text, icons and scrolling at both scales |
| File Manager sidebar | Default 192, range 160–240 | Current tree width 175; keep it resizable |
| Settings category sidebar | 176–192 | Launcher reorganization, not embedded fake pages |
| Future top panel | Height 32 | Requires work-area changes |
| Future dock | Height 56, bottom margin 8, icon 32, entry 44 | Requires shell layout and window-state integration |
| Desktop icons | Existing 32-pixel assets in cells at least 80x80 | Confirm real IconView label metrics before applying |

At 1024x768, reserve 32 pixels above and 64 below for the eventual shell, leaving
672 logical pixels of work-area height. This is a layout proposal, not current
WindowManager behavior. Use a compact/overflow layout when shell contents do not
fit; never overlap clock, applets or window controls. Preserve fullscreen and
multi-screen behavior before enabling the new shell by default.

## Borders, shadows and rendering cost

Use a single neutral stroke and a small number of opaque surface levels.
Initially retain existing shadow behavior. A later optional shadow may use a
small cached nine-slice bitmap through existing shadow paths, with an opaque,
shadow-free fallback. Test clipping, damaged regions and frame hit areas before
enabling it. Do not implement live backdrop blur, wallpaper sampling, animated
transparency, dock magnification or continuous idle animation.

Redraw only damaged regions; cache icons/shadow bitmaps and avoid per-frame
image decoding. QEMU one-CPU TCG at 1024x768 is the baseline. Compare identical
drag/resize/menu traces against the baseline and record CPU use and interaction
latency; measurements are pending implementation. Reject sustained repainting
while idle and visible stalls during ordinary interaction. Static alpha is
optional only after profiling and must have a visually complete opaque fallback.

## Iconography and control states

Reuse repository icons and licenses, starting with navigation, folders, settings
and window controls. Prefer complete existing 16/32-size families; do not enlarge
a 16-pixel bitmap into a 48-pixel dock asset and call it finished. Any later
24/48-size asset must be authored at that size and checked in both themes.
Keep recognizable shapes and text labels; do not copy Finder/VS Code or other
branded assets from the reference. Use existing symbolic glyphs where contrast
allows; audit themed title icons on dark surfaces.

- Normal: raised surface, primary text, neutral stroke.
- Hover: hover surface; same geometry, no movement or enlargement.
- Pressed: pressed surface, with immediate input response.
- Focus: persistent 2-pixel accent outline while keyboard focus is present.
- Default action: accent boundary distinct from keyboard focus where possible.
- Checked/selected: selection or pressed surface plus check/selection state.
- Disabled: disabled semantic colors, no active hover and no activation.
- Error/busy: explanatory text and existing error/progress mechanisms. Do not
  replace truthful failure/progress information with decorative success states.

The present shared painter derives some pressed colors from inactive selection;
separating these semantics, if necessary, is a later code change. Test focus plus
hover, default plus focus, and disabled plus checked rather than only isolated
states. Existing checkboxes represent existing boolean actions; a switch is not
introduced merely to match a picture.

## Windows and shell organization

Titles remain left-aligned with a real application icon, one set of minimize,
maximize/restore and close controls, and clear active/inactive contrast. Preserve
modified-document indicators, modal ownership, resize regions and context menus.
Do not make transparent pixels clickable or reduce resize targets to the visible
one-pixel border. No global menubar is part of the initial shell proposal.

Eventually place launcher entry, system identity and available status applets in
the top panel, with actual clock/calendar actions. The dock combines existing
quick-launch entries with actual running-window state: activate an existing
window, restore a minimized one, or launch an executable as appropriate. Define
multi-window grouping and overflow before implementation. Preserve Super-key,
Super+digit, show-desktop and workspace behavior; reuse WindowList/AppFile rather
than introduce another task registry.

The launcher initially exposes installed executable apps, existing categories,
Settings, File Manager, Run and real shutdown/restart actions. Application-name
filtering can be a small later feature over the actual app model. Global file
search, recent documents, recommendations and pinned-item editing are omitted
until their storage/query behavior exists. A thumbnail or icon in a reference
does not imply the application is installed.

## File Manager and Settings

File Manager: simplify visual grouping around the existing breadcrumb/navigation
and view-mode actions; retain an editable path. Keep a real tree sidebar first.
A later favorites list may link only existing directories, retaining access to
the full tree and mounted paths. Use current icon/list/column models, visible
selection and statusbar. Keep operation dialogs, explicit failures and the ZIP
progress window. No new global search, cloud locations, tags, recents index or
recycling semantics are implied. Preserve filenames containing spaces and UTF-8.

Settings: begin with searchable names/categories of installed Settings apps and
launch their current windows, preserving Escalator and Apply/Cancel/Defaults.
Proposed groups are Appearance and Display, Input, Network, Users, Time, and
Application Settings, populated from verified app metadata. Do not show an
Updates, Notifications, Bluetooth or Media page without a real backing feature.
Embedding pages is a distinct later decision with lifecycle/privilege review.

A future control-center popup is optional and follows the shell work. It may
expose theme selection, display/settings launchers, clock and a verified audio
control only when the backing service is usable. In this QEMU configuration,
omit connected Wi-Fi/Bluetooth, hardware brightness, media playback and audio
sliders unless separately demonstrated. Do not show fake values, disabled
demonstration toggles, or a fictitious connected state.
