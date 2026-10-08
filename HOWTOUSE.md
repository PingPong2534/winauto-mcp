# Using the pywinauto UIA backend

## Select and inspect

Call `list_windows`, then `attach_window(hwnd)`. Attaching shows a green
client-area outline without raising the target or taking focus. The outline
passes mouse input through and follows the target; reattaching clears old highlights.
Use `attach_window(hwnd, label="Feature: Save customer")` to name the current
test outside the outline. The label sits above the frame, or below/beside it
when space is limited; it hides if no exterior screen space is available.
Labels support Thai, are single-line, and accept at most
120 characters. Omit the label or pass `""` to clear it on the next attach.
`take_control=True` is unsupported. `capture_screen` returns the full client
image plus a UIA summary; `get_elements` returns detailed JSON. Every element
has `name`, `automation_id`, `control_type`, `enabled`, `rect`, `runtime_id`
and `actions`, plus `control_steps` for a control-scoped set. Rectangles and
coordinates are client-relative physical pixels.

Prefer a unique automation ID; exact case-insensitive names are considered
before substring names. An ambiguous selector errors rather than choosing the
first match. Names and IDs may be combined. IDs are app-provided, not globally
unique; selection is scoped to the attached window.

`list_windows` lists visible, non-minimized, titled top-level windows as
hwnd/title/process, with integrity information when writes are blocked.
It does not return rectangles. Element rectangles, click coordinates and
screenshot regions use attached-client physical pixels, not desktop coordinates.
Every attach starts a new journal session and clears the logical text target,
seen regions, pixel snapshot and in-memory heap labels. release_control only
hides the decoration and keeps the attachment and other state.

## Act and verify

Background UIA writes temporarily mark the target WS_EX_NOACTIVATE, then restore
the added bit even on errors. The server never forces the previous window back
to the foreground. App-created popups/custom providers can still change focus.

The last successful click/type/set_value/scroll is outlined in red automatically,
including script steps. Failed/refused actions and reads do not move the box.
Each successful interaction replaces manual debug boxes but keeps the attach
label. The box records bounds at the action; layout changes may move the control.

```python
# Illustrative MCP call sequence, using IDs obtained from get_elements.
type_text("Alice", automation_id="nameField", replace=True)
read_element(automation_id="nameField")
click_element(automation_id="enabledCheckBox")
read_element(automation_id="enabledCheckBox")
set_value(37, automation_id="volumeSlider")
read_element(automation_id="volumeSlider")
click_element(name="Save")
```

Text is literal Unicode, including Thai, emoji and characters that would have
been shortcut syntax in a keyboard sender. The default appends at the end of
the entire value, independent of the user's caret. `replace=True` sets the
whole value. Selecting an edit by `click_element`/`click` remembers that field
without focusing it; a later unqualified `type_text` uses that logical target.
With no logical target it only works if exactly one Edit, Document or ComboBox
matches; a second read-only/disabled text control still makes it ambiguous.
Use a selector for each field in a multi-field form. Read-only/disabled controls
refuse writes; text and numeric writes verify readback. `read_element` omits
password text.

A coordinate click resolves a UIA control in the target's own tree, so a
covering app is not clicked. Use a fresh full screenshot or `locate_in_region`
for the point. If the area differs from the most recent view covering it,
the stale guard refuses and supplies a new image. Review that image before
retrying. `force=True` bypasses only this image comparison.

`scroll(x,y,clicks)` finds a vertical ScrollPattern on the pointed element or
an ancestor inside the target. Positive is up, negative down, maximum 100
small UIA steps per call. Step distance is provider-defined.

## One control, multiple steps

Use `control_set` when the next actions belong to one control. Obtain its unique
selector, control_type and control_steps from `get_elements` first. For an enabled, writable
textbox, send click, type and capture together:

```python
control_set(
    automation_id="nameField",
    expected_control_type="EditControl",
    steps=[
        {"do": "click"},
        {"do": "type", "text": "ทดสอบข้อมูลลูกค้า", "replace": True},
        {"do": "capture"},
    ],
)
```

All steps use the same runtime ID. The set validates every step and capability
before starting; a disappeared control stops the set instead of redirecting it
to another matching field. Text is verified by readback. No physical click or
Windows focus change is requested. Text defaults to append; use replace=True to
set the complete value. The set allows up to 20 steps, with one final capture;
delay_ms (0–3000, default 120) lets the app repaint between steps.

The supported control_steps depend on the actual UIA control:

| Control state | Available steps |
| --- | --- |
| Enabled Edit/Document with writable ValuePattern | click, type, capture |
| Enabled read-only Edit/Document with ValuePattern | click, capture |
| Enabled control with Invoke/Toggle/SelectionItem/ExpandCollapsePattern | click, capture |
| Disabled control or no supported activation pattern | capture |

Capture still requires usable bounds and pixels. `actions` lists exposed UIA
patterns; it does not mean a disabled/read-only control permits every action.
An editable ComboBox may accept standalone type_text, but control_set currently
offers type only on Edit/Document. set_value/scroll/wait are not control_set steps;
use their individual tools or run_steps. capture is required exactly once at the
end, including for a capture-only set. expected_control_type must match the exact
returned string, such as EditControl, when supplied.

