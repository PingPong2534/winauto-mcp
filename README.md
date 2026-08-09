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

## Tools

| Tool | Purpose |
|---|---|
| `list_windows` | List visible top-level windows (title, process, hwnd) |
| `attach_window(hwnd)` | Attach to a window, foreground it, show tracking outline |
| `detach_window()` | Detach and hide the overlay |
| `capture_screen()` | Screenshot + UIA text summary of the attached window |
| `screenshot()` | Screenshot only, no UIA tree walk — fast, for re-checking the screen |
| `get_elements()` | UIA element list only, as JSON (no screenshot) |
| `click(x, y, button, double, modifiers)` | Click at client-relative coordinates; `modifiers` (e.g. `["ctrl"]`) are held down for the click -- for ctrl/shift-click multi-selection |
| `click_element(name, button, double)` | Click a UIA element by visible name (exact, else substring match) |
| `wait_for(name, timeout, interval)` | Poll until an element matching `name` appears, or time out |
| `type_text(text)` | Type Unicode text into the focused control |
| `press_key(key)` | Press a named special key (enter, tab, escape, arrows, f1-f12, ...) |
| `hotkey(keys)` | Press a chord together, e.g. `["ctrl", "shift", "p"]` for Ctrl+Shift+P |
| `scroll(x, y, clicks)` | Mouse-wheel scroll at client-relative coordinates (positive = up, negative = down) |
| `drag(x1, y1, x2, y2, button)` | Drag from one point to another -- moves through intermediate points, not a teleport, since many apps only recognize a drag if the mouse visibly moves while held |
| `locate_in_region(x1, y1, x2, y2, threshold)` | Find exact click coordinates by pixel contrast within a small region -- returns the tight content bbox and its center. Use instead of eyeballing coordinates off a displayed screenshot crop, which has repeatedly been wrong by 50-150+ px (displayed crops can be rescaled in ways that don't map back to real source pixels) |
| `snapshot()` | Store the current screenshot as a reference point |
| `diff_since_snapshot(threshold)` | Compare the current screen to the last `snapshot()`, return the bounding box of changed pixels or "no change detected" -- objective confirmation an action had a visible effect, instead of eyeballing two screenshots side by side |
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
- `recall_location`'s re-verify margin can itself cause a false "stale"
  report if set too large for the target's surroundings: expanding into a
  neighboring label/icon merges them into one bigger bbox, which fails the
  size-ratio check even though the actual target never moved — confirmed
  against Blender's tightly packed viewport header (`View Select Add
  Object`, items only ~15-20px apart). Use a small `margin` (5-10px) for
  dense menu/toolbar rows; the default (15) assumes moderate spacing.
