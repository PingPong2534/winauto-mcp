"""Close the Notepad windows that test runs leaked -- and nothing else.

Every diag/spike script here launches Notepad and kills the PID that Popen
returned. notepad.exe is a stub that hands off to a packaged Notepad.exe with
a different PID and then exits, so those kills hit nothing and a window is
left behind on every run. 54 had accumulated by 2026-08-26.

The dangerous part is that one of them is the person's own note, unsaved. So
this works by allowlist, not blocklist: a window is only closed if its title
matches a shape that the tests are known to produce. Anything unrecognised is
kept, including anything this file's author did not anticipate.

Run with --dry-run (the default) to print the decision for every window.
Pass --close to actually close the ones marked CLOSE.
"""

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32gui
import win32process

import window_manager

SUFFIX = " - Notepad"

# Titles the tests are known to leave behind. Deliberately narrow.
#   "ไม่มีชื่อ"                     an untitled, unmodified window
#   "*WWWWWW", "*YZXW", "*hello"   typing tests: ASCII letters only
#   "*สวัสดี hello", "*สวัสดี YYYY"  the Thai/ASCII mixed typing test
TEST_TITLES = [
    re.compile(r"^ไม่มีชื่อ$"),
    re.compile(r"^\*[A-Za-z]+$"),
    re.compile(r"^\*สวัสดี ?[A-Za-z]*$"),
]


def classify(title):
    body = title[: -len(SUFFIX)] if title.endswith(SUFFIX) else title
    for pattern in TEST_TITLES:
        if pattern.match(body):
            return "CLOSE"
    return "KEEP"


def main():
    close_for_real = "--close" in sys.argv

    windows = []
    for w in window_manager.list_windows():
        if not w["process"].lower().startswith("notepad"):
            continue
        pid = win32process.GetWindowThreadProcessId(w["hwnd"])[1]
        windows.append({"hwnd": w["hwnd"], "pid": pid, "title": w["title"],
                        "verdict": classify(w["title"])})

    keep_pids = {w["pid"] for w in windows if w["verdict"] == "KEEP"}
    close_pids = {w["pid"] for w in windows if w["verdict"] == "CLOSE"}

    for w in sorted(windows, key=lambda w: (w["verdict"], w["title"])):
        shared = " <-- SHARES A PROCESS WITH A KEEP" if (
            w["verdict"] == "CLOSE" and w["pid"] in keep_pids) else ""
        print(f"  {w['verdict']}  pid={w['pid']:<8} hwnd={w['hwnd']:<10} "
              f"{w['title']!r}{shared}")

    overlap = close_pids & keep_pids
    print(f"\n  {len(windows)} notepad windows: "
          f"{sum(1 for w in windows if w['verdict'] == 'CLOSE')} CLOSE, "
          f"{sum(1 for w in windows if w['verdict'] == 'KEEP')} KEEP")
    print(f"  distinct pids: {len(close_pids)} to close, {len(keep_pids)} to keep")

    if overlap:
        print(f"\n  REFUSING TO CLOSE ANYTHING: {len(overlap)} process(es) host both a "
              f"window to close and one to keep, so killing by pid would take the "
              f"kept window down too: {sorted(overlap)}")
        return 1

    if not close_for_real:
        print("\n  dry run -- nothing was closed. Pass --close to do it.")
        return 0

    closed = 0
    for pid in sorted(close_pids):
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        closed += (r.returncode == 0)
    print(f"\n  closed {closed} of {len(close_pids)} processes")

    left = [w for w in window_manager.list_windows()
            if w["process"].lower().startswith("notepad")]
    print(f"  notepad windows remaining: {len(left)}")
    for w in left:
        print(f"    {w['hwnd']:<10} {w['title']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