The response contains a JSON report and one PNG cropped to the textbox's current
visible rectangle, including its frame, at original resolution. It supplies
region, crop_size and coordinate_offset; the crop's (0,0) is that offset in the
attached client area. It does not send a full-window screenshot. On runtime
failure it returns a report with completed steps and no image; actions are not
rolled back, so inspect the report before retrying. Preflight failures are MCP
tool errors before any step runs. Runtime failures return a report with ok=false,
completed_steps, stopped_at_step and per-step results; the MCP call itself may
complete normally, so inspect report.ok. Successful mutations can leave the red
highlight visible even if the later capture fails. The PNG contains app pixels,
including the native control border where present, rather than the red/green
decoration or feature label. Covered capture remains
dependent on PrintWindow; there is no stale-image or foreground fallback.

## Unsupported actions

Real hover, drag, key presses, shortcuts, right/double/modifier clicks and
foreground takeover return an error. They never fall back to SendInput.
For Save/Enter/Escape shortcuts, use a UIA button or command exposed by the app;
if it has no semantic equivalent, this backend cannot perform that action.
For a slider use `set_value`, not drag. Canvas-only controls are unsupported.

There is no keyboard hold or user-input monitor. `keyboard_status` always
reports blocking/watching off, and `release_keyboard` cannot enable it.
`keep_cursor` and `stop_if_user_types` are ignored compatibility arguments.
`keep_foreground(False)` confirms background mode; True errors.

The server makes no focus/input calls, but the app's own UIA provider can show
popups or change focus in response to a command. Test the actual target app
before relying on concurrent work with it.

## Screenshots and waiting

`capture_screen` gives full pixels and summary, `screenshot` just the full
image, and `capture_region(x1,y1,x2,y2)` supplies a cropped image and its offset.
Crops and journal images may be resized by the client; their displayed pixel
positions are not client coordinates. `locate_in_region` measures a small area
in source pixels. The location cache rechecks live content before reuse.

PrintWindow can capture a covered target. If it cannot and another visible
window overlaps the target, capture errors instead of showing that window.
Use `get_elements`/`read_element`, or uncover the target manually. The server
never brings the window forward to repair a screenshot.

`wait_for(name)` polls the bounded interactive-element inspection list for an
exact/substring name. It can report multiple matches; it is a presence check,
not an actionable unique selector. It raises TimeoutError if none appears.
Verify completion using read_element, get_elements or a fresh screenshot. There
is no pixel-stability wait tool or script action.
Waiting does not count as viewing pixels: capture again before coordinate use.
`snapshot` and `diff_since_snapshot` report a bounding box around all changed
pixels, which can include untouched space between changes. Its center is not
necessarily an actionable point.

## Script short, verified sequences

```python
run_steps([
    {"do": "type", "text": "Alice", "automation_id": "nameField", "replace": True},
    {"do": "click_element", "automation_id": "saveButton"},
    {"do": "check", "expect": "changed", "region": [0, 0, 400, 200]},
    {"do": "capture"}
])
```

Supported kinds: `click`, `click_element`, `type`, `set_value`, `scroll`, `wait`,
`capture`, `check`. Unsupported gesture/key steps reject the
entire batch before any mutation. Syntax/shape validation occurs first;
control capabilities, selector uniqueness and read-only/integrity constraints
are checked at execution, unlike control_set's capability preflight.
Runtime errors stop by default. `stop_on_error=False` records and continues.
The `performed` compatibility field counts attempted steps, including failures.

Limits: 40 steps and 60 seconds of requested waits. A coordinate click before
other mutations is guarded against the caller's prior image; after an action mutates the
UI, later predicted coordinates are not guarded. The guard compares pixels only
when a prior seen region and current frame are available. Prefer selectors and insert
`check`/`capture` checkpoints. Check compares against script start or the latest
capture/check checkpoint, not simply the immediately previous action.

run_steps returns a JSON report followed by zero or more images in the order
listed in images. Its capture step accepts region=[x1,y1,x2,y2], otherwise it
returns the full client area. If a fresh grab is unavailable, this step can reuse
the previous script frame and still report success; its report does not identify
that fallback. Use control_set, screenshot or capture_region for a fresh capture
that fails when pixels are unavailable. On an action error run_steps may include
a full-window stop image, whereas control_set returns no image on failure.
The 60-second limit covers requested wait steps, not action durations
or delay_ms; delay_ms defaults to 120 and is clamped to 0–3000 milliseconds.

`history` lists outcomes and available frames; `replay_frame` returns downscaled
evidence. run_steps attempts before/after frames for each step. control_set logs
each step as text and keeps an after-frame for successful capture; it does not
keep click/type before/after frames. Aggregate batch records can have ok=true
because the tool returned a report; inspect that report and the script:/control:
step records for failures. Journals live under `%TEMP%\winauto-mcp`, retain five
session folders and keep the latest 300 records in memory. Arguments/results are
truncated in the journal; use the returned report for complete step details.
`highlight` shows red debug rectangles; an empty list hides the overlay.
`release_control` hides the outline and rectangles while keeping the target attached.
Reattach or perform a successful interaction to restore the outline and label.
`heap_snapshot`/`heap_diff` are optional .NET diagnostics requiring
`dotnet-gcdump`; a heap collection pauses the target for GC and must be assessed
separately from UI control.
