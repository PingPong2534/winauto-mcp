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
| **What counts as "seen"** | The server tracks what the caller has actually been *shown*, as a list of views, each covering a rectangle of the window. Looking at the whole window counts for the whole window; looking at part of it counts for **that part only**. A newer view supersedes any older view it fully contains, and only the most recent 8 are kept. The stale-target guard asks "which view covers this point, and has that view's area changed since?" — so a partial look can never certify a coordinate elsewhere on screen. |

## Reading the screen

### `list_windows()`
- **Output** JSON list of visible, non-minimized, titled top-level windows:
  `hwnd`, `title`, `process`, `pid`, `rect`.

### `attach_window(hwnd, take_control=False)`
- **Input** a window handle from `list_windows`.
- **Output** confirmation naming the window, whether it was raised, and the
  journal session started.
- **Rules** Chooses which window every other tool acts on. By default it
  **does not touch the desktop**: the window is left in front or behind
  exactly as it was, and no outline is drawn. All reading tools already work
  from behind, so a run that only looks never disturbs anyone. The window is
  raised, and the outline appears, at the **first input**, which is the first
  moment input requires it. `take_control=true` raises it immediately instead,
  for when a person should see which window is about to be driven. Either way
  it starts a **new journal session**, discarding the record of the previous
  one from memory, and clears any memory of what the caller has looked at.
  Rejects a handle that is not a window.
- **Cost** Deferring the raise adds nothing, because every input path raised
  the window itself already — it was being paid twice. Measured: attach 6.0 ms
  deferred against 26.7 ms immediate; the first click afterwards costs 11.9 ms
  more than a later one. Net ≈ 9 ms saved, and a read-only run pays none of it.

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
  correct while nothing covers them. Both mark the returned frame as seen for
  the **whole** client area. Fails if the window has no visible area
  (minimized, offscreen).

### `capture_region(x1, y1, x2, y2)`
- **Input** a rectangle in client coordinates.
- **Output** two parts: a JSON header giving the region actually captured, the
  crop's size, and the offset to add to a coordinate read off the crop to get a
  client coordinate; then a PNG of **only that rectangle**.
- **Rules** The rectangle is clamped to the client area, so a request running
  off the edge returns the overlapping part and says so in the header rather
  than failing. A rectangle that is inverted, empty, or entirely outside the
  client area is rejected. Captured the same occlusion-tolerant way as
  `screenshot`. Counts as having looked at **that rectangle only** — a later
  click inside it is judged against this view, and a click outside every
  rectangle looked at this run is refused. The journal stores the whole frame,
  not the crop, so a replay still shows the surrounding context.
