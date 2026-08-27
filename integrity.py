"""Whether Windows will actually deliver our input to a given window.

UIPI (User Interface Privilege Isolation) drops input sent from a lower
integrity level into a higher one. It drops it *silently*: SendInput accepts
the events, returns the full count and leaves GetLastError at 0 -- the events
are discarded later, when they are routed to the higher-integrity thread's
input queue. Measured on Windows 11 26200 against an elevated pwsh:

    SetForegroundWindow  -> foreground == target hwnd
    SendInput(2 events)  -> returns 2
    GetLastError         -> 0
    ...and nothing arrives.

So there is no error to check for after the fact. The only honest place to
answer "did that keystroke land" is *before* sending it, by comparing the two
processes' integrity levels -- which is what this module does.

MEASURED, not assumed (tests/probe_integrity.py, 2026-08-27): a medium process
CAN read a high process's integrity level. PROCESS_QUERY_LIMITED_INFORMATION
is granted across integrity levels, so the answer is a direct read, not an
inference from a denial. Across all 16 windowed processes on the test machine
-- 15 medium and one elevated mmc.exe -- the level was read outright every
time and nothing was unreadable. That matters: had it been unreadable we would
have had to treat "cannot tell" as "elevated", and every process that denies
for some unrelated reason would become a window we wrongly refuse to drive.
As it stands the check is exact, and costs 3 microseconds.
"""

import ctypes
import ctypes.wintypes as wt

import win32process

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenUIAccess = 26
TokenIntegrityLevel = 25

# The mandatory-label RIDs, low to high. Compared as numbers, never matched as
# an enum: the values are a continuum and Windows does use in-between ones
# (AppContainers sit just above low, "medium plus" at 0x2100 exists), so a
# level this table has no name for still compares correctly.
UNTRUSTED = 0x0000
LOW = 0x1000
MEDIUM = 0x2000
HIGH = 0x3000
SYSTEM = 0x4000

_NAMES = ((SYSTEM, "system"), (HIGH, "high"), (0x2100, "medium-plus"),
          (MEDIUM, "medium"), (LOW, "low"), (UNTRUSTED, "untrusted"))

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_adv = ctypes.WinDLL("advapi32", use_last_error=True)

# ctypes defaults every restype to C int, which is 32 bits. Left alone, every
# HANDLE and every SID pointer below comes back truncated on 64-bit Windows --
# and it does not fail as a type error, it fails as a plausible-looking wrong
# number read out of a bad address. Declared explicitly for that reason.
_k32.GetCurrentProcess.restype = wt.HANDLE
_k32.OpenProcess.restype = wt.HANDLE
_k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_k32.CloseHandle.argtypes = [wt.HANDLE]
_adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
_adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
_adv.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
_adv.GetSidSubAuthority.restype = ctypes.POINTER(wt.DWORD)
_adv.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wt.DWORD]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wt.DWORD)]


class InputBlocked(PermissionError):
    """Raised instead of sending input Windows would discard on the way.

    Its own type rather than the ValueError the rest of the server raises for
    bad arguments, because this one is not a bad argument: the call was
    well-formed and the window is real. Nothing the caller can pass will make
    it work, so it must not read as "try again differently".
    """


def level_name(rid) -> str:
    if rid is None:
        return "unknown"
    for value, name in _NAMES:
        if rid == value:
            return name
        if rid > value:
            return f"{name}+ (0x{rid:04x})"
    return f"0x{rid:04x}"


def _integrity_of_token(token):
    size = wt.DWORD()
    _adv.GetTokenInformation(token, TokenIntegrityLevel, None, 0, ctypes.byref(size))
    buf = ctypes.create_string_buffer(size.value)
    if not _adv.GetTokenInformation(token, TokenIntegrityLevel, buf, size, ctypes.byref(size)):
        return None
    sid = ctypes.cast(buf, ctypes.POINTER(_SidAndAttributes)).contents.Sid
    count = _adv.GetSidSubAuthorityCount(sid).contents.value
    return _adv.GetSidSubAuthority(sid, count - 1).contents.value


def _own_token_flag(info_class):
    token = wt.HANDLE()
    if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        value, size = wt.DWORD(), wt.DWORD()
        if not _adv.GetTokenInformation(token, info_class, ctypes.byref(value),
                                        ctypes.sizeof(value), ctypes.byref(size)):
            return None
        return value.value
    finally:
        _k32.CloseHandle(token)


def _own_integrity():
    token = wt.HANDLE()
    if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        return _integrity_of_token(token)
    finally:
        _k32.CloseHandle(token)


# Our own token cannot change while the process runs, so these are read once.
# The *target's* level is never cached: a pid is only unique while its process
# lives, and caching would let a recycled pid answer for the wrong program.
_OWN_LEVEL = _own_integrity()
_OWN_UIACCESS = bool(_own_token_flag(TokenUIAccess))


def own_level():
    return _OWN_LEVEL


def has_uiaccess() -> bool:
    """Whether this process holds the uiAccess privilege, which exempts it from
    UIPI entirely. Granted only to a signed binary in a trusted location that
    asks for it in its manifest, so this is almost always False -- but when it
    is True, refusing to drive an elevated window would be refusing something
    that works."""
    return _OWN_UIACCESS


def process_level(pid):
    """The integrity level RID of a process, or None if it could not be read."""
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        token = wt.HANDLE()
        if not _adv.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            return _integrity_of_token(token)
        finally:
            _k32.CloseHandle(token)
    finally:
        _k32.CloseHandle(handle)


def window_level(hwnd):
    """The integrity level RID of the process owning `hwnd`, or None."""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except Exception:  # noqa: BLE001 - a dead window is "cannot tell", not a crash
        return None
    return process_level(pid) if pid else None


def blocks_input(target_level) -> bool:
    """Whether UIPI will discard input we send to a window at `target_level`.

    Unreadable (None) counts as NOT blocked. That is deliberate and it is the
    conservative choice *for this tool*: refusing on "cannot tell" would refuse
    real windows on the strength of a guess, and the failure it prevents is one
    the caller still discovers -- whereas a wrong refusal makes a working
    window permanently undriveable with no way to find out why. It was also
    measured never to happen (see the module docstring).
    """
    if _OWN_UIACCESS or target_level is None or _OWN_LEVEL is None:
        return False
    return target_level > _OWN_LEVEL


def why_blocked(target_level, title=None, process=None) -> str:
    """The message a caller gets instead of a false success. Says what is true,
    what will not work, and the two things that actually fix it -- an LLM that
    reads only this line should not go on to reason about a screen that never
    changed."""
    where = f' "{title}"' if title else ""
    who = f" ({process})" if process else ""
    return (
        f"input would be silently discarded: the target window{where}{who} runs at "
        f"{level_name(target_level)} integrity and this server runs at {level_name(_OWN_LEVEL)}. "
        "Windows UIPI drops input sent upward, and it does so without an error -- "
        "SendInput reports success and nothing arrives, which is why this is refused "
        "here instead of reported as done. Nothing was sent. "
        "To drive this window, restart the MCP server elevated (Run as administrator); "
        "otherwise pick a non-elevated window. "
        "Reading the window still works from here -- screenshot, capture_region and "
        "locate_in_region are unaffected by UIPI."
    )
