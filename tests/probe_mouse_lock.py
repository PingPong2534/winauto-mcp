"""Can a low-level mouse hook actually pin the pointer where we put it?

The hover design is: put the pointer on the target, hold it still, wait for the
app to react, photograph the result, then give the pointer and the foreground
back. All of that is straightforward except the holding, and the holding rests
on one fact I have not measured: **does returning 1 from a WH_MOUSE_LL callback
stop the cursor from moving, or only stop apps from hearing about it?**

If it merely hides the event, the pointer still slides off the target under the
person's hand and the photograph is of nothing. Then the mechanism has to be
ClipCursor instead -- which is system-wide state that does NOT belong to this
process, so a crash mid-hold could leave a stranger's cursor trapped in a 1x1
box. That is the risk this measurement exists to avoid taking blindly.

A hook cannot be fooled with a fake struct the way `Guard.decide` can, so this
installs a real one. What it cannot do is grow a hand: every event Python can
send is injected, so "the person's mouse" is played by an injection carrying a
FOREIGN signature -- which reaches the callback through exactly the same path a
hand does, differing only in a flag this decision never reads. The genuinely
human case is reported opportunistically: if you happen to move the mouse while
it runs, the count below is not zero.

Run: .venv\\Scripts\\python.exe tests\\probe_mouse_lock.py
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32api

import input_sim

WH_MOUSE_LL = 14
LLMHF_INJECTED = 0x0001
WM_QUIT = 0x0012

# Someone else's automation, as far as the callback can tell.
FOREIGN = 0xDEADBEEF

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wt.POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wt.WPARAM, ctypes.POINTER(MSLLHOOKSTRUCT)
)


class Hook:
    """Swallows every mouse event that is not ours, while `deadline` is ahead.

    The deadline is the lease from input_guard, reduced to its essentials: the
    hold ends on its own whatever the caller does, so a bug here cannot cost
    more than a couple of seconds of pointer.
    """

    def __init__(self):
        self.handle = None
        self.deadline = 0.0
        self.swallowed = 0
        self.passed = 0
        self.human_seen = 0          # genuinely not injected: a real hand
        self.worst_callback_ms = 0.0
        self._tid = None
        self._ready = threading.Event()
        self._proc = HOOKPROC(self._callback)  # kept alive deliberately
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _callback(self, nCode, wParam, lParam):
        started = time.perf_counter()
        try:
            if nCode >= 0:
                m = lParam[0]
                extra = m.dwExtraInfo or 0
                if not m.flags & LLMHF_INJECTED:
                    self.human_seen += 1
                if extra != input_sim.SIGNATURE and time.perf_counter() < self.deadline:
                    self.swallowed += 1
                    return 1
                self.passed += 1
        finally:
            self.worst_callback_ms = max(
                self.worst_callback_ms, (time.perf_counter() - started) * 1000
            )
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        self.handle = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        self._ready.set()
        if not self.handle:
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.DispatchMessageW(ctypes.byref(msg))

    def start(self):
        self._thread.start()
        self._ready.wait(3.0)
        return bool(self.handle)

    def stop(self):
        self.deadline = 0.0
        if self.handle:
            user32.UnhookWindowsHookEx(self.handle)
            self.handle = None
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)


def move_as(x, y, signature):
    """input_sim.move_to, but stamped with a signature of our choosing."""
    ax, ay = input_sim._screen_to_absolute(x, y)
    flags = (input_sim.MOUSEEVENTF_MOVE | input_sim.MOUSEEVENTF_ABSOLUTE
             | input_sim.MOUSEEVENTF_VIRTUALDESK)
    mi = input_sim.MouseInput(ax, ay, 0, flags, 0, signature)
    inp = input_sim.Input(type=input_sim.INPUT_MOUSE, ii=input_sim.InputUnion(mi=mi))
    arr = (input_sim.Input * 1)(inp)
    user32.SendInput(1, ctypes.pointer(arr), ctypes.sizeof(input_sim.Input))


PASSED, FAILED = [], []


def check(label, ok, detail=""):
    (PASSED if ok else FAILED).append(label)
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{'  -- ' + detail if detail else ''}")


def settle(seconds=0.12):
    """SendInput is asynchronous; the cursor moves a moment after we ask."""
    time.sleep(seconds)


def near(got, want, slack=2):
    """An absolute move is expressed in 0..65535 across the virtual desktop, so
    it lands within a pixel of the asked-for point rather than exactly on it.
    Measured: asking for (600, 400) puts the cursor at (599, 399). That is the
    coordinate conversion, not the lock, so these comparisons allow for it --
    the question here is only whether the pointer moved or stayed."""
    return abs(got[0] - want[0]) <= slack and abs(got[1] - want[1]) <= slack


def main():
    parked = win32api.GetCursorPos()
    anchor = (600, 400)
    away = (900, 650)

    hook = Hook()
    if not hook.start():
        print(f"SetWindowsHookEx failed: {ctypes.get_last_error()}")
        return 1

    try:
        print("hook installed\n")

        print("while the hold is running:")
        hook.deadline = time.perf_counter() + 3.0
        move_as(*anchor, input_sim.SIGNATURE)
        settle()
        landed = win32api.GetCursorPos()
        check("our own move still reaches the cursor", near(landed, anchor),
              f"asked for {anchor}, cursor is at {landed}")
        anchor = landed  # where it actually is, so "did not move" means exactly that

        move_as(*away, FOREIGN)
        settle()
        after = win32api.GetCursorPos()
        check("someone else's move does NOT move the cursor", after == anchor,
              f"asked for {away}, cursor is at {after}")

        # The whole point: many events in a row, as a hand produces, not one.
        for step in range(1, 21):
            move_as(anchor[0] + step * 12, anchor[1] + step * 8, FOREIGN)
        settle(0.3)
        after = win32api.GetCursorPos()
        check("a stream of 20 foreign moves leaves the cursor where we put it",
              after == anchor, f"cursor is at {after}")

        print("\nafter the hold ends:")
        hook.deadline = 0.0
        move_as(*away, FOREIGN)
        settle()
        after = win32api.GetCursorPos()
        check("the pointer is given back", near(after, away), f"cursor is at {after}")

        print("\nthe lease expires on its own:")
        hook.deadline = time.perf_counter() + 0.5
        move_as(*anchor, FOREIGN)
        settle()
        held = win32api.GetCursorPos()
        time.sleep(0.6)
        move_as(*anchor, FOREIGN)
        settle()
        released = win32api.GetCursorPos()
        check("held while the lease runs, released when it expires",
              held == after and near(released, anchor),
              f"during {held}, after {released}")

        print("\ncost:")
        check("callback stays far under LowLevelHooksTimeout (300ms)",
              hook.worst_callback_ms < 50,
              f"worst {hook.worst_callback_ms:.2f}ms over "
              f"{hook.swallowed + hook.passed} events")
        print(f"  swallowed {hook.swallowed}, passed {hook.passed}, "
              f"genuinely-human events seen: {hook.human_seen}"
              f"{' (nobody touched the mouse, so the human path is untested here)' if not hook.human_seen else ''}")

        print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
        for label in FAILED:
            print(f"  FAILED: {label}")
        return 1 if FAILED else 0
    finally:
        hook.stop()
        settle()
        win32api.SetCursorPos(parked)


if __name__ == "__main__":
    sys.exit(main())
