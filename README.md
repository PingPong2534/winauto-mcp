# winauto-mcp

MCP server that lets an LLM see and drive any running Windows app: pick a
window, screenshot it, read its UI Automation element tree (buttons/menus/
inputs with coordinates), click, type, and press keys. A transparent green
outline tracks whichever window is currently attached; red boxes can be
drawn on demand to highlight specific elements.

Windows-only. Built and tested against Python 3.12 on Windows 11.

## Design

- **Coordinates**: every coordinate a tool accepts or returns (click x/y,
  element rects, highlight rects) is relative to the attached window's
  *client area* — the same pixel space as the image `capture_screen`
  returns. The server does all client-rect <-> screen-rect translation
  internally.
- **Hybrid inspection**: `capture_screen` returns both an image and a text
  summary built from the Windows UI Automation (UIA) tree. For native
  apps (browsers, Win32/WPF/UWP apps) the UIA summary is usually enough to
  act on by name. For canvas-rendered apps (games, custom-drawn UIs) the
  UIA tree comes back empty — the calling LLM should read the screenshot
  directly and pass pixel coordinates to `click`.
- **Single active target**: one attached window at a time (`attach_window`
  replaces whatever was attached before).
- **Input simulation**: uses the Win32 `SendInput` API directly (not
  `pyautogui`), with `KEYEVENTF_UNICODE` for typing — so arbitrary Unicode
  text (Thai included) types correctly without keycode mapping.
- **DPI awareness**: the server calls `SetProcessDpiAwarenessContext` (with
  fallbacks) at startup, before any other Win32/GDI call. Without this, an
  unaware process gets its coordinates silently scaled to 96 DPI while `mss`
  captures true physical pixels — on any scaled display (the common case)
  click coordinates read off a screenshot would land in the wrong place.
- **Foreground-lock workaround**: `attach_window` uses `AttachThreadInput` to
  temporarily join input queues with the current foreground window's thread
  before calling `SetForegroundWindow` — otherwise Windows silently ignores
  that call when it comes from a background/automated process (this server).
- **Act on the current screen, not a remembered one**: `click` and `drag`
  compare the area around their target against the frame the caller was
  actually *shown covering that spot*. If it changed, nothing is clicked — the
  call returns a refusal plus the window as it looks now, and re-issuing then
  goes through. This is the guard against the most common failure in a long
  run: deciding where to click, spending a few turns elsewhere, and clicking a
  coordinate the app has since moved on from. `force=true` skips it.
- **"Seen" is per region, not per window**: the server keeps the last few
  *views* the caller was shown — each with the rectangle it covered — rather
  than one whole-window frame. So looking at a 200x60 toolbar crop lets you
  click inside that toolbar and nothing else: a coordinate in a part of the
  window you have not looked at this run is refused as coming from memory.
  Without this, one partial capture would launder every stale coordinate on
  screen into "already checked".
- **Look at part of a window, not all of it**: `capture_region(x1,y1,x2,y2)`
  returns just that rectangle. Re-checking one field or one dialog costs a
  fraction of a full screenshot to send and to read — measured 4,244 bytes
  against 26,404 for the same moment's whole window.
- **A batch of steps in one call**: `run_steps([...])` performs a list of
  actions in order with a pause between them, so a familiar app doesn't cost a
  round trip per click. The whole script is validated before any of it runs,
  every step is journaled with its own before/after frames, and `check` steps
  stop the run when the screen didn't do what the script predicted. Only the
  *first* step gets the stale-coordinate guard — see Known limitations.
- **Waiting is never automatic**: `wait_stable()` polls until the window stops
  repainting, but only when asked. Nothing sleeps behind the caller's back, so
  timing stays a visible decision rather than a hidden constant.
