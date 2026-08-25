# winauto-mcp — behaviour specification

What this server does, stated so someone who cannot see the code can check it.
It says nothing about how the code is organized; a rewrite from scratch that
satisfied this document would be a correct rewrite.

**Scope.** A stdio MCP server, Windows only, Python 3.12. It attaches to one
running window at a time and lets a caller read it and drive it.

## Global rules

| | |
|---|---|
| **Coordinates** | Every coordinate accepted or returned — click points, element rects, regions, highlight boxes — is relative to the attached window's **client area**, in physical pixels, matching the image `capture_screen`/`screenshot` return. Screen-coordinate translation is never the caller's problem. |
| **Attachment** | Exactly one window is attached. Attaching replaces any previous attachment. Every tool except `list_windows` and `attach_window` fails with "no window attached" until one is. If the attached window has closed, the attachment is dropped and the call fails. |
| **DPI** | Per-monitor DPI awareness is established before any other Win32 or graphics call, so captured pixels and clicked coordinates are in the same space on scaled displays. |
| **Errors** | A tool that cannot do what was asked raises; the client sees an error result. Nothing fails silently, and no tool returns a success string for an action it did not perform. |
| **Journal** | Every tool call is recorded (below), including calls that failed and actions that were refused. |

## Reading the screen

### `list_windows()`
- **Output** JSON list of visible, non-minimized, titled top-level windows:
  `hwnd`, `title`, `process`, `pid`, `rect`.

### `attach_window(hwnd)`
- **Input** a window handle from `list_windows`.
- **Output** confirmation naming the window and the journal session started.
- **Rules** Brings the window to the front and starts a tracking overlay.
  Starts a **new journal session**, discarding the record of the previous one
  from memory. Clears any memory of what the caller has looked at. Rejects a
  handle that is not a window.

### `detach_window()`
- **Output** confirmation. Hides the overlay. Later calls needing a window fail.

### `capture_screen()` / `screenshot()`
- **Output** a PNG image of the attached window's client area.
  `capture_screen` additionally returns a text summary of the window's UI
  Automation tree (element names, types, rects); `screenshot` skips the tree
  walk and is the one to use for re-checking the screen.
- **Rules** Both work **while the window is behind other windows** — the
  window is asked to render itself rather than the screen being scraped. Apps
  that cannot render on request fall back to a screen grab, which is only
  correct while nothing covers them. Both mark the returned frame as "the
  frame the caller has seen", which is what the stale-target guard compares
  against. Fails if the window has no visible area (minimized, offscreen).

### `get_elements()`
- **Output** JSON list of UI Automation elements with names, control types and
  client-relative rects.
- **Rules** The walk is capped (depth 15 / 3000 nodes / 150 results) so a deep
  tree cannot hang the call; elements past the cap are absent. Canvas-drawn
  apps (Blender, the Godot editor, games) legitimately return an **empty**
  tree — for those, pixels are the only route.

### `locate_in_region(x1, y1, x2, y2, threshold=30)`
- **Output** JSON `{bbox, center}` — the tight bounding box of pixels
  differing from the region's dominant background colour by more than
  `threshold` on any channel, plus its centre point, in client coordinates.
- **Rules** Assumes a small, mostly-flat region containing one piece of
  content. Fails if the region is empty of content or lies outside the client
  area. Marks the frame it measured as seen. **This is the supported way to
  get a click coordinate**; reading one off a displayed screenshot crop is
  not, because displayed crops may be rescaled.

### `snapshot()` / `diff_since_snapshot(threshold=10, region=None)`
- **Output** `snapshot` stores the current frame as a reference.
  `diff_since_snapshot` returns `{"changed_bbox": [x1,y1,x2,y2]}` or the
  string `no change detected`.
- **Rules** `diff_since_snapshot` fails if no snapshot was stored. `region`
  limits the comparison; the returned box stays in full-image coordinates.
  An inverted or degenerate region is rejected.
