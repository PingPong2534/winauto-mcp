"""Does typed text still arrive, now that _send checks SendInput's return?

Issue #6's real complaint is that the server reported "typed 6 characters"
without anyone ever asking whether the characters arrived. Adding a return-value
check to _send puts a raise on the path of every keystroke in the project, so
the first thing to establish is that the path still works at all -- a guard that
turns working input into an exception would be a far worse bug than the one
being fixed.

So this asks the question the server never asked: type, then READ THE TEXT BACK
out of the control and compare. Not "the call returned", not "a pixel changed"
-- the actual string.

It uses an EDIT control it creates itself. Notepad would do the same job and
nine other scripts in this folder leak a Notepad window per run; this one dies
with the process.

Run:  .venv\\Scripts\\python.exe tests\\probe_typing_lands.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32con  # noqa: E402
import win32gui  # noqa: E402

import input_sim  # noqa: E402
import integrity  # noqa: E402

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{'ok' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got  {got!r}")
        print(f"         want {want!r}")


class EditWindow:
    """A top-level window whose whole client area is one EDIT control, on its
    own thread with its own message pump."""

    def __init__(self, title="winauto typing probe"):
        self.hwnd = None
        self.edit = None
        self._ready = threading.Event()
        threading.Thread(target=self._run, args=(title,), daemon=True).start()
        if not self._ready.wait(5.0):
            raise RuntimeError("probe window never appeared")

    def _run(self, title):
        cls = f"winauto_typing_{id(self)}"
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = win32gui.DefWindowProc
        wc.lpszClassName = cls
        wc.hbrBackground = win32con.COLOR_WINDOW + 1
        wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        win32gui.RegisterClass(wc)
        self.hwnd = win32gui.CreateWindow(
            cls, title, win32con.WS_OVERLAPPEDWINDOW, 260, 260, 520, 200, 0, 0, 0, None
        )
        self.edit = win32gui.CreateWindow(
            "EDIT", "",
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_BORDER | win32con.ES_AUTOHSCROLL,
            10, 10, 480, 40, self.hwnd, 0, 0, None,
        )
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNORMAL)
        win32gui.UpdateWindow(self.hwnd)
        self._ready.set()
        win32gui.PumpMessages()

    def text(self):
        return win32gui.GetWindowText(self.edit)

    def clear(self):
        win32gui.SendMessage(self.edit, win32con.WM_SETTEXT, 0, "")

    def close(self):
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)


def main():
    print(f"server integrity: {integrity.level_name(integrity.own_level())}\n")
    win = EditWindow()
    try:
        # The window has to be in front and the EDIT focused, or this measures
        # focus rather than delivery.
        input_sim.bring_to_foreground(win.hwnd)
        time.sleep(0.25)
        # Focused by clicking, not by SetFocus: the window pumps messages on
        # its own thread and SetFocus across input queues fails with
        # ACCESS_DENIED. Clicking is also what a caller would really do.
        input_sim.click_in_window(win.hwnd, 40, 30, keep_cursor=True)
        time.sleep(0.2)

        print("plain ASCII:")
        win.clear()
        time.sleep(0.1)
        input_sim.type_text("whoami", hwnd=win.hwnd)
        time.sleep(0.4)
        check("the text that was typed is the text in the control", win.text(), "whoami")

        # The reason SendInput is used at all rather than a per-key VK mapping.
        # If the return-value check were wrong about counts this is where a
        # multi-byte path would break first.
        print("\nUnicode (Thai), the case SendInput exists for:")
        win.clear()
        time.sleep(0.1)
        input_sim.type_text("สวัสดี", hwnd=win.hwnd)
        time.sleep(0.5)
        check("Thai text arrives intact", win.text(), "สวัสดี")

        print("\nspecial keys still reach the control:")
        win.clear()
        time.sleep(0.1)
        input_sim.type_text("abc", hwnd=win.hwnd)
        time.sleep(0.3)
        input_sim.press_key("backspace", hwnd=win.hwnd)
        time.sleep(0.3)
        check("backspace removed exactly one character", win.text(), "ab")

        print("\nchords still reach the control:")
        input_sim.press_keys(["ctrl", "a"], hwnd=win.hwnd)
        time.sleep(0.2)
        input_sim.press_key("delete", hwnd=win.hwnd)
        time.sleep(0.3)
        check("ctrl+a then delete emptied it", win.text(), "")

        print("\nthe window itself is not blocked (it is ours, at our level):")
        check("input_blocked is False for our own window",
              integrity.blocks_input(integrity.window_level(win.hwnd)), False)
    finally:
        win.close()

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