- **Rolling journal**: every tool call is appended to a throwaway session
  folder under `%TEMP%\winauto-mcp\` with downscaled before/after JPEGs, kept
  for the last 5 sessions. `history()` lists the steps (failures included) and
  `replay_frame(seq)` returns the screen as it actually was at that step, so
  "what happened before this?" is answerable from evidence rather than recall.
- **Reads work while the window is covered**: capture goes through
  `PrintWindow(PW_RENDERFULLCONTENT)` first, which asks the window to render
  itself, and falls back to scraping the screen only if that returns nothing
  usable. Verified against Blender 5.2 (OpenGL), the Godot 4.6 editor and
  Windows 11 Notepad while each was fully covered by another window. Only
  *input* still needs the window in front.
- **The pointer is shared**: `click`/`drag`/`scroll` put the mouse back where
  the person left it (`keep_cursor=true` opts out, for modal tools that keep
  following the pointer), and `release_control()` hands the foreground back to
  the window they were using.
- **Attaching is not taking over**: `attach_window` only chooses which window
  the other tools mean. It does not raise the window and draws no outline —
  the window is raised, and the green outline appears, by itself at the first
  input, because that is the first moment input actually needs it.
  `release_control()` puts both back. This costs nothing: every input path
  already raised the window itself, so the raise was being paid twice.
  Measured — attach 6.0 ms lazy vs 26.7 ms eager, and the first click pays
  11.9 ms more than a later one, so the change is **~9 ms faster overall** and
  a read-only run never pays it at all (`tests\diag_attach_cost.py`).
- **The outline only repaints when something moved**: it used to redraw a
  transparent topmost window the size of the target on every 150 ms poll, for
  the whole session — enough compositor churn to make the tracked app stutter
  and the outline flicker, which reads as the automation having hung. Now the
  poll compares against what is already drawn and touches Tk only on a real
  change: measured 1 repaint over 3 idle seconds, against ~20 before, while a
  window that does move still updates (`tests\diag_overlay_paint.py`).

## Tools

| Tool | Purpose |
|---|---|
| `list_windows` | List visible top-level windows (title, process, hwnd) |
| `attach_window(hwnd, take_control=False)` | Choose which window the other tools act on. Does **not** raise it and draws no outline — reading works from behind, and the first input raises it by itself. `take_control=true` raises it immediately |
| `detach_window()` | Detach and hide the overlay |
| `capture_screen()` | Screenshot + UIA text summary of the attached window |
| `screenshot()` | Screenshot only, no UIA tree walk — fast, for re-checking the screen |
| `capture_region(x1, y1, x2, y2)` | Screenshot of **one part** of the window, plus a header giving the region and the offset to add back to get client coordinates. Much cheaper than a full frame for a spot check. Counts as having looked at *that region only* |
| `get_elements()` | UIA element list only, as JSON (no screenshot) |
| `click(x, y, button, double, modifiers, force, keep_cursor)` | Click at client-relative coordinates; `modifiers` (e.g. `["ctrl"]`) are held down for the click -- for ctrl/shift-click multi-selection. Refuses and returns the current screen if the target area changed since the caller last looked (`force=true` overrides); the pointer is restored afterwards unless `keep_cursor=true` |
| `click_element(name, button, double)` | Click a UIA element by visible name (exact, else substring match) |
| `wait_for(name, timeout, interval)` | Poll until an element matching `name` appears, or time out |
| `type_text(text)` | Type Unicode text into the focused control |
| `press_key(key)` | Press a named special key (enter, tab, escape, arrows, f1-f12, ...) |
| `hotkey(keys)` | Press a chord together, e.g. `["ctrl", "shift", "p"]` for Ctrl+Shift+P |
| `scroll(x, y, clicks, keep_cursor)` | Mouse-wheel scroll at client-relative coordinates (positive = up, negative = down) |
| `drag(x1, y1, x2, y2, button, force, keep_cursor)` | Drag from one point to another -- moves through intermediate points, not a teleport, since many apps only recognize a drag if the mouse visibly moves while held. Same stale-target refusal as `click` |
| `run_steps(steps, delay_ms, stop_on_error)` | Run up to 40 actions in one call, in order, with `delay_ms` between them: `click`, `drag`, `scroll`, `type`, `key`, `hotkey`, `click_element`, `wait`, `wait_stable`, `capture` (returns a crop mid-run), `check` (stops the run if a region didn't change / did change as predicted). The whole script is validated before any step runs, and each step is journaled with its own before/after frames. **Only step 1 is guarded against a stale coordinate** |
| `wait_stable(timeout, settle_ms, interval, threshold, region)` | Poll until the window (or `region` of it) stops repainting for `settle_ms`. Never called automatically -- reports timing, not pixels, so take a fresh screenshot after |
| `history(last, tool_name, failures_only)` | The steps taken so far this session, from the journal, with their arguments, results and which frames were kept |
| `replay_frame(seq, which)` | The before/after screen image stored for step `seq` -- evidence for "what did it look like then?", downscaled, never a coordinate source |
| `release_control()` | Put the window the person was using back in front and hide the tracking outline. Reading the attached window keeps working from behind; the next action takes it again by itself |
| `locate_in_region(x1, y1, x2, y2, threshold)` | Find exact click coordinates by pixel contrast within a small region -- returns the tight content bbox and its center. Use instead of eyeballing coordinates off a displayed screenshot crop, which has repeatedly been wrong by 50-150+ px (displayed crops can be rescaled in ways that don't map back to real source pixels) |
| `snapshot()` | Store the current screenshot as a reference point |
| `diff_since_snapshot(threshold, region)` | Compare the current screen to the last `snapshot()`, return the bounding box of changed pixels or "no change detected" -- objective confirmation an action had a visible effect, instead of eyeballing two screenshots side by side. Pass `region` to narrow a sprawling box down (see Known limitations) |
| `remember_location(label, x1, y1, x2, y2)` | Save a click target under a semantic label, scoped to the attached process's name + client size |
| `recall_location(label, margin=15, threshold)` | Look up a saved label, but only after re-scanning the live screen (`find_content_bbox` on the cached area expanded by `margin`) and confirming it still matches -- returns `cache_hit: false` and drops the stale entry if the UI moved or that area is now empty. Use a smaller `margin` (5-10px) for labels packed into a dense menu/toolbar row -- see Known limitations |
| `highlight(rects)` | Draw debug boxes on the overlay (visual only) |

## Setup

```powershell
cd F:\tools\winauto-mcp
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## Register with an MCP client

