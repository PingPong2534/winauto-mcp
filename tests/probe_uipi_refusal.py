"""Prove the UIPI refusal fires when it must and stays quiet when it must not.

A guard that refuses too eagerly is worse than none: it makes working windows
permanently undriveable and there is no error message that says "this refusal
was wrong". So both directions are asserted here, and the quiet direction is
asserted against every window on the machine rather than one chosen example.

The refusal is tested WITHOUT sending anything into the elevated window. That
is not a limitation of the test, it is the behaviour: bring_to_foreground
raises before it takes control, so the check is "did it raise, and was the
desktop left exactly as it was" -- no clicks land in an administrator's Task
Scheduler to find out.

Run:  .venv\\Scripts\\python.exe tests\\probe_uipi_refusal.py
"""

import os
import sys
import time

import win32gui
import win32process

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrity  # noqa: E402
import input_sim  # noqa: E402
import window_manager  # noqa: E402

passed, failed = 0, 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}" + (f"  -- {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f"  -- {detail}" if detail else ""))


print(f"server integrity: {integrity.level_name(integrity.own_level())}"
      f"  uiAccess={integrity.has_uiaccess()}\n")

windows = window_manager.list_windows()
above = [w for w in windows if w["input_blocked"]]
peers = [w for w in windows if not w["input_blocked"]]

print(f"{len(windows)} windows: {len(above)} above us, {len(peers)} at or below\n")

# --- the quiet direction, checked against every ordinary window -------------
print("must NOT refuse (every window at or below our level):")
wrongly_blocked = []
for w in peers:
    level = integrity.window_level(w["hwnd"])
    if integrity.blocks_input(level):
        wrongly_blocked.append(w)
check("no window at or below our level is refused",
      not wrongly_blocked,
      f"{len(peers)} checked" if not wrongly_blocked else f"wrongly refused: {wrongly_blocked}")

# An unreadable level must read as "allowed", never as "elevated" -- otherwise
# any process that denies for an unrelated reason becomes undriveable.
check("an unreadable integrity level is not treated as elevated",
      integrity.blocks_input(None) is False)

check("a low-integrity target is not refused",
      integrity.blocks_input(integrity.LOW) is False,
      "sandboxed windows are below us, not above")

# --- the loud direction ------------------------------------------------------
print("\nmust refuse (windows above our level):")
if not above:
    print("  SKIP  no elevated window is open -- start one 'as administrator' to cover this")
else:
    for w in above:
        hwnd = w["hwnd"]
        label = f'{w["process"]} "{w["title"][:34]}"'
        print(f"  target: {label}  integrity={w['integrity']}")

        before_fg = win32gui.GetForegroundWindow()
        before_cursor = win32gui.GetCursorPos() if hasattr(win32gui, "GetCursorPos") else None

        raised = None
        try:
            window_manager.bring_to_foreground(hwnd)
        except integrity.InputBlocked as exc:
            raised = exc
        except Exception as exc:  # noqa: BLE001
            raised = exc

        check("bring_to_foreground raises InputBlocked",
              isinstance(raised, integrity.InputBlocked),
              type(raised).__name__ if raised else "did not raise at all")

        if isinstance(raised, integrity.InputBlocked):
            msg = str(raised)
            check("the message names both integrity levels",
                  w["integrity"].split()[0] in msg and integrity.level_name(integrity.own_level()) in msg)
            check("the message says nothing was sent", "Nothing was sent" in msg)
            check("the message says how to fix it", "Run as administrator" in msg)
            check("the message says reading still works", "screenshot" in msg)

        time.sleep(0.15)
        check("the foreground was not touched",
              win32gui.GetForegroundWindow() == before_fg,
              f"was {before_fg}, now {win32gui.GetForegroundWindow()}")

        # Every input tool must refuse, not just the one that happens to be
        # tested -- they all funnel through the same call, and this is what
        # proves that is actually true rather than intended.
        for name, call in (
            ("click_in_window", lambda: input_sim.click_in_window(hwnd, 10, 10)),
            ("type_text", lambda: input_sim.type_text("x", hwnd=hwnd)),
            ("press_key", lambda: input_sim.press_key("escape", hwnd=hwnd)),
            ("press_keys", lambda: input_sim.press_keys(["ctrl", "c"], hwnd=hwnd)),
            ("scroll_in_window", lambda: input_sim.scroll_in_window(hwnd, 10, 10, 1)),
            ("drag_in_window", lambda: input_sim.drag_in_window(hwnd, 10, 10, 20, 20)),
        ):
            try:
                call()
                check(f"{name} refuses", False, "it went ahead and sent input")
            except integrity.InputBlocked:
                check(f"{name} refuses", True)
            except Exception as exc:  # noqa: BLE001
                check(f"{name} refuses", False, f"raised {type(exc).__name__} instead")

# --- the SendInput return check must not have broken ordinary input ---------
print("\nordinary input still works (the SendInput return-value check):")
try:
    origin = __import__("win32api").GetCursorPos()
    input_sim.move_to(origin[0], origin[1])
    check("move_to with no target window succeeds", True, "SendInput took every event")
except Exception as exc:  # noqa: BLE001
    check("move_to with no target window succeeds", False, f"{type(exc).__name__}: {exc}")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
