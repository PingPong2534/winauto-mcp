"""Read the text out of every open Notepad window, before anything closes them.

Read-only: UI Automation, no input, no focus changes, nothing typed. Exists
because the leaked-window cleanup is irreversible and was decided from window
*titles*, and a Notepad title only shows the first line -- so a window judged
to be test junk could still hold something below the fold.

Writes one file per window into a folder, plus an index, and prints a summary
that flags anything not matching the shapes the tests are known to produce.
"""

import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32gui
import uiautomation as auto

import window_manager

# Content the tests produce: runs of ASCII letters, optionally after the Thai
# word the typing test uses. Anything else is worth a human's eyes.
TEST_CONTENT = re.compile(r"^(สวัสดี ?)?[A-Za-z]*$")


def read_text(hwnd):
    control = auto.ControlFromHandle(hwnd)
    doc = control.DocumentControl(searchDepth=20)
    try:
        value = doc.GetValuePattern().Value
        if value:
            return value
    except Exception:  # noqa: BLE001 - fall through to the text pattern
        pass
    return doc.GetTextPattern().DocumentRange.GetText(-1)


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\news4\OneDrive\Desktop\notepad-rescue-2026-08-26"
    os.makedirs(dest, exist_ok=True)

    windows = [w for w in window_manager.list_windows()
               if w["process"].lower().startswith("notepad")]
    print(f"{len(windows)} notepad windows\n")

    index = []
    unusual = []
    failed = []
    for w in windows:
        hwnd = w["hwnd"]
        try:
            text = read_text(hwnd)
        except Exception as exc:  # noqa: BLE001 - report, never abort the sweep
            failed.append((hwnd, w["title"], repr(exc)))
            continue
        path = os.path.join(dest, f"{hwnd}.txt")
        io.open(path, "w", encoding="utf-8-sig", newline="").write(text)
        index.append((hwnd, w["title"], len(text)))
        if text.strip() and not TEST_CONTENT.match(text.strip()):
            unusual.append((hwnd, w["title"], len(text)))

    io.open(os.path.join(dest, "index.txt"), "w", encoding="utf-8-sig") .write(
        "\n".join(f"{h}\t{n}\t{t}" for h, t, n in index)
    )

    empty = sum(1 for _, _, n in index if n == 0)
    print(f"  saved   : {len(index)} files into {dest}")
    print(f"  empty   : {empty}")
    print(f"  failed  : {len(failed)}")
    for hwnd, title, err in failed:
        print(f"      {hwnd} {title!r} -> {err}")

    print(f"\n  NOT recognisable as test output: {len(unusual)}")
    for hwnd, title, n in unusual:
        print(f"      {hwnd:<10} {n:>6} chars  {title!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
