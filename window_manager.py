"""Enumerate and attach to top-level Windows windows."""

import win32gui
import win32process
import psutil

import integrity


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
