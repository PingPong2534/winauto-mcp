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

import functools
import inspect
import json
import time

from mcp.server.mcpserver import Image, MCPServer

import input_sim
import journal
import location_cache
import uia_inspect
import window_manager
from overlay import get_overlay
from screenshot import changed_bbox, find_content_bbox, grab_window, to_png

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
        "next time is a hit.\n\n"
        "ACT ON THE SCREEN IN FRONT OF YOU, NOT THE ONE YOU REMEMBER. A long "
        "run drifts: you read a screenshot, do a few other things, then click "
        "a coordinate the app has since moved on from. click() and drag() "
        "check for exactly that and REFUSE rather than click blind -- you get "
        "a report plus the window as it looks now, and re-issuing then works. "
        "That refusal is information, not an obstacle: read the frame it hands "
        "back before deciding what to do next, and reach for force=true only "
        "when you know the area repaints on its own.\n"
        "Nothing waits for you automatically. After an action that starts a "
        "load, a render or an animation, call wait_stable() until the window "
        "stops repainting rather than screenshotting a half-drawn frame and "
        "reasoning about it. wait_stable() reports timing, not pixels, so take "
        "a fresh screenshot afterwards.\n"
        "Every call is journaled with before/after frames. When something is "
        "inexplicable -- a click landed on nothing, a panel is not what you "
        "expected -- use history() to see the steps that led here and "
        "replay_frame(seq) to look at the screen as it actually was at that "
        "step, instead of reconstructing it from memory. Those frames are "
        "downscaled evidence: never take click coordinates off them.\n"
        "The mouse, keyboard and desktop belong to a person who is probably "
        "still using them. Reading the window (screenshot, wait_stable, "
        "locate_in_region, diff_since_snapshot) works while it sits behind "
        "other windows, so do not raise it just to look. Only sending input "
        "brings it to the front; when you finish a piece of work and are about "
        "to think, report or wait, call release_control() to hand the "
        "foreground back -- but not in the middle of one interaction, since an "
        "open menu closes when its window loses focus."
    ),
)

_state = {
    "hwnd": None,
    "last_snapshot": None,
    # The most recent frame the caller actually looked at (or measured a
    # coordinate from), and when. click() checks its target area against this
    # to catch a click aimed using an out-of-date view of the screen.
    "seen": None,
    "seen_t": None,
    "seen_seq": None,
    # Frame captured by the journaling decorator immediately before the
    # current action tool ran; the staleness check reuses it instead of
    # grabbing the screen a second time.
    "pre_frame": None,
    # Frame a tool captured itself and handed to journal_frame(), used as the
    # journal's 'after' image so the screen isn't captured twice.
    "post_frame": None,
}


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


def _mark_seen(frame) -> None:
    """Note that the caller has now been shown this frame, or has derived a
    coordinate from it. Only frames that informed the caller's next decision
    count -- a capture taken purely for internal bookkeeping (a settle poll, a
    journal thumbnail) must NOT be marked, or the staleness check silently
    starts approving clicks aimed from a screen nobody looked at."""
    _state["seen"] = frame
    _state["seen_t"] = time.monotonic()
    _state["seen_seq"] = journal.session_info()["records"] + 1


# Tools whose effect is visual and worth a before/after frame in the journal.
# Everything else is journaled as text only: a window list or a UIA query
# doesn't change the screen, so a frame pair would cost two captures and show
# nothing. capture_screen/screenshot are excluded for a different reason --
# they hand their own frame to journal_frame() rather than being captured twice.
_FRAME_TOOLS = frozenset(
    {"click", "click_element", "drag", "scroll", "type_text", "press_key", "hotkey"}
)


# How far around a click target the screen is compared against the frame the
# caller last looked at. Deliberately local: a spinner or clock elsewhere in
# the window is irrelevant to whether *this* button is still where it was, and
# comparing window-wide would block almost every click in a live app.
STALE_RADIUS = 40
STALE_THRESHOLD = 10


