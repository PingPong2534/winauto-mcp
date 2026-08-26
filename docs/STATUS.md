# Status — winauto-mcp

Snapshot of right now. Overwritten, never appended — history lives in `git log`;
finished behaviour lives in [SPEC.md](SPEC.md).

## Doing right now

Idle. Branch `setup-and-howto-docs`, one commit ahead of the last push: the two
heap tools below, with `SPEC.md` updated in the same commit. Nothing is running.

The doc work from earlier is committed (`5f38961` — `SETUP.md` and `HOWTOUSE.md`
at the repo root); the working tree is clean apart from what that commit
contains.

**[PR #4](https://github.com/PingPong2534/winauto-mcp/pull/4) is merged**
(`5a82791`, 2026-08-26): the foreground hand-back, `hover` and its pointer hold,
the overlay fix, and the `smoke.py` Notepad-leak fix are all on `master` now.
There is no open PR.

**Everything run is green:**

| | |
|---|---|
| `tests\probe_heap_diff.py` | **passes** — the new one, numbers below. |
| `tests\diag_hover.py` | **18/18.** |
| `tests\test_input_guard.py` | **88/88.** |
| `tests\probe_overlay_activation.py` | **19/19.** |
| `tests\probe_mouse_lock.py` | **6/6.** |
| `tests\diag_focus_return.py` | **18/18, five runs in a row.** |

`tests\smoke.py` has **not** been run since the merge, on purpose: it launches
Notepad, which restores the 56 leaked windows (see below). It is the one gap in
the current green.

## Just finished

**Two new tools — `heap_snapshot(label)` and `heap_diff(before, after)`** — so
the question "we opened a screen and closed it; what did it leave behind?" can
be answered by object type, not by watching the working set. Total tool count
is now **32** (was 30).

- **`heap.py`** wraps `dotnet-gcdump` (a separately-installed .NET SDK global
  tool, not a Python dependency). `attach_window` now records the owning pid —
  the heap tools need it *after* the window may already be gone, which is
  exactly the case a leak hunt cares about — and clears any held snapshots, so
  a diff can never compare two different programs.
- **Why gcdump and not memory size**: .NET does not hand heap segments back to
  the OS, so working set stays high with nothing leaking and stays flat while
  leaking. gcdump induces a gen2 blocking GC and counts only survivors.

**Four things were measured before the code was written, and each changed it:**

- **The report's first column is per-object size, not the row total.** Summing
  (per-object × count) over every row came to 7,441,674 bytes against the
  9,816,966 the report's own header claims — **75.8%**. So counts are exact and
  **byte figures are approximate and are never summed** into a total.
- **A type can appear on several rows**, split by size bucket. `System.String`
  came back as three rows of 4, 25 and 57,406; **30 of 1,882 types** were split.
  Rows are aggregated by type name before anything is compared.
- **`bytes_per_obj` comes from the row with the most instances**, not the
  largest. Taking the largest was written first and caught in verification: it
  reported every `System.String` as 28,130 bytes when 57,406 of 57,435 are 22.
- **There is a real noise floor.** Two snapshots of a process doing nothing but
  `Start-Sleep` differ by **4,217 objects across 255 types** — taking a snapshot
  itself makes the runtime materialize reflection metadata. Both tools say so in
  their own output; a single before/after pair is not a leak list.

**Cost, measured**: collect against a 100 MB heap took 1.58 s wall-clock, of
which the target was actually *stopped* for **24 ms**. Each dump is ~3 MB on
disk in the journal session folder.

**`tests\probe_heap_diff.py`** proves the whole chain against a known answer: it
launches its own PowerShell target, has it hold 20,000 `System.Uri`, snapshots,
tells it to allocate 20,000 more, snapshots again, and asserts the delta.
Result: `System.Uri +20,000` (56 bytes each) — ranked **#3 of 59 types that
grew**, with the biggest noise entry at +674, so the signal sat 30× above it. It
also asserts `attach_window` recorded the right pid, and kills the target.

**Not done, and not asked for yet:** no run against the real Uno application —
none was running this session. `dotnet-dump` (for SOS `gcroot`, i.e. *why* a
surviving object is still referenced) is not installed; that is the natural next
tool once a leaking type has a name.

## Waiting on the user

- **`SETUP.md` and `HOWTOUSE.md` now say "30 tools" and are stale at 32.**
  `SETUP.md`'s figure came from a real stdio handshake (`tools: 30`), and
  `HOWTOUSE.md` documents all 30 individually — so fixing this means re-running
  the handshake and writing two entries, not editing a number. Left for a
  separate commit rather than smuggled into this one.
- **`smoke.py` has never run against what is now on `master`.** It was 109/109
  at the first of the three merged commits and has not been run since, because
  it launches Notepad (below).
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
  it. Five findings belong in that doc and are not yet written there: menu mode
  (`GUI_INMENUMODE`) is sticky per thread and never clears; Win11 Notepad puts
  every window in one process *and* restores them after a kill; a Tk overlay
  needs `WS_EX_NOACTIVATE` on the `winfo_id()`+`GA_ROOT` window, resolved on the
  Tk thread, before first map; `PrintWindow` cannot capture tooltips; and
  gcdump's report column is per-object size with types split across size
  buckets.
