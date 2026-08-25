"""Spike stage 2: the same two questions (capture while covered, input without
focus) against Blender -- the app this server actually exists to drive.

Blender is the interesting case because it inverts Notepad's answers. It runs
a classic Win32 message loop, so posted input has a chance of being read; but
it paints with OpenGL, which is exactly what PrintWindow tends to return black
for. Notepad said capture-yes / input-no. If Blender says capture-no, no
amount of input work will make background mode possible for it.

Run:  .venv\\Scripts\\python.exe tests\\spike_background_blender.py
"""

import os
import subprocess
import sys
import time

import win32api
import win32con
import win32gui

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from screenshot import changed_bbox, grab_window  # noqa: E402
from spike_background import describe, print_window, similarity  # noqa: E402
from window_manager import list_windows  # noqa: E402

BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
OUT = os.path.join(os.environ["TEMP"], "winauto-diag")


def lparam(x, y):
    return (int(y) << 16) | (int(x) & 0xFFFF)


def main():
    os.makedirs(OUT, exist_ok=True)
    proc = subprocess.Popen([BLENDER])
    cover = None
    try:
        print("launching Blender (this takes a few seconds)...")
        hwnd = None
        for _ in range(40):
            time.sleep(0.5)
            hwnd = next(
                (w["hwnd"] for w in list_windows() if w["pid"] == proc.pid and w["title"].strip()),
                None,
            )
            if hwnd:
                break
        if hwnd is None:
            print("  could not find a Blender window")
            return
        time.sleep(4)
        print(f"blender hwnd={hwnd}  title={win32gui.GetWindowText(hwnd)!r}")

        print("\n[1] in front")
        visible = grab_window(hwnd)
        print(f"    screen grab : {describe(visible)}")
        print(f"    PrintWindow : {describe(print_window(hwnd))}")

        print("\n[2] now covered by another window")
        cover = subprocess.Popen(["notepad.exe"])
        time.sleep(2.5)
        fg = win32gui.GetForegroundWindow()
        print(f"    foreground hwnd={fg} ({'STILL blender' if fg == hwnd else 'not blender'})")
        scraped = grab_window(hwnd)
        painted = print_window(hwnd)
        print(f"    screen grab : {describe(scraped)}")
        print(f"    PrintWindow : {describe(painted)}")
        print(f"    painted vs the real earlier view: {similarity(painted, visible):.2f}"
              "   (high = PrintWindow really got Blender's own pixels)")
        print(f"    scraped vs the real earlier view: {similarity(scraped, visible):.2f}"
              "   (low = the screen grab is showing whatever covered it)")
        if painted:
            painted.save(os.path.join(OUT, "blender_printwindow.png"))
        scraped.save(os.path.join(OUT, "blender_screengrab.png"))
        visible.save(os.path.join(OUT, "blender_visible.png"))

        print("\n[3] posted mouse messages, no cursor moved, no focus taken")
        # Sweep the mouse across the top menu row. If Blender reads posted
        # messages at all, a hover highlight appears under the pointer -- a
        # visible change with no real cursor anywhere near the window.
        w, h = visible.size
        y = 20
        base = print_window(hwnd)
        for x in range(int(w * 0.05), int(w * 0.45), 25):
            win32api.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam(x, y))
            time.sleep(0.05)
        time.sleep(1.0)
        after = print_window(hwnd)
        if base and after:
            box = changed_bbox(base, after, threshold=12)
            print(f"    changed after the sweep: {box}")
            print(f"    similarity base vs after: {similarity(base, after):.3f}")
            after.save(os.path.join(OUT, "blender_after_postmessage.png"))
        print("    (a change near the menu row = Blender acted on posted input)")
        print(f"\n    frames written to {OUT}")
    finally:
        proc.kill()
        if cover:
            cover.kill()


if __name__ == "__main__":
    main()
