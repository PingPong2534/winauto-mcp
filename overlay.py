"""A transparent, click-through, always-on-top window that outlines the
currently attached target window and can highlight specific element rects.

Runs its own Tk instance on a background thread; all state changes go
through a thread-safe queue so callers never touch Tk objects directly.
"""

import queue
import threading

import tkinter as tk

import win32con
import win32gui

from window_manager import get_client_rect_screen, window_exists

BORDER_COLOR = "#00FF66"
HIGHLIGHT_COLOR = "#FF3355"


class Overlay:
    def __init__(self, poll_ms=150):
        self._poll_ms = poll_ms
        self._cmd_q = queue.Queue()
        self._tracked_hwnd = None
        self._highlights = []
        # What is currently on screen. The poll loop compares against this and
        # touches Tk only when it disagrees. Repainting a transparent topmost
        # window the size of the target every 150ms for the whole session is
        # enough compositor work to make the tracked app feel like it is
        # stuttering -- and the outline itself flickers, which reads as the
        # automation having hung when it is only redrawing.
        self._drawn = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)

    def _run(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", "black")
        self.root.config(bg="black")
        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.root.withdraw()
        self._deny_activation()
        self._ready.set()
        self._tick()
        self.root.mainloop()

    def _deny_activation(self):
        """Tell Windows this window may never hold the foreground.

        Measured 2026-08-26: showing the outline activated it. After a tool
        call the foreground window was class `TkTopLevel` -- the outline --
        instead of the app being driven. An outline is a decoration; if it
        holds the foreground then keystrokes are aimed at a rectangle, and the
        window the person was using is never handed back to them.
        """
        try:
            self._hwnd = int(self.root.wm_frame(), 16)
            style = win32gui.GetWindowLong(self._hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(
                self._hwnd, win32con.GWL_EXSTYLE, style | win32con.WS_EX_NOACTIVATE
            )
        except Exception:  # noqa: BLE001 - a cosmetic overlay must never fail
            self._hwnd = None

    def _tick(self):
        try:
            while True:
                cmd, data = self._cmd_q.get_nowait()
                if cmd == "track":
                    self._tracked_hwnd = data
                elif cmd == "untrack":
                    self._tracked_hwnd = None
                    self._highlights = []
                    self._drawn = None
                    self.root.withdraw()
                elif cmd == "highlights":
                    self._highlights = data
                elif cmd == "stop":
                    self.root.quit()
                    return
        except queue.Empty:
            pass

        if self._tracked_hwnd is not None:
            if window_exists(self._tracked_hwnd):
                try:
                    rect = get_client_rect_screen(self._tracked_hwnd)
                    self._redraw(rect)
                except Exception:
                    pass
            else:
                self._tracked_hwnd = None
                self._drawn = None
                self.root.withdraw()

        self.root.after(self._poll_ms, self._tick)

    def _redraw(self, rect):
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return
        # Nothing has moved and nothing is highlighted differently, so the
        # pixels already on screen are correct. Reading the rect is cheap; it
        # is the drawing that costs, so the poll stays fast and the paint stops.
        state = (rect, tuple(self._highlights))
        if state == self._drawn:
            return
        self._drawn = state
        self.root.deiconify()
        self.root.geometry(f"{width}x{height}+{left}+{top}")
        self.canvas.config(width=width, height=height)
        self.canvas.delete("all")
        self.canvas.create_rectangle(1, 1, width - 1, height - 1, outline=BORDER_COLOR, width=3)
        for hx1, hy1, hx2, hy2 in self._highlights:
            self.canvas.create_rectangle(hx1, hy1, hx2, hy2, outline=HIGHLIGHT_COLOR, width=2)

    # --- thread-safe public API ---

    def track(self, hwnd):
        self._cmd_q.put(("track", hwnd))

    def untrack(self):
        self._cmd_q.put(("untrack", None))

    def set_highlights(self, rects):
        """rects: list of (x1, y1, x2, y2) in the tracked window's client-relative space."""
        self._cmd_q.put(("highlights", rects))

    def stop(self):
        self._cmd_q.put(("stop", None))


_overlay = None


def get_overlay():
    global _overlay
    if _overlay is None:
        _overlay = Overlay()
    return _overlay
