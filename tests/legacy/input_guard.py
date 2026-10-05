"""Keep the person's input out of the app while automation is driving it.

A low-level hook (WH_KEYBOARD_LL, WH_MOUSE_LL) sees every input event on the
machine and can swallow one by returning without passing it on. That is the
mechanism. Everything else here exists to make sure the input comes back.

WHAT IS STORED: nothing. Not the key, not the scan code, not a count of which
keys, and for the mouse not a single coordinate. Only "a human input event
happened, at this monotonic time". The hooks are deliberately incapable of
recording what anyone typed or where they pointed, in this process or any
other -- a global keyboard hook that kept key codes would be a keylogger, and
the only honest way to promise it is not one is for the data never to exist.

HOW OUR OWN INPUT GETS THROUGH: every event this process sends carries
input_sim.SIGNATURE in dwExtraInfo. Windows' LLKHF_INJECTED flag alone is not
enough -- it is set by any injector, including other automation tools and the
on-screen keyboard. Ours is passed through; the person's is swallowed.

THE FIVE WAYS THE KEYBOARD COMES BACK, so that no single failure strands it:

  1. The block is a LEASE, not a lock. It expires on its own after
     MAX_LEASE_SECONDS whatever happens -- no release call needed, no
     cooperation from the caller, no working server required.
  2. ESCAPE_PRESSES taps of Esc inside ESCAPE_WINDOW release it immediately AND
     latch it off, so the next tool call cannot silently take the keyboard back.
     Someone who reaches for this is having a problem; re-blocking on them
     would be the worst thing to do.
  3. Windows removes a low-level hook whose callback overruns
     LowLevelHooksTimeout (300ms default), and removes all hooks when the
     owning process exits. So a hung callback or a killed server self-heal.
  4. Ctrl+Alt+Del is handled by Winlogon beneath the hook chain and cannot be
     blocked by anything here, by design of the OS.

THE MOUSE IS HELD TOO, but only by hover(), and on much tighter terms. Holding
the pointer is a bigger imposition than holding the keyboard -- the pointer is
what a person reaches for when anything at all goes wrong -- so the mouse hold
is bounded by MAX_MOUSE_LEASE_SECONDS rather than MAX_LEASE_SECONDS, is never
taken while a mouse button is physically down (that would strand a drag), and
is refused outright once the escape chord has been used. It exists because a
hover has to be still to be photographed: the pointer must stay on the target
long enough for the app to raise its tooltip, and a hand on the mouse during
that half-second produces a picture of nothing. See `holding_mouse`.
"""

import collections
import contextlib
import ctypes
import ctypes.wintypes as wt
import threading
import time

import input_sim

# --- constants -----------------------------------------------------------------

WH_KEYBOARD_LL = 13
LLKHF_INJECTED = 0x10
LLKHF_UP = 0x80
VK_ESCAPE = 0x1B
WM_QUIT = 0x0012

# No caller may hold the keyboard longer than this without renewing. Chosen to
# be longer than any single action but far shorter than a person's patience:
# the worst case a bug can produce is this many seconds of a dead keyboard.
MAX_LEASE_SECONDS = 20.0

ESCAPE_PRESSES = 3
ESCAPE_WINDOW = 1.5

HUMAN, OURS, OTHER_INJECTOR = "human", "ours", "other_injector"

PASS, SWALLOW = False, True


def classify(flags: int, extra: int) -> str:
    """Who sent this event. Pure; the whole discriminator."""
    if not flags & LLKHF_INJECTED:
        return HUMAN
    return OURS if extra == input_sim.SIGNATURE else OTHER_INJECTOR