Claude Desktop / Claude Code `mcpServers` config:

```json
{
  "mcpServers": {
    "winauto": {
      "command": "F:\\tools\\winauto-mcp\\.venv\\Scripts\\python.exe",
      "args": ["F:\\tools\\winauto-mcp\\server.py"]
    }
  }
}
```

Claude Code CLI:

```powershell
claude mcp add winauto -- F:\tools\winauto-mcp\.venv\Scripts\python.exe F:\tools\winauto-mcp\server.py
```

## Known limitations (v0.1)

- One attached window at a time.
- `SendInput` is userspace input — most apps and browsers receive it fine,
  but some DirectInput/XInput-hooked fullscreen games may ignore it.
- `SetForegroundWindow`'s foreground-lock rejection is mitigated (see
  `AttachThreadInput` note above) but not guaranteed in every edge case;
  `attach_window`/`click`/`type_text` don't hard-fail if it's still refused.
- UIA tree walk is capped (depth 15 / 3000 nodes / 150 results) to stay
  fast on deep trees (e.g. Chrome with heavy pages) — very deeply nested
  elements past the cap won't show up in the text summary.
- `click_element`/`wait_for` match by UIA `Name` (exact, then substring,
  case-insensitive); if multiple elements share a name the first one found
  in tree order is used — check `get_elements` if you need to disambiguate.
