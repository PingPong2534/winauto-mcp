"""Can Notepad's text be read back exactly? Deciding how to verify a swallow.

A "the keystroke did NOT arrive" check cannot be done by pixel diff here --
Notepad's caret blinks, so an unchanged document still produces changed pixels.
This probes whether UI Automation gives the document text verbatim instead,
and cross-checks the answer against the screen so a wrong UIA read cannot pass
for an empty document.
"""

import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import comtypes  # noqa: E402
import uiautomation as auto  # noqa: E402

import input_sim  # noqa: E402
import screenshot  # noqa: E402
import window_manager  # noqa: E402


def read_document(hwnd):
    """Whatever the edit surface says it contains, by any pattern that answers."""
    root = auto.ControlFromHandle(hwnd)
    out = {}

    def walk(c, d=0):
        if d > 12:
            return
        try:
            name = c.ControlTypeName
            if name in ("EditControl", "DocumentControl"):
                for pattern in ("GetValuePattern", "GetTextPattern"):
                    try:
                        p = getattr(c, pattern)()
                        out[pattern] = (
                            p.Value if pattern == "GetValuePattern"
                            else p.DocumentRange.GetText(-1)
                        )
                    except Exception as exc:
                        out[pattern] = f"<{type(exc).__name__}>"
            if name == "TabItemControl":
                out["tab_name"] = c.Name
            for ch in c.GetChildren():
                walk(ch, d + 1)
        except Exception:
            pass

    walk(root)
    return out


proc = subprocess.Popen(["notepad.exe"])
time.sleep(1.5)
hwnd = next(
    (w["hwnd"] for w in window_manager.list_windows()
     if w["pid"] == proc.pid or "notepad" in w["process"].lower()),
    None,
)
print(f"hwnd={hwnd}")
try:
    comtypes.CoInitialize()
    window_manager.bring_to_foreground(hwnd)
    time.sleep(0.8)

    print(f"empty:  {read_document(hwnd)}")
    before = screenshot.grab_window(hwnd)

    input_sim.type_text("hello", hwnd=hwnd)
    time.sleep(1.0)

    print(f"typed:  {read_document(hwnd)}")
    after = screenshot.grab_window(hwnd)

    # The screen is the arbiter: if UIA says empty but pixels moved in the text
    # area, UIA is the thing that is wrong, and cannot be used as the check.
    changed = sum(1 for a, b in zip(before.tobytes(), after.tobytes()) if a != b)
    print(f"bytes differing on screen after typing: {changed}")
finally:
    proc.kill()