class Guard:
    """The decision logic, with no Win32 in it, so it can be tested against
    synthetic events instead of against a real keyboard. Installing the hook
    is a separate object below -- verifying a keyboard lock by locking the
    keyboard is exactly the experiment that leaves you unable to type the fix.
    """

    def __init__(self, max_lease=MAX_LEASE_SECONDS, escape_presses=ESCAPE_PRESSES,
                 escape_window=ESCAPE_WINDOW):
        self._max_lease = max_lease
        self._escape_presses = escape_presses
        self._escape_window = escape_window
        self._lock = threading.Lock()
        self._lease_until = 0.0
        self._latched_off = False          # the person demanded the keyboard back
        self._escapes = collections.deque()
        self._held_down = set()            # keys physically down when the block began
        self.human_events = 0              # a count and a time. Never a key code.
        self.last_human = None
        self.releases = []                 # why each block ended, for reporting

    # --- state ------------------------------------------------------------

    def blocking(self, now=None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return self._blocking_locked(now)

    def _blocking_locked(self, now) -> bool:
        if self._latched_off:
            return False
        if now >= self._lease_until:
            if self._lease_until:
                self._lease_until = 0.0
                self.releases.append("lease expired")
            return False
        return True

    def take(self, seconds=None, now=None, held_down=()) -> bool:
        """Start or renew the block. Returns False if the person has latched it
        off -- automation carries on, it just does not get the keyboard."""
        now = time.monotonic() if now is None else now
        seconds = self._max_lease if seconds is None else min(seconds, self._max_lease)
        with self._lock:
            if self._latched_off:
                return False
            if not self._lease_until:
                # Keys the person is physically holding as the block starts:
                # their key-down already reached the app, so swallowing the
                # key-up would strand it down forever. Let those up.
                self._held_down = set(held_down)
            self._lease_until = now + seconds
            return True

    def release(self, reason="released", now=None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._lease_until:
                self.releases.append(reason)
            self._lease_until = 0.0
            self._held_down.clear()

    def rearm(self) -> None:
        """Allow blocking again after the person latched it off. Deliberately
        explicit: nothing re-arms on its own, or the escape chord would be a
        suggestion rather than a decision."""
        with self._lock:
            self._latched_off = False
            self._escapes.clear()

    def latched_off(self) -> bool:
        with self._lock:
            return self._latched_off

    # --- the hook's decision ---------------------------------------------

    def decide(self, flags: int, extra: int, vk: int, now=None) -> bool:
        """SWALLOW or PASS for one key event. Must stay cheap: Windows drops a
        low-level hook whose callback overruns its timeout, and does not say so.
        """
        now = time.monotonic() if now is None else now
        source = classify(flags, extra)
        if source is not HUMAN:
            return PASS

        with self._lock:
            self.human_events += 1
            self.last_human = now

            if vk == VK_ESCAPE and not flags & LLKHF_UP:
                self._escapes.append(now)
                while self._escapes and now - self._escapes[0] > self._escape_window:
                    self._escapes.popleft()
                if len(self._escapes) >= self._escape_presses:
                    self._escapes.clear()
                    if self._lease_until:
                        self.releases.append("escape chord")
                    self._lease_until = 0.0
                    self._latched_off = True
                    self._held_down.clear()
                    return PASS

            if not self._blocking_locked(now):
                return PASS

            # A key already down before the block started must be allowed to
            # come up, or the app it went down in keeps it down forever.
            if vk in self._held_down:
                if flags & LLKHF_UP:
                    self._held_down.discard(vk)
                return PASS

            return SWALLOW

    def status(self, now=None) -> dict:
        now = time.monotonic() if now is None else now
        with self._lock:
            held = max(0.0, self._lease_until - now)
            return {
                "blocking": self._blocking_locked(now),
                "lease_remaining_s": round(held, 2),
                "latched_off_by_user": self._latched_off,
                "human_key_events": self.human_events,
                "seconds_since_human_key": (
                    None if self.last_human is None else round(now - self.last_human, 2)
                ),
                "last_releases": self.releases[-5:],
            }


# --- the part that touches Windows ---------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wt.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT)
)


def keys_physically_down():
    """VKs held right now, so their key-up can be let through. GetAsyncKeyState
    reads the hardware, which is what "physically" has to mean here."""
    down = set()
    for vk in range(1, 256):
        if user32.GetAsyncKeyState(vk) & 0x8000:
            down.add(vk)
    return down


class KeyboardHook:
    """Owns the OS hook and a thread to pump messages for it. One per process."""

    def __init__(self, guard: Guard):
        self.guard = guard
        self._handle = None
        self._tid = None
        self._ready = threading.Event()
        self._proc = HOOKPROC(self._callback)  # kept alive deliberately
        self._thread = threading.Thread(target=self._run, daemon=True, name="winauto-kbd-hook")

    def _callback(self, nCode, wParam, lParam):
        if nCode >= 0:
            k = lParam[0]
            try:
                if self.guard.decide(k.flags, k.dwExtraInfo or 0, k.vkCode) is SWALLOW:
                    return 1  # consumed: no other hook and no app will see it
            except Exception:  # noqa: BLE001 - never let a bug here eat the keyboard
                pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        self._handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        self._ready.set()
        if not self._handle:
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.DispatchMessageW(ctypes.byref(msg))

    def start(self, timeout=3.0) -> bool:
        if self._thread.is_alive():
            return bool(self._handle)
        self._thread.start()
        self._ready.wait(timeout=timeout)
        return bool(self._handle)

    def stop(self) -> None:
        if self._handle:
            user32.UnhookWindowsHookEx(self._handle)
            self._handle = None
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._tid = None

    @property
    def installed(self) -> bool:
        return bool(self._handle)


_guard = Guard()
_hook = None


def guard() -> Guard:
    return _guard


def ensure_hook() -> bool:
    """Install the hook on first use. Not installed at import: a server that is
    only ever used to read screens has no business watching the keyboard."""
    global _hook
    if _hook is None:
        _hook = KeyboardHook(_guard)
    return _hook.start()


def hook_installed() -> bool:
    return _hook is not None and _hook.installed


enabled = True  # a single switch, so blocking can be turned off wholesale


@contextlib.contextmanager
def holding(seconds=None):
    """Hold the keyboard for the duration of a block, and let go however the
    block ends -- return, exception, or the process dying.

    Yields the number of human key events seen so far, so the caller can tell
    afterwards whether the person tried to type while it was running. Yields
    None when the keyboard was not taken at all (blocking switched off, the
    hook would not install, or the person has latched it off), which is the
    signal that automation is running without exclusive use of the keyboard --
    not an error, just less certainty about what reached the app.
    """
    if not enabled or not ensure_hook():
        yield None
        return
    # Only scan the hardware when a fresh block is starting: take() ignores
    # held_down while a lease is already running, and 255 GetAsyncKeyState
    # calls on every click would be paid for nothing.
    held = () if _guard.blocking() else keys_physically_down()
    if not _guard.take(seconds, held_down=held):
        yield None
        return
    try:
        yield _guard.human_events
    finally:
        _guard.release("action finished")


def renew(seconds=None) -> bool:
    """Extend a lease that is already running. Does nothing if the keyboard is
    not currently held, so this can never start a block by itself."""
    if not _guard.blocking():
        return False
    return _guard.take(seconds)


def interrupted_since(before) -> int:
    """How many key events the person produced since `before`, as returned by
    holding(). 0 when it was never taken, so callers need no special case."""
    if before is None:
        return 0
    return max(0, _guard.human_events - before)


# --- holding the pointer still, for hover --------------------------------------
#
# Measured 2026-08-26 (tests/probe_mouse_lock.py, 6/6): returning 1 from a
# WH_MOUSE_LL callback does not merely hide the event from applications -- the
# cursor itself does not move. 21 moves arriving during a hold left the pointer
# exactly where it was put; the moment the lease lapsed it followed again.
# Callback cost was 0.02ms against the 300ms LowLevelHooksTimeout budget.
#
# That measurement is why this is a hook and not ClipCursor. A cursor clip is
# system-wide state that does not belong to any process, so a crash mid-hold
# could leave a stranger's pointer trapped in a one-pixel box with nothing
# left running to release it. Windows tears a dead process's hooks down for us.

WH_MOUSE_LL = 14
LLMHF_INJECTED = 0x0001

# Every VK that means "a mouse button is down": left, right, middle, and the
# two side buttons.
MOUSE_BUTTON_VKS = (0x01, 0x02, 0x04, 0x05, 0x06)

# Deliberately a fraction of MAX_LEASE_SECONDS. A hover dwell is under a
# second; anything longer than this is a bug, and the cost of that bug should
# be a stutter in the pointer rather than a person wondering if their mouse
# has died.
MAX_MOUSE_LEASE_SECONDS = 3.0

MouseHold = collections.namedtuple("MouseHold", "locked mark reason")


class MouseGuard:
    """The decision logic for the pointer hold, with no Win32 in it.

    Unlike the keyboard, this swallows OTHER_INJECTOR as well as HUMAN. The
    keyboard passes other injectors because they are how some people type at
    all -- an on-screen keyboard, an IME, an assistive device. The pointer hold
    is a different question: it lasts a fraction of a second and its entire
    purpose is that *nothing* moves the cursor while the picture is taken. A
    head-tracking mouse ruins a dwell exactly as a hand does, and is let back
    in by the same lease a moment later.

    It has no release gesture of its own, because every gesture a mouse can
    make is one this is busy swallowing. Instead it answers to the keyboard's:
    `veto` is consulted on every decision, and the keyboard guard's escape
    chord is wired to it, so three taps of Esc give back the pointer as well.
    """

    def __init__(self, max_lease=MAX_MOUSE_LEASE_SECONDS, veto=None):
        self._max_lease = max_lease
        self._veto = veto
        self._lock = threading.Lock()
        self._lease_until = 0.0
        self.human_events = 0     # a count. Never a coordinate.
        self.last_human = None

    def blocking(self, now=None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return self._blocking_locked(now)

    def _blocking_locked(self, now) -> bool:
        if self._veto is not None and self._veto():
            return False
        return now < self._lease_until

    def take(self, seconds=None, now=None) -> bool:
        now = time.monotonic() if now is None else now
        seconds = self._max_lease if seconds is None else min(seconds, self._max_lease)
        with self._lock:
            if self._veto is not None and self._veto():
                return False
            self._lease_until = now + seconds
            return True

    def release(self, now=None) -> None:
        with self._lock:
            self._lease_until = 0.0

    def decide(self, flags: int, extra: int, now=None) -> bool:
        """SWALLOW or PASS for one mouse event. Must stay cheap: this runs on
        every mouse movement on the machine while the hook is installed."""
        now = time.monotonic() if now is None else now
        if classify_mouse(flags, extra) is OURS:
            return PASS
        with self._lock:
            if not flags & LLMHF_INJECTED:
                self.human_events += 1
                self.last_human = now
            if not self._blocking_locked(now):
                return PASS
            return SWALLOW


def classify_mouse(flags: int, extra: int) -> str:
    """Who sent this mouse event. Same shape as classify(), different flag:
    the injected bit for mouse events is 0x01, not the keyboard's 0x10."""
    if not flags & LLMHF_INJECTED:
        return HUMAN
    return OURS if extra == input_sim.SIGNATURE else OTHER_INJECTOR


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wt.POINT),          # read by nothing here, on purpose
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


MOUSEHOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wt.WPARAM, ctypes.POINTER(MSLLHOOKSTRUCT)
)