- Some apps with a custom-drawn title bar (e.g. Windows 11's modern Notepad)
  report menu items slightly outside `GetClientRect`, so their rect's y can
  be negative — a UIA quirk of that app, not a bug in this server.
- `diff_since_snapshot` can false-positive on apps that redraw part of their
  own UI continuously with no user input (e.g. a game/tool-script gizmo,
  blinking cursor, live counter) — confirmed against Godot's editor, whose
  2D viewport keeps redrawing a debug hint label for the currently selected
  node. A nonzero `changed_bbox` means "something changed," not necessarily
  "your action caused it" — sanity-check that the bbox's location/size fits
  the expected effect.
- The location cache (`remember_location`/`recall_location`) is stored in
  `.location_cache.json` next to the server, keyed by process name + client
  size + label. It is never trusted blind: every `recall_location` call
  re-scans the live screen and compares against the cached bbox before
  returning a coordinate, so a stale entry (app updated, window resized,
  target scrolled out of view) is detected and dropped rather than causing a
  misclick.
- **A change bounding box is one box around *every* changed pixel**, so two
  small changes far apart produce a box covering all the untouched space
  between them, and **its centre is very often a pixel that never changed**.
  Typing one word into Windows 11 Notepad does this: the text moves at the
  top-left, the tab gains a modified marker, and the status bar's Ln/Col
  readout updates at the bottom-right — measured as three clusters totalling
  ~4,600 changed pixels inside a box of ~1.6 million. A large box means "the
  change is spread out", never "everything changed". Pass `region` to
  `diff_since_snapshot` and re-run over parts of the window to find where the
  change actually is. Applies equally to `wait_stable`'s `last_change_bbox`.
- Background *input* is not possible for the apps this server targets. Posted
  window messages (`WM_MOUSEMOVE`/`WM_LBUTTONDOWN`/`WM_CHAR`, sent and posted,
  with and without a spoofed `WM_ACTIVATE`) were measured against Blender 5.2,
  the Godot 4.6 editor and Windows 11 Notepad: **none moved a single pixel.**
  Only Godot reacted at all, and only by brightening its title bar. So input
  still raises the window and borrows the real pointer — mitigated by
  restoring the cursor and by `release_control()`, not eliminated. True
  side-by-side use would need a separate Windows session or VM.
  (`tests\spike_background*.py` reproduce these measurements.)
- **`run_steps` can only guard its first step.** Steps 2..n act on a screen the
  script itself changed, which the caller has never been shown, so there is
  nothing to compare their coordinates against — they are a *prediction* of
  where the app will put things, which is exactly the assumption the
  stale-target guard otherwise refuses to make. This is a real loss of safety
  traded for the round trips, not an oversight: script only sequences already
  watched working, keep them short (the 40-step and 60s-of-waiting caps exist
  to make "short" the path of least resistance), and put a
  `{"do":"check","region":[...],"expect":"changed"}` after any step the rest
  depends on so a wrong prediction stops the run. Every step is journaled with
  before/after frames, so a script that goes wrong is reconstructable after the
  fact with `history()`/`replay_frame()` — that is the recovery path, not
  prevention.
- **Looking at a region only counts for that region.** After
  `capture_region(toolbar)`, clicking in the toolbar goes through and clicking
  anywhere else in the window is refused with the list of rectangles actually
  looked at. Only the last 8 views are kept, and a new view supersedes any
  older one it fully contains — so a long run of small crops eventually forgets
  the earliest ones and a click there will ask to be re-checked.
- `recall_location`'s re-verify margin can itself cause a false "stale"
  report if set too large for the target's surroundings: expanding into a
  neighboring label/icon merges them into one bigger bbox, which fails the
  size-ratio check even though the actual target never moved — confirmed
  against Blender's tightly packed viewport header (`View Select Add
  Object`, items only ~15-20px apart). Use a small `margin` (5-10px) for
  dense menu/toolbar rows; the default (15) assumes moderate spacing.
