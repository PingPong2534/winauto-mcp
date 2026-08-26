"""Spike: can this server drive a window WITHOUT taking over the real mouse,
keyboard and foreground -- so the machine stays usable while a run happens?

Two independent questions, and background mode needs BOTH to be yes for a
given app:

  1. CAPTURE  -- can the window be read while it is covered by another window?
                 mss screen-scrapes screen coordinates, so an occluded window
                 gives back whatever is on top of it. PrintWindow asks the
                 window to render itself instead, which does not care what is
                 in front -- but GPU-composited surfaces (OpenGL, Vulkan,
                 D3D) often come back black.
  2. INPUT    -- does the app act on posted window messages, which move no
                 cursor and need no focus, or only on real SendInput events?

Answers are per-app, so this prints measurements rather than asserting. Stage
one is Notepad: a plain Win32 client that should say yes to both. If it says
no, the harness is wrong, not the app.

Run:  .venv\\Scripts\\python.exe tests\\spike_background.py
"""

import ctypes
import os
import subprocess
import sys
import time

import win32api
import win32con
import win32gui
import win32ui
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from screenshot import grab_window  # noqa: E402
from window_manager import get_client_rect_screen, list_windows  # noqa: E402

PW_RENDERFULLCONTENT = 0x00000002


def print_window(hwnd) -> Image.Image | None:
    """Ask the window to paint itself into a bitmap. Unlike a screen grab this
    works while the window is covered, but an app that renders on the GPU may
    hand back nothing."""
    left, top, right, bottom = get_client_rect_screen(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        return None
    win_dc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(win_dc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    try:
        ok = ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if not ok:
            return None
        info, bits = bmp.GetInfo(), bmp.GetBitmapBits(True)
        return Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, win_dc)


def describe(img: Image.Image | None) -> str:
    """How much of the frame carries any signal at all -- a PrintWindow that
    'succeeded' but returned an all-black bitmap is the failure mode to catch,
    and it is indistinguishable from success by return code alone."""
    if img is None:
        return "no image"
    small = img.resize((160, 90))
    px = list(small.getdata())
    lit = sum(1 for r, g, b in px if r + g + b > 24)
    colours = len(set(px))
    return f"{img.size[0]}x{img.size[1]}, {100 * lit / len(px):>5.1f}% non-black, {colours} distinct colours"


def similarity(a: Image.Image, b: Image.Image) -> float:
    """Rough fraction of matching pixels, for 'is this the same window or the
    one covering it'."""
    if a is None or b is None:
        return -1.0
    x = list(a.resize((160, 90)).getdata())
    y = list(b.resize((160, 90)).getdata())
    same = sum(1 for p, q in zip(x, y) if max(abs(p[0] - q[0]), abs(p[1] - q[1]), abs(p[2] - q[2])) < 24)
    return same / len(x)


def post_char(hwnd, ch: str):
    win32api.PostMessage(hwnd, win32con.WM_CHAR, ord(ch), 0)


def find_focus_target(hwnd):
    """Posted keyboard messages go to a specific control, not to a top-level
    frame -- Notepad's frame ignores WM_CHAR, its edit child consumes it. Find
    the deepest child so the spike tests the app, not the address."""
    found = []
    win32gui.EnumChildWindows(hwnd, lambda h, _: found.append(h), None)
    return found[-1] if found else hwnd


def main():
    notepad = subprocess.Popen(["notepad.exe"])
    cover = None
    try:
        time.sleep(1.5)
        hwnd = next(
            w["hwnd"]
            for w in list_windows()
            if w["pid"] == notepad.pid or "notepad" in w["process"].lower()
        )
        print(f"notepad hwnd={hwnd}")

        print("\n[1] visible, nothing in front")
        print(f"    screen grab : {describe(grab_window(hwnd))}")
        print(f"    PrintWindow : {describe(print_window(hwnd))}")

        print("\n[2] posted keystrokes, while Notepad is NOT focused")
        target = find_focus_target(hwnd)
        print(f"    posting WM_CHAR to child hwnd={target}")
        before = grab_window(hwnd)
        # Hand the foreground to something else first: the whole point is
        # input arriving at a window the user is not looking at.
        cover = subprocess.Popen(["calc.exe"])
        time.sleep(2.5)
        fg = win32gui.GetForegroundWindow()
        print(f"    foreground is now hwnd={fg} ({'notepad' if fg == hwnd else 'something else'})")
        for ch in "POSTED":
            post_char(target, ch)
            time.sleep(0.05)
        time.sleep(0.8)

        print("\n[3] read it back while covered")
        scraped = grab_window(hwnd)
        painted = print_window(hwnd)
        print(f"    screen grab : {describe(scraped)}")
        print(f"    PrintWindow : {describe(painted)}")
        print(f"    grab vs painted similarity: {similarity(scraped, painted):.2f}"
              "   (low = the screen grab is showing the window on top instead)")
        print(f"    painted vs before-typing  : {similarity(painted, before):.2f}"
              "   (low = the posted keystrokes changed the window)")

        out = os.path.join(os.environ["TEMP"], "winauto-diag")
        os.makedirs(out, exist_ok=True)
        if painted:
            painted.save(os.path.join(out, "bg_printwindow.png"))
        scraped.save(os.path.join(out, "bg_screengrab.png"))
        print(f"\n    frames written to {out}")
    finally:
        notepad.kill()
        if cover:
            cover.kill()


if __name__ == "__main__":
    main()
