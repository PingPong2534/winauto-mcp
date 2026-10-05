"""Does the person get their window back when an action finishes?

Checks the promise directly: after an action that took the foreground, the
window that was in front beforehand is in front again -- so a keystroke typed
in the gap between two actions lands in the person's window, not in the app
being automated.

The interesting cases are the refusals, because a hand-back that fires when it
shouldn't is worse than one that never fires:

  - it must NOT fire while a menu is open (the menu would close, undoing the
    click that opened it), and must not forget what it owes
  - it must NOT fire if the person has already moved somewhere else
  - it must NOT fire when keep_foreground(true) has been asked for

This creates its own two windows rather than driving Notepad or Calculator.
Three reasons, each learned the hard way:

  - **It cannot leak.** The windows die with the process. Every diag script
    that launched Notepad leaked one per run; 53 had piled up by 2026-08-26.
  - **Nothing else can perturb it.** An earlier version took "the person's
    window" to be whatever was in the foreground, which turned out to be the
    console window created by the harness running the test -- a window that
    appears, steals focus and dies on every single command.
  - **Alt+Space opens a real Win32 system menu on our own window**, with no
    modern app free to ignore it or bind it to something else.

Run: .venv\\Scripts\\python.exe tests\\diag_focus_return.py
"""

import ctypes
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32api
import win32con
import win32gui

import input_sim
import server
import window_manager

PASS = FAIL = 0


def check(label, got, want, explain=None):
    global PASS, FAIL
    ok = got == want
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{'ok' if ok else 'FAIL'}] {label}: got {got!r}, want {want!r}")
    if not ok and explain is not None:
        print(f"          got  = {explain(got)}")
        print(f"          want = {explain(want)}")


def describe(hwnd):
    """What that window actually is, in words.

    A failed foreground check reports a bare handle, and a handle nobody
    recognises is the whole question -- is it the person's, the app's, or some
    third window that appeared? Naming it turns a guess into a measurement.
    """
    if not hwnd:
        return "0 (no foreground window)"
    if not win32gui.IsWindow(hwnd):
        return f"{hwnd} (not a window any more)"
    return (f"{hwnd} class={win32gui.GetClassName(hwnd)!r} "
            f"title={win32gui.GetWindowText(hwnd)!r}")


class Window:
    """A real top-level window with its own message pump.

    Its own thread, because a menu is modal: while one is open the owning
    thread sits inside Windows' menu loop and does not return. The test's
    thread has to stay free to observe that.
    """

    _registered = set()

    def __init__(self, title, x, y):
        self.title = title
        self.hwnd = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(x, y), daemon=True)
        self._thread.start()
        if not self._ready.wait(5.0):
            raise RuntimeError(f"window {title!r} never appeared")

    def _run(self, x, y):
        cls = f"winauto_diag_{id(self)}"
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = win32gui.DefWindowProc
        wc.lpszClassName = cls
        wc.hbrBackground = win32con.COLOR_WINDOW + 1
        wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        win32gui.RegisterClass(wc)
        self.hwnd = win32gui.CreateWindow(
            cls, self.title,
            win32con.WS_OVERLAPPEDWINDOW,
            x, y, 420, 260, 0, 0, 0, None,
        )
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNORMAL)
        win32gui.UpdateWindow(self.hwnd)
        self._ready.set()
        win32gui.PumpMessages()

    def close(self):
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)


def settled_foreground(want=None, timeout=2.5):
    """The foreground window once it has settled.

    Two reasons a plain read is not enough: during a focus change
    GetForegroundWindow briefly returns 0, and a raise is not instantaneous.
    Waits for `want` if given, so a pass is quick and a failure still reports
    what is actually in front.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        fg = win32gui.GetForegroundWindow()
        if fg and (want is None or fg == want):
            return fg
        time.sleep(0.1)
    return win32gui.GetForegroundWindow()


def main():
    person = Window("PERSON -- stands in for what you were using", 80, 80)
    app = Window("APP -- stands in for the automated window", 560, 80)
    print(f"person window = {person.hwnd}\napp window    = {app.hwnd}\n")

    try:
        window_manager._force_foreground(person.hwnd)
        if settled_foreground(person.hwnd) != person.hwnd:
            print("could not put the stand-in person's window in front; "
                  "something else is holding the foreground")
            return 1

        print("plain hand-back:")
        window_manager.bring_to_foreground(app.hwnd)
        check("automation took the foreground", settled_foreground(app.hwnd), app.hwnd)
        title, reason = window_manager.hand_back_foreground()
        check("hand-back reported a reason", reason, "handed back")
        check("the person's window is in front again",
              settled_foreground(person.hwnd), person.hwnd)
        check("and it named the right window", title, person.title)

        print("\nnothing owed the second time:")
        _, reason2 = window_manager.hand_back_foreground()
        check("does not fire twice", reason2, "nothing to hand back")

        print("\nrefuses while a menu is open:")
        window_manager.bring_to_foreground(app.hwnd)
        settled_foreground(app.hwnd)
        input_sim.press_keys(["alt", "space"])   # the system menu: a real Win32 menu
        time.sleep(1.0)
        check("a menu is detected", window_manager.a_menu_is_open(), True)
        _, reason3 = window_manager.hand_back_foreground()
        check("refuses to hand back", reason3, "a menu is open")
        check("and the app still has the foreground",
              settled_foreground(app.hwnd), app.hwnd)
        input_sim.press_key("escape")
        time.sleep(0.8)
        check("menu gone after Escape", window_manager.a_menu_is_open(), False)

        print("\nand what it owed was not forgotten:")
        _, reason4 = window_manager.hand_back_foreground()
        check("hands back once the menu is closed", reason4, "handed back")
        check("back to the person's window",
              settled_foreground(person.hwnd), person.hwnd)

        print("\nrefuses if the person already moved elsewhere:")
        window_manager.bring_to_foreground(app.hwnd)
        settled_foreground(app.hwnd)
        # Stand in for the person clicking away mid-action.
        window_manager._force_foreground(person.hwnd)
        check("something else is in front", settled_foreground(person.hwnd), person.hwnd)
        _, reason5 = window_manager.hand_back_foreground()
        check("refuses", reason5, "the person is somewhere else already")

        print("\nthe whole path through the server wrapper:")
        import asyncio
        server._focus_return["enabled"] = True
        server._state["hwnd"] = app.hwnd
        window_manager._force_foreground(person.hwnd)
        settled_foreground(person.hwnd)
        asyncio.run(server.mcp.call_tool("press_key", {"key": "escape"}))
        check("after a real tool call the person has their window back",
              settled_foreground(person.hwnd), person.hwnd, describe)

        print("\nkeep_foreground(true) stops it:")
        asyncio.run(server.mcp.call_tool("keep_foreground", {"enabled": True}))
        check("switch is off", server._focus_return["enabled"], False)
        asyncio.run(server.mcp.call_tool("press_key", {"key": "escape"}))
        check("the app keeps the foreground",
              settled_foreground(app.hwnd), app.hwnd, describe)
        asyncio.run(server.mcp.call_tool("keep_foreground", {"enabled": False}))
        check("switch is on again", server._focus_return["enabled"], True)
        check("and it gave the window back immediately",
              settled_foreground(person.hwnd), person.hwnd, describe)
    finally:
        server._focus_return["enabled"] = True
        person.close()
        app.close()

    print(f"\nPASS: {PASS}, FAIL: {FAIL}")
    print("ALL PASSED" if FAIL == 0 else "FAILURES ABOVE")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
