"""Enumerate and attach to top-level Windows windows."""

import win32api
import win32con
import win32gui
import win32process
import psutil


def list_windows():
    """Return visible, non-minimized top-level windows with a title."""
    windows = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.IsIconic(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title.strip():
            return True
        rect = win32gui.GetWindowRect(hwnd)
        if rect[2] - rect[0] <= 0 or rect[3] - rect[1] <= 0:
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            process_name = psutil.Process(pid).name()
        except psutil.Error:
            process_name = "?"
        windows.append(
            {
                "hwnd": hwnd,
                "title": title,
                "process": process_name,
                "pid": pid,
                "rect": list(rect),
            }
        )
        return True

    win32gui.EnumWindows(callback, None)
    return windows


def window_exists(hwnd):
    return bool(win32gui.IsWindow(hwnd))


def get_window_title(hwnd):
    return win32gui.GetWindowText(hwnd)


def get_process_name(hwnd):
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    try:
        return psutil.Process(pid).name()
    except psutil.Error:
        return "?"


def get_pid(hwnd):
    """The process owning the window. Stable for the window's lifetime, so it
    is read once at attach rather than on every call that needs it."""
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return pid


def process_alive(pid):
    """Whether a pid taken earlier still refers to a running process. Asked
    before reading a heap, so that "the app exited" is reported as itself
    rather than as whatever the heap tool says when it cannot attach."""
    return psutil.pid_exists(pid)


def get_client_size(hwnd):
    """Client area (width, height) in pixels -- used as part of the location
    cache key, since a resized window invalidates cached coordinates."""
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    return (right - left, bottom - top)


def get_client_rect_screen(hwnd):
    """Client area rect (excludes title bar/borders), in screen coordinates."""
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    left, top = win32gui.ClientToScreen(hwnd, (left, top))
    right, bottom = win32gui.ClientToScreen(hwnd, (right, bottom))
    return (left, top, right, bottom)


# The window the person was using before automation pulled focus away, so it
# can be handed back. Only a window we did not raise ourselves is remembered.
# "raised" is the window we put in front instead, so the automatic hand-back
# can tell "we are still where we left off" from "the person has moved on".
_displaced = {"hwnd": None, "raised": None}


def _force_foreground(hwnd) -> None:
    try:
        win32gui.SetForegroundWindow(hwnd)
    except win32gui.error:
        pass
    if win32gui.GetForegroundWindow() == hwnd:
        return

    # Windows' foreground-lock policy silently ignores SetForegroundWindow from
    # a process that isn't itself foreground (e.g. this server called from an
    # MCP client, or any automated/background caller). Temporarily attaching
    # our input queue to the current foreground window's thread lifts that
    # restriction -- a well-known, documented workaround for this exact case.
    cur_thread = win32api.GetCurrentThreadId()
    fg_hwnd = win32gui.GetForegroundWindow()
    fg_thread = win32process.GetWindowThreadProcessId(fg_hwnd)[0] if fg_hwnd else 0
    attached = False
    try:
        if fg_thread and fg_thread != cur_thread:
            attached = win32process.AttachThreadInput(cur_thread, fg_thread, True)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except win32gui.error:
        pass
    finally:
        if attached:
            win32process.AttachThreadInput(cur_thread, fg_thread, False)


# Called with the hwnd the moment automation actually takes the desktop over,
# i.e. right before input. Kept as a hook rather than an import so this module
# stays free of the overlay (which imports this one), and so "taking control"
# has exactly one definition instead of being re-decided at each input tool.
_control_hook = {"fn": None}


def set_control_hook(fn) -> None:
    _control_hook["fn"] = fn


def _took_control(hwnd) -> None:
    fn = _control_hook["fn"]
    if fn is None:
        return
    try:
        fn(hwnd)
    except Exception:  # noqa: BLE001 - a cosmetic hook must never fail an action
        pass


def bring_to_foreground(hwnd):
    # Before the early-out below: we are about to send input either way, so
    # this is the moment control is taken, whether or not a raise is needed.
    _took_control(hwnd)
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    previous = win32gui.GetForegroundWindow()
    _displaced["raised"] = hwnd
    if previous == hwnd:
        return
    if previous:
        _displaced["hwnd"] = previous
    _force_foreground(hwnd)


def restore_foreground():
    """Put back whatever window was in front before automation took over, and
    forget it. Returns its title, or None if there is nothing to give back.

    Unconditional: the caller has said the interaction is over. The version
    that runs by itself after every action is hand_back_foreground() below,
    which refuses in the cases this one doesn't check.
    """
    hwnd = _displaced["hwnd"]
    _displaced["hwnd"] = None
    _displaced["raised"] = None
    if not hwnd or not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
        return None
    _force_foreground(hwnd)
    return win32gui.GetWindowText(hwnd)


# Classes Windows itself uses for menus and for a dropped-down combo box. A
# window of one of these being visible anywhere means someone has something
# open that a focus change would dismiss.
_MENU_CLASSES = ("#32768", "ComboLBox")


def a_menu_is_open() -> bool:
    """True if a menu, system menu or dropdown is currently open.

    Asks one question: is a window of a menu class visible anywhere? Measured
    against a real system menu (Alt+Space) on a window created for the test:
    the menu window appears when it opens and is gone within 500 ms of Escape.

    GUITHREADINFO's GUI_INMENUMODE flags were tried first and REJECTED, on
    measurement rather than taste. They are set correctly when a menu opens
    (flags 0xc) but never clear: sampled every 500 ms for 3 s after Escape,
    with the menu window long gone, the flags still read 0xc, and hwndMenuOwner
    still pointed at the window. Menu mode is sticky per thread. Since this
    decides whether the person gets their window back, a signal that latches
    True forever would silently disable the hand-back for the rest of the
    session the first time any menu was opened -- exactly the class of bug
    where nothing looks broken and a promised behaviour just stops happening.

    Machine-wide on purpose. A menu open in some *other* app is very likely
    the person's own, and declining to yank the foreground while they are
    using a menu is the behaviour wanted anyway.

    KNOWN BLIND SPOT, measured, not assumed: this sees menus that *Windows*
    owns. A menu drawn by the app itself -- XAML/WinUI, Electron, Qt, a game's
    own UI -- is just a rectangle painted inside the window, and no window of
    a menu class ever exists. Windows 11 Notepad's own File menu is invisible
    to it. So this makes the automatic hand-back safe for classic menus and no
    worse than before for modern ones; it is not a guarantee. keep_foreground()
    is the escape hatch for the apps it cannot see.
    """
    found = []

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) in _MENU_CLASSES:
            found.append(hwnd)
        return True

    try:
        win32gui.EnumWindows(visit, None)
    except win32gui.error:
        return False
    return bool(found)


