# Status — winauto-mcp

Snapshot of right now. Overwritten, never appended — history lives in `git log`;
finished behaviour lives in [SPEC.md](SPEC.md).

## Doing right now

Idle. The `hover` tool, the pointer hold behind it, the overlay fix and the SPEC
entries for all of it are committed and pushed as `d562aab` on `focus-return`,
which is the branch **[PR #4](https://github.com/PingPong2534/winauto-mcp/pull/4)**
is open on. That PR now carries three commits, 20 files, +2689/−127 — its title
still says "Give the person their window back when an action finishes", which
covered the first commit and no longer covers the branch.

**Everything run is green:**

| | |
|---|---|
| `tests\diag_hover.py` | **18/18.** Creates its own window with a real Win32 tooltip on it. |
| `tests\test_input_guard.py` | **88/88** (was 56). |
| `tests\probe_overlay_activation.py` | **19/19.** |
| `tests\probe_mouse_lock.py` | **6/6.** |
| `tests\diag_focus_return.py` | **18/18, five runs in a row** — it was flaky at 16/18 until the overlay fix below. |

`tests\smoke.py` has **not** been run since these changes, on purpose: it
launches Notepad, which restores the 55 leaked windows (see below). It is the
one gap in the current green.

## Just finished

**`hover(x, y, dwell_ms=700, force=False)`** — rest the pointer somewhere, wait,
photograph what the app shows, put the pointer back. Four things were measured
first and each one changed the design:

- **A screen grab, not `PrintWindow`.** A tooltip is its own top-level window
  and `PrintWindow` renders one window, so the usual capture path returns a
  picture with no tooltip in it. Confirmed by looking at both PNGs.
- **The pointer is genuinely pinned**, not merely hidden from apps: a
  `WH_MOUSE_LL` hook returning 1 leaves the cursor where it was when a move to a
  far corner is sent. `ClipCursor` was rejected — it is system-wide state owned
  by no process, so a crash mid-hold would trap a stranger's pointer; Windows
  tears a dead process's hooks down for free.
- **Popup text is read through UIA, not transcribed from pixels** — a tooltip's
  `Name` is exactly the string the app set, so a caller can assert on it.
- **A hover image marks nothing as seen.** I had it marking the frame as looked
  at until the two capture paths were compared: outside the tooltip they differ
  on 0.87 % of pixels, worst channel delta 245, spread across 100 rows and not
  explained by edges. Unexplained disagreement plus a picture of a transient is
  not a basis for aiming a click, so `hover` now tells callers to `screenshot()`
  first.

**Yesterday's overlay fix was wrong and had been believed for a day.** It set
`WS_EX_NOACTIVATE` on the handle from Tk's `wm_frame()`, which is a *different
window* — measured with the outline on screen, the visible `TkTopLevel` had the
style clear. The 18/18 that shipped it was luck, and `diag_focus_return.py` went
back to 16/18 today with the outline holding the foreground again. What works:
`winfo_id()` walked up with `GA_ROOT`, **on the Tk thread**, which resolves the
right handle even while the window is withdrawn — so the style is applied before
the window is ever mapped and there is no first showing to race.
`tests\probe_overlay_activation.py` now asks this of the window actually on
screen, across three show/hide cycles.

**An honest gap in `probe_mouse_lock.py`:** it reports `genuinely-human events
seen: 0`. Every event Python can send is injected, so a physical hand cannot be
scripted; the probe stands in for one with a foreign signature. The real-hand
path is inferred — it reaches the same decision by the same route and differs
only in a flag the decision never reads.

## Waiting on the user

- **PR #4 is still open and unmerged**, and its title and description now
  describe only its first commit. Either it gets retitled to cover the whole
  branch, or `hover` comes out into a PR of its own — not decided.
- **Notepad's saved session still holds 56 windows.** The desktop is clear, but
  `LocalState\TabState` under `Microsoft.WindowsNotepad_8wekyb3d8bbwe` still has
  **56 files (5 KB)**, and the next launch of Notepad by anything restores them
  — which is why `smoke.py` has not been run. Emptying that folder is
  irreversible; both dumps of the person's note are on the Desktop and verified
  byte-identical, but nothing will be deleted without a decision.
- **Nine more scripts still leak Notepad windows the same way** —
  `diag_typing.py`, `diag_stability.py`, `diag_keyboard_block.py`,
  `diag_overlay_paint.py`, `diag_attach_cost.py`, `probe_popup_detect.py`,
  `probe_notepad_text.py` and the three `spike_background*.py`. `smoke.py`'s
  `notepad_hwnds()` / `wait_for_new_notepad()` / `close_notepad()` are the fix,
  ready to be reused.
- **Three incident scripts** — `rescue_notepad_text.py`,
  `cleanup_leaked_notepads.py`, `probe_notepad_text.py` — written to deal with
  that leak. Keep them in the repo or delete them?
- **`F:\knowledge` commit decision**, still unanswered.
  `development/windows-background-capture-and-input.md` is written and
  cross-linked but uncommitted, because that repo's `README.md` diff also
  carries ~13 lines of unrelated in-flight work. Options put to the user: (1)
  add `node_modules/` to `.gitignore` and commit everything (my
  recommendation), (2) commit only the two `development/*.md` files, (3) leave
  it. Four findings belong in that doc and are not yet written there: menu mode
  (`GUI_INMENUMODE`) is sticky per thread and never clears; Win11 Notepad puts
  every window in one process *and* restores them after a kill; a Tk overlay
  needs `WS_EX_NOACTIVATE` on the `winfo_id()`+`GA_ROOT` window, resolved on the
  Tk thread, before first map; and `PrintWindow` cannot capture tooltips.
