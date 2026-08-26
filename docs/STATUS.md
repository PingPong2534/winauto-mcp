# Status — winauto-mcp

Snapshot of right now. Overwritten, never appended — history lives in `git log`;
finished behaviour lives in [SPEC.md](SPEC.md).

## Doing right now

Idle. Nothing is running. Branch `screen-freshness-and-desktop-sharing` is
committed and pushed up to `8411927`; working tree clean.

## Just finished

**The "attached window feels stuck" complaint — two causes, both fixed.**

1. **The tracking outline was the actual freeze.** `overlay.py` repainted
   unconditionally on every 150 ms poll — `deiconify` + `geometry` +
   `canvas.delete("all")` + redraw, on a transparent always-on-top window the
   size of the target, for the entire session. It now compares against what is
   already drawn and touches Tk only on a real change. Measured
   (`tests\diag_overlay_paint.py`): **1 repaint over 3 idle seconds, was ~20**;
   a window that moves still updates, and untracking stops it dead.
2. **Attaching no longer takes the desktop.** `attach_window(hwnd)` now only
   chooses the target — it does not raise the window and draws no outline. The
   window is raised, and the outline appears, at the **first input**, via a
   control hook in `window_manager`. `release_control()` puts both back.
   `take_control=true` restores the old immediate behaviour when a person
   should see what is about to be driven. The outline now means "being driven
   right now" rather than "bookmarked".

**Answering "มันจะทำให้ช้าหรือเปล่า" with numbers, not reasoning**
(`tests\diag_attach_cost.py`): every input path in `input_sim` already called
`bring_to_foreground` itself, so the raise was being paid twice. Deferring it
removes one of the two rather than adding a new cost —

| | |
|---|---|
| `attach_window()` deferred | **6.0 ms** |
| `attach_window(take_control=True)` | 26.7 ms |
| first click after a deferred attach | 381.2 ms *(pays the raise)* |
| next click, already in front | 369.2 ms |

≈ **9 ms faster overall**, and a read-only run never pays it at all.

Verified: **88 checks, all passed** (was 81). Seven are new and deliberately
run with Calculator genuinely in front — attach leaves the person's window
foreground, draws no outline, the window stays readable anyway, the first input
raises it *and* brings the outline up, and `release_control` removes it. On the
freshly-launched Notepad this test could not have failed, since the target is
already foreground there.

## Previously finished (same commit)

Two features asked for to cut the cost of driving a familiar app:

- **`capture_region(x1, y1, x2, y2)`** — screenshot one rectangle instead of
  the whole window, returning a header with the region captured and the offset
  to add back to get client coordinates. Measured **4,244 bytes against 26,404**
  for the same moment's full frame.
- **`run_steps(steps, delay_ms, stop_on_error)`** — up to 40 actions in one
  call (`click`, `drag`, `scroll`, `type`, `key`, `hotkey`, `click_element`,
  `wait`, `wait_stable`, `capture`, `check`). The whole script is validated
  before any of it runs, each step is journaled separately as `script:<action>`
  with its own before/after frames, and a `check` step stops a run whose
  prediction turned out wrong instead of driving the app further.
- **The staleness guard was rewritten first, before either of those**, because
  a naive crop tool would have silently defeated it — the guard held one
  whole-window frame, so a partial capture would have replaced it with a fresh
  full grab, and every later comparison would have been a fresh frame against
  itself. It now keeps the last 8 *views* (rectangle + frame + when), so
  looking at a toolbar certifies clicks in that toolbar and nowhere else, and a
  coordinate in a part of the window never looked at this run is refused with
  the list of rectangles that *were* looked at. This also fixed a pre-existing
  bug where `locate_in_region` blinded the guard for the rest of the window.

**Verification of those two:** the checks that were hard to get honest — an unknown action is
rejected *with nothing performed*, proven by asking the journal for `script:*`
records rather than by looking for an absence of change on screen (Notepad's
blinking caret is enough to fake a diff); and the crop-is-cheaper claim
compares two frames taken at the same moment rather than against a byte count
measured earlier on a differently-populated window.

**Docs updated in the same change:** `README.md` (Design bullets, two Tools
rows, two Known-limitations entries), `docs/SPEC.md` (`capture_region`,
`run_steps`, the region-scoped "seen" rule, per-step journaling), and the
server's `instructions=` string so the calling model is told to prefer a crop
for a spot check and that only step 1 of a script is guarded.

**Trade-off accepted deliberately, not overlooked:** `run_steps` can only guard
its first step — steps 2..n act on a screen the script itself changed, which
the caller has never been shown, so their coordinates are a prediction. The
recovery path is the per-step journal, not prevention. Stated in both README
and SPEC rather than left implicit.

## Waiting on the user

- **Merge this branch?** `screen-freshness-and-desktop-sharing` is pushed but
  no PR is open — open one, or merge to `master` directly? Everything on it is
  two commits ahead of `master`, and `master` has never seen any of the
  freshness, desktop-sharing, batching or region-capture work.
- **`F:\knowledge` commit decision**, still unanswered.
  `development/windows-background-capture-and-input.md` is written and
  cross-linked but uncommitted, because that repo's `README.md` diff also
  carries ~13 lines of unrelated in-flight work and index entries pointing at
  still-untracked files. The options put to the user: (1) add `node_modules/`
  to `.gitignore` and commit everything (my recommendation), (2) commit only
  the two `development/*.md` files, (3) leave it.
- **PR #1 "update doc of codex"** (branch `update_doc`, +39/−0) is open and
  unreviewed — review the diff, or merge as-is?
- Whether the diagnostics stay in the repo — `tests\diag_stability.py`,
  `tests\diag_typing.py`, `tests\diag_overlay_paint.py`,
  `tests\diag_attach_cost.py` and the three
  `tests\spike_background*.py` scripts. They are diagnostics,
  not tests: they print measurements and assert nothing. Each earned its keep
  by overturning a wrong assumption, and `docs/SPEC.md` and the README cite
  them as the reproducible source of the measured numbers.