def _stale_block(x: int, y: int, what: str):
    """If the area being aimed at no longer looks like it did in the frame the
    caller last saw, return a response that refuses the action and hands back
    the current screen; otherwise None.

    This is the guard against the most common failure in a long automation
    run: deciding where to click from a screenshot, spending a few turns doing
    other things, and clicking that remembered coordinate after the app has
    moved on -- a dialog opened, the page finished loading, the list scrolled.
    The click lands on whatever is there now, the next screenshot shows an
    inexplicable state, and the run derails.

    The returned frame is marked as seen, so re-issuing the same action after
    looking at it goes through. Blocking twice for one change would just be a
    loop the caller can't escape.
    """
    seen, current = _state["seen"], _state["pre_frame"]
    if seen is None or current is None:
        return None  # nothing looked at yet, or the window couldn't be grabbed

    age = time.monotonic() - (_state["seen_t"] or 0)
    context = {
        "blocked": True,
        "performed": False,
        "action": what,
        "seen_at_step": _state["seen_seq"],
        "seen_age_s": round(age, 1),
    }

    if seen.size != current.size:
        _mark_seen(current)
        context["reason"] = (
            f"the window was resized since you last looked ({seen.size[0]}x{seen.size[1]} "
            f"-> {current.size[0]}x{current.size[1]}), so every coordinate from that view "
            "is off. Attached is the window as it is now."
        )
        return [json.dumps(context, ensure_ascii=False), Image(data=to_png(current), format="png")]

    w, h = current.size
    region = (
        max(0, x - STALE_RADIUS),
        max(0, y - STALE_RADIUS),
        min(w, x + STALE_RADIUS),
        min(h, y + STALE_RADIUS),
    )
    if region[2] <= region[0] or region[3] <= region[1]:
        return None  # target is outside the client area; let the action fail on its own terms
    box = changed_bbox(seen, current, threshold=STALE_THRESHOLD, region=region)
    if box is None:
        return None

    _mark_seen(current)
    context["changed_bbox"] = list(box)
    context["checked_region"] = list(region)
    context["reason"] = (
        f"NOT PERFORMED. The screen within {STALE_RADIUS}px of ({x}, {y}) changed after the "
        f"frame you took your coordinates from ({age:.1f}s ago, step {_state['seen_seq']}), "
        "so that target is no longer what you saw. Attached is the window as it is right now: "
        "check whether your target is still there and still at these coordinates. Re-issue the "
        "action (it will go through now that you have looked), or re-locate the target with "
        "locate_in_region. If the app repaints this area continuously and the change is "
        "irrelevant, pass force=true. If it is still loading, wait_stable() first."
    )
    return [json.dumps(context, ensure_ascii=False), Image(data=to_png(current), format="png")]


def journal_frame(frame) -> None:
    """Offer a frame a tool already captured to the journal, as this call's
    'after' image, instead of making the decorator capture the screen again."""
    _state["post_frame"] = frame


def _try_grab():
    """Capture the attached window for journaling. Returns None rather than
    raising -- a frame that can't be grabbed (window closing, minimized) must
    not turn into a failed automation step."""
    hwnd = _state["hwnd"]
    if hwnd is None or not window_manager.window_exists(hwnd):
        return None
    try:
        return grab_window(hwnd)
    except (ValueError, OSError):
        return None


def _result_text(result) -> str:
    """Flatten a tool's return value to something loggable. Tools may return a
    string, an Image, or a list mixing both."""
    if isinstance(result, list):
        return " | ".join(p if isinstance(p, str) else "<image>" for p in result)
    if isinstance(result, Image):
        return "<image>"
    return result


def journaled(fn):
    """Record every call to a tool -- arguments, outcome, duration, and for
    tools that drive the UI, a before/after thumbnail -- so that a later step
    can ask what the screen actually looked like earlier instead of relying on
    the model's recollection of it.

    Failures are journaled too, then re-raised unchanged: the record of what
    was attempted is most valuable exactly when it didn't work.
    """
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        name = fn.__name__
        wants_frames = name in _FRAME_TOOLS
        _state["pre_frame"] = _try_grab() if wants_frames else None
        _state["post_frame"] = None

        try:
            bound = signature.bind(*args, **kwargs)
            logged_args = dict(bound.arguments)
        except TypeError:
            logged_args = {"args": repr(args), "kwargs": repr(kwargs)}

        started = time.monotonic()
        error = None
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - journaled, then re-raised as-is
            error = exc
            result = f"{type(exc).__name__}: {exc}"
        elapsed_ms = round((time.monotonic() - started) * 1000)

        after = _state["post_frame"]
        if after is None and wants_frames and error is None:
            after = _try_grab()

        hwnd = _state["hwnd"]
        journal.record(
            name,
            args=logged_args,
            ok=error is None,
            result=_result_text(result) if error is None else result,
            ms=elapsed_ms,
            window=window_manager.get_window_title(hwnd) if hwnd else None,
            before=_state["pre_frame"],
            after=after,
        )
        _state["pre_frame"] = None
        _state["post_frame"] = None

        if error is not None:
            raise error
        return result

    return wrapper


