"""Click, type and key-press simulation via the Win32 SendInput API.

Uses SendInput (not the higher-level win32api.mouse_event/keybd_event, which
are legacy wrappers around the same call) so mouse and keyboard events look
like real hardware input to the target app -- this is what lets Unicode
(e.g. Thai) text typing work without needing per-key virtual-key mappings.
"""

import ctypes
import ctypes.wintypes
import time

import win32api
import win32con

from window_manager import bring_to_foreground

# --- ctypes SendInput structures -------------------------------------------------

# dwExtraInfo is ULONG_PTR -- a VALUE the sender chooses, which Windows carries
# through to GetMessageExtraInfo() and to low-level hooks untouched. It was
# declared here as POINTER(c_ulong) and passed ctypes.pointer(...), which is a
# widespread copy-paste error: it compiles, input works, and every event goes
# out stamped with the address of a temporary instead of a value -- a different
# number each call. That made the field useless for its one purpose, telling
# our own input apart from the person's.
ULONG_PTR = ctypes.wintypes.WPARAM


class KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", InputUnion)]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120

KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002

# Stamped on every event this process sends, so a keystroke or click can be
# attributed later. Windows' own LLKHF_INJECTED flag only says "some process
# injected this" -- an on-screen keyboard, a remote desktop session or another
# automation tool all set it. This says it was US. Arbitrary constant; it only
# has to be unlikely to collide with another injector's choice.
SIGNATURE = 0x7A170001


def _send(*inputs: Input):
    n = len(inputs)
    arr = (Input * n)(*inputs)
    ctypes.windll.user32.SendInput(n, ctypes.pointer(arr), ctypes.sizeof(Input))


def _mouse_input(dx, dy, flags, mouse_data=0):
    return Input(type=INPUT_MOUSE, ii=InputUnion(mi=MouseInput(dx, dy, mouse_data, flags, 0, SIGNATURE)))


def _key_unicode_input(char_code, key_up=False):
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
    return Input(type=INPUT_KEYBOARD, ii=InputUnion(ki=KeyBdInput(0, char_code, flags, 0, SIGNATURE)))


def _key_vk_input(vk_code, key_up=False):
    flags = KEYEVENTF_KEYUP if key_up else 0
    return Input(type=INPUT_KEYBOARD, ii=InputUnion(ki=KeyBdInput(vk_code, 0, flags, 0, SIGNATURE)))


def _screen_to_absolute(x, y):
    vs_left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
    vs_top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
    vs_width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
    vs_height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
    abs_x = int(((x - vs_left) * 65535) / vs_width)
    abs_y = int(((y - vs_top) * 65535) / vs_height)
    return abs_x, abs_y


# --- public API --------------------------------------------------------------------


def move_to(screen_x, screen_y):
    abs_x, abs_y = _screen_to_absolute(screen_x, screen_y)
    _send(_mouse_input(abs_x, abs_y, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK))


class _borrowed_cursor:
    """Put the pointer back where the person left it once the action is done.

    There is one real cursor and it is shared with whoever is at the keyboard,
    so an automated click that abandons the pointer somewhere in the target
    app leaves them to find it again. Restoring costs one extra move and makes
    a run far less disruptive to sit through.

    Not always wanted: anything that keeps following the pointer after the
    click -- Blender's modal transform after G/R/S, a rubber-band selection
    continued in a later step -- reads the restored position as the user's
    intent. Those callers pass keep_cursor=True.
    """

    def __init__(self, keep_cursor: bool):
        self.keep_cursor = keep_cursor
        self.origin = None

    def __enter__(self):
        if not self.keep_cursor:
            try:
                self.origin = win32api.GetCursorPos()
            except win32api.error:
                self.origin = None
        return self

    def __exit__(self, *_exc):
        if self.origin is None:
            return False
        # Let the app finish reacting to the button first: a mouse-move
        # arriving in the same instant as the click can be read as a drag.
        time.sleep(0.05)
        try:
            move_to(*self.origin)
        except OSError:
            pass
        return False


def click_screen(screen_x, screen_y, button="left", double=False):
    down_flag = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
    up_flag = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP

    move_to(screen_x, screen_y)
    time.sleep(0.03)
    clicks = 2 if double else 1
    for i in range(clicks):
        _send(_mouse_input(0, 0, down_flag))
        time.sleep(0.02)
        _send(_mouse_input(0, 0, up_flag))
        if i < clicks - 1:
            time.sleep(0.05)


def scroll_in_window(hwnd, client_x, client_y, clicks: int, keep_cursor: bool = False):
    """Scroll the mouse wheel at (client_x, client_y) in the window's client
    area. Positive `clicks` scrolls up/away from the user, negative scrolls
    down -- matches the sign convention of a physical wheel notch."""
    import win32gui

    bring_to_foreground(hwnd)
    time.sleep(0.05)
    screen_x, screen_y = win32gui.ClientToScreen(hwnd, (int(client_x), int(client_y)))
    with _borrowed_cursor(keep_cursor):
        move_to(screen_x, screen_y)
        time.sleep(0.03)
        delta = (clicks * WHEEL_DELTA) & 0xFFFFFFFF  # mouseData is c_ulong; pack signed value
        _send(_mouse_input(0, 0, MOUSEEVENTF_WHEEL, mouse_data=delta))


