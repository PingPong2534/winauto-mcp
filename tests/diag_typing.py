"""Diagnostic: what does typing into a cleared Notepad actually change?

Not a test -- it prints the diff box and writes the two frames to
%TEMP%\\winauto-diag so a human can look at them. Written because the smoke
test's stale-guard section kept getting a window-wide diff box from typing a
handful of characters, which no amount of re-aiming the click would explain.
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import server  # noqa: E402
from screenshot import changed_bbox, grab_window  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "winauto-diag")


def main():
    os.makedirs(OUT, exist_ok=True)
    proc = subprocess.Popen(["notepad.exe"])
    try:
        time.sleep(1.5)
        hwnd = next(
            w["hwnd"]
            for w in server.window_manager.list_windows()
            if w["pid"] == proc.pid or "notepad" in w["process"].lower()
        )
        asyncio.run(server.mcp.call_tool("attach_window", {"hwnd": hwnd}))
        time.sleep(0.5)

        server.input_sim.type_text("hello world", hwnd=hwnd)
        time.sleep(0.4)
        server.input_sim.press_keys(["ctrl", "a"], hwnd=hwnd)
        time.sleep(0.2)
        server.input_sim.press_key("delete", hwnd=hwnd)
        time.sleep(0.6)

        before = grab_window(hwnd)
        before.save(os.path.join(OUT, "before.png"))
        print(f"cleared frame: {before.size}")

        server.input_sim.type_text("Z" * 20, hwnd=hwnd)
        time.sleep(0.6)
        after = grab_window(hwnd)
        after.save(os.path.join(OUT, "after.png"))

        for th in (0, 10, 40, 80):
            print(f"  threshold {th:>3}: {changed_bbox(before, after, threshold=th)}")

        # Where does the change actually live? Count changed pixels per band so
        # a stray one-pixel repaint at the bottom can't masquerade as "the whole
        # window changed", which a bounding box alone cannot distinguish.
        from PIL import ImageChops

        diff = ImageChops.difference(before, after).convert("L").point(lambda p: 255 if p > 10 else 0)
        w, h = diff.size
        rows = []
        for i in range(10):
            band = diff.crop((0, h * i // 10, w, h * (i + 1) // 10))
            n = sum(1 for p in band.getdata() if p)
            rows.append(f"    rows {h*i//10:>4}-{h*(i+1)//10:>4}: {n:>6} changed px  {'#' * min(40, n // 20)}")
        print("\n".join(rows))
        cols = []
        for i in range(10):
            band = diff.crop((w * i // 10, 0, w * (i + 1) // 10, h))
            n = sum(1 for p in band.getdata() if p)
            cols.append(f"    cols {w*i//10:>4}-{w*(i+1)//10:>4}: {n:>6} changed px  {'#' * min(40, n // 20)}")
        print("\n".join(cols))

        print("\n  tight box per row band (a band's own box, not the union):")
        for i in range(10):
            top, bot = h * i // 10, h * (i + 1) // 10
            box = changed_bbox(before, after, threshold=10, region=(0, top, w, bot))
            if box:
                print(f"    rows {top:>4}-{bot:>4}: {box}")
        print(f"\nframes written to {OUT}")
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
