"""MCP server that lets an LLM attach to a running Windows app, see it
(screenshot + UI Automation element list), and drive it (click/type/keys).

Coordinate convention: every coordinate this server accepts or returns
(click x/y, element rects, highlight rects) is relative to the attached
window's *client area* -- the same pixel space as the image returned by
capture_screen. The server does all screen-coordinate translation internally.
"""

import ctypes


def _set_dpi_awareness():
    """Must run before any USER32/GDI call. An unaware process gets its
    coordinates silently virtualized to 96 DPI by Windows, while mss's
    screen capture returns real physical pixels -- on any display scaled
    above 100% (the common case) that mismatch makes click coordinates read
    off a screenshot land in the wrong place."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except OSError:
        pass


_set_dpi_awareness()

import json
import time

from mcp.server.mcpserver import Image, MCPServer

import input_sim
import location_cache
import uia_inspect
import window_manager
from overlay import get_overlay
from screenshot import capture_window_png, diff_bbox, find_content_bbox

mcp = MCPServer(
    name="winauto-mcp",
    instructions=(
        "Attach to a running Windows window, inspect it, and drive it. "
        "Typical flow: list_windows -> attach_window -> capture_screen "
        "(read the returned text summary and/or image) -> click/type_text/press_key. "
        "All coordinates are relative to the attached window's client area, "
        "matching the pixel space of the image from capture_screen. "
        "Do NOT eyeball click coordinates from a screenshot crop -- displayed "
        "crops can be rescaled in ways that don't map back to real pixels. "
        "Use locate_in_region(x1,y1,x2,y2) on a small candidate area instead "
        "to get an exact center point. Use snapshot() before an action and "
        "diff_since_snapshot() after it to objectively confirm the action had "
        "a visible effect, instead of comparing two screenshots by eye. "
        "For an app you expect to drive repeatedly across sessions, call "
        "recall_location(label) first -- it re-verifies any cached spot "
        "against the live screen before returning it, so a cache hit still "
        "means 'confirmed right now', not 'trusted blindly'. On a miss, "
        "locate it the normal way and call remember_location(label,...) so "
        "next time is a hit."
    ),
)

_state = {"hwnd": None, "last_snapshot": None}


def _require_attached() -> int:
    hwnd = _state["hwnd"]
    if hwnd is None:
        raise ValueError("no window attached -- call attach_window first")
    if not window_manager.window_exists(hwnd):
        _state["hwnd"] = None
        get_overlay().untrack()
        raise ValueError("attached window was closed -- call attach_window again")
    return hwnd


def _find_element(hwnd, name: str):
    """Find an element by name: exact case-insensitive match first, else
    substring match. Returns (element, total_match_count) or (None, 0)."""
    elements = uia_inspect.get_elements(hwnd)
    needle = name.strip().lower()
    exact = [el for el in elements if el["name"].strip().lower() == needle]
    if exact:
        return exact[0], len(exact)
    partial = [el for el in elements if needle in el["name"].strip().lower()]
    if partial:
        return partial[0], len(partial)
    return None, 0


@mcp.tool()
def list_windows() -> str:
    """List visible top-level windows that can be attached to (title, process, hwnd)."""
    windows = window_manager.list_windows()
    return json.dumps(
        [{"hwnd": w["hwnd"], "title": w["title"], "process": w["process"]} for w in windows],
        ensure_ascii=False,
    )


@mcp.tool()
def attach_window(hwnd: int) -> str:
    """Attach to a window by hwnd (from list_windows). Brings it to the foreground
    and shows a green tracking outline around it."""
    if not window_manager.window_exists(hwnd):
        raise ValueError(f"no such window: {hwnd}")
    window_manager.bring_to_foreground(hwnd)
    _state["hwnd"] = hwnd
    get_overlay().track(hwnd)
    title = window_manager.get_window_title(hwnd)
    return f'attached to "{title}" (hwnd={hwnd})'


@mcp.tool()
def detach_window() -> str:
    """Detach from the current window and hide the tracking overlay."""
    _state["hwnd"] = None
    get_overlay().untrack()
    return "detached"


@mcp.tool()
def capture_screen():
    """Screenshot the attached window's client area, plus a text summary of
    interactive elements found via UI Automation (buttons, inputs, menus, ...).
    If the app renders to a canvas (e.g. a game) the UIA summary will be empty
    -- read the image directly and pass pixel coordinates to click()."""
    hwnd = _require_attached()
    png_bytes = capture_window_png(hwnd)
    elements = uia_inspect.get_elements(hwnd)
    summary = uia_inspect.summarize(hwnd, elements)
    elements_json = json.dumps(elements, ensure_ascii=False)
    return [summary + "\n\nelements_json: " + elements_json, Image(data=png_bytes, format="png")]


@mcp.tool()
def screenshot():
    """Screenshot the attached window's client area only -- no UIA tree walk,
    much faster than capture_screen. Use this when you just need to look at
    the screen again (e.g. after a click) and don't need the element list."""
    hwnd = _require_attached()
    png_bytes = capture_window_png(hwnd)
    return Image(data=png_bytes, format="png")