def tool(fn):
    """Register `fn` as an MCP tool, journaled."""
    return mcp.tool()(journaled(fn))


@tool
def list_windows() -> str:
    """List visible top-level windows that can be attached to (title, process, hwnd)."""
    windows = window_manager.list_windows()
    return json.dumps(
        [{"hwnd": w["hwnd"], "title": w["title"], "process": w["process"]} for w in windows],
        ensure_ascii=False,
    )


@tool
def attach_window(hwnd: int) -> str:
    """Attach to a window by hwnd (from list_windows). Brings it to the foreground
    and shows a green tracking outline around it."""
    if not window_manager.window_exists(hwnd):
        raise ValueError(f"no such window: {hwnd}")
    window_manager.bring_to_foreground(hwnd)
    _state["hwnd"] = hwnd
    _state["seen"] = _state["seen_t"] = _state["seen_seq"] = None
    get_overlay().track(hwnd)
    title = window_manager.get_window_title(hwnd)
    # Attaching is the start of an automation run, so it starts a fresh
    # journal -- history() should describe this run, not trail off into the
    # previous app's steps.
    session = journal.start_session(f"{title} (hwnd={hwnd})")
    return f'attached to "{title}" (hwnd={hwnd}); journal session {session}'


@tool
def detach_window() -> str:
    """Detach from the current window and hide the tracking overlay."""
    _state["hwnd"] = None
    get_overlay().untrack()
    return "detached"


@tool
def capture_screen():
    """Screenshot the attached window's client area, plus a text summary of
    interactive elements found via UI Automation (buttons, inputs, menus, ...).
    If the app renders to a canvas (e.g. a game) the UIA summary will be empty
    -- read the image directly and pass pixel coordinates to click()."""
    hwnd = _require_attached()
    frame = grab_window(hwnd)
    _mark_seen(frame)
    journal_frame(frame)
    png_bytes = to_png(frame)
    elements = uia_inspect.get_elements(hwnd)
    summary = uia_inspect.summarize(hwnd, elements)
    elements_json = json.dumps(elements, ensure_ascii=False)
    return [summary + "\n\nelements_json: " + elements_json, Image(data=png_bytes, format="png")]


@tool
def screenshot():
    """Screenshot the attached window's client area only -- no UIA tree walk,
    much faster than capture_screen. Use this when you just need to look at
    the screen again (e.g. after a click) and don't need the element list."""
    hwnd = _require_attached()
    frame = grab_window(hwnd)
    _mark_seen(frame)
    journal_frame(frame)
    return Image(data=to_png(frame), format="png")


@tool
def get_elements() -> str:
    """UI Automation element list for the attached window only (no screenshot),
    as JSON: [{name, control_type, category, enabled, rect: [x1,y1,x2,y2]}, ...].
    rect is client-relative, same space as capture_screen's image."""
    hwnd = _require_attached()
    return json.dumps(uia_inspect.get_elements(hwnd), ensure_ascii=False)


@tool
def click(
    x: int,
    y: int,
    button: str = "left",
    double: bool = False,
    modifiers: list[str] | None = None,
    force: bool = False,
    keep_cursor: bool = False,
):
    """Click at (x, y) relative to the attached window's client area (same
    space as the capture_screen image). button is 'left' or 'right'.
    `modifiers` (e.g. ["ctrl"] or ["shift"]) are held down for the click --
    use for ctrl/shift-click multi-selection in a tree or list.

    Refuses to click blind: if the area around (x, y) no longer matches the
    frame you took the coordinate from -- the app finished loading, a dialog
    opened, the list scrolled -- nothing is clicked. You get back a report
    saying so plus the window as it looks now, and the click is yours to
    re-issue once you have looked. `force=true` skips that check, for a
    target sitting in an area the app repaints on its own.

    The pointer is returned to where the person left it afterwards, since it
    is their pointer too. Pass keep_cursor=true to leave it on the target
    instead -- needed when the next step keeps following the pointer, such as
    a Blender modal transform started with G/R/S, or a hover-driven menu.
    """
    hwnd = _require_attached()
    if button not in ("left", "right"):
        raise ValueError("button must be 'left' or 'right'")
    if not force:
        blocked = _stale_block(x, y, f"click({x}, {y})")
        if blocked is not None:
            return blocked
    input_sim.click_in_window(hwnd, x, y, button=button, double=double, modifiers=modifiers,
                              keep_cursor=keep_cursor)
    mod_note = f" modifiers={modifiers}" if modifiers else ""
    return f"clicked ({x}, {y}) button={button} double={double}{mod_note}"


