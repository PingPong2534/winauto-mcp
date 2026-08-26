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
| **Whose keystroke is whose** | Every key and mouse event this server sends carries a signature that Windows delivers untouched. A key event is attributed to the **person** only when Windows says nothing injected it; to **this server** when it carries our signature; and to **some other injector** otherwise. Windows' own "injected" flag alone cannot answer this — an on-screen keyboard, a remote-desktop session and another automation tool all set it. |

## Sharing the keyboard with the person at the machine

Automation and the person share one physical keyboard. While an action runs,
the person's keystrokes are **held out** of the app so they cannot land in the
middle of what is being typed; the server's own keystrokes pass the same block
because they are signed.

**What is recorded: nothing.** Not the key, not the character, not which keys —
only a running count of "a human key event happened" and when the last one was.
A machine-wide keyboard hook that kept key codes would be a keylogger; the only
honest way to promise it is not one is for the data never to exist. Nothing in
the journal, the status output, or on disk can reconstruct anything typed by
anyone.

**The keyboard always comes back.** No single failure can strand it, because
the ways out do not depend on each other:

| | |
|---|---|
| **It is a lease, not a lock** | The block expires by itself after at most 20 seconds. No release call, no cooperating caller and no working server is needed for the keyboard to return. |
| **Three Escapes** | Three presses of Esc within 1.5 seconds release it immediately **and latch it off**, so nothing takes it again until `release_keyboard()` is called. Someone reaching for this is having a problem; silently re-blocking them would be the worst possible response. |
| **The mouse is never blocked** | A held keyboard with a working mouse is an inconvenience. Blocking both would be locking someone out of their own machine. |
| **Windows removes it** | Windows discards a keyboard hook whose handler is too slow, and discards all of a process's hooks when it exits. A hung or killed server therefore heals by itself. |
| **Ctrl+Alt+Del** | Handled by Windows beneath any hook and cannot be blocked here, by design of the OS. |

**Nothing watches the keyboard until input is sent.** The hook is installed on
the first action that drives the window, not at startup, so a session that only
reads screens never installs one.

**The person's attempt to type is reported.** An action that ran while they
pressed a key returns its normal result plus a note that N key events were held
out, and `run_steps` stops at that step by default. Someone reaching for the
keyboard mid-run wants the machine more than the script does.

## Sharing the foreground with the person

Sending input requires the target window to be in front, so an action raises
it. **When the action finishes, the desktop is handed straight back** to
whatever window was in front beforehand — so a key typed in the gap between two
actions lands in the window the person is looking at, and not in the middle of
the app being driven. It happens after every tool that sends input, and **once**
at the end of `run_steps` rather than between its steps, since a script is one
interaction however many steps it has.

The refusals matter more than the hand-back:

| | |
|---|---|
| **Only what it took** | The window handed back to is the one automation itself displaced. A window nobody displaced is never raised. |
| **Not while a menu is open** | A menu closes the moment its owner loses focus, so handing back would undo the click that opened it. What is owed is **not forgotten** — the hand-back happens when a later action ends with the menu closed. An open menu also blocks any foreground change desktop-wide, so this is correctness, not only manners. |
| **Not if the person moved on** | If a third window took the foreground after automation did, the person has already gone elsewhere; what is owed is forgotten rather than yanked back. |
| **Not if the window is gone** | A window that has closed or been hidden is dropped silently. |
| **Never fails an action** | A hand-back that cannot happen changes nothing about the action's result. |

**Known blind spot.** "A menu is open" is answered from menus *Windows* owns.
A menu the app draws itself — XAML/WinUI, Electron, Qt, a game's own UI — is
paint inside the window and is invisible here; Windows 11 Notepad's own File
menu is one. For those, `keep_foreground(true)`.

**The tracking outline never holds the foreground.** It is a decoration; if it
did, keystrokes would be aimed at a rectangle and the person's window would
never come back.

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
and keyboard. Every input tool raises the window if it is not already there,
and holds the person's keyboard out for as long as it runs (above), returning a
note on its normal result if they pressed a key while it did.

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
- **Against the automatic hand-back** Unconditional where that one refuses: it
  asks none of the questions above, because the caller has said the interaction
  is over. It also releases the keyboard and removes the outline, which the
  hand-back does not.

### `keep_foreground(enabled)`
- **Output** confirmation stating whether actions will hand the desktop back,
  and, when switching back on, the window handed back immediately if one was
  owed.
- **Rules** `true` suppresses the automatic hand-back for the rest of the
  session: actions raise the target and **leave it in front**. `false` restores
  the default and hands back at once if a window is owed. Changes nothing about
  the keyboard, the outline, or the attachment.
- **What it is for** An interaction that spans several tool calls and dies if
  focus moves between them: a Blender G/R/S transform, a rubber-band selection
  continued in a later call, an app-drawn dropdown that cannot be detected.

### `keyboard_status()`
- **Output** JSON: whether the block is on, how many seconds of lease remain,
  whether the person latched it off with three Escapes, how many human key
  events have occurred this session, how long ago the last one was, the reasons
  the last few blocks ended, whether a hook is installed at all, and whether
  blocking is enabled.
- **Rules** Reports a **count and a time, never a key**. Installs nothing:
  asking before any input has been sent correctly answers that nothing is
  watching.