- **Why it exists** A spot check ("did the dialog appear?", "did that field
  update?") should not cost a whole window. Measured on a Notepad window: 4,244
  bytes for the crop against 26,404 for the same moment's full frame.

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
  area. Counts as having looked at the region it measured, and nothing else —
  so the coordinate it just returned can be clicked, while the rest of the
  window still has to be looked at on its own. **This is the supported way to
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
- **Rules — refusal** Before clicking, the server finds the most recent view
  covering `(x, y)` and refuses in two cases. **(1) Never looked there**: no
  view covers that point, so the coordinate came from memory rather than from
  anything seen this run — the refusal lists the rectangles that *were* looked
  at. **(2) Looked, but it moved**: the area within 40 px of `(x, y)`,
  intersected with that view's rectangle, differs from how the view showed it.
  Either way **nothing is clicked**: the call returns a report (`blocked:
  true`, `performed: false`, how stale the view was, at which step, which
  region was checked, where it changed) **plus a fresh image of the whole
  window**. That fresh frame counts as seen everywhere, so re-issuing the same
  call goes through — a change never blocks twice. A window resized since the
  caller last looked always refuses, whatever else is true. `force=true` skips
  the check entirely. Refused calls appear in the journal like any other.
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
  focus, and forgets it, so a second call in a row reports nothing to do. Also
  removes the tracking outline, which comes back at the next action. The
  attachment survives; **reading the window keeps working from behind**. It is
  the caller's job not to call this mid-interaction, because a menu opened by
  the previous click closes when its window loses focus.

### The tracking outline
- **What it means** A green outline is drawn around the attached window
  **exactly while automation holds the desktop** — from the first input until
  `release_control()`, `detach_window()`, or the window closing. It is a
  statement about right now, not a bookmark: an attached window that is only
  being read is not outlined.
- **Rules** It is repainted only when the window's rectangle or the highlight
  boxes actually change. A window sitting still is not redrawn, no matter how
  long the session runs; a window that moves is followed. It is click-through
  and changes nothing about the target app.
- **Why the rule exists** Redrawing a transparent always-on-top window the
  size of the target several times a second for a whole session is enough
  compositor work to make the tracked app stutter, and makes the outline
  itself flicker — which reads as the automation having frozen when it has
  not. Measured: 1 repaint over 3 idle seconds, against ~20 before.

### `highlight(rects)`
- **Rules** Draws debug boxes on the overlay, showing the outline if it is not
  already up. An empty list clears them and puts the overlay away. Visual
  only; changes nothing about the target app.

## Doing several things in one call

### `run_steps(steps, delay_ms=120, stop_on_error=True)`

The point of this tool is to spend one round trip on a sequence the caller
already knows works, instead of one per click.

- **Input** a non-empty list of step objects, each with `do` naming the action
  plus that action's arguments:

  | `do` | required | notes |
  |---|---|---|
  | `click` | `x`, `y` | also `button`, `double`, `modifiers`, `keep_cursor`, `force` |
  | `drag` | `x1`, `y1`, `x2`, `y2` | also `button`, `keep_cursor`, `force` |
  | `scroll` | `x`, `y`, `clicks` | |
  | `type` | `text` | |
  | `key` | `key` | one of `press_key`'s names |
  | `hotkey` | `keys` | |
  | `click_element` | `name` | |
  | `wait` | `ms` | |
  | `wait_stable` | — | `timeout`, `settle_ms`, `threshold`, `region`; waits exactly as the `wait_stable` tool does |
  | `capture` | — | `region` optional; returns an image mid-run |
  | `check` | `region`, `expect` | `expect` is `changed` or `unchanged` |

- **Output** a JSON summary — whether the whole script succeeded, how many of
  how many steps were performed, which step it stopped at (or null), a
  per-step record of action / result / duration, and the labels of the images
  that follow — then those images, in the order listed.
- **Rules — validated whole, then run** The entire script is checked before
  **any** step is performed: unknown action, missing argument, bad `button` or
  `expect`, more than 40 steps, or more than 60 s of total requested waiting
  are all rejected with nothing performed. A typo in step 7 must not be
  discovered with steps 1–6 already applied to a real app.
- **Rules — the guard covers step 1 only** The stale-target refusal of `click`
  and `drag` is applied to the first action the script performs, and then
  disarmed. It cannot apply to the rest: step 2 acts on a screen step 1
  deliberately changed, which the caller has never been shown, so their
  coordinates are a prediction rather than an observation. If step 1 is
  refused, nothing runs and the whole window comes back.
- **Rules — stopping** A step that raises is recorded as failed and stops the
  script, unless `stop_on_error=false`, in which case the remaining steps still
  run and the summary reports which ones failed. A `check` step fails when the
  region did not change (or did change) against the previous checkpoint — the
  script's start, or the last `capture`/`check` — which is how a wrong
  prediction ends a run instead of driving the app further into an unplanned
  state. When a script stops early and has no image of its own, the window as
  it looks at that moment is attached.
- **Rules — cost and evidence** Each step is journaled separately as
  `script:<action>`, with its own before/after frames, so a run that goes wrong
  is reconstructable with `history()`/`replay_frame()`. `delay_ms` (0–3000)
  pauses between steps. A `capture` step's region counts as looked at, the same
  as `capture_region`; the rest of the window does not.
- **Caveat** This trades a real safety property for round trips. It is for
  sequences already watched working; nothing in it verifies that the app is in
  the state the script assumes except the `check` steps the caller adds.

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
- **Written per step of a script** a `run_steps` call produces one record per
  step, named `script:<action>`, rather than one record for the whole call —
  so a batch is as reviewable afterwards as the same actions issued one by one.
- **Frames** actions with a visual effect (click, drag, scroll, type, key,
  chord, element-click, and every scripted step) also store **before and after
  JPEGs**, downscaled to 800 px wide. The scale factor is recorded with the entry, because these are
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
frames and end to end through a real click, including a click in a part of the
window that was never looked at), the region scoping of "seen", `wait_stable`
against a window deliberately kept repainting, reading a window while another
is parked on top of it, and pointer/foreground restoration.

It also checks the two claims made above that are easy to assert and never
measure: that a crop really is cheaper to send than the whole window (it
compares the byte counts of both taken at the same moment, rather than against
a number recorded earlier), and that a rejected script performs **nothing** —
verified by asking the journal whether any `script:*` record exists, not by
looking for an absence of change on screen, which a blinking text caret is
enough to fake.

The attach/outline behaviour is checked with **another application genuinely in
front**, not on the freshly-launched target: an attach that stole the desktop
would look identical to one that did not if the target were already foreground.

`tests\diag_*.py` and `tests\spike_background*.py` are diagnostics, not tests:
they print measurements and assert nothing. They exist because each overturned
a wrong assumption — that an idle window drifts pixel to pixel, that a diff box
means everything in it changed, that background input might be possible, that
an overlay tracking a still window costs nothing to repaint, and that deferring
the foreground raise to the first action would make that action slower.