def drag_in_window(hwnd, x1, y1, x2, y2, button="left", steps: int = 12, step_delay: float = 0.02,
                   keep_cursor: bool = False):
    """Drag from (x1, y1) to (x2, y2), both client-relative. Presses the
    button down at the start point, moves through `steps` intermediate
    points (many apps -- including Godot -- only recognize a drag if they
    see the mouse actually move while the button is held, not a teleport),
    then releases at the end point."""
    import win32gui

    down_flag = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
    up_flag = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP

    bring_to_foreground(hwnd)
    time.sleep(0.05)
    sx1, sy1 = win32gui.ClientToScreen(hwnd, (int(x1), int(y1)))
    sx2, sy2 = win32gui.ClientToScreen(hwnd, (int(x2), int(y2)))

    with _borrowed_cursor(keep_cursor):
        move_to(sx1, sy1)
        time.sleep(0.03)
        _send(_mouse_input(0, 0, down_flag))
        time.sleep(0.03)
        for i in range(1, steps + 1):
            t = i / steps
            move_to(int(sx1 + (sx2 - sx1) * t), int(sy1 + (sy2 - sy1) * t))
            time.sleep(step_delay)
        time.sleep(0.03)
        _send(_mouse_input(0, 0, up_flag))


def click_in_window(hwnd, client_x, client_y, button="left", double=False, modifiers=None,
                    keep_cursor: bool = False):
    """Click at coordinates relative to the window's client area (same space
    as the image returned by screenshot.grab_window). `modifiers`, if
    given, is a list like ["ctrl"] or ["shift"] held down for the duration of
    the click -- e.g. for ctrl/shift-click multi-selection in a tree/list."""
    import win32gui

    bring_to_foreground(hwnd)
    time.sleep(0.05)
    screen_x, screen_y = win32gui.ClientToScreen(hwnd, (int(client_x), int(client_y)))

    mod_vks = []
    for key in modifiers or []:
        vk = SPECIAL_KEYS.get(key.lower())
        if vk is None:
            raise ValueError(f"unknown modifier '{key}', expected one of ctrl/alt/shift")
        mod_vks.append(vk)

    for vk in mod_vks:
        _send(_key_vk_input(vk, key_up=False))
        time.sleep(0.02)
    try:
        with _borrowed_cursor(keep_cursor):
            click_screen(screen_x, screen_y, button=button, double=double)
    finally:
        for vk in reversed(mod_vks):
            _send(_key_vk_input(vk, key_up=True))
            time.sleep(0.02)


def type_text(text: str, hwnd=None):
    """Type literal Unicode text into whichever control currently has focus."""
    if hwnd is not None:
        bring_to_foreground(hwnd)
        time.sleep(0.05)
    for ch in text:
        code = ord(ch)
        _send(_key_unicode_input(code, key_up=False))
        _send(_key_unicode_input(code, key_up=True))
        time.sleep(0.01)


SPECIAL_KEYS = {
    "enter": win32con.VK_RETURN,
    "tab": win32con.VK_TAB,
    "escape": win32con.VK_ESCAPE,
    "backspace": win32con.VK_BACK,
    "delete": win32con.VK_DELETE,
    "up": win32con.VK_UP,
    "down": win32con.VK_DOWN,
    "left": win32con.VK_LEFT,
    "right": win32con.VK_RIGHT,
    "home": win32con.VK_HOME,
    "end": win32con.VK_END,
    "pageup": win32con.VK_PRIOR,
    "pagedown": win32con.VK_NEXT,
    "space": win32con.VK_SPACE,
    "ctrl": win32con.VK_CONTROL,
    "alt": win32con.VK_MENU,
    "shift": win32con.VK_SHIFT,
}
for _i in range(1, 13):
    SPECIAL_KEYS[f"f{_i}"] = getattr(win32con, f"VK_F{_i}")


def press_key(key: str, hwnd=None):
    """Send a named special key (see SPECIAL_KEYS) as a real keydown/keyup."""
    vk = SPECIAL_KEYS.get(key.lower())
    if vk is None:
        raise ValueError(f"unknown key '{key}', expected one of {sorted(SPECIAL_KEYS)}")
    if hwnd is not None:
        bring_to_foreground(hwnd)
        time.sleep(0.05)
    _send(_key_vk_input(vk, key_up=False))
    time.sleep(0.02)
    _send(_key_vk_input(vk, key_up=True))


# a-z / 0-9 aren't in SPECIAL_KEYS (type_text handles literal text) but chords
# like Ctrl+Shift+P need a plain letter as the non-modifier key too.
_LETTER_DIGIT_VKS = {c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"}


def press_keys(keys: list[str], hwnd=None):
    """Send a chord: hold every key in `keys` down in order, then release in
    reverse order (e.g. ["ctrl", "shift", "p"] for Ctrl+Shift+P). Each name is
    looked up in SPECIAL_KEYS first, then as a single a-z/0-9 letter/digit."""
    vks = []
    for key in keys:
        k = key.lower()
        vk = SPECIAL_KEYS.get(k)
        if vk is None:
            vk = _LETTER_DIGIT_VKS.get(k)
        if vk is None:
            raise ValueError(f"unknown key '{key}', expected one of {sorted(SPECIAL_KEYS)} or a-z/0-9")
        vks.append(vk)
    if hwnd is not None:
        bring_to_foreground(hwnd)
        time.sleep(0.05)
    for vk in vks:
        _send(_key_vk_input(vk, key_up=False))
        time.sleep(0.02)
    for vk in reversed(vks):
        _send(_key_vk_input(vk, key_up=True))
        time.sleep(0.02)
