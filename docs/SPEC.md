# pywinauto UIA backend contract

## Scope and execution

One window is attached per server process. MCP tools are serialized by a
reentrant lock because attachment, screenshot history, logical text target and
journal state are shared. Each UIA call initializes/balances COM on its worker
thread; an existing STA host is respected. COM wrappers do not survive a call;
only runtime IDs are remembered for a logically selected field.

Each background mutation temporarily sets WS_EX_NOACTIVATE on the target HWND.
The finally path removes only the bit it added; pre-existing bits and concurrent
style changes are preserved. A foreground target is not modified. This blocks
the reproduced standard Win32 provider activation; it does not constrain new
popups or explicit activation by arbitrary providers. No focus is restored.

UIA queries start at the attached HWND, not at the desktop or foreground
window. Tree traversal is bounded to depth 15 and 3,000 nodes; element-list
output is capped at 150. Names match case-insensitive exact first, substring
second; automation IDs match exactly. Action selection refuses multiple
matches. Action traversal refuses a truncated or unreadable tree rather than assuming uniqueness from a partial search. `get_elements` is an inspection list, not
permission to operate a canvas or unsupported pattern.

## 35 MCP tools

| Group | Tools | Contract |
| --- | --- | --- |
| Attachment | `list_windows`, `attach_window`, `detach_window` | Background target selection with a green client outline; take_control=True refused |
| Inspection | `capture_screen`, `screenshot`, `capture_region`, `get_elements`, `read_element` | Client pixels/tree/state; read_element hides password text |
| UIA actions | `click`, `click_element`, `type_text`, `scroll`, `set_value` | Semantic patterns only; enabled/read-only/integrity checks |
| Waits/scripts | `wait_for`, `wait_stable`, `run_steps`, `control_set` | Explicit waits; prevalidated batches; control_set binds one control and returns only its crop |
| Pixel/cache helpers | `locate_in_region`, `snapshot`, `diff_since_snapshot`, `remember_location`, `recall_location` | Freshness and pixel evidence; cache reverified |
| Debug/evidence | `highlight`, `release_control`, `history`, `replay_frame` | Explicit visual overlay and journal; replay is downscaled |
| Heap | `heap_snapshot`, `heap_diff` | .NET gcdump, optional external tool, blocking GC |
| Unsupported input | `hover`, `drag`, `press_key`, `hotkey` | Error without physical input |
| Compatibility | `keep_foreground`, `keyboard_status`, `release_keyboard` | Foreground True refused; no input watching/blocking |

## Action mapping

| UIA control/pattern | Behavior |
| --- | --- |
| Button/InvokePattern | Invoke exposed command |
| CheckBox/TogglePattern | Toggle state |
| RadioButton/ListItem/TreeItem/TabItem with SelectionItemPattern | Select item |
| ExpandCollapsePattern | Expand or collapse |
| Edit/Document with ValuePattern | Logical text target; no real focus |
| ValuePattern | Set literal text, read back; append-at-end default or replace |
| RangeValuePattern | Set finite in-range numeric value, read back |
| ScrollPattern | Vertical small steps; search pointed element then ancestors |

Coordinate clicks choose the smallest containing interactive UIA control;
Text labels do not eclipse enclosing buttons. No Desktop.from_point is used.
There is no SendInput, click_input, type_keys, set_focus or Win32 foreground
operation in production control paths. UIA provider behavior remains owned by
the target application and is not a universal guarantee of background access.

## Control-scoped steps

`control_set(steps, name=None, automation_id=None, expected_control_type=None,
delay_ms=120)` resolves one unique control inside the attached window. It accepts
up to 20 steps: `click`, `type` (string `text`, optional boolean `replace`),
and one required final `capture`. Unknown fields, per-step selectors/coordinates,
unsupported capabilities, invalid delays and type mismatches reject the set
before mutation. `get_elements` exposes each control's available `control_steps`;
Edit/Document controls with writable ValuePattern support all three steps.
Defaults for text match `type_text`: append, or replace the entire value.

