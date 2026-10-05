"""Does attaching lazily -- without raising the window -- cost anything later?

The worry it answers: if attach_window no longer brings the window to the
front, the first click has to do it instead, so has the work just moved to a
worse place? Prints milliseconds; asserts nothing.

The reason to expect "no" is that every input path in input_sim already calls
bring_to_foreground itself (6 call sites), so the raise was being paid at
attach AND again at the first action. Removing it from attach deletes one of
the two, it does not defer a new cost. This measures whether that holds.
"""

import asyncio
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import win32gui  # noqa: E402

import server  # noqa: E402
import window_manager  # noqa: E402

ROUNDS = 5


def call(name, **kwargs):
    """Through the MCP layer, the way a client reaches it -- measuring the bare
    function would leave out whatever the transport and the journal decorator
    cost, which is part of what the caller waits for."""
    return asyncio.run(server.mcp.call_tool(name, kwargs))


def find_notepad(pid):
    for _ in range(60):
        for w in window_manager.list_windows():
            if w["pid"] == pid or "notepad" in w["process"].lower():
                return w["hwnd"]
        time.sleep(0.1)
    raise RuntimeError("window never appeared")


def ms(fn):
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2]


def main():
    notepad = subprocess.Popen(["notepad.exe"])
    other = subprocess.Popen(["calc.exe"])
    try:
        hwnd = find_notepad(notepad.pid)
        time.sleep(3.5)
        lazy, eager, click_behind, click_front, reads = [], [], [], [], []

        for _ in range(ROUNDS):
            # Park someone else's window in front, the situation that matters.
            their = win32gui.GetForegroundWindow()
            if their == hwnd:
                print("  (calculator never took the foreground; numbers are less meaningful)")

            lazy.append(ms(lambda: call("attach_window", hwnd=hwnd)))
            reads.append(ms(lambda: call("screenshot")))
            # First action after a lazy attach: this is where the raise lands.
            click_behind.append(ms(lambda: call("click", x=300, y=300, force=True)))
            # Second action, window already in front: bring_to_foreground's
            # early-out should make this nearly free.
            click_front.append(ms(lambda: call("click", x=300, y=300, force=True)))

            window_manager.restore_foreground()
            time.sleep(0.6)
            eager.append(ms(lambda: call("attach_window", hwnd=hwnd, take_control=True)))
            window_manager.restore_foreground()
            time.sleep(0.6)

        print(f"attach_window() lazy .................. {median(lazy):6.1f} ms")
        print(f"attach_window(take_control=True) ...... {median(eager):6.1f} ms")
        print(f"screenshot while it is behind ......... {median(reads):6.1f} ms")
        print(f"first click after a lazy attach ....... {median(click_behind):6.1f} ms  <- pays the raise")
        print(f"next click, already in front .......... {median(click_front):6.1f} ms")
        print()
        print(f"cost moved to the first action: {median(click_behind) - median(click_front):6.1f} ms")
        print(f"cost removed from attach:       {median(eager) - median(lazy):6.1f} ms")
    finally:
        notepad.kill()
        other.kill()


if __name__ == "__main__":
    main()
