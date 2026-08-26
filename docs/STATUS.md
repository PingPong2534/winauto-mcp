# Status — winauto-mcp

Snapshot of right now. Overwritten, never appended — history lives in `git log`;
finished behaviour lives in [SPEC.md](SPEC.md).

## Doing right now

Idle. Nothing is running. The keyboard work below is **committed and pushed** on
`screen-freshness-and-desktop-sharing`. The only file left dirty is
`tests\probe_notepad_text.py`, a scratch script that proved UI Automation reads
Notepad's text exactly; it was deliberately not committed.

## Just finished

**Telling the person's keystrokes from the tool's, and holding theirs out while
automation types.**

The discriminator is one line. Windows' own `LLKHF_INJECTED` flag only says
"something injected this" — an on-screen keyboard, a remote-desktop session or
another automation tool all set it. What identifies *us* is a signature carried
in each event's `dwExtraInfo`, which Windows delivers untouched.

**A bug found by measuring rather than reading:** `dwExtraInfo` is a
`ULONG_PTR` — a **value** — and was declared `POINTER(c_ulong)` with
`ctypes.pointer(...)` passed in. That compiles, input works, and every event
went out stamped with the address of a temporary: a different number each call.
The spike printed `0x16565c56098`, `0x16565c56218`, `0x16565c55b98` and the
field was useless for the one thing it exists for. Now a value; re-measured
reading back `0x7a170001`.

**What is stored: nothing.** Not the key, not the character, not which keys —
only a count of "a human key event happened" and when the last one was. A
machine-wide hook that kept key codes would be a keylogger, and the only honest
way to promise it is not one is for the data never to exist.

**Five independent ways the keyboard comes back**, because the instruction was
*"ต้องระวัง มันค้างจนพิมอะไรไม่ได้ ต้องวางแผนปล่อยให้ด้วยตอนมีปัญหา"*: the block is a
**lease** that expires by itself within 20 s with no release call and no working
server needed; **three Escapes** in 1.5 s release it *and* latch it off until
`release_keyboard()`; the **mouse is never blocked**; Windows discards a hook
that is too slow and every hook of a process that exits; Ctrl+Alt+Del is beneath
any hook by OS design. Nothing is installed until the first input is sent.

New tools: `keyboard_status()` and `release_keyboard(enable_blocking)`.
`run_steps` gained `stop_if_user_types` (default true) and holds the keyboard
across the whole script rather than per step. `release_control()` and
`detach_window()` hand it back.

**Verified in three layers, none of which ever blocked the real keyboard:**

| | |
|---|---|
| `tests\test_input_guard.py` | **56 checks** against synthetic events with an injected clock. Covers every release route *and* the cases where the block must stay on: three slow Escapes must not release it, an injected Escape must not (only the person can), a key already held when the block began must be let back up so it does not stick down. |
| `tests\diag_keyboard_block.py` | **14 checks** through a real `SetWindowsHookEx` against a real Notepad. The document read back `'YZ'` → `'YZX'` → `'YZXW'`: swallowed while held, arrived once the lease expired on its own, arrived again after three Escapes cut a 20-second lease at **0.58 s**. |
| `tests\smoke.py` | **106 checks, all passed** (was 88). The wiring: no hook exists until the first input; the keyboard is held **during** an action — sampled from another thread, 30/40 samples blocked — and released after; with blocking off, 0/40 samples and the characters still reach the app. Also the interruption path, driven by handing a synthetic human event to the same `decide()` the hook calls, from a thread, mid-script: the script stopped at step 1 of 3 with `"user_interrupted": 1`, `stopped_because` naming the person, and `stop_if_user_types=false` running it anyway. |

The real-hook test avoids locking the keyboard by relabelling which events count
as the person's: a character it types itself is treated as human, and anything
typed on the real keyboard falls into a class that always passes. Verifying a
keyboard lock by locking the keyboard is the one experiment that can leave
nobody able to type the fix.

**Notepad is read back through UI Automation, not by diffing pixels** — its
caret blinks, so "no character arrived" would still show changed pixels.

**Separately: a leak found while setting the above up.** `smoke.py` launched
Calculator with `subprocess.Popen(["calc.exe"])` and killed that handle — but
`calc.exe` is a stub that hands off to a packaged `CalculatorApp.exe` with a
different PID and exits, the same trap already documented for `notepad.exe` in
this repo. Every run leaked two Calculator windows; **27 had accumulated**, and
one of them held the foreground so firmly that `bring_to_foreground` could not
displace it across three attempts, which is what blocked the keyboard testing in
the first place. smoke.py now records the Calculator PIDs that existed before it
launched one and closes only the new ones, never a Calculator the person already
had open. Verified: 0 leaked across a full run, where the count used to rise
by 2.

## Waiting on the user

- **Merge this branch?** `screen-freshness-and-desktop-sharing` is two commits
  ahead of `master` and pushed, but no PR is open — open one, or merge to
  `master` directly? `master` has never seen any of the freshness,
  desktop-sharing, batching, region-capture or keyboard work.
- **`F:\knowledge` commit decision**, still unanswered.
  `development/windows-background-capture-and-input.md` is written and
  cross-linked but uncommitted, because that repo's `README.md` diff also
  carries ~13 lines of unrelated in-flight work and index entries pointing at
  still-untracked files. The options put to the user: (1) add `node_modules/`
  to `.gitignore` and commit everything (my recommendation), (2) commit only
  the two `development/*.md` files, (3) leave it. The keystroke-attribution
  finding above **has now been written into that doc** — the `ULONG_PTR` trap
  with the observed junk addresses, the lease/panic-chord shape, and how to
  test a keyboard lock without locking the keyboard — so the doc is a further
  reason to resolve this, not a reason to wait.
- **PR #1 "update doc of codex"** (branch `update_doc`, +39/−0) is open and
  unreviewed — review the diff, or merge as-is?
- Whether the diagnostics stay in the repo — `tests\diag_stability.py`,
  `tests\diag_typing.py`, `tests\diag_overlay_paint.py`,
  `tests\diag_attach_cost.py`, `tests\diag_keyboard_block.py` and the three
  `tests\spike_background*.py` scripts, plus `tests\spike_input_attribution.py`.
  They print measurements and mostly assert nothing. Each earned its keep by
  overturning a wrong assumption, and `docs/SPEC.md` and the README cite them as
  the reproducible source of the measured numbers.