Bind the initial runtime ID and re-resolve it before each step. Never select a
replacement control by name/automation ID if the original disappears. Wrappers
stay within this call's COM session. Recheck enabled/read-only/integrity state
before each mutation; verify text readback. Click on text selects the logical
target without taking focus. Each attempted step is journaled as `control:<do>`.

Final capture uses a fresh attached-window frame and current control bounds,
clipped to the client area. Refuse changed geometry during capture or unavailable
pixels; never return an earlier frame or a full-window image on failure. The
response contains a JSON report and, on success, one original-size PNG of the
control's visible rectangle, including its frame. Return region, crop_size and
coordinate_offset; only this crop counts as seen. Internal journal frames may
cover the full client area. Runtime failures stop immediately and report completed
steps without rollback; callers must inspect the report before retrying mutations.

## Compatibility changes

Text no longer types at the physical caret. Keyboard gestures, hover, drag,
right/double/modifier clicks, foreground holding and attach takeover no longer
execute. `force` bypasses only stale image checking; `keep_cursor` and
`stop_if_user_types` have no effect. `release_keyboard(enable_blocking=True)`
cannot enable input blocking. Deprecated tool names remain registered so old
clients receive a clear error instead of silently injecting input.

The initial coordinate click in a batch uses the caller's latest relevant
view. After a script action mutates the UI, later predicted coordinates are
unguarded. Batch limits are 40 steps/60 seconds of requested waits. The legacy
`performed` count includes attempted failures; inspect each step's `ok`.

## Capture and diagnostics

PrintWindow supplies client-area pixels without activation. If unavailable,
the screen fallback is allowed only when no visible window above overlaps the
target; covered/minimized targets fail explicitly rather than being raised.
Before/after frames are optional evidence: semantic UIA actions may succeed
when pixel capture is unavailable. A stale guard has no image evidence to
compare in that case.

Journal sessions are temporary and retain five folders; history keeps 300
records in memory. Location cache entries are keyed by process name/client
size/label and checked against current pixels. Heap diagnostics and debug
overlays do not implement input control.

Attach shows the green outline and clears prior highlights. The overlay has
WS_EX_NOACTIVATE and WS_EX_TRANSPARENT and does not raise the target.
The optional attach `label` is a single-line Unicode string, at most 120
characters, drawn in a separate non-activating, click-through native window
outside the client outline, with ellipsis when necessary. Prefer above, then
below, then either side within the target monitor's bounds; hide the label
if no exterior space fits. Never move the label inside the frame. Hide both
decoration windows during release, target hiding, detach and screen capture.
An empty label removes the tab; a new attach replaces the prior label and
includes it in the journal session name. It does not select or type into the app.
Successful UIA mutations replace debug boxes with one red box for their resolved
target (the scrollable ancestor for scroll). Edit selection also counts as an
interaction. Bounds are captured before mutation, so controls destroyed by a
successful action can still be indicated. Reads and errors do not change the box.
This applies to individual tools and each run_steps mutation. Cosmetic failures
must not turn a completed UIA mutation into an error. No COM wrappers are retained.
If a provider has no usable in-client bounds, the previous box is left unchanged.
Detach, release_control and highlight([]) hide it; reattach or the next successful
interaction shows it again, preserving the feature label for the attached target.
Screen fallback temporarily hides this decoration and restores it after capture
or refusal; other covering windows still cause a refusal.

## Tests

`tests/smoke.py` discovers current `test_*.py` modules. Native owned windows
exercise real Value/Invoke/Toggle/RangeValue/Scroll patterns under a cover,
refusals, readback, script/journal behavior, worker COM and STA hosting. Real
stdio tests initialize the server from a different directory and verify all
35 tool names. Historical probes in `tests/legacy` are excluded and require
the former backend revision.
