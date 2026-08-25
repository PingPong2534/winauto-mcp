# Status — winauto-mcp

Snapshot of right now. Overwritten, never appended — history lives in `git log`.

## Doing right now

Idle. The reliability work and the mouse/keyboard-sharing work are both done,
documented and verified; nothing is committed yet.

## Just finished

**1. Acting on the current screen, not a remembered one** (three mechanisms):

- **Stale-frame guard** — `click()` and `drag()` compare the 40 px around the
  target against the frame the caller was last *shown*. If it changed, the
  action is refused and the current screen comes back instead, with how long
  ago and at which step the stale view was taken. Re-issuing then goes
  through; `force=true` skips the check.
- **`wait_stable()`** — polls until the window (or a region of it) stops
  repainting. Deliberately a separate tool that is never called
  automatically, so waiting stays the model's decision.
- **Journal** — every tool call appended to `%TEMP%\winauto-mcp\<session>\`
  with before/after JPEG thumbnails, readable back via `history()` and
  `replay_frame()`. Failures are recorded too, not just successes.

**2. Sharing the mouse, keyboard and desktop with whoever is at the machine**
— answering "เป็นไปได้ไหมถ้าไม่อยากให้มันแย้ง mouse กับ keyboard". The honest
split, established by measurement rather than assertion:

- **Reading is now fully solved.** Capture goes through
  `PrintWindow(PW_RENDERFULLCONTENT)` — the window renders itself — and falls
  back to the old screen grab only when that returns nothing usable (including
  the all-black bitmap case, which `PrintWindow` reports as success). Verified
  against Blender 5.2 (OpenGL), the Godot 4.6 editor and Windows 11 Notepad
  while each was fully covered.
- **Input is not solvable** for these apps. Four posted/sent-message variants
  (`WM_MOUSEMOVE`, `WM_LBUTTONDOWN`, `WM_CHAR`, with and without a spoofed
  `WM_ACTIVATE`) moved **zero pixels** in all three. Godot's only reaction was
  a title-bar brightening. True side-by-side use needs a separate session or
  VM.
- **Mitigation shipped instead:** `click`/`drag`/`scroll` put the pointer back
  where the person left it (`keep_cursor=true` opts out, for modal tools that
  keep following the mouse), and the new `release_control()` hands the
  foreground back to the window they were using — reading the attached window
  keeps working from behind.

**Verification:** `tests\smoke.py` drives all of it against a throwaway
Notepad — **52 checks, all passing**, including reading a window with
Calculator parked on top of it (5% of pixels wrong via `PrintWindow` vs 24%
for a plain screen grab) and a full `release_control()` round trip.

**Docs:** `docs/SPEC.md` written (behaviour spec for all 23 tools), `README.md`
Design/Tools/Known-limitations updated, and the server `instructions` string
now tells the model about staleness, `wait_stable`, the journal and giving the
desktop back.

Also from a diagnostic run: a change bounding box is one box around *every*
changed pixel, so typing one word into Notepad (text + tab marker + status
bar) yields a near-window-wide box whose centre never changed. Documented on
`changed_bbox` and `diff_since_snapshot`, and `diff_since_snapshot` gained a
`region` argument so the model can narrow a sprawling box down.

## Waiting on the user

- **Nothing is committed.** The whole of the above is uncommitted on `master`.
- **PR #1 "update doc of codex"** (branch `update_doc`, +39/−0) is open and
  unreviewed — review the diff, or merge as-is?
- Whether `tests\diag_stability.py`, `tests\diag_typing.py` and the three
  `tests\spike_background*.py` scripts stay in the repo. They are diagnostics,
  not tests: they print measurements and assert nothing. Each earned its keep
  by overturning a wrong assumption, and `docs/SPEC.md` and the README already
  cite them as the reproducible source of the measured numbers.
