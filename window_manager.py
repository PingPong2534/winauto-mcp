"""Enumerate and attach to top-level Windows windows."""

from contextlib import contextmanager
import ctypes

import win32con
import win32gui
import win32process
import psutil

import integrity

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_set_window_long = getattr(_user32, "SetWindowLongPtrW", _user32.SetWindowLongW)
_set_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
_set_window_long.restype = ctypes.c_ssize_t


def _set_ex_style(hwnd, style):
    # ctypes releases the GIL while Windows sends WM_STYLECHANGING/CHANGED.
    # A same-process target may run a Python window procedure on another thread.
    ctypes.set_last_error(0)
    previous = _set_window_long(hwnd, win32con.GWL_EXSTYLE, style)
    error = ctypes.get_last_error()
    if not previous and error:
        raise ctypes.WinError(error)


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
        # Asked here so a caller can avoid an undriveable window before
        # attaching to it, rather than finding out at the first click. Costs
        # ~3 us per window, measured -- cheap enough not to be worth caching.
        level = integrity.process_level(pid)
        windows.append(
            {
                "hwnd": hwnd,
                "title": title,
                "process": process_name,
                "pid": pid,
                "rect": list(rect),
                "integrity": integrity.level_name(level),
                "input_blocked": integrity.blocks_input(level),
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


def ensure_input_allowed(hwnd):
    """Check target integrity without changing the foreground or cursor."""
    level = integrity.window_level(hwnd)
    if integrity.blocks_input(level):
        raise integrity.InputBlocked(
            integrity.why_blocked(level, get_window_title(hwnd), get_process_name(hwnd))
        )


@contextmanager
def background_action(hwnd):
    """Suppress target activation during UIA mutations made from another app.

    Win32 UIA providers can activate even when the client uses only patterns.
    Restore only the style bit we added, including on failure. Never move focus
    back: the person may have switched windows during the action.
    """
    if win32gui.GetForegroundWindow() == hwnd:
        yield
        return
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    added = not (style & win32con.WS_EX_NOACTIVATE)
    if added:
        _set_ex_style(hwnd, style | win32con.WS_EX_NOACTIVATE)
    try:
        yield
    finally:
        if added and window_exists(hwnd):
            current = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            _set_ex_style(hwnd, current & ~win32con.WS_EX_NOACTIVATE)