- **Caveat that changes how the output must be read** The box is drawn around
  *every* changed pixel, so several small changes in different corners yield a
  box spanning the space between them, most of which never changed, and whose
  centre is not a changed pixel. A large box means "spread out", not
  "everything". Narrow with `region` to find the actual clusters.

### `wait_stable(timeout=5.0, settle_ms=400, interval=0.12, threshold=10, region=None)`
- **Output** JSON: whether it settled, how long it waited, how many polls,
  whether anything changed during the wait, and where the last change was.
- **Rules** Polls the window until it goes `settle_ms` without a change above
  `threshold`, or `timeout` elapses. **Never called automatically by any other
  tool** — waiting is always an explicit decision. Reports timing, not pixels,
  so it does **not** count as the caller having looked at the screen. Rejects
  `timeout <= 0` and an inverted region.

### `wait_for(name, timeout=10.0, interval=0.5)`
- **Rules** Polls the UI Automation tree for an element whose name matches
  (exact, then substring, case-insensitive); raises on timeout. Useless
  against apps with no UIA tree — use `wait_stable` there.

## Driving the window

Sending input **requires the window in front**, and uses the one real mouse
and keyboard. Every input tool raises the window if it is not already there.

### `click(x, y, button="left", double=False, modifiers=None, force=False, keep_cursor=False)`
- **Output** on success, a line stating what was clicked.
- **Rules — refusal** Before clicking, the area within 40 px of `(x, y)` is
  compared against the last frame the caller was *shown*. If it changed,
  **nothing is clicked**: the call returns a report (`blocked: true`,
  `performed: false`, how stale the view was, at which step, which region was
  checked, where it changed) **plus a fresh image of the window**. That fresh
  frame counts as seen, so re-issuing the same call goes through — a change
  never blocks twice. A window resized since the caller last looked always
  refuses, whatever else is true. `force=true` skips the check entirely.
  Refused calls appear in the journal like any other.
- **Rules — pointer** Afterwards the pointer is returned to where it was
  before the call, unless `keep_cursor=true`. `keep_cursor` is required when
  the next step follows the pointer (a Blender modal transform after G/R/S, a
  hover-driven menu).
- **Rules** `button` must be `left` or `right`; `modifiers` entries must be
  ctrl/alt/shift. Both are rejected otherwise.

### `drag(x1, y1, x2, y2, button="left", force=False, keep_cursor=False)`
- **Rules** Presses at the start point, moves through intermediate points (not
  a teleport — many apps only register a drag if the pointer visibly moves
  while held), releases at the end. Same refusal rule as `click`, applied to
  the **start** point. Same pointer restoration.

### `scroll(x, y, clicks, keep_cursor=False)`
- **Rules** One `clicks` unit is one wheel notch; positive scrolls up. Same
  pointer restoration. No stale-target refusal — a scroll does not depend on
  what is under the pointer the way a click does.

### `type_text(text)` / `press_key(key)` / `hotkey(keys)`
- **Rules** `type_text` sends literal Unicode to the focused control, so Thai
  and any other script type correctly without keycode mapping. `press_key`
  takes one name from a fixed set (enter, tab, escape, backspace, delete,
  arrows, home, end, pageup, pagedown, space, ctrl, alt, shift, f1–f12) and
  rejects anything else, listing what is valid. `hotkey` holds a chord in
  order and releases in reverse; entries are those same names or a single
  a–z/0–9 character.

### `click_element(name, button="left", double=False)`
- **Rules** Finds a UI Automation element by visible name (exact, then
  substring, case-insensitive) and clicks its centre. Fails if nothing
  matches. If several match, the first in tree order is used and the result
  says so.

### `release_control()`
- **Output** the title of the window put back in front, or a note that there
  is nothing to give back.
- **Rules** Restores the window that was in front before automation took
  focus, and forgets it, so a second call in a row reports nothing to do. The
  attachment survives; **reading the window keeps working from behind**. It is
  the caller's job not to call this mid-interaction, because a menu opened by
  the previous click closes when its window loses focus.

### `highlight(rects)`
- **Rules** Draws debug boxes on the overlay. Visual only; changes nothing
  about the target app.

