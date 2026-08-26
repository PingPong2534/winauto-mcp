"""Drive heap_snapshot()/heap_diff() end to end against a target that leaks a
KNOWN amount, and check the tools report that amount.

Owns its target completely: it launches its own PowerShell process, makes it
hold a counted set of objects, tells it to allocate a second counted set
between the two snapshots, and kills it at the end. Nothing else on the machine
is touched, and the expected answer is the same on any machine -- neither is
true of pointing this at a real application.

The number is the point. A probe that only checked "some types grew" would pass
against a tool that reported the runtime's own background churn, which measures
in the thousands of objects all by itself: two snapshots of a process doing
nothing at all differ by ~4,200 objects across ~255 types. Asserting that
System.Uri grew by ~20,000 is a claim that noise cannot satisfy.

Diagnostic, not a unit test: run it directly, read what it prints.
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

import server
import window_manager

TEMP = os.environ.get("TEMP", ".")
READY = os.path.join(TEMP, "probe_heap_ready.flag")
GO = os.path.join(TEMP, "probe_heap_go.flag")
DONE = os.path.join(TEMP, "probe_heap_done.flag")
SCRIPT = os.path.join(TEMP, "probe_heap_target.ps1")

BATCH = 20_000

TARGET = f"""
$ErrorActionPreference = 'Stop'
$kept = New-Object 'System.Collections.Generic.List[System.Uri]'
for ($i = 0; $i -lt {BATCH}; $i++) {{ $kept.Add([System.Uri]"https://example.com/a$i") }}
New-Item -ItemType File '{READY}' -Force | Out-Null
while (-not (Test-Path '{GO}')) {{ Start-Sleep -Milliseconds 100 }}
for ($i = 0; $i -lt {BATCH}; $i++) {{ $kept.Add([System.Uri]"https://example.com/b$i") }}
New-Item -ItemType File '{DONE}' -Force | Out-Null
Start-Sleep -Seconds 600
"""

CREATE_NEW_CONSOLE = 0x00000010


def _clear_flags():
    for path in (READY, GO, DONE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _wait_for(path, timeout, what):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return
        time.sleep(0.1)
    raise TimeoutError(f"target never signalled {what} within {timeout}s")


def _find_window(pid, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for w in window_manager.list_windows():
            if w["pid"] == pid:
                return w
        time.sleep(0.3)
    raise TimeoutError(f"no visible window ever appeared for pid {pid}")


def main():
    _clear_flags()
    with open(SCRIPT, "w", encoding="utf-8") as fh:
        fh.write(TARGET)

    proc = subprocess.Popen(
        ["pwsh", "-NoProfile", "-File", SCRIPT],
        creationflags=CREATE_NEW_CONSOLE,
    )
    print(f"launched target pid {proc.pid}")

    try:
        _wait_for(READY, 60, "READY")
        print(f"target allocated its first {BATCH:,} System.Uri")

        win = _find_window(proc.pid)
        print(f"attaching to hwnd {win['hwnd']} ({win['process']})")
        print(" ", server.attach_window(win["hwnd"]))

        if server._state["pid"] != proc.pid:
            raise AssertionError(
                f"attach recorded pid {server._state['pid']}, expected {proc.pid}"
            )
        print(f"  attach recorded pid {server._state['pid']} -- correct")

        before = json.loads(server.heap_snapshot("before"))
        print(f"\nbefore: {before['live_objects']:,} objects, "
              f"{before['distinct_types']:,} types")

        open(GO, "w").close()
        _wait_for(DONE, 120, "DONE")
        print(f"target allocated a second {BATCH:,} System.Uri")

        after = json.loads(server.heap_snapshot("after"))
        print(f"after : {after['live_objects']:,} objects, "
              f"{after['distinct_types']:,} types")

        diff = json.loads(server.heap_diff("before", "after", top=8))
        print(f"\ntotal objects {diff['total_objects_delta']:+,}, "
              f"{diff['types_that_grew']} types grew")
        print("biggest movers:")
        for row in diff["grew"]:
            print(f"  {row['delta']:+9,}  {row['type'][:46]:<46} "
                  f"{row['before']:>8,} -> {row['after']:>8,}")

        uri = next((r for r in diff["grew"] if r["type"] == "System.Uri"), None)
        if uri is None:
            raise AssertionError(
                "System.Uri is absent from the growth list, but 20,000 more were "
                "allocated and held between the snapshots"
            )
        # Not exact: the runtime holds a handful of its own, and PowerShell
        # builds a few while running the loop. Anything within a few hundred of
        # the batch means the count tracked the allocation rather than drifting.
        if abs(uri["delta"] - BATCH) > 500:
            raise AssertionError(
                f"System.Uri grew by {uri['delta']:,}, expected about {BATCH:,}"
            )
        print(f"\nOK: System.Uri {uri['delta']:+,} against {BATCH:,} allocated "
              f"({uri['approx_bytes_each']} bytes each)")

        rank = [r["type"] for r in diff["grew"]].index("System.Uri") + 1
        print(f"OK: it ranked #{rank} of the types that grew, above the "
              f"{diff['types_that_grew'] - 1} others")
    finally:
        proc.kill()
        proc.wait(timeout=10)
        _clear_flags()
        try:
            os.remove(SCRIPT)
        except FileNotFoundError:
            pass
        print(f"\ncleaned up target pid {proc.pid}")


if __name__ == "__main__":
    main()
