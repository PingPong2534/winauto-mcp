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
  compare the area around their target against the last frame the caller was
  actually *shown*. If it changed, nothing is clicked — the call returns a
  refusal plus the window as it looks now, and re-issuing then goes through.
  This is the guard against the most common failure in a long run: deciding
  where to click, spending a few turns elsewhere, and clicking a coordinate
  the app has since moved on from. `force=true` skips it.
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

## Tools

| Tool | Purpose |
|---|---|
| `list_windows` | List visible top-level windows (title, process, hwnd) |
| `attach_window(hwnd)` | Attach to a window, foreground it, show tracking outline |
| `detach_window()` | Detach and hide the overlay |
| `capture_screen()` | Screenshot + UIA text summary of the attached window |
| `screenshot()` | Screenshot only, no UIA tree walk — fast, for re-checking the screen |
| `get_elements()` | UIA element list only, as JSON (no screenshot) |
| `click(x, y, button, double, modifiers, force, keep_cursor)` | Click at client-relative coordinates; `modifiers` (e.g. `["ctrl"]`) are held down for the click -- for ctrl/shift-click multi-selection. Refuses and returns the current screen if the target area changed since the caller last looked (`force=true` overrides); the pointer is restored afterwards unless `keep_cursor=true` |
| `click_element(name, button, double)` | Click a UIA element by visible name (exact, else substring match) |
| `wait_for(name, timeout, interval)` | Poll until an element matching `name` appears, or time out |
| `type_text(text)` | Type Unicode text into the focused control |
| `press_key(key)` | Press a named special key (enter, tab, escape, arrows, f1-f12, ...) |
| `hotkey(keys)` | Press a chord together, e.g. `["ctrl", "shift", "p"]` for Ctrl+Shift+P |
| `scroll(x, y, clicks, keep_cursor)` | Mouse-wheel scroll at client-relative coordinates (positive = up, negative = down) |
| `drag(x1, y1, x2, y2, button, force, keep_cursor)` | Drag from one point to another -- moves through intermediate points, not a teleport, since many apps only recognize a drag if the mouse visibly moves while held. Same stale-target refusal as `click` |
| `wait_stable(timeout, settle_ms, interval, threshold, region)` | Poll until the window (or `region` of it) stops repainting for `settle_ms`. Never called automatically -- reports timing, not pixels, so take a fresh screenshot after |
| `history(last, tool_name, failures_only)` | The steps taken so far this session, from the journal, with their arguments, results and which frames were kept |
| `replay_frame(seq, which)` | The before/after screen image stored for step `seq` -- evidence for "what did it look like then?", downscaled, never a coordinate source |
| `release_control()` | Put the window the person was using back in front. Reading the attached window keeps working from behind |
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
- `recall_location`'s re-verify margin can itself cause a false "stale"
  report if set too large for the target's surroundings: expanding into a
  neighboring label/icon merges them into one bigger bbox, which fails the
  size-ratio check even though the actual target never moved — confirmed
  against Blender's tightly packed viewport header (`View Select Add
  Object`, items only ~15-20px apart). Use a small `margin` (5-10px) for
  dense menu/toolbar rows; the default (15) assumes moderate spacing.