## Remembering a target across sessions

### `remember_location(label, x1, y1, x2, y2)` / `recall_location(label, margin=15, threshold=30)`
- **Output** `recall_location` returns the coordinates and `cache_hit: true`
  only after **re-scanning the live screen** inside the cached box expanded by
  `margin` and confirming the content is still there and still about the same
  size. Otherwise it reports `cache_hit: false` and drops the entry.
- **Rules** Entries are stored on disk next to the server, keyed by process
  name + client size + label, so a resized window or a different app cannot
  hit. A cache hit therefore means "confirmed just now", never "trusted".
- **Caveat** `margin` must be smaller than the gap to the nearest neighbouring
  element, or the re-scan merges neighbours into one large box and reports a
  false miss — 5–10 px for a dense toolbar row.

## The journal

Every tool call is appended to a session folder under
`%TEMP%\winauto-mcp\<session-id>\`, created by `attach_window`.

- **Written per call** a line in `journal.jsonl` with sequence number,
  timestamp, tool name, arguments, success flag, result and duration —
  arguments and results truncated so one huge value cannot bloat the file.
- **Frames** actions with a visual effect (click, drag, scroll, type, key,
  chord, element-click) also store **before and after JPEGs**, downscaled to
  800 px wide. The scale factor is recorded with the entry, because these are
  evidence to look at, **not** a coordinate source.
- **Retention** the last 5 session folders; older ones are deleted when a new
  session starts. This is scratch evidence, not an archive.
- **Never fatal** a journal that cannot be written does not fail the call it
  was documenting.

### `history(last=20, tool_name=None, failures_only=False)`
- **Output** JSON: session details plus the most recent records, oldest first,
  optionally filtered by tool name (substring) or to failures only.

### `replay_frame(seq, which="after")`
- **Output** the stored image for that step, plus a text header naming the
  tool, time and arguments, and warning against reading coordinates off it.
- **Rules** Rejects an unknown step (saying which range is available) and a
  `which` other than `before`/`after`.

## Third-party dependencies

| | |
|---|---|
| Win32 API (`pywin32`, `ctypes`) | window enumeration, foreground control, `SendInput` for all simulated input, `PrintWindow` for occlusion-tolerant capture |
| `mss` | screen capture, used only as the fallback when `PrintWindow` returns nothing usable |
| `Pillow` | all image comparison, cropping, scaling and encoding |
| `uiautomation` | the UI Automation element tree |
| `psutil` | process names for window listing |
| `mcp` | the MCP server transport |
| `tkinter` | the tracking/highlight overlay |

## Costs and irreversibility

- **No money is spent.** No network calls, no external services, no API keys.
- **Actions on the target app are real and are not undoable by this server.**
  A click that deletes something has deleted it; there is no dry-run mode and
  no undo tool. The stale-target refusal reduces *misdirected* clicks but is
  not a confirmation prompt.
- **The mouse, keyboard and foreground are shared with whoever is at the
  machine.** Input cannot be delivered in the background to the apps this
  server targets (measured against Blender, the Godot editor and Notepad), so
  a run visibly takes over the desktop while acting. Reading does not.
- **Journal frames are screenshots of whatever was on the window**, written
  unencrypted to `%TEMP%`. If the window shows something private, so do they,
  until the folder is pruned five sessions later.
- `.location_cache.json` next to the server is written and pruned by
  `remember_location`/`recall_location`.

## Verification

`tests\smoke.py` drives the real tools against a throwaway Notepad it launches
and kills, checking each behaviour above that can be checked without a human:
the journal, the stale-target refusal (both as a predicate against synthetic
frames and end to end through a real click), `wait_stable` against a window
deliberately kept repainting, reading a window while another is parked on top
of it, and pointer/foreground restoration.

`tests\diag_*.py` and `tests\spike_background*.py` are diagnostics, not tests:
they print measurements and assert nothing. They exist because each overturned
a wrong assumption — that an idle window drifts pixel to pixel, that a diff box
means everything in it changed, and that background input might be possible.
