"""Can a keystroke be attributed -- did this come from the person or from us?

Installs a WH_KEYBOARD_LL hook and prints, for every key event that passes
through the machine, what it was and who it came from. Then injects a few of
our own so both kinds appear side by side. Asserts nothing; prints.

Two independent signals are being tested:

  LLKHF_INJECTED (flags & 0x10)  -- set by Windows on anything delivered via
      SendInput/keybd_event by ANY process. Answers "was this typed on a real
      keyboard?" but not "was it us?" -- an on-screen keyboard, a remote
      desktop session, another automation tool all set it too.

  dwExtraInfo == SIGNATURE       -- an arbitrary 32-bit value SendInput lets
      the sender attach to each event and Windows carries through untouched.
      Answers "was this US specifically?", which is the one that matters for
      deciding whether to back off.

Run it, then type on the real keyboard while it is running, and compare.
"""

import ctypes
import ctypes.wintypes as wt
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import input_sim  # noqa: E402

WH_KEYBOARD_LL = 13
LLKHF_EXTENDED = 0x01
LLKHF_LOWER_IL_INJECTED = 0x02
LLKHF_INJECTED = 0x10
LLKHF_UP = 0x80

# Arbitrary but ours. Anything arriving with this in dwExtraInfo was sent by
# this process; anything without it was not, however it got here.
SIGNATURE = input_sim.SIGNATURE
OTHER_TOOL = 0x0BADCAFE  # stands in for a different injector on the machine

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wt.WPARAM, ctypes.POINTER(KBDLLHOOKSTRUCT)
)

events = []
_ready = threading.Event()
_hook_id = {"h": None, "tid": None}


def attribute(flags, extra):
    """The whole discriminator, in three lines."""
    if not flags & LLKHF_INJECTED:
        return "HUMAN (real keyboard)"
    if extra == SIGNATURE:
        return "US (winauto-mcp)"
    return f"OTHER INJECTOR (extraInfo=0x{extra:X})"


def _proc(nCode, wParam, lParam):
    if nCode >= 0:
        k = lParam[0]
        extra = ctypes.cast(k.dwExtraInfo, ctypes.c_void_p).value or 0
        # Deliberately trivial: Windows silently drops a low-level hook whose
        # callback exceeds LowLevelHooksTimeout (300ms by default), and it
        # does not tell you. Record, do not think.
        events.append((k.vkCode, k.scanCode, k.flags, extra))
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


_callback = HOOKPROC(_proc)


def _pump():
    _hook_id["tid"] = kernel32.GetCurrentThreadId()
    _hook_id["h"] = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _callback, None, 0)
    if not _hook_id["h"]:
        print(f"  SetWindowsHookEx failed: {ctypes.get_last_error()}")
        _ready.set()
        return
    _ready.set()
    msg = wt.MSG()
    # A low-level hook only fires on a thread that pumps messages.
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def send_tagged(char, signature):
    """Same as input_sim's typing path, but with a chosen dwExtraInfo, so both
    'us' and 'some other injector' can be produced for comparison."""
    down = input_sim.Input(
        type=input_sim.INPUT_KEYBOARD,
        ii=input_sim.InputUnion(
            ki=input_sim.KeyBdInput(0, ord(char), input_sim.KEYEVENTF_UNICODE, 0, signature)
        ),
    )
    up = input_sim.Input(
        type=input_sim.INPUT_KEYBOARD,
        ii=input_sim.InputUnion(
            ki=input_sim.KeyBdInput(
                0, ord(char), input_sim.KEYEVENTF_UNICODE | input_sim.KEYEVENTF_KEYUP, 0, signature,
            )
        ),
    )
    input_sim._send(down, up)


def main():
    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    _ready.wait(timeout=3)
    if not _hook_id["h"]:
        return
    print("hook installed\n")

    print("injecting 'A' tagged as us, 'B' tagged as another tool, "
          "and 'C' the way input_sim sends today:")
    send_tagged("A", SIGNATURE)
    time.sleep(0.15)
    send_tagged("B", OTHER_TOOL)
    time.sleep(0.15)
    input_sim.type_text("C")
    time.sleep(0.3)

    print("\nnow type on the real keyboard for 6 seconds -- "
          "anything you press should read as HUMAN:")
    time.sleep(6)

    user32.PostThreadMessageW(_hook_id["tid"], 0x0012, 0, 0)  # WM_QUIT
    print(f"\n{len(events)} key events seen\n")
    print(f"{'vk':>4} {'scan':>5} {'flags':>7} {'extraInfo':>12}  verdict")
    for vk, scan, flags, extra in events:
        updown = "up  " if flags & LLKHF_UP else "down"
        print(f"{vk:>4} {scan:>5} {flags:#07x} {extra:#12x}  {updown}  {attribute(flags, extra)}")

    injected = sum(1 for _, _, f, _ in events if f & LLKHF_INJECTED)
    ours = sum(1 for _, _, f, e in events if f & LLKHF_INJECTED and e == SIGNATURE)
    print(f"\ninjected: {injected}/{len(events)}   ours by signature: {ours}")


if __name__ == "__main__":
    main()
