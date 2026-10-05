"""Diagnostic: what is actually changing in an idle window between frames?

Answers whether wait_stable's window-wide 'never settles' result on Notepad is
a blinking caret, the tracking overlay bleeding into the capture, or genuine
per-frame noise -- each of which needs a different fix.
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from PIL import ImageChops  # noqa: E402

import window_manager  # noqa: E402
from screenshot import changed_bbox, grab_window  # noqa: E402


def describe(a, b, label):
    diff = ImageChops.difference(a, b)
    gray = diff.convert("L")
    hist = gray.histogram()
    nonzero = sum(hist[1:])
    strong = sum(hist[11:])
    peak = max(i for i, c in enumerate(hist) if c) if nonzero else 0
    print(
        f"  {label}: bbox(thr=10)={changed_bbox(a, b, 10)} "
        f"bbox(thr=0)={changed_bbox(a, b, 0)}\n"
        f"      pixels differing at all={nonzero}, by >10={strong}, max luma diff={peak}"
    )


def main():
    proc = subprocess.Popen(["notepad.exe"])
    try:
        time.sleep(1.5)
        # Windows 11's Notepad relaunches itself into another process, so the
        # window rarely belongs to the pid we started.
        hwnd = next(
            w["hwnd"]
            for w in window_manager.list_windows()
            if w["pid"] == proc.pid or "notepad" in w["process"].lower()
        )
        window_manager.bring_to_foreground(hwnd)
        time.sleep(0.5)

        print("\nA) idle window, no overlay, frames 150ms apart")
        frames = []
        for _ in range(6):
            frames.append(grab_window(hwnd))
            time.sleep(0.15)
        for i in range(1, len(frames)):
            describe(frames[i - 1], frames[i], f"frame {i-1}->{i}")

        print("\nB) same, but with the tracking overlay shown")
        from overlay import get_overlay

        get_overlay().track(hwnd)
        time.sleep(0.8)
        frames = []
        for _ in range(6):
            frames.append(grab_window(hwnd))
            time.sleep(0.15)
        for i in range(1, len(frames)):
            describe(frames[i - 1], frames[i], f"frame {i-1}->{i}")
        get_overlay().untrack()
        time.sleep(0.3)
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
