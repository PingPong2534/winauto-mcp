"""End-to-end smoke test against a throwaway Notepad window.

Run it directly:  .venv\\Scripts\\python.exe tests\\smoke.py

Drives the real MCP tools against a real window -- there is no useful way to
fake a screen capture, a click, or a foreground switch, and every bug this
project has hit so far (DPI scaling, foreground lock, rescaled crops) only
appears against a real one. Launches its own Notepad and kills it afterwards
so it never touches a window anyone is using.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time

from mcp.server.mcpserver.exceptions import ToolError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import server  # noqa: E402


def call(name, **kwargs):
    """Invoke a tool the way a client would. A tool that raises surfaces here
    as ToolError (the transport turns that into an is_error result), so both
    outcomes come back as (is_error, text)."""
    try:
        result = asyncio.run(server.mcp.call_tool(name, kwargs))
    except ToolError as exc:
        return True, str(exc)
    parts = []
    for block in result.content:
        kind = getattr(block, "type", "?")
        parts.append(block.text if kind == "text" else f"<{kind} {len(block.data)}b b64>")
    return result.is_error, "\n".join(parts)


def check(label, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{(' -- ' + detail) if detail else ''}")
    return bool(condition)


def main():
    failures = 0
    proc = subprocess.Popen(["notepad.exe"])
    try:
        time.sleep(1.5)
        hwnd = next(
            (
                w["hwnd"]
                for w in server.window_manager.list_windows()
                if w["pid"] == proc.pid or "notepad" in w["process"].lower()
            ),
            None,
        )
        if hwnd is None:
            print("  [FAIL] could not find the Notepad window we launched")
            return 1

        print("\n-- attach + journal session")
        err, out = call("attach_window", hwnd=hwnd)
        failures += not check("attach_window succeeded", not err, out[:120])
        failures += not check("reports a journal session", "journal session" in out)
        time.sleep(0.4)

        print("\n-- capture marks the frame as seen")
        err, out = call("screenshot")
        failures += not check("screenshot returned an image", not err and "<image" in out)
        failures += not check("frame recorded as seen", server._state["seen"] is not None)

        print("\n-- an action gets before/after frames")
        err, out = call("type_text", text="สวัสดี hello")
        failures += not check("type_text succeeded", not err, out[:80])
        time.sleep(0.3)

        print("\n-- wait_stable()")
        # An idle window really is pixel-identical frame to frame, so the
        # not-stable path needs something genuinely repainting: keep typing
        # from another thread rather than trusting an incidental repaint,
        # which made an earlier version of this test pass for the wrong reason.
        keep_typing = threading.Event()
        keep_typing.set()

        def churn():
            while keep_typing.is_set():
                server.input_sim.type_text("x", hwnd=hwnd)
                time.sleep(0.1)

        noisy = threading.Thread(target=churn, daemon=True)
        noisy.start()
        time.sleep(0.3)
        err, out = call("wait_stable", timeout=1.5, settle_ms=500)
        busy = json.loads(out)
        failures += not check("does not call a repainting window stable", not err and not busy["stable"], out[:160])
        failures += not check("says what is still moving", busy.get("last_change_bbox") is not None, out[:200])
        print(f"         busy: {out[:150]}")

        keep_typing.clear()
        noisy.join(timeout=3)
        err, out = call("wait_stable", timeout=4.0, settle_ms=300)
        quiet = json.loads(out)
        failures += not check("settles once repainting stops", not err and quiet["stable"], out[:160])
        print(f"         quiet: {out}")

        err, out = call("wait_stable", timeout=3.0, settle_ms=200, region=[300, 300, 700, 500])
        failures += not check("settles when watching a region", not err and json.loads(out)["stable"], out[:160])
        err, out = call("wait_stable", timeout=2.0, region=[100, 100, 10, 10])
        failures += not check("rejects an inverted region", err, out[:120])

        print("\n-- stale-frame guard: the predicate itself")
        # Checked against synthetic frames rather than through a real click,
        # so each case is exact and the 'must stay quiet' cases are covered as
        # carefully as the 'must fire' ones.
        from PIL import ImageDraw

        base = server.grab_window(hwnd)
        far, near = base.copy(), base.copy()
        ImageDraw.Draw(far).rectangle((20, 20, 60, 60), fill=(255, 0, 0))
        ImageDraw.Draw(near).rectangle((490, 490, 530, 530), fill=(255, 0, 0))

        def predicate(seen, now, x=500, y=500):
            server._state["seen"], server._state["seen_t"] = seen, time.time()
            server._state["seen_seq"], server._state["pre_frame"] = 1, now
            return server._stale_block(x, y, "test")

        failures += not check("identical frames do not block", predicate(base, base) is None)
        failures += not check("a change far from the target does not block", predicate(base, far) is None)
        blocked_far = predicate(base, near)
        failures += not check("a change at the target blocks", blocked_far is not None)
        failures += not check(
            "a resize blocks whatever else is true",
            predicate(base, base.resize((base.width // 2, base.height // 2))) is not None,
        )
        failures += not check("no reference frame means no block", predicate(None, base) is None)
        server._state["seen"] = server._state["pre_frame"] = None

        print("\n-- stale-frame guard on click()")
        # A guard that blocks everything is as useless as no guard, so the
        # quiet case is checked first and by itself: look, then click an
        # unchanged spot, and nothing should get in the way.
        call("screenshot")
        err, out = call("click", x=400, y=400)
        failures += not check("click goes through when nothing changed", not err and "blocked" not in out, out[:160])

        # Aim at where the screen actually changed rather than guessing: a
        # click in Notepad's empty area puts the caret at the end of the text,
        # so typed characters appear near the top-left, nowhere near the click.
        #
        # Deriving that point needs care. Typing one word changes three places
        # at once -- the text, the tab title (it gains a modified marker) and
        # the status bar's Ln/Col readout -- and a single box drawn around all
        # three covers almost the whole window, most of which never changed.
        # So compare inside a band that holds only the text, below the tab
        # strip and above the status bar. Frames are grabbed directly rather
        # than through screenshot() because only frames the caller was actually
        # shown may count as 'seen'.
        call("hotkey", keys=["ctrl", "a"])
        call("press_key", key="delete")
        time.sleep(0.4)

        TEXT_BAND = (0, 60, 800, 500)
        call("screenshot")  # this is the frame the guard will judge against
        ref = server.grab_window(hwnd)
        call("type_text", text="Z" * 20)
        time.sleep(0.4)
        box = server.changed_bbox(ref, server.grab_window(hwnd), threshold=10, region=TEXT_BAND)
        failures += not check(
            "the typed text is findable in the text band",
            box is not None and (box[2] - box[0]) < 700,
            f"{box} -- without a tight box the click below would aim at pixels "
            "that never changed, and would test nothing",
        )
        # Aim at the first characters, not the middle of the box: the caret
        # sits at the end of the line and blinks, so a target beside it would
        # block or not depending on which half of the blink got captured.
        tx, ty = box[0] + 5, (box[1] + box[3]) // 2
        print(f"         typing changed {box}; aiming at ({tx}, {ty})")
        err, out = call("click", x=tx, y=ty)
        blocked = json.loads(out.split("\n")[0]) if out.startswith("{") else {}
        failures += not check("click is blocked after the target area changed", blocked.get("blocked") is True, out[:200])
        failures += not check("and reports it did not click", blocked.get("performed") is False)
        failures += not check("and hands back the current screen", "<image" in out)
        failures += not check("and says how stale the view was", "seen_age_s" in blocked, str(blocked)[:200])
        print(f"         reason: {str(blocked.get('reason'))[:150]}")

        err, out = call("click", x=tx, y=ty)
        failures += not check("re-issuing after being shown the screen works", not err and "blocked" not in out, out[:160])

        # The re-issued click put the caret at (tx, ty), so these characters
        # land right on the target -- exactly the situation that just blocked.
        # Only force=true should make the difference.
        call("screenshot")
        call("type_text", text="Y" * 5)
        time.sleep(0.3)
        err, out = call("click", x=tx, y=ty, force=True)
        failures += not check("force=true bypasses the guard", not err and "blocked" not in out, out[:160])

        print("\n-- diff_since_snapshot() narrowed to a region")
        call("snapshot")
        call("type_text", text="Q" * 8)
        time.sleep(0.4)
        _, wide = call("diff_since_snapshot")
        _, narrow = call("diff_since_snapshot", region=list(TEXT_BAND))
        wb, nb = json.loads(wide)["changed_bbox"], json.loads(narrow)["changed_bbox"]
        area = lambda b: (b[2] - b[0]) * (b[3] - b[1])  # noqa: E731
        failures += not check(
            "narrowing to a band shrinks the box by a lot",
            area(nb) * 5 < area(wb),
            f"{wb} ({area(wb)}px2) -> {nb} ({area(nb)}px2) -- the whole-window box is mostly "
            "empty space between separate changes, which is the trap being documented",
        )
        failures += not check("and stays in full-image coordinates", nb[1] >= TEXT_BAND[1], str(nb))
        err, out = call("diff_since_snapshot", region=[10, 10, 5, 5])
        failures += not check("an inverted region is rejected", err, out[:120])

        print("\n-- history()")
        err, out = call("history", last=100)
        failures += not check("history returned", not err, out[:80] if err else "")
        data = json.loads(out)
        tools_seen = [r["tool"] for r in data["records"]]
        failures += not check("run starts at attach_window", tools_seen[0] == "attach_window", str(tools_seen[:4]))
        failures += not check("type_text is recorded", "type_text" in tools_seen)
        typed = next(r for r in data["records"] if r["tool"] == "type_text")
        failures += not check("its arguments were kept", typed["args"].get("text") == "สวัสดี hello", str(typed.get("args")))
        blocked_step = next((r for r in data["records"] if "blocked" in str(r.get("result", ""))), None)
        failures += not check("the blocked click is in the record too", blocked_step is not None,
                              "a refused action must be as visible in history as a performed one")
        failures += not check("it kept a before frame", "before" in typed, str(sorted(typed)))
        failures += not check("it kept an after frame", "after" in typed)
        failures += not check("scale recorded", isinstance(typed.get("scale"), float), str(typed.get("scale")))

        print("\n-- replay_frame()")
        err, out = call("replay_frame", seq=typed["seq"], which="before")
        failures += not check("before frame is retrievable", not err and "<image" in out, out[:120])
        failures += not check("warns against reading coordinates off it", "do not read click coordinates" in out)
        err, out = call("replay_frame", seq=9999)
        failures += not check("unknown seq is rejected", err, out[:120])
        err, out = call("replay_frame", seq=typed["seq"], which="sideways")
        failures += not check("bad `which` is rejected", err, out[:120])

        print("\n-- failures are journaled too, not swallowed")
        err, out = call("press_key", key="not_a_real_key")
        failures += not check("bad key raised", err)
        err, out = call("history", last=5, failures_only=True)
        bad = json.loads(out)["records"]
        failures += not check("the failed step is in history", any(r["tool"] == "press_key" for r in bad), str([r["tool"] for r in bad]))

        print("\n-- sharing the pointer and the desktop with whoever is at the keyboard")
        import win32api  # noqa: PLC0415
        import win32gui  # noqa: PLC0415

        parked = (70, 70)
        win32api.SetCursorPos(parked)
        call("screenshot")
        call("click", x=300, y=300, force=True)
        back = win32api.GetCursorPos()
        failures += not check("the pointer goes back where the person left it",
                              max(abs(back[0] - parked[0]), abs(back[1] - parked[1])) <= 3,
                              f"left at {parked}, ended at {back}")

        win32api.SetCursorPos(parked)
        call("screenshot")
        call("click", x=300, y=300, force=True, keep_cursor=True)
        stayed = win32api.GetCursorPos()
        target = win32gui.ClientToScreen(hwnd, (300, 300))
        failures += not check("keep_cursor leaves it on the target instead",
                              max(abs(stayed[0] - target[0]), abs(stayed[1] - target[1])) <= 3,
                              f"target {target}, ended at {stayed} -- a modal tool that follows "
                              "the pointer needs it left where the click landed")

        # Handing the desktop back has to be asked for, and has to actually
        # work: a run that leaves the person's window buried is the complaint
        # this is here to answer.
        their_window = subprocess.Popen(["calc.exe"])
        try:
            # Take whatever ends up in front as "their window" rather than
            # picking it by process name: Calculator owns several windows, and
            # an earlier version of this test matched one that was never the
            # foreground, then declared a working release_control broken.
            time.sleep(3.5)
            their_hwnd = win32gui.GetForegroundWindow()
            failures += not check(
                "another window is in front to start with",
                their_hwnd and their_hwnd != hwnd,
                f'hwnd={their_hwnd} "{win32gui.GetWindowText(their_hwnd)}"',
            )
            call("screenshot")
            call("click", x=300, y=300, force=True)
            failures += not check("acting on the attached window takes the foreground",
                                  win32gui.GetForegroundWindow() == hwnd)
            err, out = call("release_control")
            time.sleep(0.8)
            failures += not check("release_control gives it back", not err
                                  and win32gui.GetForegroundWindow() == their_hwnd, out[:140])
            # The whole point of the PrintWindow work: still readable from behind.
            err, out = call("screenshot")
            failures += not check("and the attached window is still readable from behind",
                                  not err and "<image" in out
                                  and win32gui.GetForegroundWindow() == their_hwnd, out[:140])
            err, out = call("release_control")
            failures += not check("asking twice says there is nothing left to give back",
                                  "nothing to give back" in out, out[:140])
        finally:
            their_window.kill()
            time.sleep(0.5)

        print("\n-- reading a window that is covered by another one")
        # The point of the whole exercise: a run should not need the window on
        # top, so the machine stays usable while it happens. Verified by
        # parking another window exactly over this one -- not by trusting that
        # PrintWindow returned something, since it returns a black bitmap that
        # 'succeeded' for windows it cannot render.
        import win32con  # noqa: PLC0415
        import win32gui  # noqa: PLC0415

        visible = server.grab_window(hwnd)
        cover = subprocess.Popen(["calc.exe"])
        cover_hwnd = None
        try:
            for _ in range(30):
                time.sleep(0.4)
                cover_hwnd = next(
                    (w["hwnd"] for w in server.window_manager.list_windows()
                     if "calc" in w["process"].lower()),
                    None,
                )
                if cover_hwnd:
                    break
            cl, ct, cr, cb = server.window_manager.get_client_rect_screen(hwnd)
            if cover_hwnd:
                win32gui.SetWindowPos(cover_hwnd, win32con.HWND_TOPMOST, cl, ct,
                                      cr - cl, cb - ct, win32con.SWP_SHOWWINDOW)
            time.sleep(1.2)
            failures += not check("something is actually covering it now", cover_hwnd is not None)

            painted = server.grab_window(hwnd)
            scraped = server.grab_window(hwnd, allow_occluded=False)

            def like(a, b):
                pa = list(a.resize((96, 96)).getdata())
                pb = list(b.resize((96, 96)).getdata())
                near = sum(1 for p, q in zip(pa, pb)
                           if max(abs(p[0] - q[0]), abs(p[1] - q[1]), abs(p[2] - q[2])) < 24)
                return near / len(pa)

            # Measured as disagreement with the known-good view, not as a
            # similarity score against a hand-picked cutoff: both windows are
            # dark-themed, so "looks different" is a weak signal while "wrong
            # by several times as many pixels" is not.
            wrong_painted, wrong_scraped = 1 - like(painted, visible), 1 - like(scraped, visible)
            failures += not check("the covered window still reads as itself",
                                  wrong_painted < 0.1, f"{wrong_painted:.0%} of pixels wrong")
            failures += not check(
                "a plain screen grab would have read the cover instead",
                wrong_scraped > wrong_painted * 3,
                f"screen grab {wrong_scraped:.0%} wrong vs {wrong_painted:.0%}",
            )
            failures += not check("and it is not a blank frame",
                                  len(set(painted.resize((64, 64)).getdata())) > 20)
        finally:
            cover.kill()
            time.sleep(0.5)

        print("\n-- session folder on disk")
        d = server.journal.session_dir()
        files = sorted(os.listdir(d))
        failures += not check("journal.jsonl written", "journal.jsonl" in files)
        failures += not check("thumbnails written", any(f.endswith(".jpg") for f in files), str(files[:6]))
        size_kb = sum(os.path.getsize(os.path.join(d, f)) for f in files) / 1024
        print(f"         session dir: {d}\n         {len(files)} files, {size_kb:.0f} KB")
    finally:
        proc.kill()

    print(f"\n{'ALL PASSED' if not failures else str(failures) + ' CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
