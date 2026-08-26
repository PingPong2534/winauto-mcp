# Status — winauto-mcp

Snapshot of right now. Overwritten, never appended — history lives in `git log`;
finished behaviour lives in [SPEC.md](SPEC.md).

## Doing right now

Idle. The focus-return feature is **finished and documented**; nothing is
committed yet, so the whole of it is still sitting in the working tree —
`window_manager.py`, `server.py`, `overlay.py`, `tests\smoke.py`,
`tests\diag_focus_return.py`, `tests\probe_notepad_lifecycle.py`,
`docs/SPEC.md`, `README.md`, this file.

**Everything is green:**

| | |
|---|---|
| `tests\diag_focus_return.py` | **18/18.** Creates its own two windows, so it cannot leak one and nothing on the desktop can perturb it. The hand-back fires; refuses while a menu is open without forgetting what it owes; refuses if the person already moved; works through the real `mcp.call_tool` path; `keep_foreground(true)` stops it. |
| `tests\smoke.py` | **109/109** (was 106). |
| `tests\test_input_guard.py` | **56/56.** |

## Just finished

**A real bug the diagnostic caught, found by measuring rather than guessing:
the green outline was stealing the foreground.** Two checks failed reporting a
handle that was neither the person's window nor the app's; printing its class
named it — `TkTopLevel`, the overlay itself. `root.deiconify()` activates the
window it shows, so the outline took the foreground away from the very window
it was outlining. A decoration holding the foreground means keystrokes are
aimed at a rectangle, and the hand-back correctly concluded "the person has
moved on" and refused. `overlay.py` now sets `WS_EX_NOACTIVATE` on the Tk
toplevel, which was enough on its own: 16/18 → **18/18**.

**Win11 Notepad restores its unsaved windows when it is next launched.** The
`taskkill` did not delete them. Running `tests\smoke.py` launched `notepad.exe`
at 13:24:27 today and **56 windows came back**, the whole leaked set plus the
person's `*[ครุ่นคิด]…` note. Read back through UI Automation, that note is
byte-identical to the rescued copy on the Desktop, so nothing is lost — but it
means the leak cannot be cleaned up by killing anything.

**The leak is fixed at source in `smoke.py`, and now asserted rather than
hoped for.** Two separate faults, both measured first in
`tests\probe_notepad_lifecycle.py`:

- It picked its target with `"notepad" in process`, i.e. *whichever Notepad
  window enumerated first*. Harmless on an empty desktop, data loss on this
  one — it would have attached to one of 56 and typed into an unsaved note. It
  now takes only a handle that appeared **after** it asked for one, and refuses
  outright if none does.
- It closed nothing. `proc.kill()` kills the stub. Measured instead: undoing
  the typing clears Notepad's `*` modified marker, and Alt+F4 on an unmodified
  document closes with no save prompt to answer. The teardown refuses in both
  directions — no key is sent at all if the window cannot be focused (an Alt+F4
  aimed at whatever is in front would close somebody's app), and the window is
  left open if the marker will not clear, because a leaked window is a nuisance
  and a wrongly-answered save prompt is lost work. Two consecutive runs:
  **56 before, 56 after.**

## Waiting on the user

- **Notepad's saved session still holds all 56 windows.** The desktop is clear
  — `taskkill /PID 32480 /F` on 2026-08-26 left 0 windows and 0 processes — but
  that is cosmetic: `LocalState\TabState` under
  `Microsoft.WindowsNotepad_8wekyb3d8bbwe` still contains **56 files (5 KB)**,
  and the next launch of Notepad by anything restores them, exactly as the last
  `smoke.py` run did. So the next `smoke.py` run will bring 55 junk windows
  back with it. Emptying that folder is what makes it permanent, and it is
  irreversible; both dumps of the note are on the Desktop and verified
  byte-identical, but nothing there will be deleted without a decision.
- **Nine more scripts still leak the same way** — `diag_typing.py`,
  `diag_stability.py`, `diag_keyboard_block.py`, `diag_overlay_paint.py`,
  `diag_attach_cost.py`, `probe_popup_detect.py`, `probe_notepad_text.py` and
  the three `spike_background*.py`. `smoke.py`'s `notepad_hwnds()`,
  `wait_for_new_notepad()` and `close_notepad()` are the fix, ready to be
  reused; whether it is worth doing depends on whether the diagnostics stay
  (below).
- **`F:\knowledge` commit decision**, still unanswered.
  `development/windows-background-capture-and-input.md` is written and
  cross-linked but uncommitted, because that repo's `README.md` diff also
  carries ~13 lines of unrelated in-flight work and index entries pointing at
  still-untracked files. Options put to the user: (1) add `node_modules/` to
  `.gitignore` and commit everything (my recommendation), (2) commit only the
  two `development/*.md` files, (3) leave it. Three findings from this session
  belong in that doc and are not yet written there: menu mode
  (`GUI_INMENUMODE`) is sticky per thread and never clears; Win11 Notepad puts
  every window in one process *and* restores them after a kill; a Tk overlay
  steals the foreground unless it is marked `WS_EX_NOACTIVATE`.
- Whether the diagnostics stay in the repo — they print measurements and mostly
  assert nothing, but each earned its keep by overturning a wrong assumption,
  and `docs/SPEC.md` and the README cite them as the source of measured numbers.
