"""Does the keyboard block actually swallow, and actually let go? Real hook.

tests\\test_input_guard.py proves the decision logic in isolation. This proves
the other half -- that SetWindowsHookEx really is installed, that returning 1
from the callback really does stop a character reaching the app, and that our
own input really does get through the same block. Those cannot be tested
against a fake; they need the OS.

WHY THIS DOES NOT LOCK THE REAL KEYBOARD, even for an instant:

The guard used here relabels who is who. An event tagged OTHER_TOOL is treated
as if it came from a person, and an event that genuinely came from a person is
treated as if it came from another injector -- a class that always passes. The
production decide() runs completely unmodified; only which events reach its
human branch changes. So the swallow path is exercised for real, through a real
hook, on characters this script types itself, while anything typed on the
actual keyboard is guaranteed to pass. Verifying a keyboard lock by locking the
keyboard is the one experiment that can leave you unable to type the fix.

Notepad is read back through UI Automation rather than by diffing pixels: its
caret blinks, so "no character arrived" would still show changed pixels.

Run: .venv\\Scripts\\python.exe tests\\diag_keyboard_block.py
"""

import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import comtypes  # noqa: E402
import uiautomation as auto  # noqa: E402

import input_guard  # noqa: E402
import input_sim  # noqa: E402
import window_manager  # noqa: E402
from input_guard import LLKHF_INJECTED, Guard  # noqa: E402

OTHER_TOOL = 0x0BADCAFE      # stands in for the person, inside this test only
NOT_US = 0xDEADBEEF          # what the real person's keys get relabelled to
LEASE = 2.5

results = []


def check(label, condition, detail=""):
    results.append(bool(condition))
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{(' -- ' + detail) if detail else ''}")


class ImpostorGuard(Guard):
    """Swaps the two labels so the human branch can be driven from software.

    A real keypress cannot be manufactured -- LLKHF_INJECTED is set by Windows
    on anything SendInput delivers, and clearing it would need a driver. So
    instead of faking a human event, this makes the guard call one of our
    tagged events the human. The consequence that matters is the other half of
    the swap: a genuine keypress lands in a class that is never swallowed.
    """

    def decide(self, flags, extra, vk, now=None):
        if flags & LLKHF_INJECTED and extra == OTHER_TOOL:
            flags &= ~LLKHF_INJECTED      # -> reads as the person
            extra = 0
        elif not flags & LLKHF_INJECTED:
            flags |= LLKHF_INJECTED       # the person -> reads as another tool
            extra = NOT_US
        return super().decide(flags, extra, vk, now)


def send_tagged(char, signature):
    """Type one character with a chosen dwExtraInfo, so this script can pose as
    the person, as us, or as a third tool."""
    for up in (False, True):
        flags = input_sim.KEYEVENTF_UNICODE | (input_sim.KEYEVENTF_KEYUP if up else 0)
        input_sim._send(input_sim.Input(
            type=input_sim.INPUT_KEYBOARD,
            ii=input_sim.InputUnion(
                ki=input_sim.KeyBdInput(0, ord(char), flags, 0, signature)),
        ))
        time.sleep(0.01)


def send_vk_tagged(vk, signature):
    for up in (False, True):
        flags = input_sim.KEYEVENTF_KEYUP if up else 0
        input_sim._send(input_sim.Input(
            type=input_sim.INPUT_KEYBOARD,
            ii=input_sim.InputUnion(ki=input_sim.KeyBdInput(vk, 0, flags, 0, signature)),
        ))
        time.sleep(0.02)


def document(hwnd):
    """What Notepad says it contains, verbatim."""
    root = auto.ControlFromHandle(hwnd)

    def walk(c, d=0):
        if d > 12:
            return None
        try:
            if c.ControlTypeName == "DocumentControl":
                return c.GetValuePattern().Value
            for ch in c.GetChildren():
                found = walk(ch, d + 1)
                if found is not None:
                    return found
        except Exception:
            pass
        return None

    return walk(root)


def main():
    proc = subprocess.Popen(["notepad.exe"])
    guard = ImpostorGuard()
    hook = input_guard.KeyboardHook(guard)
    try:
        time.sleep(2.0)
        hwnd = next(
            (w["hwnd"] for w in window_manager.list_windows()
             if w["pid"] == proc.pid or "notepad" in w["process"].lower()),
            None,
        )
        if hwnd is None:
            print("  [FAIL] no Notepad window")
            return 1
        comtypes.CoInitialize()
        window_manager.bring_to_foreground(hwnd)
        time.sleep(0.8)
        if document(hwnd) != "":
            print(f"  [FAIL] Notepad did not start empty: {document(hwnd)!r}")
            return 1

        print("\n-- installing the hook")
        check("SetWindowsHookEx succeeded", hook.start())
        check("reports itself installed", hook.installed)

        print("\n-- with the block held, who gets through")
        check("take() granted", guard.take(seconds=LEASE))
        send_tagged("X", OTHER_TOOL)          # posing as the person
        input_sim.type_text("Y")              # genuinely us
        send_tagged("Z", 0x11223344)          # a third injector
        time.sleep(0.4)
        text = document(hwnd)
        check("the person's keystroke was swallowed", "X" not in (text or ""), repr(text))
        check("our own keystroke got through", "Y" in (text or ""), repr(text))
        check("another tool's keystroke got through", "Z" in (text or ""), repr(text))
        check("the guard counted a human key without storing it",
              guard.status()["human_key_events"] >= 1,
              f"count={guard.status()['human_key_events']}")

        print(f"\n-- the lease expiring on its own ({LEASE}s, no release() call)")
        deadline = time.monotonic() + LEASE + 1.0
        while guard.blocking() and time.monotonic() < deadline:
            time.sleep(0.1)
        check("stopped blocking without being told to", not guard.blocking())
        send_tagged("X", OTHER_TOOL)
        time.sleep(0.4)
        text = document(hwnd)
        check("the same keystroke now arrives", "X" in (text or ""), repr(text))
        check("recorded why it ended", guard.status()["last_releases"][-1:] == ["lease expired"],
              str(guard.status()["last_releases"]))

        print("\n-- the panic path: three Escapes cut a 20-second lease short")
        check("a long lease was taken", guard.take(seconds=20.0))
        started = time.monotonic()
        for _ in range(3):
            send_vk_tagged(input_guard.VK_ESCAPE, OTHER_TOOL)
            time.sleep(0.15)
        elapsed = time.monotonic() - started
        check("released well before the lease would have expired",
              not guard.blocking(), f"after {elapsed:.2f}s of a 20s lease")
        check("latched off, so automation cannot take it straight back",
              guard.latched_off() and guard.take(seconds=5) is False)
        before = document(hwnd)
        send_tagged("W", OTHER_TOOL)
        time.sleep(0.4)
        after = document(hwnd)
        check("typing works again immediately", "W" in (after or ""),
              f"{before!r} -> {after!r}")

        print("\n-- what the AI would be shown")
        for key, value in guard.status().items():
            print(f"         {key}: {value}")
    finally:
        hook.stop()
        proc.kill()

    failed = results.count(False)
    print(f"\n{results.count(True)} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
