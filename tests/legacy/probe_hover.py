"""Does a tooltip raised by hovering ever reach the caller?

A hover tool is only worth building if its result can show what the hover did.
This measures that, before any of it is written:

  1. Does hovering actually raise a tooltip we can see at all?
  2. Does `grab_window()` -- PrintWindow, the capture path every reading tool
     uses -- contain it? A Win32 tooltip is its own top-level window of class
     `tooltips_class32`, and PrintWindow asks *one* window to draw itself, so
     the expectation is no. Expectation, not knowledge, which is why this runs.
  3. Can UI Automation read the tooltip's text? If so, a hover result can state
     what the tooltip says instead of returning pixels and hoping.

Creates its own window with a real tooltip control attached, so it touches no
application belonging to anyone, leaks nothing, and gives the same answer on
any machine. Moves the real mouse -- there is no way to test hovering without
it -- and puts it back where it was found.

Run: .venv\\Scripts\\python.exe tests\\probe_hover.py
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32api
import win32con
import win32gui

import uiautomation as auto

import input_sim
import server
import window_manager

TOOLTIP_TEXT = "hover probe tooltip"

TTS_ALWAYSTIP = 0x01
TTF_IDISHWND = 0x0001
TTF_SUBCLASS = 0x0010
TTM_ADDTOOLW = win32con.WM_USER + 50


class TOOLINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT),
        ("uFlags", wt.UINT),
        ("hwnd", wt.HWND),
        ("uId", ctypes.c_void_p),
        ("rect", wt.RECT),
        ("hinst", wt.HINSTANCE),
        ("lpszText", wt.LPWSTR),
        ("lParam", wt.LPARAM),
        ("lpReserved", ctypes.c_void_p),
    ]


class ToolTipWindow:
    """A top-level window with a genuine Win32 tooltip attached to it.

    Its own thread with its own message pump: TTF_SUBCLASS makes the tooltip
    control watch the tool window's messages itself, so the window must be
    pumping for a hover to be noticed at all.
    """

    def __init__(self, title, x, y):
        self.title = title
        self.hwnd = None
        self.tooltip_hwnd = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(x, y), daemon=True)
        self._thread.start()
        if not self._ready.wait(5.0):
            raise RuntimeError("window never appeared")

    def _run(self, x, y):
        ctypes.windll.comctl32.InitCommonControls()
        cls = f"winauto_hover_{id(self)}"
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = win32gui.DefWindowProc
        wc.lpszClassName = cls
        wc.hbrBackground = win32con.COLOR_WINDOW + 1
        wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        win32gui.RegisterClass(wc)
        self.hwnd = win32gui.CreateWindow(
            cls, self.title, win32con.WS_OVERLAPPEDWINDOW, x, y, 480, 300, 0, 0, 0, None
        )
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNORMAL)
        win32gui.UpdateWindow(self.hwnd)

        self.tooltip_hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_TOPMOST, "tooltips_class32", None,
            win32con.WS_POPUP | TTS_ALWAYSTIP,
            0, 0, 0, 0, self.hwnd, 0, 0, None,
        )
        info = TOOLINFO()
        info.cbSize = ctypes.sizeof(TOOLINFO)
        info.uFlags = TTF_IDISHWND | TTF_SUBCLASS
        info.hwnd = self.hwnd
        info.uId = self.hwnd
        info.lpszText = TOOLTIP_TEXT
        added = ctypes.windll.user32.SendMessageW(
            self.tooltip_hwnd, TTM_ADDTOOLW, 0, ctypes.byref(info)
        )
        self.added = bool(added)
        self._ready.set()
        win32gui.PumpMessages()


def visible_tooltip_windows():
    found = []

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "tooltips_class32":
            rect = win32gui.GetWindowRect(hwnd)
            if rect[2] - rect[0] > 0 and rect[3] - rect[1] > 0:
                found.append((hwnd, rect))
        return True

    win32gui.EnumWindows(visit, None)
    return found


def differing_fraction(a, b):
    """How much of two same-sized crops disagree, ignoring compression noise."""
    pa, pb = list(a.getdata()), list(b.getdata())
    off = sum(1 for p, q in zip(pa, pb)
              if max(abs(p[0] - q[0]), abs(p[1] - q[1]), abs(p[2] - q[2])) > 24)
    return off / max(1, len(pa))


def main():
    parked = win32api.GetCursorPos()
    win = ToolTipWindow("HOVER PROBE -- has a real Win32 tooltip", 200, 200)
    print(f"window {win.hwnd}, tooltip control {win.tooltip_hwnd}, "
          f"TTM_ADDTOOL accepted: {win.added}\n")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_hover_probe")
    os.makedirs(out, exist_ok=True)
    try:
        window_manager._force_foreground(win.hwnd)
        time.sleep(0.6)

        left, top, right, bottom = window_manager.get_client_rect_screen(win.hwnd)
        centre = ((left + right) // 2, (top + bottom) // 2)

        print(f"tooltip windows before hovering: {len(visible_tooltip_windows())}")

        # Two moves, not one. A single jump to the point can leave an app with
        # nothing to notice; a tooltip is armed by movement *into* the tool.
        input_sim.move_to(centre[0] - 40, centre[1] - 40)
        time.sleep(0.15)
        input_sim.move_to(*centre)

        appeared, waited = [], 0.0
        while waited < 3.0:
            appeared = visible_tooltip_windows()
            if appeared:
                break
            time.sleep(0.1)
            waited += 0.1
        print(f"tooltip windows after hovering : {len(appeared)} (after {waited:.1f}s)")
        if not appeared:
            print("\nno tooltip appeared, so nothing below can be measured")
            return 0

        tip_hwnd, tip_rect = appeared[0]
        print(f"  tooltip window {tip_hwnd} at {tip_rect}")

        # 2. Is it in what the reading tools would return?
        painted = server.grab_window(win.hwnd)
        scraped = server.grab_window(win.hwnd, allow_occluded=False)
        painted.save(os.path.join(out, "printwindow.png"))
        scraped.save(os.path.join(out, "screengrab.png"))

        # Compare only where the tooltip sits, in the target's client space.
        tl, tt, tr, tb = tip_rect
        box = (max(0, tl - left), max(0, tt - top),
               min(painted.width, tr - left), min(painted.height, tb - top))
        if box[2] - box[0] > 4 and box[3] - box[1] > 4:
            diff = differing_fraction(painted.crop(box), scraped.crop(box))
            print(f"\n  where the tooltip sits, PrintWindow and the screen differ by "
                  f"{diff:.0%} of pixels")
            print(f"  -> PrintWindow {'does NOT contain' if diff > 0.15 else 'appears to contain'} "
                  "the tooltip")
        else:
            print("\n  the tooltip is drawn outside the window's client area, so a "
                  "capture of the window cannot contain it at all")

        # 3. Can its text be read instead of photographed?
        try:
            control = auto.ControlFromHandle(tip_hwnd)
            print(f"\n  UIA name : {control.Name!r}")
            print(f"  UIA type : {control.ControlTypeName}")
            print(f"  matches the text we set: {control.Name == TOOLTIP_TEXT}")
        except Exception as exc:  # noqa: BLE001 - reporting, not asserting
            print(f"\n  UIA could not read it: {exc!r}")

        print(f"\n  images written to {out}")
        return 0
    finally:
        win32api.SetCursorPos(parked)
        win32gui.PostMessage(win.hwnd, win32con.WM_CLOSE, 0, 0)


if __name__ == "__main__":
    sys.exit(main())
