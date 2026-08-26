"""Is the green outline really forbidden the foreground?

The claim in overlay.py is that the outline can never become the foreground
window. That claim was wrong once already: the WS_EX_NOACTIVATE style was being
applied to a handle captured while the window was still hidden, and the window
that later appeared on screen did not have it. The diagnostic that was supposed
to cover this passed anyway, so the claim was believed for a day.

This asks the question of the window that is actually on screen, and asks it
across repeated hide/show cycles, because the handle is not guaranteed to
survive one. Everything here is created by this script.

Run: .venv\\Scripts\\python.exe tests\\probe_overlay_activation.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32con
import win32gui

import window_manager
from overlay import get_overlay

PASSED, FAILED = [], []


def check(label, ok, detail=""):
    (PASSED if ok else FAILED).append(label)
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{'  -- ' + detail if detail else ''}")


def visible_outlines():
    return {h: v for h, v in window_manager.visible_top_levels().items()
            if v[0].startswith("Tk")}


def main():
    overlay = get_overlay()
    bait = win32gui.CreateWindow(
        "STATIC", "outline probe", win32con.WS_OVERLAPPEDWINDOW | win32con.WS_VISIBLE,
        320, 320, 420, 260, 0, 0, 0, None,
    )
    win32gui.ShowWindow(bait, win32con.SW_SHOWNORMAL)
    time.sleep(0.3)

    handles = []
    try:
        for cycle in range(1, 4):
            print(f"\n-- show/hide cycle {cycle}")
            window_manager._force_foreground(bait)
            time.sleep(0.2)
            overlay.track(bait)
            time.sleep(0.9)

            on_screen = visible_outlines()
            check("exactly one outline is on screen", len(on_screen) == 1, str(on_screen))
            if not on_screen:
                break
            hwnd, (cls, _) = next(iter(on_screen.items()))
            handles.append(hwnd)

            style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            check("the window on screen has WS_EX_NOACTIVATE",
                  bool(style & win32con.WS_EX_NOACTIVATE), f"{cls} {hwnd}")
            check("and it is the handle the overlay reports",
                  hwnd == overlay.hwnd, f"on screen {hwnd}, reported {overlay.hwnd}")

            fg = win32gui.GetForegroundWindow()
            check("the outline is not the foreground window", fg != hwnd,
                  f"foreground is {fg} ({win32gui.GetClassName(fg)!r})")
            check("the window being outlined still has the foreground", fg == bait,
                  f"foreground is {fg} ({win32gui.GetClassName(fg)!r}), bait is {bait}")

            overlay.untrack()
            time.sleep(0.5)
            check("the outline is gone once untracked", not visible_outlines(),
                  str(visible_outlines()))

        print("\n-- across cycles")
        check("the outline's handle is stable, so marking it once would have held",
              len(set(handles)) == 1,
              f"handles seen: {handles} -- if these differ, Tk recreates the window "
              "on every show and the style must be re-applied each time, which is "
              "what the code now does")

        print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
        for label in FAILED:
            print(f"  FAILED: {label}")
        return 1 if FAILED else 0
    finally:
        overlay.untrack()
        time.sleep(0.2)
        win32gui.DestroyWindow(bait)


if __name__ == "__main__":
    sys.exit(main())
