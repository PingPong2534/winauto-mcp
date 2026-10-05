"""Spike stage 3: does ANY posted-message input reach a target app?

Stage 1 (Notepad) and stage 2 (Blender) both answered no to a posted mouse
move, but a hover highlight is the weakest possible signal -- an app can
legitimately ignore hover while still acting on a click, and some only look at
input once they believe they are active. So this tries the stronger variants
in order of increasing intrusiveness and reports which, if any, moved a pixel:

    move        WM_MOUSEMOVE only
    click       WM_MOUSEMOVE + WM_LBUTTONDOWN/UP, posted
    send-click  the same, sent synchronously (SendMessage) instead of posted
    activate    WM_ACTIVATE/WM_NCACTIVATE first, then the click

None of these move the real cursor or take the foreground, which is the whole
point. Everything is read back with PrintWindow, so the app may stay covered.

Run:  .venv\\Scripts\\python.exe tests\\spike_background_input.py <exe> [x] [y]
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

from screenshot import changed_bbox  # noqa: E402
from spike_background import describe, print_window  # noqa: E402
from window_manager import list_windows  # noqa: E402

OUT = os.path.join(os.environ["TEMP"], "winauto-diag")
MK_LBUTTON = 0x0001


def lp(x, y):
    return (int(y) << 16) | (int(x) & 0xFFFF)


def attempt_move(hwnd, x, y):
    for step in range(6):
        win32api.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lp(x - 60 + step * 12, y))
        time.sleep(0.05)


def attempt_click(hwnd, x, y):
    attempt_move(hwnd, x, y)
    win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, MK_LBUTTON, lp(x, y))
    time.sleep(0.08)
    win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lp(x, y))


def attempt_send_click(hwnd, x, y):
    win32gui.SendMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lp(x, y))
    win32gui.SendMessage(hwnd, win32con.WM_LBUTTONDOWN, MK_LBUTTON, lp(x, y))
    time.sleep(0.08)
    win32gui.SendMessage(hwnd, win32con.WM_LBUTTONUP, 0, lp(x, y))


def attempt_activate_click(hwnd, x, y):
    win32api.PostMessage(hwnd, win32con.WM_NCACTIVATE, 1, 0)
    win32api.PostMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_CLICKACTIVE, 0)
    win32api.PostMessage(hwnd, win32con.WM_SETFOCUS, 0, 0)
    time.sleep(0.2)
    attempt_click(hwnd, x, y)


ATTEMPTS = [
    ("move", attempt_move),
    ("click", attempt_click),
    ("send-click", attempt_send_click),
    ("activate+click", attempt_activate_click),
]


def main():
    exe = sys.argv[1]
    os.makedirs(OUT, exist_ok=True)
    tag = os.path.splitext(os.path.basename(exe))[0][:20]
    proc = subprocess.Popen([exe])
    cover = None
    try:
        print(f"launching {exe} ...")
        hwnd = None
        for _ in range(60):
            time.sleep(0.5)
            hwnd = next(
                (w["hwnd"] for w in list_windows() if w["pid"] == proc.pid and w["title"].strip()),
                None,
            )
            if hwnd:
                break
        if hwnd is None:
            print("  no window appeared")
            return
        time.sleep(5)
        print(f"hwnd={hwnd}  title={win32gui.GetWindowText(hwnd)!r}")

        frame = print_window(hwnd)
        print(f"PrintWindow while in front: {describe(frame)}")
        if frame is None:
            print("  cannot capture this window at all -- background mode is impossible for it")
            return
        w, h = frame.size
        x = int(sys.argv[2]) if len(sys.argv) > 2 else w // 2
        y = int(sys.argv[3]) if len(sys.argv) > 3 else int(h * 0.45)

        cover = subprocess.Popen(["notepad.exe"])
        time.sleep(2.5)
        fg = win32gui.GetForegroundWindow()
        print(f"covered; foreground hwnd={fg} ({'still target!' if fg == hwnd else 'not the target'})")
        print(f"probing at ({x}, {y}) of {w}x{h}\n")

        for name, fn in ATTEMPTS:
            before = print_window(hwnd)
            fn(hwnd, x, y)
            time.sleep(1.2)
            after = print_window(hwnd)
            box = changed_bbox(before, after, threshold=12)
            verdict = "NOTHING HAPPENED" if box is None else f"CHANGED {box}"
            print(f"  {name:<16} {verdict}")
            if box is not None:
                after.save(os.path.join(OUT, f"{tag}_{name.replace('+', '_')}.png"))
            # Undo any menu/dialog the attempt may have opened, so the next
            # attempt starts from the same screen instead of inheriting state.
            win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
            win32api.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_ESCAPE, 0)
            time.sleep(0.5)

        print(f"\nframes (only for attempts that changed something) in {OUT}")
    finally:
        proc.kill()
        if cover:
            cover.kill()


if __name__ == "__main__":
    main()
