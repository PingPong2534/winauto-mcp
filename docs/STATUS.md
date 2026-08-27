# Status — winauto-mcp

Snapshot of right now. Overwritten, never appended — history lives in `git log`;
finished behaviour lives in [SPEC.md](SPEC.md).

## Doing right now

Idle. Nothing is running.

Branch **`uipi-refusal`** is pushed and tracking `origin`, one commit ahead of
`master`. **[PR #7](https://github.com/PingPong2534/winauto-mcp/pull/7) is open**
and closes [issue #6](https://github.com/PingPong2534/winauto-mcp/issues/6),
which has [a comment](https://github.com/PingPong2534/winauto-mcp/issues/6#issuecomment-5436843424)
explaining the fix and correcting the issue's `ACCESS_DENIED` assumption.
Nothing is merged — the PR is waiting on review.

## Just finished

**Input sent to an elevated window is now refused instead of reported as
done.** Issue #6: with the server at medium integrity and the target started
with *Run as administrator*, Windows UIPI discarded every click and keystroke
while `click`, `type_text`, `press_key` and `run_steps` all reported success.
The reporter spent six tool calls and reached a wrong root cause (that a human
was typing elsewhere) before suspecting the tool.

**What was measured before anything was written, and how it changed the fix:**

- **The check is a reading, not a guess.** The issue assumed a medium process
  could not read a high process's token and that the fix would have to infer
  elevation from `ACCESS_DENIED`. Measured: `PROCESS_QUERY_LIMITED_INFORMATION`
  is granted *across* integrity levels. Across **all 16 windowed processes** on
  this machine the level was read outright and **0 were unreadable**. So there
  is no false-positive class to defend against, and an unreadable level is
  treated as *drivable* rather than as elevated.
- **A live elevated target already existed** — `mmc.exe`, Task Scheduler — so
  the refusal was tested against a genuinely elevated window without asking
  anyone to approve a UAC prompt. Independently confirmed to really be more
  privileged: it denies `PROCESS_VM_READ` (err 5) while peers grant it.
- **Cost: ~3 µs per window**, so nothing is cached. Caching a pid→level answer
  would let a recycled pid speak for the wrong program.
- **`uiAccess` is honoured.** A server holding it is exempt from UIPI, and
  blocking would then be a wrong refusal. Read once from our own token (it is 0
  here).

**The refusal lives in one place** — `window_manager.bring_to_foreground`,
which is the single function every input path already funnels through (six call
sites in `input_sim`, plus `hover` and `attach_window(take_control=True)`). It
raises *before* `_took_control`, so the green outline never appears around a
window we are about to refuse. The hand-back path uses `_force_foreground`
directly and is untouched — returning the person's own elevated window to them
must never be blocked.

**Three places now tell the caller, escalating:** `list_windows` adds
`"input_blocked": true` + `"integrity"` (only on affected windows — an ordinary
row is byte-for-byte what it was), `attach_window` appends a loud warning but
**still attaches**, and the first input call raises `InputBlocked`. Attaching is
deliberately not refused: reading an elevated window works fine, and the
reporter was doing exactly that.

**`SendInput`'s return value is now checked** — but the docstring says plainly
that this does *not* catch UIPI (it returns the full count with
`GetLastError` 0). It catches the different, real case Windows does report: the
secure desktop being up for a UAC prompt, or another process holding
`BlockInput`. A partial send is reported as partial rather than as nothing.

**Verification — 21 new assertions, all green:**

| | |
|---|---|
| `tests\probe_integrity.py` | the measurement above; 16 read, 0 unreadable |
| `tests\probe_uipi_refusal.py` | **16/16.** Both directions: all 6 input entry points refuse the elevated window and the foreground is asserted *unchanged* after; and **no window at or below our level is refused**, checked against all 21, not one example |
| `tests\probe_typing_lands.py` | **5/5.** Types into an EDIT control it creates itself and **reads the string back** — ASCII, Thai, backspace, Ctrl+A. This is the regression that mattered: the `SendInput` check sits on the path of every keystroke in the project |

Tool-level, against the real elevated `mmc.exe`: `type_text` and `click` raise;
`run_steps` returns `{"ok": false, "stopped_at_step": 1}` where the issue
recorded `{"ok": true, "performed": 5, "of": 5}`; `screenshot` still works.

**Regression run:** `test_input_guard.py` **88/88**, `probe_mouse_lock.py`
**6/6**, `probe_overlay_activation.py` **19/19**.

**`SPEC.md` and `HOWTOUSE.md` changed in the same commit** — a new *Windows
that cannot be driven* section in the spec, and a fourth entry in HOWTOUSE's
"rules that explain most surprises" plus two anti-patterns, including the one
this issue is a case study in: concluding *the person must be typing somewhere
else* when input has no effect.

## Waiting on the user

- **`diag_hover.py` is 17/1, not the 18/18 this file previously claimed.** The
  failing assertion is *"its text is read, not guessed at"* — the tooltip
  appears at the right rect with the right class, but its UIA `Name` reads
  `None`. **Confirmed pre-existing and unrelated**: it fails identically on
  `master` with this work stashed. Not investigated; it is its own bug.
- **`run_steps` reports `performed` as steps *attempted*, not succeeded**, so a
  script stopped by its first step says `performed: 1` with that step's
  `ok: false`. It disagrees with the single-action refusal, which reports
  `performed: false` for the same event. It was changed and then **deliberately
  reverted**: three assertions in `smoke.py` encode the current meaning, and
  `smoke.py` cannot be run right now (below). Editing a field and its
  unrunnable tests together would be a change nobody had checked. Worth its own
  issue.
- **`smoke.py` still has never run against `master`.** Unchanged from before:
  it launches Notepad, which restores the 56 leaked windows.
- **Notepad's saved session still holds 56 windows.** `LocalState\TabState`
  under `Microsoft.WindowsNotepad_8wekyb3d8bbwe` still has **56 files (5 KB)**.
  Emptying it is irreversible; both dumps of the person's note are on the
  Desktop and verified byte-identical, but nothing will be deleted without a
  decision.
- **Nine more scripts still leak Notepad windows the same way** —
  `diag_typing.py`, `diag_stability.py`, `diag_keyboard_block.py`,
  `diag_overlay_paint.py`, `diag_attach_cost.py`, `probe_popup_detect.py`,
  `probe_notepad_text.py` and the three `spike_background*.py`. `smoke.py`'s
  `notepad_hwnds()` / `wait_for_new_notepad()` / `close_notepad()` are the fix,
  ready to be reused.
- **Three incident scripts** — `rescue_notepad_text.py`,
  `cleanup_leaked_notepads.py`, `probe_notepad_text.py`. Keep or delete?
- **`F:\knowledge` commit decision**, still unanswered.
  `development/windows-background-capture-and-input.md` is written and
  cross-linked but uncommitted, because that repo's `README.md` diff also
  carries ~13 lines of unrelated in-flight work. Options put to the user: (1)
  add `node_modules/` to `.gitignore` and commit everything (my
  recommendation), (2) commit only the two `development/*.md` files, (3) leave
  it. **A sixth finding now belongs in that doc**: UIPI drops input with no
  error signal at all, and the target's integrity level is readable from a
  lower one via `PROCESS_QUERY_LIMITED_INFORMATION` — which is what makes a
  preflight possible instead of a guess.
