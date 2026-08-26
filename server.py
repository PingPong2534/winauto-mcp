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


def _show_outline(hwnd) -> None:
    """The green outline means "this window is being driven right now", not
    "this window is bookmarked" -- so it appears when control is taken and is
    removed by release_control()/detach_window(). Tracking only while driving
    is also the only time its repainting is worth anything to anyone."""
    if hwnd == _state["hwnd"]:
        get_overlay().track(hwnd)


window_manager.set_control_hook(_show_outline)

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
        "Do not pay for the whole window when you only need part of it. To "
        "re-check one thing -- did the dialog appear, did that field update -- "
        "call capture_region(x1,y1,x2,y2) instead of screenshot(): a crop is a "
        "fraction of the size to send and to read. Looking at a region counts "
        "as having looked at THAT REGION, so a click inside it goes through "
        "while one aimed somewhere you have not looked this run is still "
        "refused; that is the point, not an obstacle.\n"
        "Once you know an app, stop paying a round trip per click. run_steps() "
        "performs a list of actions in order with a pause between them, and can "
        "return small crops from the middle of the run. But only the FIRST step "
        "is guarded against a stale coordinate -- the rest act on a screen the "
        "script itself changed, which you have not seen. So script only what you "
        "have watched the app do before, keep scripts short, and put "
        "{\"do\":\"check\",\"region\":[...],\"expect\":\"changed\"} after any step the "
        "rest depends on, so a wrong prediction stops the run instead of "
        "carrying it into a state you did not plan for.\n"
        "The mouse, keyboard and desktop belong to a person who is probably "
        "still using them. attach_window() does NOT take the desktop -- it "
        "only picks which window the other tools mean, and leaves it wherever "
        "it was. Reading the window (screenshot, capture_region, wait_stable, "
        "locate_in_region, diff_since_snapshot) works while it sits behind "
        "other windows, so do not raise it just to look. The window comes to "
        "the front, and the green outline appears, by itself the first time "
        "you send input -- you never have to ask for that. When you finish a "
        "piece of work and are about to think, report or wait, call "
        "release_control() to hand the foreground back -- but not in the "
        "middle of one interaction, since an open menu closes when its window "
        "loses focus."
    ),
)

