"""How often does the tracking overlay actually repaint a window that is not
moving? Prints a count; asserts nothing.

It exists because the overlay used to redraw unconditionally on every 150ms
poll -- a transparent topmost window the size of the target, fully repainted
~6.7 times a second for the whole session. That is the "attached window feels
stuck" report: the outline flickers and the app underneath fights a compositor
that never gets to rest.
"""

import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import window_manager  # noqa: E402
from overlay import Overlay  # noqa: E402

SECONDS = 3.0


def find_window(pid):
    for _ in range(60):
        for w in window_manager.list_windows():
            # Win11's Notepad is a packaged app: the process we spawned hands
            # off to another pid, so match on the process name as well.
            if w["pid"] == pid or "notepad" in w["process"].lower():
                return w["hwnd"]
        time.sleep(0.1)
    raise RuntimeError("window never appeared")


def main():
    proc = subprocess.Popen(["notepad.exe"])
    try:
        hwnd = find_window(proc.pid)
        ov = Overlay()

        paints = {"n": 0}
        real = ov.canvas.create_rectangle

        def counting(*a, **k):
            paints["n"] += 1
            return real(*a, **k)

        ov.canvas.create_rectangle = counting

        ov.track(hwnd)
        time.sleep(SECONDS)
        still = paints["n"]
        print(f"window held still for {SECONDS}s: {still} rectangles drawn")
        print(f"  (one paint = one outline; the old code drew ~{int(SECONDS * 1000 / 150)})")

        # Moving the window must still update the outline, or the saving above
        # was bought by making the overlay wrong.
        before = paints["n"]
        left, top, right, bottom = window_manager.get_client_rect_screen(hwnd)
        window_manager.move_window(hwnd, left + 60, top + 40) if hasattr(
            window_manager, "move_window"
        ) else _move(hwnd, left + 60, top + 40)
        time.sleep(1.0)
        print(f"after moving the window: {paints['n'] - before} more rectangles drawn")

        ov.untrack()
        time.sleep(0.5)
        after_untrack = paints["n"]
        time.sleep(1.0)
        print(f"after untrack, over 1s: {paints['n'] - after_untrack} more rectangles drawn")
    finally:
        proc.kill()


def _move(hwnd, x, y):
    import win32gui

    l, t, r, b = win32gui.GetWindowRect(hwnd)
    win32gui.MoveWindow(hwnd, x, y, r - l, b - t, True)


if __name__ == "__main__":
    main()
