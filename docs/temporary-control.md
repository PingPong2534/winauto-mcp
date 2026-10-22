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
3. Refuse any held key/button with unknown origin. Install short-lived WH_KEYBOARD_LL and
   WH_MOUSE_LL hooks on a dedicated message-pump thread. Suppress human/foreign
   injected input; pass only this worker's tagged events. Physical Escape
   cancels and releases the gate without restoring focus/cursor over human action.
4. Probe responsiveness and request native SetForegroundWindow once. Do not use
   an unbounded UIA SetFocus fallback. Verify actual foreground, target PID/liveness/integrity,
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
can be denied. Probe the target's responsiveness with WM_NULL (200 ms), request
native foreground once, and acknowledge with another bounded WM_NULL. Denial or
failure aborts before input. Do not call provider-dependent UIA SetFocus while
the gate is held: it has no bounded completion contract. Native focus/cursor
calls use ctypes WinDLL, allowing the Python hook thread to keep servicing input.
Restoration probes the previous window for 100 ms and skips unresponsive windows;
it never retries an activation request after lost focus. See Microsoft's
[activation timing explanation](https://devblogs.microsoft.com/oldnewthing/20161118-00/?p=94745/).
Lost-focus diagnostics identify HWND/PID/class without window titles. SendInput remains subject
to UIPI and an accepted event count does not prove app success.

## Reports and practical limits

Reports persist each confirmed step. Inspect ok/completed_steps/stopped_at_step,
steps/phase/error and restoration fields. Actions are not rolled back. A worker
exit makes the in-flight step outcome unknown and can skip focus/cursor restore.
Read current state before retrying. Physical type uses the actual caret;
replace is Ctrl+A. There is no semantic readback inside a batch. Clear logical
UIA target/seen-image cache after returning. Off-client popup pixels require
separate attachment. Drag is supported as a foreground step or explicitly opted-in
`drag` call: straight client-pixel path, left/right button, duration 100..1500 ms.
The worker prechecks sampled path ownership, then rechecks during motion. No
cross-app drop, held key/button between steps/calls or renewable lease is exposed.
Tracked button-ups precede cursor/focus restoration on normal/error cleanup;
Escape/lost-focus cleanup may emit only those ups without ordinary input guards.
The watchdog attempts the same release before exit. Forced kill or native input
failure may leave release unconfirmed; inspect `mouse_buttons_released` (null
unless a durable cleanup checkpoint already acknowledges those ups), not process termination alone.
`worker_exit_phase` records the last checkpoint and `release_confirmed` preserves
acknowledged release even if post-release COM cleanup subsequently stalls.
Diagnostic checkpoint write failures cannot prevent key/button/gate release.

The hook pump waits for native messages instead of sleeping after every pump;
each tagged event wakes it immediately. A 50 ms idle timeout preserves health
checks and bounded shutdown. A wait failure removes both hooks and refuses more
input. The hard watchdog still covers a stuck native call.

Action/keyboard checks stop 500 ms before the original worker deadline to reserve
cleanup time. Unicode text uses chunks of at most four Python characters, keeping
surrogates and down/up pairs together, and always releases its owned possible
downs on exit. Cleanup refuses to race an in-flight SendInput call; such release
remains unconfirmed instead of claiming successful recovery. Never release an
unknown async VK (including 231) or replay a partial append.

`native_phase` and `native_elapsed_ms` identify native probe/request/acknowledgement
and typing checkpoints. `failure_phase`/`failure_native_phase` retain the original
failure even after restoration changes the current phase. Cooperative expiry
reports `timed_out=true`, `cooperative_timeout=true`; `utf16_units_dispatched` is
injection progress, not application readback. A restoring-stage hard exit retains
durable owned key/button acknowledgements but does not confirm gate/focus release.

Click/drag steps accept distinct ctrl/shift/alt modifiers held through button-up.
Key steps use scan-code injection and a separate down interval (`hold_ms=80`,
20..500 ms). Track possible key-downs before SendInput so partial sends, Escape
and watchdog cleanup can release them. Inspect `keys_released` as well as mouse
release; neither delivery nor cleanup proves the application's command response.
See Microsoft's [KEYBDINPUT contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput).

Capture imports load before the gate. Screen read/PNG work runs in a daemon
thread with a 1 s bound and 300 ms reserved from the original lease for cleanup.
The thread cannot inject input or save a late image. `capture_phase` records
occlusion_check/dwm_flush/screen_grab/png_encode/verify_and_save/complete. Failed
capture preserves completed input and returns no image. DWM-cloaked windows are
excluded from occlusion checks; visible foreign windows remain blockers and are
identified by HWND/PID/class without titles. Check coverage/geometry again after
the screen read. Use the opt-in `tests/probe_app_interop.py` for isolated Unity
UI Toolkit, DX12 Game View and Edge WebGL verification. The Unity fixture uses
a release player without profiler/debugger listeners; it requires no firewall
rule. Browser HTTP is bound to loopback only. Original application content is
not part of these synthetic fixtures.

Mouse state uses explicit down/motion/up events per Microsoft's
[MOUSEINPUT contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput).
An accepted [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
count proves injection, not application drop acceptance; verify the app outcome.

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