@tool
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


@tool
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


@tool
def wait_stable(
    timeout: float = 5.0,
    settle_ms: int = 400,
    interval: float = 0.12,
    threshold: int = 10,
    region: list[int] | None = None,
) -> str:
    """Wait until the attached window stops changing -- i.e. until it has
    finished drawing whatever it was drawing.

    Actions here return as soon as the input has been sent, which is well
    before a menu has opened, a dialog has appeared or a document has
    rendered. Call this after an action that triggers a slow redraw, then
    take a fresh screenshot before deciding where to click next. Unlike
    wait_for(name) this needs no UI Automation tree, so it works on
    canvas-drawn apps (games, Blender, Godot) where the element list is empty.

    Settles when `settle_ms` passes with no pixel changing by more than
    `threshold`; gives up at `timeout` seconds. Returns JSON with `stable`
    (did it settle), `waited_ms`, and, when it did not settle, the
    `last_change_bbox` of what is still moving.

    An app that repaints part of itself forever -- a blinking caret, a live
    viewport, a clock -- will never settle window-wide. Pass `region`
    [x1,y1,x2,y2] to watch only the part you care about, using the
    `last_change_bbox` from a failed attempt to see what to exclude.

    This does NOT count as looking at the screen: it reports timing, not
    pixels, so a click aimed from a screenshot taken before this call is
    still working from a stale view and click() will say so. Take a fresh
    screenshot() or locate_in_region() after this returns.
    """
    hwnd = _require_attached()
    if timeout <= 0:
        raise ValueError("timeout must be > 0")
    watched = tuple(region) if region else None
    if watched is not None and (len(watched) != 4 or watched[2] <= watched[0] or watched[3] <= watched[1]):
        raise ValueError("region must be [x1,y1,x2,y2] with x2>x1 and y2>y1")

    started = time.monotonic()
    deadline = started + timeout
    previous = grab_window(hwnd)
    steady_since = time.monotonic()
    polls, changes, last_change = 1, 0, None

    while True:
        time.sleep(interval)
        current = grab_window(hwnd)
        polls += 1
        box = changed_bbox(previous, current, threshold=threshold, region=watched)
        now = time.monotonic()
        previous = current
        if box is None:
            if (now - steady_since) * 1000 >= settle_ms:
                journal_frame(to_png(current))
                return json.dumps(
                    {
                        "stable": True,
                        "waited_ms": round((now - started) * 1000),
                        "polls": polls,
                        "changed_during_wait": changes > 0,
                    }
                )
        else:
            steady_since = now
            changes += 1
            last_change = list(box)
        if now >= deadline:
            journal_frame(to_png(current))
            return json.dumps(
                {
                    "stable": False,
                    "waited_ms": round((now - started) * 1000),
                    "polls": polls,
                    "last_change_bbox": last_change,
                    "hint": (
                        "still repainting at timeout -- if that bbox is a region that "
                        "animates on its own (caret, viewport, clock), re-run with "
                        "region=[...] around the part you actually care about, or raise "
                        "threshold; otherwise the app is still busy, so raise timeout"
                    ),
                }
            )


@tool
def type_text(text: str) -> str:
    """Type literal Unicode text (any language) into whatever control currently
    has focus in the attached window. Click into the target field first."""
    hwnd = _require_attached()
    input_sim.type_text(text, hwnd=hwnd)
    return f"typed {len(text)} characters"


@tool
def press_key(key: str) -> str:
    """Press a special key: enter, tab, escape, backspace, delete, up, down,
    left, right, home, end, pageup, pagedown, space, ctrl, alt, shift, f1-f12."""
    hwnd = _require_attached()
    input_sim.press_key(key, hwnd=hwnd)
    return f"pressed {key}"


