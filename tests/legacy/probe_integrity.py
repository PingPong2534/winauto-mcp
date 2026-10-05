"""Measure what a medium-integrity process can actually learn about another
process's integrity level -- before any of it is wired into the server.

Issue #6 claims input is silently dropped into elevated windows and proposes
asking the target's token as the preflight. That proposal rests on three
assumptions, none of which were measured:

  1. our own level is readable                      (expected: trivially yes)
  2. a same-level process's level is readable       (expected: yes)
  3. a higher-level process denies, and the denial
     is distinguishable from other failures         (expected: ACCESS_DENIED)

If (3) came back as "succeeds and reports High", the preflight would be exact.
If it comes back ACCESS_DENIED, the preflight is an *inference* from a denial,
and everything else that denies for a different reason becomes a false
positive -- which is the thing worth counting here, since a false positive
refuses to drive a window that would have worked fine.

Run:  .venv\\Scripts\\python.exe tests\\probe_integrity.py
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys

import psutil
import win32gui
import win32process

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- raw Win32, so the failure code is visible instead of a pywin32 exception ---

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenIntegrityLevel = 25
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87

LEVELS = {
    0x0000: "untrusted",
    0x1000: "low",
    0x2000: "medium",
    0x2100: "medium-plus",
    0x3000: "high",
    0x4000: "system",
    0x5000: "protected",
}

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi = ctypes.WinDLL("advapi32", use_last_error=True)

# ctypes defaults every restype to C int -- 32 bits. Anything here that returns
# a HANDLE or a pointer would come back truncated on 64-bit, which fails as
# "the SID is garbage" rather than as a type error. Declared explicitly.
k32.GetCurrentProcess.restype = wt.HANDLE
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.CloseHandle.argtypes = [wt.HANDLE]
advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
advapi.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
advapi.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
advapi.GetSidSubAuthority.restype = ctypes.POINTER(wt.DWORD)
advapi.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wt.DWORD]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wt.DWORD)]


def _level_name(rid):
    if rid in LEVELS:
        return LEVELS[rid]
    # RIDs are a continuum, not an enum -- name the band it falls in.
    for value in sorted(LEVELS, reverse=True):
        if rid > value:
            return f"{LEVELS[value]}+ (0x{rid:04x})"
    return f"0x{rid:04x}"


def token_integrity(handle):
    """(rid, None) or (None, win32 error code) for an already-open token."""
    size = wt.DWORD()
    advapi.GetTokenInformation(handle, TokenIntegrityLevel, None, 0, ctypes.byref(size))
    buf = ctypes.create_string_buffer(size.value)
    if not advapi.GetTokenInformation(handle, TokenIntegrityLevel, buf, size, ctypes.byref(size)):
        return None, ctypes.get_last_error()
    label = ctypes.cast(buf, ctypes.POINTER(SID_AND_ATTRIBUTES)).contents
    n = advapi.GetSidSubAuthorityCount(label.Sid).contents.value
    return advapi.GetSidSubAuthority(label.Sid, n - 1).contents.value, None


def own_level():
    handle = wt.HANDLE()
    advapi.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(handle))
    rid, err = token_integrity(handle)
    k32.CloseHandle(handle)
    return rid, err


def probe_pid(pid, access):
    """What one access right yields for one pid: (rid, stage, errcode)."""
    proc = k32.OpenProcess(access, False, pid)
    if not proc:
        return None, "OpenProcess", ctypes.get_last_error()
    handle = wt.HANDLE()
    if not advapi.OpenProcessToken(proc, TOKEN_QUERY, ctypes.byref(handle)):
        err = ctypes.get_last_error()
        k32.CloseHandle(proc)
        return None, "OpenProcessToken", err
    rid, err = token_integrity(handle)
    k32.CloseHandle(handle)
    k32.CloseHandle(proc)
    if rid is None:
        return None, "GetTokenInformation", err
    return rid, None, 0


def describe(pid):
    for access, label in (
        (PROCESS_QUERY_LIMITED_INFORMATION, "LIMITED"),
        (PROCESS_QUERY_INFORMATION, "FULL"),
    ):
        rid, stage, err = probe_pid(pid, access)
        if rid is not None:
            return f"{_level_name(rid)} (via {label})", rid, None
    return f"UNKNOWN -- {stage} failed, err={err}", None, (stage, err)


def main():
    rid, err = own_level()
    print(f"this process: pid={os.getpid()} integrity={_level_name(rid)} (rid=0x{rid:04x}) err={err}")
    print(f"  -> UIPI lets us drive windows at or below 0x{rid:04x}\n")

    # Every visible titled top-level window -- the exact population attach_window
    # can be pointed at, so a false positive here is a window we would refuse.
    seen = {}

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd).strip():
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        seen.setdefault(pid, win32gui.GetWindowText(hwnd)[:44])
        return True

    win32gui.EnumWindows(visit, None)

    print(f"{'pid':>7}  {'process':22}  {'integrity':28}  window")
    print("-" * 104)
    denied, ok, above = [], [], []
    for pid in sorted(seen):
        try:
            name = psutil.Process(pid).name()
        except psutil.Error:
            name = "?"
        text, target_rid, failure = describe(pid)
        print(f"{pid:>7}  {name:22}  {text:28}  {seen[pid]}")
        if failure:
            denied.append((pid, name, failure))
        else:
            ok.append((pid, name, target_rid))
            if target_rid > rid:
                above.append((pid, name, target_rid))

    print("\n--- summary ---")
    print(f"windowed processes:        {len(seen)}")
    print(f"integrity read outright:   {len(ok)}")
    print(f"  of those, ABOVE ours:    {len(above)}  {[n for _, n, _ in above]}")
    print(f"could not be read at all:  {len(denied)}")
    for pid, name, (stage, code) in denied:
        why = {ERROR_ACCESS_DENIED: "ACCESS_DENIED", ERROR_INVALID_PARAMETER: "INVALID_PARAMETER"}.get(code, code)
        print(f"    pid={pid:<7} {name:22} {stage} -> {why}")

    print(
        "\nRead this as: any row under 'could not be read' is a window the "
        "proposed preflight would refuse.\nIf that list holds ordinary "
        "non-elevated apps, refusing on a denial alone is too blunt."
    )


if __name__ == "__main__":
    main()