@mcp.tool()
def get_elements() -> str:
    """UI Automation element list for the attached window only (no screenshot),
    as JSON: [{name, control_type, category, enabled, rect: [x1,y1,x2,y2]}, ...].
    rect is client-relative, same space as capture_screen's image."""
    hwnd = _require_attached()
    return json.dumps(uia_inspect.get_elements(hwnd), ensure_ascii=False)


@mcp.tool()
def click(x: int, y: int, button: str = "left", double: bool = False, modifiers: list[str] | None = None) -> str:
    """Click at (x, y) relative to the attached window's client area (same
    space as the capture_screen image). button is 'left' or 'right'.
    `modifiers` (e.g. ["ctrl"] or ["shift"]) are held down for the click --
    use for ctrl/shift-click multi-selection in a tree or list."""
    hwnd = _require_attached()
    if button not in ("left", "right"):
        raise ValueError("button must be 'left' or 'right'")
    input_sim.click_in_window(hwnd, x, y, button=button, double=double, modifiers=modifiers)
    mod_note = f" modifiers={modifiers}" if modifiers else ""
    return f"clicked ({x}, {y}) button={button} double={double}{mod_note}"


@mcp.tool()
def click_element(name: str, button: str = "left", double: bool = False) -> str:
    """Click a UIA element by its visible name/label (e.g. a button or menu
    item's text) instead of pixel coordinates -- matches exactly first, falls
    back to a substring match. Fails if no element matches; use get_elements
    or capture_screen first to see available names."""
    hwnd = _require_attached()
    if button not in ("left", "right"):
        raise ValueError("button must be 'left' or 'right'")
    el, count = _find_element(hwnd, name)
    if el is None:
        raise ValueError(f'no element found matching name "{name}" -- check get_elements for exact names')
    x1, y1, x2, y2 = el["rect"]
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    input_sim.click_in_window(hwnd, cx, cy, button=button, double=double)
    note = f" (ambiguous: {count} elements matched, clicked the first)" if count > 1 else ""
    return f'clicked element "{el["name"]}" at ({cx}, {cy}){note}'


@mcp.tool()
def wait_for(name: str, timeout: float = 10.0, interval: float = 0.5) -> str:
    """Poll the attached window's UIA tree until an element matching `name`
    (exact, else substring, case-insensitive) appears, or timeout (seconds)
    elapses. Useful after an action that triggers a slow UI update (dialog,
    page load, ...) instead of guessing a fixed sleep."""
    hwnd = _require_attached()
    deadline = time.monotonic() + timeout
    while True:
        el, count = _find_element(hwnd, name)
        if el is not None:
            note = f" ({count} elements matched)" if count > 1 else ""
            return f'found "{el["name"]}" at rect {el["rect"]}{note}'
        if time.monotonic() >= deadline:
            raise TimeoutError(f'timed out after {timeout}s waiting for element matching "{name}"')
        time.sleep(interval)


@mcp.tool()
def type_text(text: str) -> str:
    """Type literal Unicode text (any language) into whatever control currently
    has focus in the attached window. Click into the target field first."""
    hwnd = _require_attached()
    input_sim.type_text(text, hwnd=hwnd)
    return f"typed {len(text)} characters"


@mcp.tool()
def press_key(key: str) -> str:
    """Press a special key: enter, tab, escape, backspace, delete, up, down,
    left, right, home, end, pageup, pagedown, space, ctrl, alt, shift, f1-f12."""
    hwnd = _require_attached()
    input_sim.press_key(key, hwnd=hwnd)
    return f"pressed {key}"


@mcp.tool()
def drag(x1: int, y1: int, x2: int, y2: int, button: str = "left") -> str:
    """Drag from (x1, y1) to (x2, y2), both relative to the attached window's
    client area. Presses the button down, moves through intermediate points
    (not a teleport -- many apps only recognize a drag if the mouse visibly
    moves while held), then releases. Use for reordering, drag-into-viewport,
    or dragging a file from a browser/dock onto a node."""
    hwnd = _require_attached()
    if button not in ("left", "right"):
        raise ValueError("button must be 'left' or 'right'")
    input_sim.drag_in_window(hwnd, x1, y1, x2, y2, button=button)
    return f"dragged ({x1}, {y1}) -> ({x2}, {y2}) button={button}"