@tool
def drag(x1: int, y1: int, x2: int, y2: int, button: str = "left", force: bool = False,
         keep_cursor: bool = False):
    """Drag from (x1, y1) to (x2, y2), both relative to the attached window's
    client area. Presses the button down, moves through intermediate points
    (not a teleport -- many apps only recognize a drag if the mouse visibly
    moves while held), then releases. Use for reordering, drag-into-viewport,
    or dragging a file from a browser/dock onto a node.

    Like click(), this refuses to start a drag from a spot that has changed
    since you last looked at the screen -- grabbing the wrong thing is worse
    than clicking the wrong thing, since it also drops it somewhere. See
    click() for what comes back and how `force` behaves."""
    hwnd = _require_attached()
    if button not in ("left", "right"):
        raise ValueError("button must be 'left' or 'right'")
    if not force:
        blocked = _stale_block(x1, y1, f"drag({x1}, {y1}) -> ({x2}, {y2})")
        if blocked is not None:
            return blocked
    input_sim.drag_in_window(hwnd, x1, y1, x2, y2, button=button, keep_cursor=keep_cursor)
    return f"dragged ({x1}, {y1}) -> ({x2}, {y2}) button={button}"


@tool
def scroll(x: int, y: int, clicks: int, keep_cursor: bool = False) -> str:
    """Scroll the mouse wheel at (x, y) relative to the attached window's
    client area. Positive clicks scrolls up, negative scrolls down (one
    click = one wheel notch). Use this to reach content below the fold in
    scrollable panels (e.g. a long Inspector property list).

    See click() for keep_cursor -- the pointer goes back to the person's
    position afterwards unless you ask for it to stay."""
    hwnd = _require_attached()
    input_sim.scroll_in_window(hwnd, x, y, clicks, keep_cursor=keep_cursor)
    return f"scrolled {clicks} click(s) at ({x}, {y})"


@tool
def release_control() -> str:
    """Give the desktop back: put whatever window the person was using before
    this run took focus back in front. Call it when you have finished a piece
    of work and are about to think, report, or wait -- not between two steps
    of one interaction, because a menu or dropdown you just opened closes the
    moment its window loses focus.

    Nothing else is lost by calling it. The window stays attached, and reading
    it (screenshot, wait_stable, locate_in_region, diff_since_snapshot) does
    not need it in front -- only sending input does, and the next action
    raises it again by itself."""
    title = window_manager.restore_foreground()
    if title is None:
        return "nothing to give back -- no window was displaced, or it has since closed"
    return f'foreground returned to "{title}"; reading the attached window still works'


@tool
def hotkey(keys: list[str]) -> str:
    """Press a chord of keys together, e.g. ["ctrl", "shift", "p"] for
    Ctrl+Shift+P. Holds each key down in order, then releases in reverse
    order. Each entry is a SPECIAL_KEYS name (see press_key) or a single
    a-z/0-9 letter/digit."""
    hwnd = _require_attached()
    input_sim.press_keys(keys, hwnd=hwnd)
    return f"pressed chord {'+'.join(keys)}"


@tool
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
    frame = grab_window(hwnd)
    # The returned centre is measured off this frame, so the caller's next
    # click is aimed at this frame -- record it as seen, or the staleness
    # check would reject a coordinate that was in fact freshly measured.
    _mark_seen(frame)
    journal_frame(frame)
    bbox = find_content_bbox(frame, (x1, y1, x2, y2), threshold=threshold)
    if bbox is None:
        return (
            f"no content found in region ({x1},{y1})-({x2},{y2}) above threshold={threshold} "
            "-- region may be empty/uniform background, or try a higher threshold"
        )
    bx1, by1, bx2, by2 = bbox
    cx, cy = (bx1 + bx2) // 2, (by1 + by2) // 2
    return json.dumps({"bbox": [bx1, by1, bx2, by2], "center": [cx, cy]})


@tool
def snapshot() -> str:
    """Take a screenshot of the attached window and store it as the reference
    point for diff_since_snapshot(). Call this right before an action whose
    visual effect you want to verify objectively."""
    hwnd = _require_attached()
    _state["last_snapshot"] = grab_window(hwnd)
    return "snapshot stored"