def buttons_physically_down():
    """Mouse buttons held right now. A hold started mid-drag would swallow the
    button-up and strand the drag in whatever app it began in, so this exists
    to refuse rather than to work around."""
    return {vk for vk in MOUSE_BUTTON_VKS if user32.GetAsyncKeyState(vk) & 0x8000}


class MouseHook:
    """Owns the OS mouse hook and a thread to pump messages for it.

    The hook is installed on first use and then left in place: uninstalling it
    between hovers would trade a per-event cost of 0.02ms for a thread handoff
    on every call. When no lease is running `decide` returns PASS after one
    comparison, so an installed-but-idle hook costs the machine nothing worth
    measuring.
    """

    def __init__(self, guard: MouseGuard):
        self.guard = guard
        self._handle = None
        self._tid = None
        self._ready = threading.Event()
        self._proc = MOUSEHOOKPROC(self._callback)  # kept alive deliberately
        self._thread = threading.Thread(target=self._run, daemon=True, name="winauto-mouse-hook")

    def _callback(self, nCode, wParam, lParam):
        if nCode >= 0:
            m = lParam[0]
            try:
                if self.guard.decide(m.flags, m.dwExtraInfo or 0) is SWALLOW:
                    return 1
            except Exception:  # noqa: BLE001 - never let a bug here eat the mouse
                pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        self._handle = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        self._ready.set()
        if not self._handle:
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.DispatchMessageW(ctypes.byref(msg))

    def start(self, timeout=3.0) -> bool:
        if self._thread.is_alive():
            return bool(self._handle)
        self._thread.start()
        self._ready.wait(timeout=timeout)
        return bool(self._handle)

    def stop(self) -> None:
        self.guard.release()
        if self._handle:
            user32.UnhookWindowsHookEx(self._handle)
            self._handle = None
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._tid = None

    @property
    def installed(self) -> bool:
        return bool(self._handle)


