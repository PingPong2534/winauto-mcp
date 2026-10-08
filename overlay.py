"""A click-through, non-activating Windows outline for the attached client area.

The native window is created with its input styles already set. State updates
are queued to its dedicated message-pump thread; no caller touches UI objects.
"""

import ctypes
from contextlib import contextmanager
import queue
import threading

import win32api
import win32con
import win32gui

from window_manager import get_client_rect_screen, window_exists

BORDER_COLOR = "#00FF66"
HIGHLIGHT_COLOR = "#FF3355"
LABEL_BG_COLOR = "#153026"


def _color(value):
    red, green, blue = (int(value[i:i + 2], 16) for i in (1, 3, 5))
    return red | (green << 8) | (blue << 16)


class Overlay:
    def __init__(self, poll_ms=150):
        self._poll_ms = poll_ms
        self._cmd_q = queue.Queue()
        self._tracked_hwnd = None
        self._highlights = []
        self._label = ""
        self._drawn = None
        self._suspended = 0
        self._hwnd = None
        self._label_hwnd = None
        self._error = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=3):
            raise RuntimeError("outline window did not start")
        if self._error:
            raise RuntimeError("outline window could not start") from self._error

    @property
    def hwnd(self):
        """The outline's own HWND, for diagnostics and window enumeration."""
        return self._hwnd

    @property
    def label_hwnd(self):
        """The separate, click-through feature label outside the frame."""
        return self._label_hwnd

    def _run(self):
        name = f"winauto_overlay_{id(self)}"
        registered = False
        try:
            wc = win32gui.WNDCLASS()
            wc.lpszClassName = name
            wc.lpfnWndProc = self._window_proc
            win32gui.RegisterClass(wc)
            registered = True
            style = (win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
                     | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOPMOST
                     | win32con.WS_EX_TOOLWINDOW)
            self._hwnd = win32gui.CreateWindowEx(
                style, name, "", win32con.WS_POPUP, 0, 0, 1, 1, 0, 0, 0, None)
            self._label_hwnd = win32gui.CreateWindowEx(
                style, name, "", win32con.WS_POPUP, 0, 0, 1, 1, 0, 0, 0, None)
            for hwnd in (self.hwnd, self.label_hwnd):
                win32gui.SetLayeredWindowAttributes(hwnd, 0, 255, win32con.LWA_COLORKEY)
            if not ctypes.windll.user32.SetTimer(ctypes.c_void_p(self.hwnd), 1, self._poll_ms, None):
                raise ctypes.WinError()
            self._ready.set()
            win32gui.PumpMessages()
        except Exception as exc:
            self._error = exc
        finally:
            self._ready.set()
            if self.label_hwnd and win32gui.IsWindow(self.label_hwnd):
                win32gui.DestroyWindow(self.label_hwnd)
            if self.hwnd and win32gui.IsWindow(self.hwnd):
                win32gui.DestroyWindow(self.hwnd)
            if registered:
                win32gui.UnregisterClass(name, None)

    def _window_proc(self, hwnd, msg, wp, lp):
        if msg == win32con.WM_TIMER:
            self._tick()
            return 0
        if msg == win32con.WM_PAINT:
            self._paint(hwnd)
            return 0
        if msg == win32con.WM_ERASEBKGND:
            return 1
        if msg == win32con.WM_MOUSEACTIVATE:
            return win32con.MA_NOACTIVATE
        if msg == win32con.WM_DESTROY:
            if hwnd == self.hwnd:
                ctypes.windll.user32.KillTimer(ctypes.c_void_p(hwnd), 1)
                win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wp, lp)

    def _hide(self):
        self._drawn = None
        win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)
        win32gui.ShowWindow(self.label_hwnd, win32con.SW_HIDE)

    def _tick(self):
        try:
            while True:
                cmd, data = self._cmd_q.get_nowait()
                if cmd == "track":
                    self._tracked_hwnd = data
                    self._drawn = None
                elif cmd == "untrack":
                    self._tracked_hwnd = None
                    self._highlights = []
                    self._hide()
                elif cmd == "highlights":
                    self._highlights = data
                elif cmd == "label":
                    self._label = data
                elif cmd == "suspend":
                    self._suspended += 1
                    self._hide()
                    data.set()
                elif cmd == "resume":
                    self._suspended -= 1
                elif cmd == "stop":
                    win32gui.DestroyWindow(self.hwnd)
                    return
        except queue.Empty:
            pass

        if self._tracked_hwnd is not None and not self._suspended:
            if not window_exists(self._tracked_hwnd):
                self._tracked_hwnd = None
                self._hide()
            elif win32gui.IsIconic(self._tracked_hwnd) or not win32gui.IsWindowVisible(self._tracked_hwnd):
                self._hide()
            else:
                try:
                    self._redraw(get_client_rect_screen(self._tracked_hwnd))
                except win32gui.error:
                    self._hide()

    def _redraw(self, rect):
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            self._hide()
            return
        state = (rect, tuple(self._highlights), self._label)
        if state == self._drawn:
            return
        win32gui.SetWindowPos(
            self.hwnd, win32con.HWND_TOPMOST, left, top, width, height,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
        )
        self._drawn = state
        win32gui.InvalidateRect(self.hwnd, None, False)
        win32gui.UpdateWindow(self.hwnd)
        self._position_label(rect)

    @contextmanager
    def _label_font(self, dc):
        dpi = ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(self.hwnd)) or 96
        spec = win32gui.LOGFONT()
        spec.lfFaceName = "Segoe UI"
        spec.lfHeight = -round(16 * dpi / 96)
        font = win32gui.CreateFontIndirect(spec)
        old_font = win32gui.SelectObject(dc, font)
        try:
            yield
        finally:
            win32gui.SelectObject(dc, old_font)
            win32gui.DeleteObject(font)

    def _position_label(self, rect):
        if not self._label:
            win32gui.ShowWindow(self.label_hwnd, win32con.SW_HIDE)
            return
        monitor = win32api.GetMonitorInfo(win32api.MonitorFromRect(
            rect, win32con.MONITOR_DEFAULTTONEAREST))["Monitor"]
        ml, mt, mr, mb = monitor
        left, top, right, bottom = rect
        dc = win32gui.GetDC(self.label_hwnd)
        try:
            with self._label_font(dc):
                text_width, text_height = win32gui.GetTextExtentPoint32(dc, self._label)
        finally:
            win32gui.ReleaseDC(self.label_hwnd, dc)
        width = min(text_width + 16, max(32, right - left - 16), mr - ml)
        height = text_height + 8
        x = max(ml, min(left + 8, mr - width))
        y = max(mt, min(top, mb - height))
        # Every candidate is outside the frame; never clamp it into the app.
        candidates = ((x, top - height - 4), (x, bottom + 4),
                      (right + 4, y), (left - width - 4, y))
        for x, y in candidates:
            if ml <= x and mt <= y and x + width <= mr and y + height <= mb:
                win32gui.SetWindowPos(
                    self.label_hwnd, win32con.HWND_TOPMOST, x, y, width, height,
                    win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
                win32gui.InvalidateRect(self.label_hwnd, None, False)
                win32gui.UpdateWindow(self.label_hwnd)
                return
        win32gui.ShowWindow(self.label_hwnd, win32con.SW_HIDE)

    def _paint(self, hwnd):
        dc, paint = win32gui.BeginPaint(hwnd)
        try:
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            win32gui.FillRect(dc, (left, top, right, bottom),
                              win32gui.GetStockObject(win32con.BLACK_BRUSH))
            if self._drawn is None:
                return
            if hwnd == self.label_hwnd:
                self._paint_label(dc, self._drawn[2], right, bottom)
                return
            brush = win32gui.SelectObject(dc, win32gui.GetStockObject(win32con.NULL_BRUSH))
            try:
                boxes = [(BORDER_COLOR, 3, (1, 1, right - 1, bottom - 1))]
                boxes.extend((HIGHLIGHT_COLOR, 2, rect) for rect in self._drawn[1])
                for color, width, rect in boxes:
                    pen = win32gui.CreatePen(win32con.PS_SOLID, width, _color(color))
                    old_pen = win32gui.SelectObject(dc, pen)
                    try:
                        win32gui.Rectangle(dc, *rect)
                    finally:
                        win32gui.SelectObject(dc, old_pen)
                        win32gui.DeleteObject(pen)
            finally:
                win32gui.SelectObject(dc, brush)
        finally:
            win32gui.EndPaint(hwnd, paint)

    def _paint_label(self, dc, label, width, height):
        with self._label_font(dc):
            bg = win32gui.CreateSolidBrush(_color(LABEL_BG_COLOR))
            try:
                win32gui.FillRect(dc, (0, 0, width, height), bg)
            finally:
                win32gui.DeleteObject(bg)
            old_color = win32gui.SetTextColor(dc, _color("#FFFFFF"))
            old_mode = win32gui.SetBkMode(dc, win32con.TRANSPARENT)
            try:
                win32gui.DrawText(dc, label, -1, (8, 0, width - 8, height),
                                  win32con.DT_SINGLELINE | win32con.DT_VCENTER
                                  | win32con.DT_END_ELLIPSIS | win32con.DT_NOPREFIX)
            finally:
                win32gui.SetBkMode(dc, old_mode)
                win32gui.SetTextColor(dc, old_color)

    # Thread-safe public API.
    def track(self, hwnd):
        self._cmd_q.put(("track", hwnd))

    def untrack(self):
        self._cmd_q.put(("untrack", None))

    def set_highlights(self, rects):
        """Rectangles in the target's client-relative pixel space."""
        self._cmd_q.put(("highlights", rects))

    def set_label(self, label):
        """Set the visible feature label; never change the app's text/focus."""
        self._cmd_q.put(("label", label))

    def stop(self):
        self._cmd_q.put(("stop", None))


_overlay = None


def get_overlay():
    global _overlay
    if _overlay is None:
        _overlay = Overlay()
    return _overlay


@contextmanager
def hidden_for_capture():
    """Hide our decoration during screen fallback, preserving attachment/boxes."""
    overlay = _overlay
    if overlay is None or not overlay._thread.is_alive():
        yield
        return
    ready = threading.Event()
    overlay._cmd_q.put(("suspend", ready))
    try:
        if not ready.wait(3):
            raise RuntimeError("outline could not be hidden for screen capture")
        yield
    finally:
        overlay._cmd_q.put(("resume", None))