@mcp.tool()
def scroll(x: int, y: int, clicks: int) -> str:
    """Scroll the mouse wheel at (x, y) relative to the attached window's
    client area. Positive clicks scrolls up, negative scrolls down (one
    click = one wheel notch). Use this to reach content below the fold in
    scrollable panels (e.g. a long Inspector property list)."""
    hwnd = _require_attached()
    input_sim.scroll_in_window(hwnd, x, y, clicks)
    return f"scrolled {clicks} click(s) at ({x}, {y})"


@mcp.tool()
def hotkey(keys: list[str]) -> str:
    """Press a chord of keys together, e.g. ["ctrl", "shift", "p"] for
    Ctrl+Shift+P. Holds each key down in order, then releases in reverse
    order. Each entry is a SPECIAL_KEYS name (see press_key) or a single
    a-z/0-9 letter/digit."""
    hwnd = _require_attached()
    input_sim.press_keys(keys, hwnd=hwnd)
    return f"pressed chord {'+'.join(keys)}"


@mcp.tool()
def locate_in_region(x1: int, y1: int, x2: int, y2: int, threshold: int = 30) -> str:
    """Find exact click coordinates inside a small region by pixel contrast,
    instead of eyeballing them from a screenshot. Pick a region (client-relative,
    same space as screenshot()) that tightly bounds ONE thing you want to click
    -- a button, a menu label, one list row -- not the whole window. Returns
    the tight bounding box of whatever in that region differs from its
    dominant background color, plus its center point (click that). threshold
    (0-255) is the max per-channel color difference still counted as
    background -- raise it if faint content isn't detected, lower it if the
    box comes back too big. Prefer this over reading pixel coordinates off a
    displayed screenshot crop by eye: crop thumbnails can be rescaled for
    display in ways that don't map back to real source-image pixels, which
    has caused repeated 50-150+ px click misses."""
    hwnd = _require_attached()
    if x2 <= x1 or y2 <= y1:
        raise ValueError("region must have x2>x1 and y2>y1")
    area = (x2 - x1) * (y2 - y1)
    if area > 500_000:
        raise ValueError(
            f"region too large ({x2 - x1}x{y2 - y1}={area}px) -- narrow it to a "
            "specific button/label/row first, not a whole panel"
        )
    png_bytes = capture_window_png(hwnd)
    bbox = find_content_bbox(png_bytes, (x1, y1, x2, y2), threshold=threshold)
    if bbox is None:
        return (
            f"no content found in region ({x1},{y1})-({x2},{y2}) above threshold={threshold} "
            "-- region may be empty/uniform background, or try a higher threshold"
        )
    bx1, by1, bx2, by2 = bbox
    cx, cy = (bx1 + bx2) // 2, (by1 + by2) // 2
    return json.dumps({"bbox": [bx1, by1, bx2, by2], "center": [cx, cy]})


@mcp.tool()
def snapshot() -> str:
    """Take a screenshot of the attached window and store it as the reference
    point for diff_since_snapshot(). Call this right before an action whose
    visual effect you want to verify objectively."""
    hwnd = _require_attached()
    _state["last_snapshot"] = capture_window_png(hwnd)
    return "snapshot stored"


@mcp.tool()
def diff_since_snapshot(threshold: int = 10) -> str:
    """Compare the current screen to the last snapshot() and report whether
    anything visibly changed, as the bounding box of changed pixels (or "no
    change detected"). Use this instead of eyeballing two screenshots side by
    side -- an action that silently had no effect (wrong focus, missed
    coordinate, click landed on the wrong widget) produces a screen that is
    pixel-identical to before, which is easy to miss by eye but unambiguous
    here. threshold (0-255) is the minimum grayscale diff intensity counted
    as a real change (filters out rendering noise); 0 means any difference
    at all counts."""
    hwnd = _require_attached()
    if _state["last_snapshot"] is None:
        raise ValueError("no snapshot stored -- call snapshot() first")
    png_bytes = capture_window_png(hwnd)
    bbox = diff_bbox(_state["last_snapshot"], png_bytes, threshold=threshold)
    if bbox is None:
        return "no change detected"
    return json.dumps({"changed_bbox": list(bbox)})