@tool
def diff_since_snapshot(threshold: int = 10, region: list[int] | None = None) -> str:
    """Compare the current screen to the last snapshot() and report whether
    anything visibly changed, as the bounding box of changed pixels (or "no
    change detected"). Use this instead of eyeballing two screenshots side by
    side -- an action that silently had no effect (wrong focus, missed
    coordinate, click landed on the wrong widget) produces a screen that is
    pixel-identical to before, which is easy to miss by eye but unambiguous
    here. threshold (0-255) is the minimum grayscale diff intensity counted
    as a real change (filters out rendering noise); 0 means any difference
    at all counts.

    Read the box as "the change is somewhere in here", not "all of this
    changed": it is one box around every changed pixel, so a few small edits
    in different corners give a box covering the whole window. Typing one word
    into a text editor already does this -- the text, the title's modified
    marker and a status bar all change at once. Do NOT click the box's centre;
    it is very often a pixel that never changed. To find out where the change
    actually is, pass `region` [x1,y1,x2,y2] and re-run over parts of the
    window until the box comes back tight."""
    hwnd = _require_attached()
    if _state["last_snapshot"] is None:
        raise ValueError("no snapshot stored -- call snapshot() first")
    watched = tuple(region) if region else None
    if watched is not None and (len(watched) != 4 or watched[2] <= watched[0] or watched[3] <= watched[1]):
        raise ValueError("region must be [x1,y1,x2,y2] with x2>x1 and y2>y1")
    frame = grab_window(hwnd)
    journal_frame(frame)
    bbox = changed_bbox(_state["last_snapshot"], frame, threshold=threshold, region=watched)
    if bbox is None:
        return "no change detected"
    return json.dumps({"changed_bbox": list(bbox)})


@tool
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


@tool
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
    frame = grab_window(hwnd)
    _mark_seen(frame)  # same reasoning as locate_in_region
    journal_frame(frame)
    bbox = find_content_bbox(frame, (rx1, ry1, rx2, ry2), threshold=threshold)
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


@tool
def highlight(rects: list[list[int]]) -> str:
    """Draw red debug boxes on the overlay at the given [x1,y1,x2,y2] rects
    (client-relative). Pass an empty list to clear. Purely visual, for the
    human watching the screen -- has no effect on click/type."""
    _require_attached()
    get_overlay().set_highlights([tuple(r) for r in rects])
    return f"highlighted {len(rects)} rect(s)"


@tool
def history(last: int = 20, tool_name: str | None = None, failures_only: bool = False) -> str:
    """Replay what this automation run has actually done so far: every tool
    call since attach_window, in order, with its arguments, outcome, duration
    and whether a before/after screen thumbnail was kept for it.

    Use this when the current screen doesn't match what you expected -- it
    answers "what did I already click, and did it work?" from the record
    rather than from recollection, which is where these runs usually go wrong.
    Steps marked ok=false are attempts that raised; `result` holds the error.
    A step listing "before"/"after" has frames you can look at with
    replay_frame(seq).

    `last` caps how many of the most recent steps come back, `tool_name`
    filters to one tool (substring match, e.g. "click"), and failures_only
    keeps just the steps that raised.
    """
    records = journal.recent(last, tool=tool_name, failures_only=failures_only)
    return json.dumps(
        {
            "session": journal.session_info(),
            "note": (
                "oldest first; 'before'/'after' name thumbnails viewable via "
                "replay_frame(seq) -- they are downscaled, never read coordinates off them"
            ),
            "records": records,
        },
        ensure_ascii=False,
    )


@tool
def replay_frame(seq: int, which: str = "after"):
    """Show the screen as it actually looked at an earlier step: `which` is
    "before" (just before that step ran) or "after" (just after it did). Get
    `seq` from history().

    Use it to settle a question about the past instead of inferring it -- e.g.
    whether a dialog was already open when you clicked, or whether a click
    landed on the control you thought it did.

    The image is a downscaled JPEG kept for evidence only. Do NOT measure
    click coordinates from it: it is not the client-area pixel space, and the
    returned `scale` says by how much it shrank. To act on what you find here,
    take a fresh screenshot() or locate_in_region() first.
    """
    if which not in ("before", "after"):
        raise ValueError("which must be 'before' or 'after'")
    entry = journal.get(seq)
    if entry is None:
        seqs = [e["seq"] for e in journal.recent(journal.RING_SIZE)]
        have = f"steps {min(seqs)}-{max(seqs)} are in memory" if seqs else "nothing recorded yet"
        raise ValueError(f"no step {seq} in this session -- {have}")
    path = journal.frame_path(seq, which)
    if path is None:
        raise ValueError(
            f'step {seq} ({entry["tool"]}) has no "{which}" frame -- only tools that '
            "drive or capture the screen keep frames; see history() for which steps do"
        )
    with open(path, "rb") as f:
        data = f.read()
    header = json.dumps(
        {
            "seq": seq,
            "which": which,
            "tool": entry["tool"],
            "t": entry["t"],
            "args": entry.get("args"),
            "result": entry.get("result"),
            "scale": entry.get("scale"),
            "warning": "downscaled evidence image -- do not read click coordinates off it",
        },
        ensure_ascii=False,
    )
    return [header, Image(data=data, format="jpeg")]


if __name__ == "__main__":
    mcp.run()
