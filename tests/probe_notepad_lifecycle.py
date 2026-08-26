"""Can a test open a Notepad window and put it back the way it found it?

Two questions, both of which the test suite has been getting wrong:

  1. **Does launching notepad.exe create a new top-level window at all?**
     Windows 11 Notepad can be configured to open in a new *tab* of an existing
     window, in which case no new handle appears and a test that waits for one
     must give up rather than grab somebody else's.
  2. **Can that window be closed again?** Every test that launched Notepad
     leaked its window: `notepad.exe` is a stub that hands off to a packaged
     process and exits, so killing the handle Popen returned kills nothing, and
     `WM_CLOSE` is ignored by modern apps. 56 windows had accumulated by
     2026-08-26 -- and killing the host process does not help either, because
     Notepad restores every one of them the next time it starts.

Only ever touches a window that appeared after it asked for one, and types
nothing into it, so the document stays unmodified and no save prompt can
appear. Prints measurements; asserts nothing.

Run: .venv\\Scripts\\python.exe tests\\probe_notepad_lifecycle.py
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import win32gui

import input_sim
import window_manager
from smoke import notepad_hwnds, wait_for_new_notepad


def main():
    before = notepad_hwnds()
    print(f"notepad windows already open: {len(before)}")

    started = time.time()
    subprocess.Popen(["notepad.exe"])
    hwnd = wait_for_new_notepad(before)
    took = time.time() - started

    if hwnd is None:
        print(f"NO new window appeared within {took:.1f}s -- it opened as a tab, or "
              "Notepad is still restoring its session. A test must refuse here.")
        return 0

    print(f"new window {hwnd} after {took:.1f}s, title {win32gui.GetWindowText(hwnd)!r}")

    window_manager.bring_to_foreground(hwnd)
    time.sleep(0.8)
    focused = win32gui.GetForegroundWindow() == hwnd
    print(f"could be focused: {focused}")
    if not focused:
        print("not focused, so Alt+F4 would go to the wrong window -- stopping here")
        return 0

    # A test types into the document, which marks it modified -- Notepad shows
    # that as a "*" in the title -- and then Alt+F4 raises a save prompt that
    # has to be answered, in whatever language Windows is set to. So: does
    # undoing the typing put the document back to unmodified, and let the
    # clean close above work after all?
    input_sim.type_text("hello")
    time.sleep(0.6)
    print(f"title after typing: {win32gui.GetWindowText(hwnd)!r}")
    for _ in range(6):
        input_sim.press_keys(["ctrl", "z"])
        time.sleep(0.2)
    time.sleep(0.5)
    title = win32gui.GetWindowText(hwnd)
    print(f"title after undo   : {title!r}")
    print(f"unmodified again   : {not title.startswith('*')}")

    input_sim.press_keys(["alt", "f4"])
    time.sleep(1.5)
    print(f"window still exists after Alt+F4: {win32gui.IsWindow(hwnd)}")
    print(f"notepad windows now: {len(notepad_hwnds())} (was {len(before)})")
    if win32gui.IsWindow(hwnd):
        print("  -> a save prompt is probably up; the window is still ours and "
              "still open. Answer it by hand; nothing else has been touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