def visible_top_levels(exclude=()):
    """Every visible top-level window, as {hwnd: (class, rect)}.

    Snapshotted either side of a hover so that what the hover *raised* can be
    named by subtraction, rather than by keeping a list of the classes popups
    are allowed to have. A Win32 tooltip is `tooltips_class32`, a classic menu
    is `#32768`, a WinUI flyout is neither -- and the point of hovering is
    usually to find out which of those an unfamiliar app uses.

    `exclude` is a set of handles to leave out -- in practice the green
    outline, which is a decoration of ours and not something the app popped up.
    Handles, not "windows belonging to this process": the tests drive hover
    against a window they create themselves, and excluding our own process
    would blind the only check that the reporting works at all.
    """
    exclude = set(exclude)
    found = {}

    def visit(hwnd, _):
        if hwnd in exclude or not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            rect = win32gui.GetWindowRect(hwnd)
            if rect[2] > rect[0] and rect[3] > rect[1]:
                found[hwnd] = (win32gui.GetClassName(hwnd), rect)
        except win32gui.error:
            pass
        return True

    try:
        win32gui.EnumWindows(visit, None)
    except win32gui.error:
        pass
    return found


def hand_back_foreground():
    """Give the person their window back now that an action has finished.

    Returns (title, reason). `title` is the window handed back to, or None if
    nothing was, in which case `reason` says why. Unlike restore_foreground()
    this is called automatically, so it has to refuse in cases the explicit
    version doesn't:

    - **A menu is open.** It closes the instant its owner loses focus, which
      would undo the click that opened it. The displaced window is *not*
      forgotten, so a later hand-back still works.
    - **We are not the one in front.** If something else took the foreground
      after we did, the person has already moved on; yanking focus to a window
      they left would be the very rudeness this exists to prevent.

    An open menu also *blocks* SetForegroundWindow outright -- measured: a
    stray window with its system menu up could not be displaced at all -- so
    the first case is a correctness matter, not only a courtesy.
    """
    hwnd = _displaced["hwnd"]
    if not hwnd:
        return None, "nothing to hand back"

    raised = _displaced["raised"]
    if a_menu_is_open():
        return None, "a menu is open"

    current = win32gui.GetForegroundWindow()
    if raised and current and current != raised:
        _displaced["hwnd"] = None
        _displaced["raised"] = None
        return None, "the person is somewhere else already"

    if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
        _displaced["hwnd"] = None
        return None, "the window is gone"

    _displaced["hwnd"] = None
    _displaced["raised"] = None
    _force_foreground(hwnd)
    return win32gui.GetWindowText(hwnd), "handed back"
