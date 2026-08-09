# Driving Blender with winauto-mcp

First dogfooding pass, 2026-08-08, against Blender 5.2.0 LTS (default
startup file, "Layout" workspace). Short pass compared to the Godot manual
-- covers the baseline technique and one real gotcha, not full UI coverage.

## UIA tree is empty -- same as Godot's canvas areas

`capture_screen` on Blender's main window returns only the OS window chrome
(System menu, Minimize/Restore/Close) via UI Automation -- 4 elements total.
Blender renders its entire UI itself (menus, buttons, panels, the 3D
viewport) with no accessibility tree exposed, so **every** interaction has
to go through pixel coordinates: `locate_in_region`/`click`, never
`click_element`/`get_elements`. This is a stronger version of the
Godot finding (there only the 2D/3D canvas was UIA-empty; here the entire
app is).

## Reading word positions off a zoomed/resized crop is unreliable -- same root cause as the "don't eyeball a displayed crop" rule, worse

To find the "Add" menu label in the viewport header row (`View Select Add
Object`), a first attempt cropped the row and **upscaled it 4x** to read
word positions by eye, then converted the read-off pixel positions back to
real coordinates by dividing by 4. The resulting coordinate landed on
"Select", not "Add" -- one menu item to the left of the intended target,
confirmed by screenshotting the result and seeing the wrong dropdown open.
A second attempt cropped the **same row at 1:1 (no resize)** and read
positions directly off that -- this located "Add" correctly on the first
try. Any resize (even displaying an upscaled crop for human/model
inspection) reintroduces exactly the crop-rescaling error the manual
already warns about for full screenshots -- the fix is the same: read
coordinates from an unscaled crop, then confirm with `locate_in_region` on
a narrow candidate region before clicking, never trust a single wide guess.

## `recall_location`'s margin must match the UI's density, or it false-reports drift

After locating and clicking "Add" correctly, `remember_location` saved its
tight bbox (`[323,42,348,53]`, ~25x11px). Calling `recall_location` on the
same, unchanged UI immediately after **returned `cache_hit: false`** at the
tool's old default `margin=40` -- even though the re-scanned bbox's center
had moved **0px**. Cause: expanding the 25px-wide "Add" bbox by 40px on
each side reaches into the neighboring "Select" and "Object" labels only
~15-20px away, so `find_content_bbox` returns one large bbox spanning all
three words -- which fails the size-ratio check (>2x the original) even
though the actual target never moved. Retrying with `margin=8` (small
enough to stay inside the gap between labels) gave a correct `cache_hit:
true`. **Fixed the tool's default** from `margin=40` to `margin=15` and
documented in its docstring that dense menu/toolbar rows need a small
margin (5-10px), while isolated buttons can use a larger one. This is the
same class of bug as `diff_since_snapshot`'s live-redraw false positive --
a verification step that's too permissive for a specific app's layout
silently reports "stale" (or in that case "changed") when nothing is
actually wrong.

## Splash screen on startup

A fresh/unsaved Blender window opens with a centered "Getting Started"
splash overlay. A single `click` well outside its bounds (e.g. into the
empty viewport background) dismisses it -- confirmed objectively via
`snapshot()`/`diff_since_snapshot()` (large `changed_bbox` covering nearly
the whole client area).

## Not yet explored

Everything past the startup screen and the Add menu: Properties panel tabs,
Outliner, Shading/Sculpting/Animation workspaces, N-panel, modal tools
(move/rotate/scale, which run until a confirming click and may behave
differently under `SendInput` vs. real mouse hardware), file save/open
dialogs. Treat this file as a starting point, not full coverage, unlike
`godot-editor-manual.md`.
