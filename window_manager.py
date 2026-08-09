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


def bring_to_foreground(hwnd):
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    if win32gui.GetForegroundWindow() == hwnd:
        return
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
