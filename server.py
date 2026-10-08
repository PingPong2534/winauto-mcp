"""MCP server that lets an LLM attach to a running Windows app, see it
(screenshot + UI Automation element list), and control it via UIA patterns.

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
import math
import os
import tempfile
import threading
import time

from mcp.server.mcpserver import Image, MCPServer

import heap
import control_sets
import input_sim
import integrity
import journal
import location_cache
import uia_inspect
import window_manager
from overlay import get_overlay
from screenshot import changed_bbox, find_content_bbox, grab_window, to_png


mcp = MCPServer(
    name="winauto-mcp",
    instructions=(
        "Control an attached Windows app with pywinauto UIA patterns, without "
        "moving the physical cursor, injecting keys, holding input, or raising "
        "the target. Flow: list_windows -> attach_window -> get_elements or "
        "capture_screen -> click_element / type_text / set_value -> read_element "
        "to verify. Prefer a unique automation_id; ambiguous names are refused. "
        "click(x,y) resolves an element inside the target's own UIA tree, even "
        "when covered; it is not a physical click. Selecting an edit via click "
        "only chooses the server's text target, without Windows focus. "
        "type_text appends to the end by default, independently of the real "
        "caret; replace=True replaces the entire value. Supply name or "
        "automation_id for every field in a form. read_element reads values "
        "and selection/toggle states; text and numeric writes verify readback. "
        "get_elements also lists control_steps. For one textbox, use control_set "
        "with one selector and steps click, type(text, replace=True), capture. "
        "It binds the original runtime ID, validates all capabilities first and "
        "returns only the control's cropped image plus its coordinate offset. "
        "Check ok/completed_steps before retrying a failed set; no rollback occurs. "
        "UIA providers can have their own focus/popup behavior. Canvas-only "
        "apps and unsupported patterns cannot be controlled in this backend. "
        "hover, coordinate drag, right/double/modifier clicks and keyboard "
        "shortcuts are refused. There is no real-input fallback, including "
        "force=True. keep_cursor and stop_if_user_types are compatibility "
        "arguments with no effect; no global input hooks are installed. "
        "All coordinates are client-relative, matching screenshots. "
        "For coordinates, take a fresh screenshot or locate_in_region and "
        "respect stale-target refusals. A screenshot crop has an offset and "
        "may be resized; do not eyeball its coordinates. run_steps validates "
        "all actions first and journals each step. Only an initial coordinate "
        "click before other mutations has a stale check, so keep scripts short and use check steps. "
        "wait_stable is explicit; take a fresh screenshot afterwards. "
        "history and replay_frame provide evidence; replay images are "
        "downscaled and must not be used for aiming. Reading an elevated "
        "window is allowed, but writes to higher-integrity targets are refused."
    ),
)
_tool_lock = threading.RLock()

_state = {
    "hwnd": None,
    # Process owning the attached window, read once at attach. Only the heap
    # tools need it, and they need it after the window may already be gone --
    # a snapshot taken to see what a closed screen left behind is exactly the
    # case where the hwnd is the least reliable thing to ask.
    "pid": None,
    # Parsed gcdump summaries by label, for heap_diff. The .gcdump files
    # themselves stay on disk in the journal session folder; only the per-type
    # counts are held here, which is all a comparison needs.
    "heap": {},
    "last_snapshot": None,
    # What the caller has actually looked at, newest last: one entry per view
    # with the rect it covered and the frame as it was at that moment. click()
    # checks its target against the newest view that covers it, to catch a
    # click aimed using an out-of-date picture of that part of the window.
    # A list rather than one frame because views can be partial: after
    # capture_region(toolbar), the toolbar has been re-checked and the rest of
    # the window has not, and collapsing those into "the caller has seen the
    # screen" is exactly the blindness this guard exists to prevent.
    "seen": [],
    # Frame captured by the journaling decorator immediately before the
    # current action tool ran; the staleness check reuses it instead of
    # grabbing the screen a second time.
    "pre_frame": None,
    # Frame a tool captured itself and handed to journal_frame(), used as the
    # journal's 'after' image so the screen isn't captured twice.
    "post_frame": None,
}


def _show_latest_interaction(hwnd, rect):
    if hwnd == _state["hwnd"] and window_manager.window_exists(hwnd):
        overlay = get_overlay()
        overlay.set_highlights([rect])
        overlay.track(hwnd)


input_sim.set_interaction_hook(_show_latest_interaction)


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


MAX_SEEN_VIEWS = 8


def _covers(outer, inner) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def _mark_seen(frame, rect=None) -> None:
    """Note that the caller has now been shown part of this frame, or has
    derived a coordinate from it. `rect` is the part actually looked at,
    defaulting to the whole frame.

    Only views that informed the caller's next decision count -- a capture
    taken purely for internal bookkeeping (a settle poll, a journal thumbnail)
    must NOT be marked, or the staleness check silently starts approving
    clicks aimed from a screen nobody looked at.

    The full frame is kept even for a partial view, because it is the pixel
    reference a later comparison needs; `rect` records how much of it the
    caller is entitled to have an opinion about.
    """
    rect = tuple(rect) if rect else (0, 0, *frame.size)
    views = [v for v in _state["seen"] if not _covers(rect, v["rect"])]
    views.append(
        {
            "rect": rect,
            "frame": frame,
            "t": time.monotonic(),
            "seq": journal.session_info()["records"] + 1,
        }
    )
    _state["seen"] = views[-MAX_SEEN_VIEWS:]


def _seen_view_at(x: int, y: int):
    """The most recent view containing the point (x, y), or None if the caller
    has not looked at that part of the window.

    Containment is tested against the point, not against the whole area the
    staleness check would like to compare: a caller who looked at a 30x12
    label did legitimately see the thing it is about to click, and demanding
    that the surrounding 40px also have been seen would refuse every click
    located by locate_in_region. The comparison area is intersected with the
    view instead, so the check covers as much as was actually looked at.
    """
    for view in reversed(_state["seen"]):
        rx1, ry1, rx2, ry2 = view["rect"]
        if rx1 <= x < rx2 and ry1 <= y < ry2:
            return view
    return None


# Tools whose effect is visual and worth a before/after frame in the journal.
# Everything else is journaled as text only: a window list or a UIA query
# doesn't change the screen, so a frame pair would cost two captures and show
# nothing. capture_screen/screenshot are excluded for a different reason
# -- they hand their own frame to journal_frame() rather than being captured
# twice.
_FRAME_TOOLS = frozenset(
    {"click", "click_element", "scroll", "type_text", "set_value"}
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
    current = _state["pre_frame"]
    if not _state["seen"] or current is None:
        return None  # nothing looked at yet, or the window couldn't be grabbed

    w, h = current.size
    region = (
        max(0, x - STALE_RADIUS),
        max(0, y - STALE_RADIUS),
        min(w, x + STALE_RADIUS),
        min(h, y + STALE_RADIUS),
    )
    if region[2] <= region[0] or region[3] <= region[1]:
        return None  # target is outside the client area; let the action fail on its own terms

    context = {"blocked": True, "performed": False, "action": what}

    def refuse(reason):
        context["reason"] = reason
        _mark_seen(current)
        return [json.dumps(context, ensure_ascii=False), Image(data=to_png(current), format="png")]

    view = _seen_view_at(x, y)
    if view is None:
        looked_at = [list(v["rect"]) for v in _state["seen"]]
        context["regions_you_have_looked_at"] = looked_at
        return refuse(
            f"NOT PERFORMED. The parts of this window you have looked at are {looked_at}, and "
            f"({x}, {y}) is in none of them -- so that coordinate comes from memory, not from "
            "anything you have seen this run. Attached is the whole window as it is right now."
        )

    seen, age = view["frame"], time.monotonic() - view["t"]
    context["seen_at_step"] = view["seq"]
    context["seen_age_s"] = round(age, 1)

    if seen.size != current.size:
        return refuse(
            f"the window was resized since you last looked ({seen.size[0]}x{seen.size[1]} "
            f"-> {current.size[0]}x{current.size[1]}), so every coordinate from that view "
            "is off. Attached is the window as it is now."
        )

    # Only compare what was actually looked at: outside the view, `seen` holds
    # pixels the caller was never shown, and judging them would report a change
    # the caller had no way to know about.
    vx1, vy1, vx2, vy2 = view["rect"]
    region = (
        max(region[0], vx1),
        max(region[1], vy1),
        min(region[2], vx2),
        min(region[3], vy2),
    )
    box = changed_bbox(seen, current, threshold=STALE_THRESHOLD, region=region)
    if box is None:
        return None

    context["changed_bbox"] = list(box)
    context["checked_region"] = list(region)
    return refuse(
        f"NOT PERFORMED. The screen within {STALE_RADIUS}px of ({x}, {y}) changed after the "
        f"frame you took your coordinates from ({age:.1f}s ago, step {view['seq']}), "
        "so that target is no longer what you saw. Attached is the window as it is right now: "
        "check whether your target is still there and still at these coordinates. Re-issue the "
        "action (it will go through now that you have looked), or re-locate the target with "
        "locate_in_region. If the app repaints this area continuously and the change is "
        "irrelevant, pass force=true. If it is still loading, wait_stable() first."
    )


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
    """Serialize shared session state and journal outcomes without taking input."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _tool_lock:
            name = fn.__name__
            wants_frames = name in _FRAME_TOOLS
            _state["pre_frame"] = _try_grab() if wants_frames else None
            _state["post_frame"] = None
            try:
                logged_args = dict(signature.bind(*args, **kwargs).arguments)
            except TypeError:
                logged_args = {"args": repr(args), "kwargs": repr(kwargs)}
            started = time.monotonic()
            error = None
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                error = exc
                result = f"{type(exc).__name__}: {exc}"
            after = _state["post_frame"]
            if after is None and wants_frames and error is None:
                after = _try_grab()
            hwnd = _state["hwnd"]
            journal.record(
                name, args=logged_args, ok=error is None,
                result=_result_text(result), ms=round((time.monotonic() - started) * 1000),
                window=window_manager.get_window_title(hwnd) if hwnd and window_manager.window_exists(hwnd) else None,
                before=_state["pre_frame"], after=after,
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
    """List visible top-level windows that can be attached to (title, process, hwnd).

    A window carrying "input_blocked": true runs at a higher Windows integrity
    level than this server -- typically it was started with "Run as
    administrator" and the server was not. You can still read such a window
    where supported by the app's provider. Mutations across that boundary
    are refused before any UIA action. Prefer another window, or run the
    server at the same integrity level as the target."""
    windows = window_manager.list_windows()
    return json.dumps(
        [
            {"hwnd": w["hwnd"], "title": w["title"], "process": w["process"]}
            | ({"input_blocked": True, "integrity": w["integrity"]} if w["input_blocked"] else {})
            for w in windows
        ],
        ensure_ascii=False,
    )


@tool
def attach_window(hwnd: int, take_control: bool = False, label: str = "") -> str:
    """Choose a background UIA target and show its non-activating green outline.
    label names the feature under test (single-line Unicode, max 120 characters).
    Never raise the target. take_control=True is unsupported."""
    if len(label) > 120 or any(char in label for char in "\r\n\x00"):
        raise ValueError("label must be a single line of at most 120 characters, without NUL")
    if not window_manager.window_exists(hwnd):
        raise ValueError(f"no such window: {hwnd}")
    if take_control:
        input_sim.unsupported("attach_window(take_control=True)")
    input_sim.reset_target()
    _state.update(hwnd=hwnd, pid=window_manager.get_pid(hwnd),
                  seen=[], heap={}, last_snapshot=None)
    title = window_manager.get_window_title(hwnd)
    session_label = f"{title} (hwnd={hwnd})"
    if label:
        session_label = f"[{label}] {session_label}"
    session = journal.start_session(session_label)
    level = integrity.window_level(hwnd)
    warning = ""
    if integrity.blocks_input(level):
        warning = "\nWARNING: " + integrity.why_blocked(level, title, window_manager.get_process_name(hwnd))
    overlay = get_overlay()
    overlay.set_highlights([])
    overlay.set_label(label)
    overlay.track(hwnd)
    return (f'attached to "{title}" (hwnd={hwnd}) -- pywinauto UIA; '
            f'green outline enabled; mouse and foreground unchanged; journal session {session}{warning}')


@tool
def detach_window() -> str:
    """Detach, clear the logical text target and hide any requested overlay."""
    input_sim.reset_target()
    _state.update(hwnd=None, pid=None, seen=[], heap={}, last_snapshot=None)
    get_overlay().untrack()
    return "detached"


@tool
def capture_screen():
    """Screenshot the attached window's client area, plus a text summary of
    interactive elements found via UI Automation (buttons, inputs, menus, ...).
    If the app renders to a canvas (e.g. a game) the UIA summary will be empty
    -- inspection still works, but canvas-only controls cannot be activated."""
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
def capture_region(x1: int, y1: int, x2: int, y2: int):
    """Screenshot ONE PART of the attached window instead of all of it, given
    a client-relative rect. Use this whenever you only need to check one thing
    -- did the dialog appear, did the value in that field update, is the button
    enabled now -- rather than re-reading the whole window. A crop is a much
    smaller image to send and to look at than a full window, so checking three
    small areas this way costs less than one full screenshot.

    Coordinates in the returned crop are NOT client coordinates: the crop's
    top-left is (x1, y1) of the window. The header says so and repeats the
    offset. As always, do not eyeball a click coordinate off the image --
    call locate_in_region on the same rect to get an exact point.

    Looking at a region counts as having looked at THAT REGION ONLY. click()
    tracks this per area, so a click inside the part you just checked goes
    through, while one aimed at a part of the window you have not looked at
    this run is refused -- checking the toolbar does not make a stale memory
    of the sidebar fresh again. Use screenshot() when you do need everything.
    """
    hwnd = _require_attached()
    frame = grab_window(hwnd)
    w, h = frame.size
    rect = (max(0, min(x1, w)), max(0, min(y1, h)), max(0, min(x2, w)), max(0, min(y2, h)))
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise ValueError(
            f"region [{x1},{y1},{x2},{y2}] is empty or entirely outside the "
            f"{w}x{h} client area -- need x2>x1, y2>y1, and some overlap with the window"
        )
    _mark_seen(frame, rect)
    # The journal keeps the whole frame, not the crop: its job is to answer
    # "what was going on at that step", and the part deliberately not looked
    # at is usually where the answer is.
    journal_frame(frame)
    header = json.dumps(
        {
            "region": list(rect),
            "crop_size": [rect[2] - rect[0], rect[3] - rect[1]],
            "coordinate_offset": [rect[0], rect[1]],
            "note": (
                "crop of the attached window. Its (0,0) is client "
                f"({rect[0]}, {rect[1]}) -- add that offset to anything you read here. "
                "Only this rect counts as looked at; clicks elsewhere are still "
                "checked against whenever you last saw that part."
            ),
        }
    )
    return [header, Image(data=to_png(frame.crop(rect)), format="png")]


@tool
def get_elements() -> str:
    """UI Automation element list for the attached window only (no screenshot),
    as JSON with name, automation_id, control_type, enabled, actions,
    control_steps and rect. control_steps lists capabilities for control_set.
    rect is client-relative, same space as capture_screen's image."""
    hwnd = _require_attached()
    return json.dumps(uia_inspect.get_elements(hwnd), ensure_ascii=False)


@tool
def read_element(name: str | None = None, automation_id: str | None = None) -> str:
    """Read a uniquely identified UIA element's value and selection/toggle state."""
    return json.dumps(input_sim.read_element(_require_attached(), name, automation_id),
                      ensure_ascii=False)


@tool
def control_set(steps: list[dict], name: str | None = None,
                automation_id: str | None = None, expected_control_type: str | None = None,
                delay_ms: int = 120):
    """Run 1–20 supported steps on ONE uniquely selected control.
    Obtain control_steps and control_type from get_elements. Steps use do=click,
    type (text, optional replace=False), and one required final capture. Example:
    [{"do":"click"},{"do":"type","text":"Alice","replace":true},{"do":"capture"}].
    No per-step selectors/coordinates. expected_control_type checks the actual
    type, e.g. EditControl, before action. Text click selects without focus;
    writes verify readback. All steps are validated before mutation, then bound
    to the original runtime ID. delay_ms is 0–3000 between mutations and capture.
    Return JSON report and ONE control-only PNG with region/crop_size/offset.
    Runtime errors stop with completed_steps and no image; no rollback or stale
    image fallback. Check report.ok before retrying completed writes.
    """
    report, frame, rect = control_sets.run(
        _require_attached(), steps, name, automation_id, expected_control_type, delay_ms)
    result = [json.dumps(report, ensure_ascii=False)]
    if frame is not None:
        _mark_seen(frame, rect)
        journal_frame(frame)
        result.append(Image(data=to_png(frame.crop(rect)), format="png"))
    return result


@tool
def set_value(value: float, name: str | None = None,
              automation_id: str | None = None) -> str:
    """Set a UIA numeric control via RangeValuePattern and verify readback."""
    return json.dumps(input_sim.set_value(_require_attached(), value, name, automation_id))


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
    """Resolve client coordinates in the attached UIA tree and activate the element.
    No physical click is sent. Right/double/modifier clicks are refused.
    Stale screenshots are guarded unless force=True. force only skips the
    stale guard; keep_cursor is ignored and cannot enable real input.
    Selecting an edit only chooses the logical text target, not Windows focus.
    """
    hwnd = _require_attached()
    if button not in ("left", "right"):
        raise ValueError("button must be 'left' or 'right'")
    if not force:
        blocked = _stale_block(x, y, f"click({x}, {y})")
        if blocked is not None:
            return blocked
    action = input_sim.click_in_window(hwnd, x, y, button=button, double=double, modifiers=modifiers,
                              keep_cursor=keep_cursor)
    mod_note = f" modifiers={modifiers}" if modifiers else ""
    return f"{action} at ({x}, {y}) via pywinauto UIA{mod_note}"


@tool
def click_element(name: str | None = None, button: str = "left",
                  double: bool = False, automation_id: str | None = None) -> str:
    """Activate one UIA element by unique name or automation_id.
    Names match exactly first, then by substring; ambiguity is refused.
    An edit becomes the logical text target without changing Windows focus.
    Right/double clicks are unsupported."""
    result = input_sim.click_element(_require_attached(), name, automation_id, button, double)
    return json.dumps(result, ensure_ascii=False)


@tool
def hover(x: int, y: int, dwell_ms: int = 700, force: bool = False):
    """Unsupported: a real hover needs the shared cursor. No input is sent."""
    _require_attached()
    input_sim.unsupported("hover")


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

    Actions here can return before the app finishes redrawing, which is well
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
    return json.dumps(_settle(hwnd, timeout, settle_ms, interval, threshold, region))


def _settle(hwnd, timeout=5.0, settle_ms=400, interval=0.12, threshold=10, region=None) -> dict:
    """The polling loop behind wait_stable(), shared with run_steps'
    {"do":"wait_stable"} step so a script waits exactly the way the tool does."""
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
                return {
                    "stable": True,
                    "waited_ms": round((now - started) * 1000),
                    "polls": polls,
                    "changed_during_wait": changes > 0,
                }
        else:
            steady_since = now
            changes += 1
            last_change = list(box)
        if now >= deadline:
            journal_frame(to_png(current))
            return {
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


@tool
def type_text(text: str, name: str | None = None,
              automation_id: str | None = None, replace: bool = False) -> str:
    """Write literal Unicode through ValuePattern, with verified readback.
    Appends to the END by default (not the physical caret); replace=True sets
    the whole value. Target by name/id, a logically selected edit, or the
    only editable control. No global keyboard or focus is used."""
    result = input_sim.type_text(text, hwnd=_require_attached(), name=name,
                                 automation_id=automation_id, replace=replace)
    return json.dumps(result, ensure_ascii=False)


@tool
def press_key(key: str) -> str:
    """Unsupported in background UIA. Activate a named button instead."""
    input_sim.press_key(key, hwnd=_require_attached())


@tool
def drag(x1: int, y1: int, x2: int, y2: int, button: str = "left",
         force: bool = False, keep_cursor: bool = False):
    """Unsupported coordinate gesture; use set_value for a UIA slider."""
    input_sim.drag_in_window(_require_attached(), x1, y1, x2, y2, button=button)


@tool
def scroll(x: int, y: int, clicks: int, keep_cursor: bool = False) -> str:
    """Scroll the target element/ancestor via UIA ScrollPattern.
    Positive clicks scroll up, negative down; each is one small provider-defined
    UIA scroll step. keep_cursor is ignored because no pointer is moved."""
    result = input_sim.scroll_in_window(_require_attached(), x, y, clicks)
    return f"{result}: {clicks} step(s) at ({x}, {y})"


MAX_SCRIPT_STEPS = 40
MAX_SCRIPT_WAIT_MS = 60_000

# What a step in run_steps may say, and which of its keys are required.
_STEP_KINDS = {
    "click": ("x", "y"),
    "drag": ("x1", "y1", "x2", "y2"),
    "scroll": ("x", "y", "clicks"),
    "type": ("text",),
    "key": ("key",),
    "hotkey": ("keys",),
    "click_element": (),
    "set_value": ("value",),
    "wait": ("ms",),
    "wait_stable": (),
    "capture": (),
    "check": (),
}


def _validate_steps(steps):
    """Check the whole script before any of it runs. A typo in step 7 must not
    be discovered with steps 1-6 already applied to a real app -- half a script
    leaves the window in a state nobody planned for, and the fix has to start
    by working out what actually happened."""
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list of {'do': ...} objects")
    if len(steps) > MAX_SCRIPT_STEPS:
        raise ValueError(
            f"{len(steps)} steps is over the {MAX_SCRIPT_STEPS}-step limit -- split it, and look "
            "at the screen in between; a script that long is running on assumptions by the end"
        )
    waited = 0
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise ValueError(f"step {i} is not an object: {step!r}")
        kind = step.get("do")
        if kind not in _STEP_KINDS:
            raise ValueError(
                f"step {i}: unknown action {kind!r} -- must be one of {sorted(_STEP_KINDS)}"
            )
        if kind in ("drag", "key", "hotkey"):
            input_sim.unsupported(f"step {i}: {kind}")
        if kind in ("click", "click_element"):
            input_sim._click_options(step.get("button", "left"), bool(step.get("double", False)),
                                     step.get("modifiers"))
        if kind in ("click_element", "set_value") and not (step.get("name") or step.get("automation_id")):
            raise ValueError(f"step {i}: provide name or automation_id")
        if kind == "type" and step.get("text") is not None and not isinstance(step["text"], str):
            raise ValueError(f"step {i}: text must be a string")
        if "replace" in step and not isinstance(step["replace"], bool):
            raise ValueError(f"step {i}: replace must be a boolean")
        for selector in ("name", "automation_id"):
            if step.get(selector) is not None and (
                not isinstance(step[selector], str) or not step[selector].strip()
            ):
                raise ValueError(f"step {i}: {selector} must be a non-empty string")
        missing = [k for k in _STEP_KINDS[kind] if step.get(k) is None]
        if missing:
            raise ValueError(f"step {i} ({kind}) is missing {missing}")
        if kind == "click" and step.get("button", "left") not in ("left", "right"):
            raise ValueError(f"step {i}: button must be 'left' or 'right'")
        if kind == "check" and step.get("expect", "changed") not in ("changed", "unchanged"):
            raise ValueError(f"step {i}: expect must be 'changed' or 'unchanged'")
        for field in ("x", "y", "clicks", "ms", "settle_ms", "threshold"):
            if field in step and (isinstance(step[field], bool) or not isinstance(step[field], int)):
                raise ValueError(f"step {i}: {field} must be an integer")
        if kind == "scroll" and not -100 <= step["clicks"] <= 100:
            raise ValueError(f"step {i}: clicks must be between -100 and 100")
        if "region" in step and step["region"] is not None:
            region = step["region"]
            if (not isinstance(region, list) or len(region) != 4
                    or any(isinstance(n, bool) or not isinstance(n, int) for n in region)
                    or region[2] <= region[0] or region[3] <= region[1]):
                raise ValueError(f"step {i}: region must be [x1,y1,x2,y2] with positive area")
        if kind == "set_value":
            value = step["value"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"step {i}: value must be a finite number")
        if kind == "wait":
            if step["ms"] < 0:
                raise ValueError(f"step {i}: ms must be >= 0")
            waited += int(step["ms"])
        if kind == "wait_stable":
            timeout = step.get("timeout", 5.0)
            if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                    or not math.isfinite(timeout) or timeout <= 0):
                raise ValueError(f"step {i}: timeout must be a finite number > 0")
            waited += int(timeout * 1000)
    if waited > MAX_SCRIPT_WAIT_MS:
        raise ValueError(
            f"the script asks to wait {waited}ms in total, over the {MAX_SCRIPT_WAIT_MS}ms limit "
            "-- a script that spends a minute waiting should be split so you can look at what it "
            "is waiting for"
        )


def _clamp_region(frame, region):
    w, h = frame.size
    x1, y1, x2, y2 = region
    rect = (max(0, min(x1, w)), max(0, min(y1, h)), max(0, min(x2, w)), max(0, min(y2, h)))
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise ValueError(f"region {list(region)} is empty or outside the {w}x{h} client area")
    return rect


@tool
def run_steps(steps: list[dict], delay_ms: int = 120, stop_on_error: bool = True,
              stop_if_user_types: bool = False):
    """Run up to 40 UIA actions, validating all steps before any action.
    Kinds: click, click_element, type, set_value, scroll, wait, wait_stable,
    capture, check. type and click_element accept name/automation_id; type
    accepts replace. Unsupported gestures/key steps reject the whole script.
    An initial coordinate click before other mutations is stale-guarded. Each executed step is
    journaled. stop_on_error=False continues runtime failures.
    stop_if_user_types is ignored: the background backend never watches keys.
    """
    hwnd = _require_attached()
    _validate_steps(steps)
    delay = max(0, min(int(delay_ms), 3000)) / 1000

    report, images = [], []
    frame = _try_grab()
    mark = frame  # reference for "check" steps: script start, then each checkpoint
    guard_armed = True
    stopped_at = None
    for i, step in enumerate(steps, 1):
        kind = step["do"]
        entry = {"step": i, "do": kind, "ok": True}
        started = time.monotonic()
        before = frame
        error = None
        try:
            if kind == "click":
                x, y = int(step["x"]), int(step["y"])
                if guard_armed and not step.get("force"):
                    _state["pre_frame"] = frame
                    blocked = _stale_block(x, y, f"click({x}, {y})")
                    _state["pre_frame"] = None
                    if blocked is not None:
                        entry.update(ok=False, result=json.loads(blocked[0])["reason"])
                        report.append(entry)
                        images.append(("blocked at step 1", blocked[1]))
                        stopped_at = i
                        break
                action = input_sim.click_in_window(
                    hwnd, x, y,
                    button=step.get("button", "left"),
                    double=bool(step.get("double", False)),
                    modifiers=step.get("modifiers"),
                    keep_cursor=bool(step.get("keep_cursor", False)),
                )
                guard_armed = False
                entry["result"] = f"{action} at ({x}, {y})"
            elif kind == "scroll":
                x, y, clicks = int(step["x"]), int(step["y"]), int(step["clicks"])
                input_sim.scroll_in_window(
                    hwnd, x, y, clicks, keep_cursor=bool(step.get("keep_cursor", False))
                )
                guard_armed = False
                entry["result"] = f"scrolled {clicks} at ({x}, {y})"
            elif kind == "type":
                entry["result"] = input_sim.type_text(str(step["text"]), hwnd=hwnd,
                                    name=step.get("name"), automation_id=step.get("automation_id"),
                                    replace=bool(step.get("replace", False)))
                guard_armed = False
            elif kind == "click_element":
                entry["result"] = input_sim.click_element(
                    hwnd, name=step.get("name"), automation_id=step.get("automation_id"),
                    button=step.get("button", "left"), double=bool(step.get("double", False)),
                )
                guard_armed = False
            elif kind == "set_value":
                entry["result"] = input_sim.set_value(
                    hwnd, step["value"], name=step.get("name"), automation_id=step.get("automation_id")
                )
                guard_armed = False
            elif kind == "wait":
                time.sleep(max(0, min(int(step["ms"]), MAX_SCRIPT_WAIT_MS)) / 1000)
                entry["result"] = f"waited {step['ms']}ms"
            elif kind == "wait_stable":
                entry["result"] = _settle(
                    hwnd,
                    timeout=float(step.get("timeout", 5.0)),
                    settle_ms=int(step.get("settle_ms", 400)),
                    threshold=int(step.get("threshold", 10)),
                    region=step.get("region"),
                )
            elif kind == "capture":
                shot = _try_grab() or frame
                if shot is None:
                    raise ValueError("could not capture the window")
                rect = _clamp_region(shot, step["region"]) if step.get("region") else (0, 0, *shot.size)
                _mark_seen(shot, rect)
                images.append((f"step {i}: region {list(rect)}", Image(data=to_png(shot.crop(rect)), format="png")))
                entry["result"] = f"captured {list(rect)} (image attached, offset {rect[0]},{rect[1]})"
                mark = shot
            elif kind == "check":
                now = _try_grab()
                if now is None or mark is None:
                    raise ValueError("could not capture the window to check it")
                rect = _clamp_region(now, step["region"]) if step.get("region") else None
                expect = step.get("expect", "changed")
                box = changed_bbox(mark, now, threshold=int(step.get("threshold", 10)), region=rect)
                where = list(rect) if rect else "the whole window"
                if expect == "changed" and box is None:
                    raise ValueError(
                        f"expected {where} to have changed since the previous checkpoint, but it is "
                        "pixel-identical -- the step before this one had no visible effect there"
                    )
                if expect == "unchanged" and box is not None:
                    raise ValueError(
                        f"expected {where} to be unchanged since the previous checkpoint, but it "
                        f"changed at {list(box)}"
                    )
                entry["result"] = f"{where} was {expect} as expected"
                mark = now
        except Exception as exc:  # noqa: BLE001 - reported per step, not raised
            error = exc
            entry.update(ok=False, result=f"{type(exc).__name__}: {exc}")

        if delay:
            time.sleep(delay)
        frame = _try_grab()
        entry["ms"] = round((time.monotonic() - started) * 1000)
        journal.record(
            f"script:{kind}",
            args={"step": i, **{k: v for k, v in step.items() if k != "do"}},
            ok=entry["ok"],
            result=entry.get("result"),
            ms=entry["ms"],
            window=window_manager.get_window_title(hwnd),
            before=before,
            after=frame,
        )
        report.append(entry)
        if error is not None and stop_on_error:
            stopped_at = i
            break

    if stopped_at is not None and not images:
        final = _try_grab()
        if final is not None:
            _mark_seen(final)
            images.append(("the window where the script stopped", Image(data=to_png(final), format="png")))

    summary = {
        "ok": stopped_at is None and all(s["ok"] for s in report),
        # Compatibility field: counts attempted steps, including failures.
        "performed": len(report),
        "of": len(steps),
        "stopped_at_step": stopped_at,
        "steps": report,
        "images": [label for label, _ in images],
        "note": (
            "Images follow in the order listed. Coordinates in a cropped capture are offset by "
            "its region's top-left. Only the regions captured here count as looked at; a later "
            "click elsewhere is still judged against whenever you last saw that part. Each step "
            "is in the journal as script:<action> with before/after frames."
        ),
    }
    return [json.dumps(summary, ensure_ascii=False), *[img for _, img in images]]


@tool
def release_control() -> str:
    """Hide an explicitly requested overlay. Background control takes no focus."""
    get_overlay().untrack()
    return "outline hidden; mouse, keyboard and foreground were never taken"


@tool
def keep_foreground(enabled: bool) -> str:
    """Compatibility tool. True is unsupported; False keeps background mode."""
    if enabled:
        input_sim.unsupported("keep_foreground(True)")
    return "pywinauto UIA background mode; foreground unchanged"


@tool
def keyboard_status() -> str:
    """Report background mode. No global input is blocked or monitored."""
    return json.dumps({
        "backend": "pywinauto-uia", "blocking": False, "watching": False,
        "blocking_enabled": False, "lease_remaining_s": 0,
        "latched_off_by_user": False, "human_key_events": None,
        "seconds_since_human_key": None, "last_releases": [],
    })


@tool
def release_keyboard(enable_blocking: bool = True) -> str:
    """Compatibility no-op: keyboard blocking cannot be enabled in this backend."""
    return "keyboard blocking is OFF; background UIA never holds or monitors input"


@tool
def hotkey(keys: list[str]) -> str:
    """Unsupported in background UIA. Activate a named command instead."""
    input_sim.press_keys(keys, hwnd=_require_attached())


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
    # check would reject a coordinate that was in fact freshly measured. Only
    # this region though: measuring a button tells the caller nothing about
    # the rest of the window, and claiming otherwise would make a later click
    # elsewhere look freshly informed when it is not.
    _mark_seen(frame, (x1, y1, x2, y2))
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
    _mark_seen(frame, (rx1, ry1, rx2, ry2))  # same reasoning as locate_in_region
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


def _require_pid() -> int:
    pid = _state["pid"]
    if pid is None:
        raise ValueError("no window attached -- call attach_window first")
    if not window_manager.process_alive(pid):
        raise ValueError(
            f"the attached window's process (pid {pid}) has exited -- "
            "there is no heap left to read"
        )
    return pid


def _heap_file(label: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40] or "snapshot"
    folder = journal.session_dir() or tempfile.gettempdir()
    return os.path.join(folder, f"heap_{safe}.gcdump")


@tool
def heap_snapshot(label: str) -> str:
    """Count the live objects on the attached .NET process's managed heap, and
    remember them under `label` for heap_diff. .NET only (WinForms, WPF,
    WinUI/Uno) -- there is no managed heap in a native app to read.

    This forces a full blocking garbage collection and counts only what
    SURVIVED it, so a type still here afterwards is genuinely still referenced
    by something. That is why this answers a leak question and the process's
    memory size does not: .NET does not hand heap segments back to Windows when
    objects die, so working-set can sit high with nothing leaking at all.

    Take one before the action and one after. But do NOT read a single
    before/after pair as a list of leaks -- measured on a process that was
    doing nothing but sleeping, two snapshots differed by 4,217 objects across
    255 types, because collecting a snapshot itself makes the runtime
    materialize reflection metadata. The signal that survives that noise is a
    count that keeps climbing across REPEATED cycles of the same action: run
    the open/close 5+ times, snapshot each round, and look for the type whose
    count goes up by the same amount every time."""
    pid = _require_pid()
    path = _heap_file(label)
    heap.collect(pid, path)
    parsed = heap.parse(heap.report(path))
    _state["heap"][label] = parsed
    return json.dumps(
        {
            "label": label,
            "pid": pid,
            "live_objects": parsed["total_objects"],
            "heap_bytes": parsed["total_bytes"],
            "distinct_types": len(parsed["types"]),
            "dump_file": path,
            "labels_held": sorted(_state["heap"]),
        }
    )


@tool
def heap_diff(before: str, after: str, top: int = 25) -> str:
    """Types that gained instances between two heap_snapshot labels, biggest
    gain first. Counts only -- the report's byte column is a per-object average
    per size bucket, so byte totals derived from it do not add up to the heap
    they describe; `approx_bytes_each` is offered to weigh a count and is not
    summed.

    Read `types_that_grew` before reading `grew`. Around 250 types growing is
    what two snapshots of an IDLE process look like, so that number is the
    noise floor of this measurement, not a finding. What is a finding: a type
    you recognize -- your page, your view model, your record class -- appearing
    here at all, and appearing again with a bigger delta every time the same
    action is repeated."""
    held = sorted(_state["heap"])
    for name in (before, after):
        if name not in _state["heap"]:
            raise ValueError(
                f'no heap snapshot labelled "{name}" -- '
                f"held right now: {held or 'none'}"
            )
    result = heap.diff(_state["heap"][before], _state["heap"][after], top=top)
    result["noise_floor_note"] = (
        "~255 types / ~4,200 objects is the measured drift between two snapshots "
        "of a process doing nothing. Trust repetition, not one pair."
    )
    return json.dumps(result)


@tool
def highlight(rects: list[list[int]]) -> str:
    """Draw red debug boxes on the overlay at the given [x1,y1,x2,y2] rects
    (client-relative). Pass an empty list to clear. Purely visual, for the
    human watching the screen -- has no effect on click/type."""
    hwnd = _require_attached()
    # Asking for a box drawn is an explicit request to see the overlay, so it
    # starts tracking even if no input has been sent yet -- otherwise the
    # boxes would go to a hidden overlay and the call would silently do
    # nothing. Clearing with an empty list also puts the overlay away.
    overlay = get_overlay()
    if rects:
        overlay.track(hwnd)
        overlay.set_highlights([tuple(r) for r in rects])
    else:
        overlay.untrack()
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
