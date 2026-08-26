"""Can we tell that a menu or dropdown is open?

The point of the question: if the foreground is handed back to the person the
moment an action ends, any menu the action just opened closes -- so the
hand-back has to be skipped while one is open. That skip is only as good as
the detector, and a detector that only sees classic Win32 menus would be
silently useless on every app written after about 2015.

So this measures two signals against two kinds of menu, and prints what each
one saw rather than asserting a hoped-for answer:

  signal A  GUITHREADINFO.flags on the foreground thread, checking
            GUI_INMENUMODE / GUI_POPUPMENUMODE / GUI_SYSTEMMENUMODE
  signal B  any visible top-level window of class '#32768' (the class Windows
            uses for real menus) or 'ComboLBox' (a dropped-down combo box)

  menu 1    the system menu (Alt+Space) -- a genuine classic menu, on any
            window, guaranteed present on every Windows there has ever been
  menu 2    Windows 11 Notepad's own File menu (Alt+F) -- XAML, drawn by the
            app, not a window Windows knows is a menu

Run: .venv\\Scripts\\python.exe tests\\probe_popup_detect.py
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32con
import win32gui

import input_sim
import window_manager

user32 = ctypes.windll.user32

GUI_MENU_MASK = (
    win32con.GUI_INMENUMODE | win32con.GUI_POPUPMENUMODE | win32con.GUI_SYSTEMMENUMODE
)


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hwndActive", wt.HWND),
        ("hwndFocus", wt.HWND),
        ("hwndCapture", wt.HWND),
        ("hwndMenuOwner", wt.HWND),
        ("hwndMoveSize", wt.HWND),
        ("hwndCaret", wt.HWND),
        ("rcCaret", wt.RECT),
    ]


def signal_a():
    """Menu mode according to Windows, for the foreground thread."""
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    fg = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return None
    return {
        "flags": hex(info.flags),
        "in_menu": bool(info.flags & GUI_MENU_MASK),
        "menu_owner": info.hwndMenuOwner or None,
    }


def signal_b():
    """Visible top-level windows of a menu-ish class."""
    found = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        cls = win32gui.GetClassName(hwnd)
        if cls in ("#32768", "ComboLBox"):
            found.append((hwnd, cls))

    win32gui.EnumWindows(visit, None)
    return found


def report(label):
    a = signal_a()
    b = signal_b()
    print(f"  {label:<28} A={a}  B={b}")
    return bool(a and a['in_menu']), bool(b)


def notepad_pids():
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Notepad.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    ).stdout
    return {int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"')}


def main():
    # notepad.exe is a stub that hands off to a packaged Notepad.exe with a
    # different PID and exits, so Popen's handle is not the window. Close only
    # what appeared after we launched, never one the person already had open.
    before = notepad_pids()
    subprocess.Popen(["notepad.exe"])
    time.sleep(1.5)
    hwnd = None
    for _ in range(20):
        for w in window_manager.list_windows():
            if w["process"].lower().startswith("notepad"):
                hwnd = w["hwnd"]
                break
        if hwnd:
            break
        time.sleep(0.3)
    if not hwnd:
        print("could not find a Notepad window")
        return 1

    window_manager.bring_to_foreground(hwnd)
    time.sleep(0.5)
    print(f"notepad hwnd={hwnd}  fg={win32gui.GetForegroundWindow()}")

    print("\nbaseline (no menu):")
    a0, b0 = report("nothing open")

    print("\nsystem menu, Alt+Space (a real classic menu):")
    input_sim.press_keys(["alt", "space"])
    time.sleep(0.8)
    a1, b1 = report("system menu open")
    input_sim.press_key("escape")
    time.sleep(0.5)
    a2, b2 = report("after Esc")

    print("\nNotepad's own File menu, Alt+F (XAML on Win11):")
    input_sim.press_keys(["alt", "f"])
    time.sleep(1.0)
    a3, b3 = report("File menu open")
    input_sim.press_key("escape")
    time.sleep(0.5)
    a4, b4 = report("after Esc")

    print("\nverdict:")
    print(f"  classic menu detected by A: {a1}   by B: {b1}")
    print(f"  XAML menu    detected by A: {a3}   by B: {b3}")
    print(f"  quiet when nothing is open: A: {not a0 and not a2 and not a4} "
          f"B: {not b0 and not b2 and not b4}")

    leaked = notepad_pids() - before
    for pid in leaked:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    print(f"  closed {len(leaked)} notepad process(es) this probe started")
    return 0


if __name__ == "__main__":
    sys.exit(main())
