# Bounded temporary foreground control

## Backend selection

UIA ValuePattern changes a value without a physical caret/key sequence.
Suggestion lists may depend on those events and close on focus loss.
ScrollPattern is provider-dependent; genuine hover uses the shared pointer.
The background backend's WS_EX_NOACTIVATE guard suppresses activation; it does
not restore focus after every action. The reported app-specific issue has not
yet been reproduced in that app, so its exact trigger remains unverified.

| Mechanism | Role and limits |
| --- | --- |
| pywinauto UIA patterns | Default background semantic operations. Requires usable patterns; ValuePattern is not physical typing. |
| Win32 window messages | Useful for some classic controls, but posting WM_* does not reproduce key state, pointer hover or modern/custom behavior reliably. No generic message fallback is added. |
| SendInput with a short foreground batch | Supplement for suggestions, real hover/wheel/keys and canvas clicks. Requires a visible, bounded human-input gate. |
| Other input wrappers | Share the active desktop for physical input; changing libraries does not remove focus, integrity or desktop restrictions. No additional plugin is needed here. |

References: [pywinauto remote execution](https://pywinauto.readthedocs.io/en/latest/remote_execution.html),
[PostMessage](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-postmessagew),
[SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).

## Input gate and lifecycle

`foreground_steps(steps,reason,timeout_ms=3000,allow_takeover=False)` requires
explicit opt-in. See [tool schema](../skills/winauto-mcp/references/tools.md#foreground_steps)
and [skill workflow](../skills/winauto-mcp/references/temporary-control.md).

1. Validate the whole batch/integrity and launch an isolated worker. A watchdog
   starts before control, exits on deadline (1000..8000 ms) or parent death,
   and remains independent of native action/capture calls.
2. Show orange edges on all monitors, plus reason and Escape reminder on the
   target monitor. Announce for 400 ms before acquiring the input gate.
3. Refuse a held human key/button. Install short-lived WH_KEYBOARD_LL and
   WH_MOUSE_LL hooks on a dedicated message-pump thread. Suppress human/foreign
   injected input; pass only this worker's tagged events. Physical Escape
   cancels and releases the gate without restoring focus/cursor over human action.
4. Request activation with SetForegroundWindow, then native UIA SetFocus if
   necessary. Verify the actual foreground, target PID/liveness/integrity,
   keyboard focus and pointed window ownership before input. No Alt workaround,
   AttachThreadInput, indefinite foreground loop or changed system setting.
5. Keep focus across the short batch and final fresh visible-client capture.
   Stop if foreground escapes. Screen edges are hidden briefly for capture
   and restored in finally. Reject foreign occlusion; no stale image fallback.
6. Attempt prior cursor/focus restoration before removing the gate. Remove
   hooks and close the indicator. Worker death also removes its hooks. The
   parent has a bounded process wait and kills a stuck worker if needed.

Microsoft requires a message loop for
[keyboard hooks](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc)
and [mouse hooks](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelmouseproc).
Slow hooks may be silently removed. Callbacks do no I/O and retain no human
keys/text. The main action thread checks hook-thread liveness/pump heartbeat
before injecting. The gate is an interaction guard, not a secure desktop,
raw-input, touch, pen or game-controller lock.

[BlockInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-blockinput)
was investigated, but this host refused it at medium integrity. It also requires
the blocking thread to own SendInput/unblock calls. The selected hook gate works
at the current normal-user level and supports Escape cancellation, so production
does not use BlockInput or automatically elevate. Ctrl+Alt+Del remains Windows'
secure-desktop action; the tool does not operate there.

[SetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow)
can be denied. Native UIA SetFocus is also provider-dependent; if neither
establishes the attached foreground family, abort. SendInput remains subject
to UIPI and an accepted event count does not prove app success.

## Reports and practical limits

Reports persist each confirmed step. Inspect ok/completed_steps/stopped_at_step,
steps/phase/error and restoration fields. Actions are not rolled back. A worker
exit makes the in-flight step outcome unknown and can skip focus/cursor restore.
Read current state before retrying. Physical type uses the actual caret;
replace is Ctrl+A. There is no semantic readback inside a batch. Clear logical
UIA target/seen-image cache after returning. Off-client popup pixels require
separate attachment. No drag/held key/button or renewable lease is exposed.

Other applications can still change foreground between a guard and dispatch.
Low-level hooks are shared desktop resources and may conflict with another
input filter. Do not claim a security boundary or universal provider/IME support.
Mouse dwExtraInfo was observed truncated to 32 bits on this host; per-worker
tags therefore stay within 31 bits and are matched with the injected flag.

## Verification

Default tests never lock human input. They use mocked lifecycle calls, a real
non-activating native indicator, event-layout/Unicode checks, hook decisions,
and real watchdog processes for timeout/parent death. Run the separate
`tests/probe_foreground.py` only for explicit owned-window live validation.
Report actual text/state/capture/restoration, not event counts alone. The
reported suggestion app still needs its own runtime verification.
