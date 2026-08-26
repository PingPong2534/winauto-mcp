# HOWTOUSE — driving an app with winauto-mcp

All 30 tools, what goes in, what comes out, and how to get the most out of
them.

Installation is in [SETUP.md](SETUP.md) · the guaranteed behaviour, stated
without reference to the code, is in [docs/SPEC.md](docs/SPEC.md).

## Contents

- [The shape of a run](#the-shape-of-a-run)
- [Three rules that explain most surprises](#three-rules-that-explain-most-surprises)
- [Getting coordinates right](#getting-coordinates-right)
- [Tool reference](#tool-reference)
  - [Choosing a window](#choosing-a-window)
  - [Looking](#looking)
  - [Acting](#acting)
  - [Waiting](#waiting)
  - [Batching](#batching)
  - [Sharing the machine with the person at it](#sharing-the-machine-with-the-person-at-it)
  - [Proving something happened](#proving-something-happened)
  - [Remembering a target](#remembering-a-target)
  - [The journal](#the-journal)
- [Worked patterns](#worked-patterns)
- [Anti-patterns](#anti-patterns)

---

## The shape of a run

```
list_windows()          →  pick an hwnd
attach_window(hwnd)     →  chooses the target; costs the person nothing
capture_screen()        →  look: image + element list
locate_in_region(...)   →  aim: exact coordinates, measured not guessed
click(x, y)             →  act: raises the window, blocks the keyboard, clicks
wait_stable()           →  let the app finish drawing
screenshot()            →  look again before the next aim
release_control()       →  hand the desktop back before you stop to think
```

The loop that matters is **look → aim → act → look again**. Every failure mode
this server guards against comes from skipping one of those.

`attach_window` does *not* take the machine over. The window stays where it
was, no outline is drawn, and reading works from behind other windows. The
first call that actually sends input is the moment the window is raised, the
green outline appears, and the person's keyboard is held out — and
`release_control()` undoes all of it.

---

## Three rules that explain most surprises

### 1. Coordinates are client-relative

Every `x, y` you pass and every `rect` you get back is relative to the
**attached window's client area** — the same pixel space as the
`capture_screen` / `screenshot` image. Not screen coordinates, and not
including the title bar or borders.

The one exception: a `capture_region` crop's own `(0, 0)` is `(x1, y1)` of the
window. The crop's header repeats that offset. Add it back before using
anything you read there.

### 2. Clicks are refused when you have not looked

`click`, `drag` and `hover` compare the area around the target against the
frame you last looked at. If the app finished loading, a dialog opened, or the
list scrolled, **nothing is clicked** — you get a report saying so plus the
window as it looks now, and the call is yours to re-issue.

Which calls count as "having looked":

| Counts as looked at | Scope |
|---|---|
| `capture_screen()`, `screenshot()` | the whole window |
| `capture_region(x1,y1,x2,y2)` | **that rect only** |
| `locate_in_region(...)` | **that rect only** |
| `recall_location(...)` | the cached bbox plus its margin |

And what does **not** count, deliberately:

| Does not count | Why |
|---|---|
| `wait_stable()` | it reports timing, not pixels |
| `hover()` | it photographs a transient that is gone by the time you could click it |
| `replay_frame()` | it is downscaled evidence of the past, in no usable pixel space |
| `get_elements()` | element rects are live UIA, not a picture — but clicking them via `click_element` needs no frame |

`force=true` skips the check. Use it only for a target sitting in an area the
app repaints on its own (a clock, a live viewport), where the check will never
be satisfiable.

### 3. The machine belongs to the person at it

The foreground is handed back after every action, the pointer is put back where
they left it, and their keyboard is held out only while an action runs — with a
20-second lease and **triple-Escape** as an unconditional escape hatch. If
`keyboard_status()` reports `latched_off_by_user`, someone reached for that
chord. Treat it as a stop signal, not an obstacle: stop, report, ask.

---

## Getting coordinates right

**Never read a click coordinate off a displayed screenshot by eye.** Crops shown
in a chat transcript get rescaled for display in ways that do not map back to
real source pixels; this has caused repeated 50–150+ px misses.

In order of preference:

1. **`click_element("Save")`** — no coordinates at all. Works whenever the app
   exposes a UIA tree.
2. **`locate_in_region(x1, y1, x2, y2)`** — a tight region around **one** thing,
   returns a measured `bbox` and `center`. Click the `center`.
3. **`recall_location("save_button")`** — a previously saved target, re-verified
   against the live screen before it is returned.
4. Reading the image directly — only for canvas apps (games, Blender, Godot)
   where there is no UIA tree, and even then, confirm with `locate_in_region`
   before clicking.

`locate_in_region` works by pixel contrast against the region's dominant
background colour. Give it a region that bounds one button, one menu label, one
list row — not a whole panel. It refuses regions larger than 500,000 px. Raise
`threshold` if faint content is missed, lower it if the box comes back too big.

---

## Tool reference

Every tool below except `list_windows`, `keep_foreground`, `keyboard_status`,
`release_keyboard`, `history` and `replay_frame` requires an attached window and
raises if there is none.

### Choosing a window

#### `list_windows()`

| | |
|---|---|
| **Input** | none |
| **Output** | JSON array of `{hwnd, title, process}` for every visible top-level window |
| **Use it** | first, always — `hwnd` values are not stable across app restarts |

#### `attach_window(hwnd, take_control=False)`

| | |
|---|---|
| **Input** | `hwnd` (int, from `list_windows`); `take_control` (bool) |
| **Output** | a line naming the window and the new journal session |
| **Rules** | raises `no such window: <hwnd>` if it has closed |
| **Notes** | Starts a fresh journal, so `history()` describes this run only. `take_control=true` raises the window immediately — worth it when a person is watching and should see which window is about to be driven, pointless otherwise |

#### `detach_window()`

| | |
|---|---|
| **Input** | none |
| **Output** | `detached` |
| **Notes** | Hides the overlay and releases the person's keyboard, so walking away mid-run cannot leave it held |

---

### Looking

#### `capture_screen()`

| | |
|---|---|
| **Input** | none |
| **Output** | a text summary of interactive elements + `elements_json` + a PNG of the client area |
| **Use it** | the first look at an unfamiliar window |
| **Notes** | On canvas-drawn apps the element summary is empty — read the image and use pixel coordinates. Marks the **whole window** as looked at |

#### `screenshot()`

| | |
|---|---|
| **Input** | none |
| **Output** | a PNG of the client area |
| **Use it** | every subsequent look, when you do not need the element list |
| **Notes** | Much faster than `capture_screen` — no UIA tree walk. Marks the whole window as looked at |

#### `capture_region(x1, y1, x2, y2)`

| | |
|---|---|
| **Input** | a client-relative rect |
| **Output** | a JSON header (`region`, `crop_size`, `coordinate_offset`) + a PNG of just that crop |
| **Rules** | raises if the rect is empty or entirely outside the client area; it is clamped to the window otherwise |
| **Use it** | to check one thing — did the dialog appear, did that field update, is the button enabled now |
| **Notes** | Three small crops cost less than one full screenshot, to send and to read. Marks **that rect only** as looked at: checking the toolbar does not refresh a stale memory of the sidebar |

#### `get_elements()`

| | |
|---|---|
| **Input** | none |
| **Output** | JSON `[{name, control_type, category, enabled, rect: [x1,y1,x2,y2]}, ...]`, rects client-relative |
| **Use it** | to find the exact name string for `click_element` or `wait_for` |
| **Notes** | No screenshot, no image cost |

#### `hover(x, y, dwell_ms=700, force=False)`

| | |
|---|---|
| **Input** | client-relative point; `dwell_ms` (clamped 0–5000); `force` to skip the staleness check |
| **Output** | a JSON report + a PNG. The report has `hovered`, `dwell_ms`, `pointer_held`, and `appeared`: for each window that popped up, its `class`, `rect` in client coordinates, `text` read via UIA, and `in_the_image` |
| **Use it** | tooltips, hover highlights, menus that open on hover |
| **Notes** | See below — this is the one tool that holds the mouse |

`hover` is worth understanding in detail:

- **The image is a screen grab, not a window render.** Measured: `PrintWindow`
  does not contain tooltips, because a tooltip is its own top-level window. So
  the attached window must be in front and unobscured — which it is, since
  hovering brings it forward.
- **Read `text`, not the picture.** A popup that opened outside the client area
  is described in the report but cannot be in the image. `in_the_image` tells
  you which case you are in.
- **The pointer is genuinely pinned** for the length of the dwell, and the
  person's mouse held out — a hand on the mouse during that half-second moves
  the pointer off the target and the picture is of nothing. It refuses to take
  the mouse at all if a button is physically down (that is a drag in progress)
  or after the escape chord has been used. In those cases the hover still
  happens and `pointer_held` is `false` — judge the image accordingly.
- **It marks nothing as seen.** To click something you found this way, call
  `screenshot()` first and aim from that.
- If `appeared` is empty you get `note_no_popups`: the app may draw its hover
  state inside its own window (which the image does show), or need a longer
  `dwell_ms`, or there is nothing there.

---

### Acting

#### `click(x, y, button='left', double=False, modifiers=None, force=False, keep_cursor=False)`

| | |
|---|---|
| **Input** | client-relative point; `button` `'left'`/`'right'`; `double`; `modifiers` e.g. `["ctrl"]`, `["shift"]`; `force`; `keep_cursor` |
| **Output** | `clicked (x, y) button=... double=...`, or — if the area changed since you looked — a refusal report plus the window as it looks now |
| **Rules** | `button` must be `'left'` or `'right'` |
| **Notes** | `modifiers` are held for the click — use for ctrl/shift multi-select in a tree or list. The pointer goes back where the person left it unless `keep_cursor=true`, which you need when the next step follows the pointer: a Blender modal transform started with G/R/S, or a hover-driven menu |

#### `click_element(name, button='left', double=False)`

| | |
|---|---|
| **Input** | the element's visible name/label |
| **Output** | `clicked element "<name>" at (x, y)`, with `(ambiguous: N elements matched, clicked the first)` when more than one matched |
| **Rules** | raises if nothing matches — check `get_elements()` for exact names |
| **Notes** | Exact match first, then substring. **Prefer this over coordinates** wherever the app has a UIA tree |

#### `type_text(text)`

| | |
|---|---|
| **Input** | literal Unicode text, any language |
| **Output** | `typed N characters` |
| **Notes** | Goes to whatever control currently has focus. **Click into the field first** — this does not choose a target |

#### `press_key(key)`

| | |
|---|---|
| **Input** | one of: `enter`, `tab`, `escape`, `backspace`, `delete`, `up`, `down`, `left`, `right`, `home`, `end`, `pageup`, `pagedown`, `space`, `ctrl`, `alt`, `shift`, `f1`–`f12` |
| **Output** | `pressed <key>` |

#### `hotkey(keys)`

| | |
|---|---|
| **Input** | a list, e.g. `["ctrl", "shift", "p"]`. Each entry is a `press_key` name or a single a–z/0–9 character |
| **Output** | `pressed chord ctrl+shift+p` |
| **Notes** | Holds each key in order, releases in reverse |

#### `drag(x1, y1, x2, y2, button='left', force=False, keep_cursor=False)`

| | |
|---|---|
| **Input** | two client-relative points |
| **Output** | `dragged (x1, y1) -> (x2, y2) button=...`, or a staleness refusal |
| **Use it** | reordering, drag-into-viewport, dragging a file from a dock onto a node |
| **Notes** | Moves through intermediate points rather than teleporting — many apps only recognize a drag if the mouse visibly moves while held. The staleness check is stricter in spirit here: grabbing the wrong thing is worse than clicking it, because it also drops it somewhere |

#### `scroll(x, y, clicks, keep_cursor=False)`

| | |
|---|---|
| **Input** | client-relative point; `clicks` — positive scrolls up, negative down, one click = one wheel notch |
| **Output** | `scrolled N click(s) at (x, y)` |
| **Use it** | reaching content below the fold in a scrollable panel |

#### `highlight(rects)`

| | |
|---|---|
| **Input** | a list of `[x1,y1,x2,y2]` client-relative rects; `[]` clears |
| **Output** | `highlighted N rect(s)` |
| **Notes** | Draws red debug boxes on the overlay, purely for the human watching. No effect on click/type. Asking for a box shows the overlay even if no input has been sent yet |

---

### Waiting

An action returns as soon as the input has been *sent* — well before a menu has
opened or a document has rendered. Never guess a fixed sleep; use one of these.

#### `wait_for(name, timeout=10.0, interval=0.5)`

| | |
|---|---|
| **Input** | element name (exact, else substring, case-insensitive); seconds |
| **Output** | `found "<name>" at rect [...]` |
| **Rules** | raises `TimeoutError` when the deadline passes |
| **Use it** | after an action that triggers a slow UI update — a dialog, a page load |
| **Notes** | Needs a UIA tree, so it is useless on canvas apps |

#### `wait_stable(timeout=5.0, settle_ms=400, interval=0.12, threshold=10, region=None)`

| | |
|---|---|
| **Input** | seconds and pixel thresholds; optional `region` `[x1,y1,x2,y2]` |
| **Output** | JSON with `stable`, `waited_ms`, `polls`, `changed_during_wait`; on failure `last_change_bbox` and a `hint` |
| **Rules** | raises if `timeout <= 0` or `region` is malformed |
| **Use it** | canvas-drawn apps with no element tree, and any slow redraw |
| **Notes** | Settles when `settle_ms` passes with nothing changing by more than `threshold`. An app that repaints forever — a blinking caret, a live viewport, a clock — never settles window-wide: take `last_change_bbox` from the failed attempt and pass a `region` that excludes it. **Does not count as looking** — take a fresh `screenshot()` afterwards |

---

### Batching

#### `run_steps(steps, delay_ms=120, stop_on_error=True, stop_if_user_types=True)`

| | |
|---|---|
| **Input** | a list of step objects (max 40); `delay_ms` between steps (clamped 0–3000) |
| **Output** | a per-step report, any images requested by `capture` steps, and — if it stopped early — the window as it looks now |
| **Use it** | once you know an app well enough to predict the next few steps: open a menu and pick an item, fill three fields and press Enter |

The step verbs:

```json
{"do":"click","x":100,"y":200,"button":"left","double":false,"modifiers":["ctrl"],"keep_cursor":false}
{"do":"drag","x1":10,"y1":10,"x2":90,"y2":90,"button":"left"}
{"do":"scroll","x":400,"y":300,"clicks":-3}
{"do":"type","text":"hello"}
{"do":"key","key":"enter"}
{"do":"hotkey","keys":["ctrl","s"]}
{"do":"click_element","name":"Save"}
{"do":"wait","ms":500}
{"do":"wait_stable","timeout":5.0,"settle_ms":400,"region":[0,0,800,600]}
{"do":"capture","region":[0,0,800,600]}
{"do":"check","region":[0,0,800,600],"expect":"changed"}
```

`region` on `capture` is optional — omit it for the whole window. `expect` on
`check` is `"changed"` or `"unchanged"`.

**🔴 The staleness check only guards the first step.** It cannot guard the rest:
step 2 acts on a screen that step 1 deliberately changed, and there is no frame
you have seen of it. The coordinates in steps 2..n are your *prediction* of what
the app will do — exactly the assumption this server otherwise refuses to make
on your behalf. So:

- script only what you have watched the app do before,
- keep scripts short,
- and put a **`check` step after any step the rest of the script depends on** —
  a menu that must have opened, a dialog that must have closed. A `check`
  compares its region against the previous checkpoint (script start, or the last
  `capture`/`check`) and stops the run when the expectation does not hold.

Keep `capture` regions small — that is the entire saving over doing it step by
step.

The person's keyboard is held for the **whole script** rather than per step, so
their typing cannot land between two of its clicks. If they press a key anyway
the script stops there: someone reaching for the keyboard mid-run wants the
machine more than the script does. Set `stop_if_user_types=false` only for a
script that genuinely must not be interrupted.

Every step is journaled with its own before/after frame, so a script that goes
wrong is still reconstructable with `history()` / `replay_frame()`.

---

### Sharing the machine with the person at it

#### `release_control()`

| | |
|---|---|
| **Input** | none |
| **Output** | `foreground returned to "<title>"; outline hidden; keyboard released; ...` or `nothing to give back` |
| **Use it** | when you have finished a piece of work and are about to think, report or wait |
| **Notes** | 🔴 **Not between two steps of one interaction** — a menu or dropdown you just opened closes the moment its window loses focus. Nothing else is lost: the window stays attached, reading still works from behind, and the next action raises it again by itself |

#### `keep_foreground(enabled)`

| | |
|---|---|
| **Input** | `true` to hold the foreground, `false` to resume handing it back |
| **Output** | a line describing which mode is now in effect |
| **Use it** | before an interaction spanning several tool calls that would break if focus moved: a Blender G/R/S transform, a rubber-band selection continued in a later call, or **a menu the app draws itself** — Windows does not report those, so the automatic hand-back cannot know to skip them |
| **Notes** | Menus Windows *does* own are already handled. Always pair it with `keep_foreground(false)` |

#### `keyboard_status()`

| | |
|---|---|
| **Input** | none |
| **Output** | JSON: whether the block is on, how long the lease has left, a count of human key events, `latched_off_by_user`, `watching`, `blocking_enabled` |
| **Notes** | A count and a time, **never which keys**. Nothing that could reconstruct what anyone typed is recorded anywhere, by design |

#### `release_keyboard(enable_blocking=True)`

| | |
|---|---|
| **Input** | `enable_blocking=false` to leave blocking off for the rest of the session |
| **Output** | a line confirming the release and whether actions will hold it again |
| **Use it** | the companion to `release_control()` — when you are done driving and about to think, report or wait |
| **Notes** | Also clears the triple-Escape latch. Only clear it when you know why it was set |

---

### Proving something happened

#### `snapshot()`

| | |
|---|---|
| **Input** | none |
| **Output** | `snapshot stored` |
| **Use it** | immediately before an action whose visual effect you want to verify |

#### `diff_since_snapshot(threshold=10, region=None)`

| | |
|---|---|
| **Input** | `threshold` 0–255 (minimum grayscale diff counted as a real change); optional `region` |
| **Output** | the bounding box of changed pixels, or `no change detected` |
| **Rules** | raises if no `snapshot()` has been taken |
| **Use it** | instead of eyeballing two screenshots side by side. An action that silently had no effect — wrong focus, missed coordinate, click on the wrong widget — leaves a pixel-identical screen, which is easy to miss by eye and unambiguous here |

🔴 **Read the box as "the change is somewhere in here", not "all of this
changed".** It is one box around *every* changed pixel, so a few small edits in
different corners produce a box covering the whole window — typing one word into
an editor already does this, because the text, the title's modified marker and
the status bar all change at once. **Do not click the box's centre**; it is very
often a pixel that never changed. To find where the change actually is, pass
`region` and re-run over parts of the window until the box comes back tight.

---

### Remembering a target

#### `remember_location(label, x1, y1, x2, y2)`

| | |
|---|---|
| **Input** | a short semantic label (`"save_button"`, `"file_menu"`) and a bbox |
| **Output** | `remembered "<label>" for <process> (WxH) as [...]` |
| **Use it** | right after `locate_in_region` finds something you expect to click again in a later session |
| **Notes** | Scoped to the process name **and** client size. Persisted to `.location_cache.json` next to `server.py`. This alone does not make future clicks skip verification |

#### `recall_location(label, margin=15, threshold=30)`

| | |
|---|---|
| **Input** | the label; `margin` px of slack around the cached bbox; contrast `threshold` |
| **Output** | JSON `{cache_hit: true, bbox, center}` — **use the freshly re-scanned `center`, not the stale cached one** — or `{cache_hit: false, reason}` |
| **Notes** | A cached coordinate is never returned blindly: the cached bbox expanded by `margin` is re-scanned against the *current* screen, and the result must match within ~1.5× margin in position and 2× in size. On a miss the stale entry is dropped — fall back to `locate_in_region` over a wider area, then `remember_location` again |

Tune `margin` to how tightly packed the neighbours are. A small isolated button
tolerates a larger margin. A label inside a dense toolbar row — items 10–30 px
apart, e.g. Blender's `View Select Add Object` header — needs a **small** margin
(5–10 px): a margin that reaches into a neighbouring label merges the two into
one bigger bbox and falsely reports the target as moved.

---

### The journal

#### `history(last=20, tool_name=None, failures_only=False)`

| | |
|---|---|
| **Input** | `last` caps how many recent steps come back; `tool_name` filters by substring (e.g. `"click"`); `failures_only` keeps only steps that raised |
| **Output** | JSON with the session info and the records, oldest first: each tool call since `attach_window` with its arguments, outcome, duration, and whether before/after frames were kept |
| **Use it** | when the current screen does not match what you expected — it answers "what did I already click, and did it work?" from the record rather than from recollection, which is where these runs usually go wrong |
| **Notes** | `ok=false` steps are attempts that raised; `result` holds the error |

#### `replay_frame(seq, which='after')`

| | |
|---|---|
| **Input** | `seq` from `history()`; `which` is `"before"` or `"after"` |
| **Output** | a JSON header (including `scale`) plus a downscaled JPEG |
| **Rules** | raises if `which` is neither value, if that `seq` is not in memory (the message names the range that is), or if that step kept no frame — only tools that drive or capture the screen do |
| **Use it** | to settle a question about the past instead of inferring it: was the dialog already open when you clicked? did the click land on the control you thought? |

🔴 **Evidence only — never a coordinate source.** The image is downscaled and is
not in the client-area pixel space. To act on what you find, take a fresh
`screenshot()` or `locate_in_region()` first.

---

## Worked patterns

### Click a button on a normal Win32 app

```
attach_window(hwnd)
capture_screen()               # see the element list
click_element("Save")          # no coordinates involved at all
wait_stable()
screenshot()                   # confirm
```

### Click something in a canvas app (game, Blender, Godot)

```
attach_window(hwnd)
screenshot()                                  # UIA is empty here; read the image
locate_in_region(320, 40, 420, 70)            # tight box around ONE label
  → {"bbox": [...], "center": [366, 54]}
click(366, 54)                                # click the measured centre
wait_stable(region=[300, 30, 700, 300])       # exclude the animating viewport
screenshot()
```

### Prove an action actually did something

```
snapshot()
click(366, 54)
wait_stable()
diff_since_snapshot(region=[300, 30, 700, 300])
  → tight bbox = it worked; "no change detected" = it did not
```

### Fill a form in one call

```
run_steps([
  {"do": "click_element", "name": "Name"},
  {"do": "type", "text": "Kittipong"},
  {"do": "key", "key": "tab"},
  {"do": "type", "text": "news4ping@gmail.com"},
  {"do": "check", "region": [200, 100, 600, 260], "expect": "changed"},
  {"do": "click_element", "name": "Submit"},
  {"do": "wait_stable"},
  {"do": "capture", "region": [200, 100, 600, 300]}
])
```

### Read a tooltip

```
screenshot()                        # aim from a settled frame
hover(366, 54, dwell_ms=900)
  → appeared[0].text is the exact string the app set — assert on that,
    not on what the picture looks like
screenshot()                        # required before clicking anything you found
```

### Long interaction that must not lose focus

```
keep_foreground(true)
click(400, 300, keep_cursor=true)   # pointer stays on the target
press_key("g")                      # Blender modal transform follows the pointer
drag(400, 300, 520, 300)
press_key("enter")
keep_foreground(false)
release_control()
```

### Pause to think, without giving up your place

```
release_control()      # person gets their window and keyboard back
screenshot()           # still works — reading does not need the foreground
...think...
click(366, 54)         # raises the window again by itself
```

---

## Anti-patterns

| Don't | Do |
|---|---|
| Read click coordinates off a screenshot crop by eye | `locate_in_region` on a tight rect, or `click_element` |
| `press_key("enter")` and assume the dialog closed | `wait_stable()` then `screenshot()` — or a `check` step |
| Click the centre of a `diff_since_snapshot` box | Narrow it with `region` until the box is tight |
| Click a coordinate found in a `hover` image | `screenshot()` first, aim from that |
| Measure anything off `replay_frame` | It is downscaled evidence; take a fresh frame |
| `force=true` because a click was refused | Look again — the refusal means the screen moved. Use `force` only for areas the app repaints on its own |
| `release_control()` between opening a menu and picking from it | Only between whole pieces of work; use `keep_foreground(true)` for interactions that span calls |
| A 30-step `run_steps` script for an app you have not driven before | Short scripts of steps you have watched work, each guarded by a `check` |
| Clear the triple-Escape latch and carry on | Someone demanded the machine back. Stop and ask |
| `capture_screen()` on every loop iteration | `screenshot()` when you do not need elements; `capture_region` when you only need to check one thing |

---

Last updated: 2026-08-26 · 30 tools · Tested on Windows 11 Home Single Language 10.0.26200.0 · Python 3.12.10 · mcp 2.0.0
