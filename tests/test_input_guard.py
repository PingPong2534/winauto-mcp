"""Does the keyboard block let go? Tested without ever blocking a keyboard.

Every case here drives Guard.decide() with synthetic key events and an injected
clock. No hook is installed, so a bug in the release logic shows up as a failed
assertion rather than as a machine nobody can type on -- which is the whole
reason the decision logic lives in a class with no Win32 in it.

The clock is passed in rather than slept through: "the lease expires after 20
seconds" is checked by handing it t=21, not by waiting 21 seconds, so the test
runs instantly and the boundary is exact instead of approximate.

Run: python tests\\test_input_guard.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import input_guard  # noqa: E402
import input_sim  # noqa: E402
from input_guard import (  # noqa: E402
    HUMAN,
    LLKHF_INJECTED,
    LLKHF_UP,
    OTHER_INJECTOR,
    OURS,
    PASS,
    SWALLOW,
    VK_ESCAPE,
    Guard,
    classify,
)

PASSED, FAILED = [], []
VK_A = 0x41
OTHER_TOOL = 0x0BADCAFE


def check(label, got, want):
    if got == want:
        PASSED.append(label)
        print(f"  ok    {label}")
    else:
        FAILED.append(f"{label}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {label}: got {got!r}, wanted {want!r}")


def human(vk=VK_A, up=False):
    """A real keypress: no injected flag. That absence is the entire signal."""
    return {"flags": LLKHF_UP if up else 0, "extra": 0, "vk": vk}


def ours(vk=VK_A, up=False):
    return {"flags": LLKHF_INJECTED | (LLKHF_UP if up else 0),
            "extra": input_sim.SIGNATURE, "vk": vk}


def other(vk=VK_A, up=False):
    return {"flags": LLKHF_INJECTED | (LLKHF_UP if up else 0),
            "extra": OTHER_TOOL, "vk": vk}


def decide(g, event, now):
    return g.decide(event["flags"], event["extra"], event["vk"], now=now)


# --- who sent it -------------------------------------------------------------

print("\n-- telling the three sources apart")
check("no injected flag reads as human", classify(0, 0), HUMAN)
check("our signature reads as ours",
      classify(LLKHF_INJECTED, input_sim.SIGNATURE), OURS)
check("another injector is not us",
      classify(LLKHF_INJECTED, OTHER_TOOL), OTHER_INJECTOR)
check("a human event is human even if extra happens to match ours",
      classify(0, input_sim.SIGNATURE), HUMAN)
check("the signature is not zero -- an unstamped event must not read as ours",
      input_sim.SIGNATURE != 0, True)


# --- nothing is blocked until someone takes the lease ------------------------

print("\n-- idle: everything passes")
g = Guard()
check("not blocking at rest", g.blocking(now=0.0), False)
check("human key passes", decide(g, human(), 0.0), PASS)
check("our key passes", decide(g, ours(), 0.0), PASS)
check("another tool's key passes", decide(g, other(), 0.0), PASS)


# --- blocking ----------------------------------------------------------------

print("\n-- while the lease is held")
g = Guard()
check("take succeeds", g.take(seconds=5, now=0.0), True)
check("now blocking", g.blocking(now=1.0), True)
check("human key is swallowed", decide(g, human(), 1.0), SWALLOW)
check("our own key still gets through", decide(g, ours(), 1.0), PASS)
check("another injector still gets through", decide(g, other(), 1.0), PASS)
check("human key-up is swallowed too", decide(g, human(up=True), 1.0), SWALLOW)


# --- release route 1: the lease runs out on its own --------------------------

print("\n-- release 1: the lease expires with nobody calling release()")
g = Guard()
g.take(seconds=5, now=0.0)
check("blocked at t=4.9", decide(g, human(), 4.9), SWALLOW)
check("passes at t=5.0, the instant it expires", decide(g, human(), 5.0), PASS)
check("no longer blocking", g.blocking(now=5.0), False)
check("the expiry was recorded", g.status(now=5.0)["last_releases"][-1], "lease expired")

g = Guard()
check("a caller cannot ask for more than the cap",
      g.take(seconds=99999, now=0.0) and g.status(now=0.0)["lease_remaining_s"],
      input_guard.MAX_LEASE_SECONDS)
check("and it is genuinely released after the cap",
      decide(g, human(), input_guard.MAX_LEASE_SECONDS + 0.01), PASS)


# --- release route 2: explicit release ---------------------------------------

print("\n-- release 2: release() called normally")
g = Guard()
g.take(seconds=10, now=0.0)
g.release("done", now=1.0)
check("passes after release", decide(g, human(), 1.0), PASS)
check("reason recorded", g.status(now=1.0)["last_releases"][-1], "done")
check("releasing twice does not raise", (g.release("again", now=2.0), True)[1], True)


# --- release route 3: the escape chord ---------------------------------------

print("\n-- release 3: three Escapes inside the window")
g = Guard()
g.take(seconds=10, now=0.0)
check("Esc 1 swallowed, still blocking", decide(g, human(VK_ESCAPE), 1.0), SWALLOW)
check("Esc 2 swallowed, still blocking", decide(g, human(VK_ESCAPE), 1.2), SWALLOW)
check("Esc 3 passes through", decide(g, human(VK_ESCAPE), 1.4), PASS)
check("block is gone", g.blocking(now=1.4), False)
check("ordinary keys flow again", decide(g, human(), 1.5), PASS)
check("the chord was recorded", g.status(now=1.5)["last_releases"][-1], "escape chord")
check("and it latched off", g.latched_off(), True)
check("a later take() is refused -- automation cannot grab it back",
      g.take(seconds=10, now=2.0), False)
check("still not blocking after that refused take", decide(g, human(), 2.1), PASS)
check("rearm() is the only way back", (g.rearm(), g.take(seconds=5, now=3.0))[1], True)
check("blocking again after rearm", decide(g, human(), 3.1), SWALLOW)

print("\n-- the chord must not fire on slow, unrelated Escapes")
g = Guard()
g.take(seconds=20, now=0.0)
decide(g, human(VK_ESCAPE), 0.0)
decide(g, human(VK_ESCAPE), 1.0)
decide(g, human(VK_ESCAPE), 2.0)  # 2.0s apart: outside a 1.5s window
check("three Escapes spread over 2s do not release", g.blocking(now=2.0), True)
check("and did not latch off", g.latched_off(), False)

print("\n-- key-up must not count toward the chord (a tap is down+up)")
g = Guard()
g.take(seconds=20, now=0.0)
decide(g, human(VK_ESCAPE), 0.1)
decide(g, human(VK_ESCAPE, up=True), 0.15)
decide(g, human(VK_ESCAPE), 0.2)
decide(g, human(VK_ESCAPE, up=True), 0.25)
check("two taps (4 events) are not three presses", g.blocking(now=0.3), True)
check("the third press releases", decide(g, human(VK_ESCAPE), 0.3), PASS)
check("released", g.blocking(now=0.3), False)

print("\n-- an injected Escape must not release: only the person can")
g = Guard()
g.take(seconds=20, now=0.0)
for t in (0.1, 0.2, 0.3, 0.4, 0.5):
    decide(g, ours(VK_ESCAPE), t)
    decide(g, other(VK_ESCAPE), t)
check("still blocking after 10 injected Escapes", g.blocking(now=0.6), True)
check("still not latched off", g.latched_off(), False)


# --- keys already held down when the block starts ----------------------------

print("\n-- a key already physically down when the block began")
g = Guard()
g.take(seconds=10, now=0.0, held_down={VK_A})
check("its key-up passes, so it does not stick down", decide(g, human(VK_A, up=True), 1.0), PASS)
check("pressing it again is swallowed like anything else",
      decide(g, human(VK_A), 1.1), SWALLOW)
check("an unrelated key was never exempt", decide(g, human(0x42), 1.1), SWALLOW)

print("\n-- renewing a lease must not re-arm the held-down exemption")
g = Guard()
g.take(seconds=10, now=0.0, held_down={VK_A})
g.take(seconds=10, now=1.0, held_down={0x42})  # renewal; the person is not holding B
check("the renewal's held_down is ignored", decide(g, human(0x42, up=True), 1.1), SWALLOW)


# --- what the AI is told -----------------------------------------------------

print("\n-- status(), and what it refuses to know")
g = Guard()
decide(g, human(), 0.0)
decide(g, human(0x42), 0.5)
decide(g, ours(), 0.6)
s = g.status(now=1.0)
check("human events counted", s["human_key_events"], 2)
check("injected events not counted as human", s["human_key_events"], 2)
check("time since the last human key", s["seconds_since_human_key"], 0.5)
check("not blocking", s["blocking"], False)
check("no key codes anywhere in the status",
      any("vk" in k or "key_code" in k or "scan" in k for k in s), False)
check("status values are only numbers, bools, None and release reasons",
      all(isinstance(v, (int, float, bool, type(None), list)) for v in s.values()), True)
check("no human key yet reads as None, not 0",
      Guard().status(now=0.0)["seconds_since_human_key"], None)

print("\n-- an exception inside decide() must not eat the keyboard")


class Exploding(Guard):
    def decide(self, *a, **kw):
        raise RuntimeError("bug in the guard")


hook = input_guard.KeyboardHook(Exploding())
k = input_guard.KBDLLHOOKSTRUCT(vkCode=VK_A, scanCode=30, flags=0, time=0, dwExtraInfo=0)
# nCode < 0 short-circuits, so use 0: the callback must reach the guard, have it
# throw, and still hand the event on rather than returning 1 (swallow).
result = hook._callback(0, 0x0100, ctypes_ref := __import__("ctypes").pointer(k))
check("a throwing guard does not swallow", result != 1, True)
check("the hook was never installed by this test", hook.installed, False)
check("no hook installed at module level either", input_guard.hook_installed(), False)


# --- summary -----------------------------------------------------------------

print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
for f in FAILED:
    print(f"  {f}")
sys.exit(1 if FAILED else 0)