_mouse_guard = MouseGuard(veto=lambda: _guard.latched_off())
_mouse_hook = None


def mouse_guard() -> MouseGuard:
    return _mouse_guard


def ensure_mouse_hook() -> bool:
    global _mouse_hook
    if _mouse_hook is None:
        _mouse_hook = MouseHook(_mouse_guard)
    return _mouse_hook.start()


def mouse_hook_installed() -> bool:
    return _mouse_hook is not None and _mouse_hook.installed


@contextlib.contextmanager
def holding_mouse(seconds=None):
    """Pin the pointer where automation put it, for the length of a block.

    Yields a MouseHold saying whether the pointer is actually held and, if not,
    why -- so a caller can report "the picture may be of the wrong thing"
    instead of quietly returning one. `mark` is the human-event count at the
    start, for mouse_interrupted_since().

    It refuses, rather than working around, in three cases: blocking is
    switched off; a mouse button is physically down, because swallowing its
    button-up would strand a drag in someone else's app; and the person has
    used the escape chord, which means give me my machine back and is not a
    request that expires.
    """
    if not enabled:
        yield MouseHold(False, None, "input blocking is switched off")
        return
    if _guard.latched_off():
        yield MouseHold(False, None, "the person asked for their machine back with the escape chord")
        return
    down = buttons_physically_down()
    if down:
        yield MouseHold(False, None, "a mouse button is physically down -- refusing to interrupt a drag")
        return
    if not ensure_mouse_hook():
        yield MouseHold(False, None, "the mouse hook would not install")
        return
    if not _mouse_guard.take(seconds):
        yield MouseHold(False, None, "the pointer hold was refused")
        return
    try:
        yield MouseHold(True, _mouse_guard.human_events, "held")
    finally:
        _mouse_guard.release()


def mouse_interrupted_since(before) -> int:
    """How many mouse events the person produced since `before`. 0 when the
    pointer was never held, so callers need no special case."""
    if before is None:
        return 0
    return max(0, _mouse_guard.human_events - before)