@mcp.tool()
def remember_location(label: str, x1: int, y1: int, x2: int, y2: int) -> str:
    """Save a click target's bounding box under a short semantic label (e.g.
    "save_button", "file_menu"), scoped to the attached window's process name
    and client size -- so recall_location() can find it again in a future
    session against the same app. Call this right after locate_in_region or
    a screenshot analysis finds something you expect to click again later.
    This alone does not make future clicks skip verification -- see
    recall_location, which re-checks the live screen before trusting it."""
    hwnd = _require_attached()
    process_name = window_manager.get_process_name(hwnd)
    w, h = window_manager.get_client_size(hwnd)
    location_cache.put(process_name, w, h, label, (x1, y1, x2, y2))
    return f'remembered "{label}" for {process_name} ({w}x{h}) as [{x1},{y1},{x2},{y2}]'


@mcp.tool()
def recall_location(label: str, margin: int = 15, threshold: int = 30) -> str:
    """Look up a location saved earlier by remember_location() for the
    attached window's process+client-size, then RE-VERIFY it against the
    CURRENT screen before returning it -- a cached coordinate is never
    returned blindly, since the target UI can move after an app update, a
    scroll, or a resize. Verification: pixel-contrast-scans the cached bbox
    expanded by `margin` px on each side (find_content_bbox) and compares
    what's found now to what was cached -- center must be within ~1.5x
    margin and size within 2x. On a match, returns the freshly re-scanned
    center (use that, not the stale cached one) and refreshes the cache
    entry. On a miss (nothing cached, cached area now empty, or the re-scan
    doesn't match closely enough), the stale entry is dropped and this
    returns cache_hit=false -- fall back to locate_in_region on a wider area
    or a full screenshot, then call remember_location again with the result.
    Tune `margin` to how densely packed the target's neighbors are: a small
    isolated button can use a larger margin (more tolerant of drift); a
    label inside a tight menu/toolbar row (items only ~10-30px apart, e.g.
    Blender's "View Select Add Object" header) needs a SMALL margin
    (5-10px) -- a margin that reaches into a neighboring label merges them
    into one bigger bbox and falsely reports the target as moved/stale even
    when it didn't."""
    hwnd = _require_attached()
    process_name = window_manager.get_process_name(hwnd)
    w, h = window_manager.get_client_size(hwnd)
    cached = location_cache.get(process_name, w, h, label)
    if cached is None:
        return json.dumps({"cache_hit": False, "reason": "no cached location for this label/app/window-size"})
    cx1, cy1, cx2, cy2 = cached["bbox"]
    rx1, ry1 = max(0, cx1 - margin), max(0, cy1 - margin)
    rx2, ry2 = cx2 + margin, cy2 + margin
    png_bytes = capture_window_png(hwnd)
    bbox = find_content_bbox(png_bytes, (rx1, ry1, rx2, ry2), threshold=threshold)
    if bbox is None:
        location_cache.forget(process_name, w, h, label)
        return json.dumps({"cache_hit": False, "reason": "cached area is now empty -- UI likely moved"})
    bx1, by1, bx2, by2 = bbox
    old_cx, old_cy = (cx1 + cx2) / 2, (cy1 + cy2) / 2
    new_cx, new_cy = (bx1 + bx2) / 2, (by1 + by2) / 2
    old_w, old_h = max(cx2 - cx1, 1), max(cy2 - cy1, 1)
    new_w, new_h = max(bx2 - bx1, 1), max(by2 - by1, 1)
    dist = ((new_cx - old_cx) ** 2 + (new_cy - old_cy) ** 2) ** 0.5
    size_ok = 0.5 <= new_w / old_w <= 2.0 and 0.5 <= new_h / old_h <= 2.0
    if dist > margin * 1.5 or not size_ok:
        location_cache.forget(process_name, w, h, label)
        return json.dumps(
            {
                "cache_hit": False,
                "reason": f"re-scan found content but it moved {dist:.0f}px / size changed too much "
                "-- treating as stale, UI likely changed",
            }
        )
    location_cache.put(process_name, w, h, label, (bx1, by1, bx2, by2))
    return json.dumps(
        {"cache_hit": True, "bbox": [bx1, by1, bx2, by2], "center": [round(new_cx), round(new_cy)]}
    )


@mcp.tool()
def highlight(rects: list[list[int]]) -> str:
    """Draw red debug boxes on the overlay at the given [x1,y1,x2,y2] rects
    (client-relative). Pass an empty list to clear. Purely visual, for the
    human watching the screen -- has no effect on click/type."""
    _require_attached()
    get_overlay().set_highlights([tuple(r) for r in rects])
    return f"highlighted {len(rects)} rect(s)"


if __name__ == "__main__":
    mcp.run()
