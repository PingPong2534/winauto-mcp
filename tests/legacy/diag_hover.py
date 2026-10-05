"""Does hover() actually deliver a hover, and give the pointer back?

Drives the real tool against a window this script creates and owns, with a
genuine Win32 tooltip attached to it. Nothing on the desktop is touched, no
application belonging to anyone is driven, and the window is closed on the way
out however this ends -- so it can be run at any time without leaving anything
behind and gives the same answer on any machine.

What it will not tell you: whether a hand on the mouse is really held out. Every
event Python can send is injected, so there is no way to play the part of a hand
from here. tests/probe_mouse_lock.py measures the swallow itself against a real
hook, using a foreign signature as a stand-in, and tests/test_input_guard.py
covers the lease logic. This is the layer above: aim, wait, photograph, restore.

Run: .venv\\Scripts\\python.exe tests\\diag_hover.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32api
import win32con
import win32gui

import input_guard
import server
import window_manager
from probe_hover import TOOLTIP_TEXT, ToolTipWindow

PASSED, FAILED = [], []


def check(label, ok, detail=""):
    (PASSED if ok else FAILED).append(label)
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{'  -- ' + detail if detail else ''}")


def unwrap(result):
    """A tool returns [report_json, Image]. Split it into the two."""
    if isinstance(result, list) and result and isinstance(result[0], str):
        return json.loads(result[0]), result[1]
    raise AssertionError(f"unexpected tool result: {result!r}")


def differing_fraction(a, b):
    pa, pb = list(a.getdata()), list(b.getdata())
    off = sum(1 for p, q in zip(pa, pb)
              if max(abs(p[0] - q[0]), abs(p[1] - q[1]), abs(p[2] - q[2])) > 24)
    return off / max(1, len(pa))


def main():
    parked = win32api.GetCursorPos()
    win = ToolTipWindow("HOVER DIAG -- has a real Win32 tooltip", 240, 240)
    try:
        server._state["hwnd"] = win.hwnd
        server._state["seen"] = []
        left, top, right, bottom = window_manager.get_client_rect_screen(win.hwnd)
        cx, cy = (right - left) // 2, (bottom - top) // 2

        print("\n-- a hover on a control that has a tooltip")
        report, image = unwrap(server.hover(cx, cy, force=True))
        print("   " + json.dumps(report, ensure_ascii=False))

        tips = [p for p in report["appeared"] if p["class"] == "tooltips_class32"]
        check("the tooltip is reported as having appeared", bool(tips),
              f"appeared: {[p['class'] for p in report['appeared']]}")
        if tips:
            check("its text is read, not guessed at", tips[0]["text"] == TOOLTIP_TEXT,
                  f"text={tips[0]['text']!r}")
            check("its position is given in client coordinates",
                  all(-2000 < v < 4000 for v in tips[0]["rect"]), f"rect={tips[0]['rect']}")
            check("it is inside the returned image", tips[0]["in_the_image"] is True)

        # Caught for real: the outline is shown when a window is driven, so
        # without excluding it every hover reports our own decoration as
        # something the app popped up.
        check("our own outline is not reported as something the app raised",
              not [p for p in report["appeared"] if p["class"].startswith("Tk")],
              f"appeared: {[p['class'] for p in report['appeared']]}")

        check("the pointer was held for the dwell", report["pointer_held"] is True,
              report.get("pointer_not_held_because", ""))
        check("the dwell is reported", report["dwell_ms"] >= 500, str(report["dwell_ms"]))

        print("\n-- the image is the one that can contain a tooltip")
        painted = server.grab_window(win.hwnd)
        check("the returned image differs from a PrintWindow render of the same window",
              differing_fraction(image_to_pil(image), painted) > 0.001,
              "PrintWindow cannot render another window's tooltip, so if these matched, "
              "hover would be returning the wrong capture path")

        print("\n-- the pointer and the person's window come back")
        check("the pointer is back where it started",
              near(win32api.GetCursorPos(), parked),
              f"started {parked}, now {win32api.GetCursorPos()}")
        check("the mouse hold was let go", input_guard.mouse_guard().blocking() is False)
        check("the keyboard was let go too", input_guard.guard().blocking() is False)

        print("\n-- a hover image is not something you may aim from")
        check("nothing was marked as looked-at", server._state["seen"] == [],
              f"{len(server._state['seen'])} views recorded")

        print("\n-- hovering somewhere with nothing to show")
        server._state["seen"] = []
        report2, _ = unwrap(server.hover(6, 6, dwell_ms=250, force=True))
        check("no popup is invented", report2["appeared"] == [],
              str(report2["appeared"]))
        check("and it says so plainly", "note_no_popups" in report2)
        check("the pointer came back from that one too",
              near(win32api.GetCursorPos(), parked),
              f"now {win32api.GetCursorPos()}")

        print("\n-- it refuses the mouse mid-drag rather than stranding one")
        held = input_guard.buttons_physically_down()
        check("nobody is holding a button as this runs", held == set(), str(held))
        with input_guard.holding_mouse(0.5) as hold:
            check("the hold is taken when no button is down", hold.locked is True, hold.reason)
        check("and released at the end of the block",
              input_guard.mouse_guard().blocking() is False)

        print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
        for label in FAILED:
            print(f"  FAILED: {label}")
        return 1 if FAILED else 0
    finally:
        server._state["hwnd"] = None
        win32api.SetCursorPos(parked)
        win32gui.PostMessage(win.hwnd, win32con.WM_CLOSE, 0, 0)
        time.sleep(0.2)


def near(got, want, slack=2):
    return abs(got[0] - want[0]) <= slack and abs(got[1] - want[1]) <= slack


def image_to_pil(image):
    """The tool hands back an MCP Image carrying PNG bytes; decode it so the
    capture path can be compared rather than trusted."""
    import io

    from PIL import Image as PILImage

    return PILImage.open(io.BytesIO(image.data)).convert("RGB")


if __name__ == "__main__":
    sys.exit(main())