### `release_keyboard(enable_blocking=True)`
- **Output** confirmation, stating whether actions will hold the keyboard again.
- **Rules** Releases any current block and clears the triple-Escape latch, so
  normal operation can resume — this is the only thing that clears that latch.
  `enable_blocking=false` additionally switches blocking **off for the rest of
  the session**, after which no action takes the keyboard at all.

### The tracking outline
- **What it means** A green outline is drawn around the attached window
  **exactly while automation holds the desktop** — from the first input until
  `release_control()`, `detach_window()`, or the window closing. It is a
  statement about right now, not a bookmark: an attached window that is only
  being read is not outlined.
- **Rules** It is repainted only when the window's rectangle or the highlight
  boxes actually change. A window sitting still is not redrawn, no matter how
  long the session runs; a window that moves is followed. It is click-through,
  **can never become the foreground window**, and changes nothing about the
  target app.
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

### `run_steps(steps, delay_ms=120, stop_on_error=True, stop_if_user_types=True)`

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
- **Rules — the keyboard is held for the whole script** Not per step, so the
  person's typing cannot land between two of its clicks; the lease is renewed
  as the script proceeds and given back however the call ends, including on an
  exception. If the person presses a key anyway, the script stops at that step
  and the summary says why, unless `stop_if_user_types=false`.
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
  a run visibly takes over the desktop while acting, and hands it back when the
  action ends. Reading does not take it at all.
- **A machine-wide keyboard hook is installed once input is sent**, and it sees
  every key event on the computer, not only those going to the target window —
  that is what the mechanism is. It stores nothing and swallows only the
  person's keys, only while an action is running. It is removed when the
  process exits. The exposure is real and worth stating plainly: while this
  server is running and has sent input, a bug in it is a bug in the path every
  keystroke on the machine takes.
- **Journal frames are screenshots of whatever was on the window**, written
  unencrypted to `%TEMP%`. If the window shows something private, so do they,
  until the folder is pruned five sessions later.
- `.location_cache.json` next to the server is written and pruned by
  `remember_location`/`recall_location`.

## Verification

`tests\smoke.py` drives the real tools against a throwaway Notepad it opens and
closes, checking each behaviour above that can be checked without a human:
the journal, the stale-target refusal (both as a predicate against synthetic
frames and end to end through a real click, including a click in a part of the
window that was never looked at), the region scoping of "seen", `wait_stable`
against a window deliberately kept repainting, reading a window while another
is parked on top of it, and pointer/foreground restoration.

**It touches only windows it opened, and hands the desktop back as it found
it** — asserted at the end of the run, not assumed. It takes only a window that
appeared *after* it asked for one, rather than the first one matching the
process name, and it closes that window by undoing its own typing until the
document is unmodified and then sending Alt+F4, so there is no save prompt to
answer. It refuses in both directions: no key is sent at all if the window
cannot be focused, and the window is left open if it will not go back to
unmodified. Neither of those existed until the test had leaked **56 Notepad
windows**, one of which belonged to the person — and killing the process does
not undo it, because Windows 11 Notepad restores every window it had open the
next time it starts.

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
The same setup checks both halves of the foreground promise with **one switch
as the only difference** — by default the action ends with the other
application back in front, and with `keep_foreground(true)` it ends with the
driven window in front — so neither check can pass by accident.

`tests\diag_focus_return.py` covers the hand-back's refusals, which are the part
that matters and the part a live desktop cannot test reliably. It **creates its
own two windows** rather than driving a real app: they cannot be left behind,
and nothing else on the desktop can perturb the reading. An earlier version took
"the person's window" to be whatever was in the foreground and was measuring the
console window its own test runner had just opened. Alt+Space on a window of
one's own also opens a genuine Win32 menu, which no modern app is free to
reinterpret.

The keyboard block is verified in two halves, neither of which ever blocks the
real keyboard:

- `tests\test_input_guard.py` drives the decision logic with synthetic events
  and an injected clock — so "the lease expires after 20 seconds" is checked by
  handing it 20.01 seconds, not by waiting. It covers every release route and
  the cases where the block must *stay* on: three slow Escapes must not release
  it, an injected Escape must not release it (only the person can), and a key
  the person was already holding when the block began must be allowed to come
  back up so it does not stick down in the app.
- `tests\diag_keyboard_block.py` installs a real hook against a real Notepad
  and proves a character is genuinely swallowed, that ours still gets through
  the same block, that the lease expires with nobody calling release, and that
  three Escapes cut a 20-second lease short. It does this **without locking the
  keyboard** by relabelling which events count as the person's: a character it
  types itself is treated as human, and anything typed on the real keyboard
  falls into a class that always passes. Verifying a keyboard lock by locking
  the keyboard is the one experiment that can leave nobody able to type the fix.

`smoke.py` covers the wiring the other two cannot: that no hook exists until
the first input, that the keyboard is held **during** an action — sampled from
another thread, because "it was released afterwards" is equally true of a block
that was never taken — that it is released after, and that switching blocking
off leaves typing working.

`tests\diag_*.py` and `tests\spike_background*.py` are diagnostics, not tests:
they print measurements and assert nothing. They exist because each overturned
a wrong assumption — that an idle window drifts pixel to pixel, that a diff box
means everything in it changed, that background input might be possible, that
an overlay tracking a still window costs nothing to repaint, and that deferring
the foreground raise to the first action would make that action slower.