_state = {
    "hwnd": None,
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
def attach_window(hwnd: int, take_control: bool = False) -> str:
    """Attach to a window by hwnd (from list_windows). This only chooses which
    window the other tools act on -- it does NOT take the desktop over. The
    window is left exactly where it was, in front or behind, and no outline is
    drawn, so attaching costs the person at the keyboard nothing.

    You can already read the window from here: screenshot, capture_region,
    get_elements, wait_stable and locate_in_region all work while it is
    covered. The window is raised and the green outline appears by itself the
    first time you actually send input, because that is the first moment input
    requires it -- and it goes away again on release_control().

    Pass take_control=true only if you want it raised immediately, e.g. so a
    person can watch which window is about to be driven."""
    if not window_manager.window_exists(hwnd):
        raise ValueError(f"no such window: {hwnd}")
    _state["hwnd"] = hwnd
    _state["seen"] = []
    if take_control:
        window_manager.bring_to_foreground(hwnd)  # the hook draws the outline
    title = window_manager.get_window_title(hwnd)
    # Attaching is the start of an automation run, so it starts a fresh
    # journal -- history() should describe this run, not trail off into the
    # previous app's steps.
    session = journal.start_session(f"{title} (hwnd={hwnd})")
    how = "in front, outline showing" if take_control else "left as it was; reading works from behind"
    return f'attached to "{title}" (hwnd={hwnd}) -- {how}; journal session {session}'


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
    "click_element": ("name",),
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
        missing = [k for k in _STEP_KINDS[kind] if step.get(k) is None]
        if missing:
            raise ValueError(f"step {i} ({kind}) is missing {missing}")
        if kind == "click" and step.get("button", "left") not in ("left", "right"):
            raise ValueError(f"step {i}: button must be 'left' or 'right'")
        if kind == "check" and step.get("expect", "changed") not in ("changed", "unchanged"):
            raise ValueError(f"step {i}: expect must be 'changed' or 'unchanged'")
        if kind == "wait":
            waited += int(step["ms"])
        if kind == "wait_stable":
            waited += int(float(step.get("timeout", 5.0)) * 1000)
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
def run_steps(steps: list[dict], delay_ms: int = 120, stop_on_error: bool = True):
    """Run several actions in one call, in order, with a pause between them --
    instead of one tool call per click. Use it once you know an app well enough
    to predict what the next few steps are: opening a menu and picking an item,
    filling three fields and pressing Enter, a keyboard chord followed by a
    dialog. Each step is journaled with its own before/after frame, so a script
    that goes wrong is still reconstructable with history()/replay_frame().

    `steps` is a list of objects, each with "do" plus that action's arguments:
      {"do":"click","x":100,"y":200,"button":"left","double":false,"modifiers":["ctrl"],"keep_cursor":false}
      {"do":"drag","x1":..,"y1":..,"x2":..,"y2":..,"button":"left"}
      {"do":"scroll","x":..,"y":..,"clicks":-3}
      {"do":"type","text":"hello"}
      {"do":"key","key":"enter"}
      {"do":"hotkey","keys":["ctrl","s"]}
      {"do":"click_element","name":"Save"}
      {"do":"wait","ms":500}
      {"do":"wait_stable","timeout":5.0,"settle_ms":400,"region":[x1,y1,x2,y2]}
      {"do":"capture","region":[x1,y1,x2,y2]}   -- region optional; omit for the whole window
      {"do":"check","region":[x1,y1,x2,y2],"expect":"changed"}   -- "changed" or "unchanged"

    A "capture" step returns an image, so you can watch a couple of points in
    the script without stopping it. Keep those to small regions -- that is the
    entire saving over doing it step by step.

    A "check" step is how a script fails early instead of carrying on into a
    state you did not plan for: it compares the region against how it looked at
    the previous checkpoint (script start, or the last capture/check) and stops
    the script if the expectation does not hold. Put one after any step whose
    success the rest of the script depends on -- a menu that must have opened,
    a dialog that must have closed.

    THE STALENESS CHECK ONLY GUARDS THE FIRST STEP. It cannot guard the rest:
    step 2 acts on a screen that step 1 deliberately changed, and there is no
    frame you have seen of it. So the coordinates in steps 2..n are your
    prediction of what the app will do, which is exactly the assumption this
    server otherwise refuses to make on your behalf. Script only what you have
    seen the app do before, keep scripts short, and use "check" steps to make
    a wrong prediction stop the run rather than continue it.

    Stops at the first failing step unless stop_on_error=false, and returns a
    per-step report plus, if it stopped early, the window as it looks now.
    `delay_ms` pauses between steps so the app can react.
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
                input_sim.click_in_window(
                    hwnd, x, y,
                    button=step.get("button", "left"),
                    double=bool(step.get("double", False)),
                    modifiers=step.get("modifiers"),
                    keep_cursor=bool(step.get("keep_cursor", False)),
                )
                guard_armed = False
                entry["result"] = f"clicked ({x}, {y})"
            elif kind == "drag":
                x1, y1, x2, y2 = (int(step[k]) for k in ("x1", "y1", "x2", "y2"))
                if guard_armed and not step.get("force"):
                    _state["pre_frame"] = frame
                    blocked = _stale_block(x1, y1, f"drag({x1}, {y1}) -> ({x2}, {y2})")
                    _state["pre_frame"] = None
                    if blocked is not None:
                        entry.update(ok=False, result=json.loads(blocked[0])["reason"])
                        report.append(entry)
                        images.append(("blocked at step 1", blocked[1]))
                        stopped_at = i
                        break
                input_sim.drag_in_window(
                    hwnd, x1, y1, x2, y2,
                    button=step.get("button", "left"),
                    keep_cursor=bool(step.get("keep_cursor", False)),
                )
                guard_armed = False
                entry["result"] = f"dragged ({x1}, {y1}) -> ({x2}, {y2})"
            elif kind == "scroll":
                x, y, clicks = int(step["x"]), int(step["y"]), int(step["clicks"])
                input_sim.scroll_in_window(
                    hwnd, x, y, clicks, keep_cursor=bool(step.get("keep_cursor", False))
                )
                guard_armed = False
                entry["result"] = f"scrolled {clicks} at ({x}, {y})"
            elif kind == "type":
                input_sim.type_text(str(step["text"]), hwnd=hwnd)
                guard_armed = False
                entry["result"] = f"typed {len(str(step['text']))} characters"
            elif kind == "key":
                input_sim.press_key(str(step["key"]), hwnd=hwnd)
                guard_armed = False
                entry["result"] = f"pressed {step['key']}"
            elif kind == "hotkey":
                input_sim.press_keys(list(step["keys"]), hwnd=hwnd)
                guard_armed = False
                entry["result"] = f"pressed chord {'+'.join(step['keys'])}"
            elif kind == "click_element":
                el, count = _find_element(hwnd, str(step["name"]))
                if el is None:
                    raise ValueError(f'no element matching "{step["name"]}"')
                ex1, ey1, ex2, ey2 = el["rect"]
                cx, cy = (ex1 + ex2) // 2, (ey1 + ey2) // 2
                input_sim.click_in_window(
                    hwnd, cx, cy,
                    button=step.get("button", "left"),
                    double=bool(step.get("double", False)),
                )
                guard_armed = False
                entry["result"] = f'clicked element "{el["name"]}" at ({cx}, {cy})'
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
    """Give the desktop back: put whatever window the person was using before
    this run took focus back in front. Call it when you have finished a piece
    of work and are about to think, report, or wait -- not between two steps
    of one interaction, because a menu or dropdown you just opened closes the
    moment its window loses focus.

    Nothing else is lost by calling it. The window stays attached, and reading
    it (screenshot, wait_stable, locate_in_region, diff_since_snapshot) does
    not need it in front -- only sending input does, and the next action
    raises it again by itself. The tracking outline is removed too, and comes
    back with the next action."""
    get_overlay().untrack()
    title = window_manager.restore_foreground()
    if title is None:
        return "nothing to give back -- no window was displaced, or it has since closed"
    return f'foreground returned to "{title}"; outline hidden; reading the attached window still works'


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
