# Using the pywinauto UIA backend

## Select and inspect

Call `list_windows`, then `attach_window(hwnd)`. Attaching is passive.
`take_control=True` is unsupported. `capture_screen` returns the full client
image plus a UIA summary; `get_elements` returns detailed JSON. Every element
has `name`, `automation_id`, `control_type`, `enabled`, `rect`, `runtime_id`
and `actions`. Rectangles and coordinates are client-relative physical pixels.

Prefer a unique automation ID; exact case-insensitive names are considered
before substring names. An ambiguous selector errors rather than choosing the
first match. Names and IDs may be combined. IDs are app-provided, not globally
unique; selection is scoped to the attached window.

## Act and verify

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
With no logical target it only works if exactly one editable control matches.
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

`wait_for(name)` waits for an exposed element. `wait_stable` polls pixels until
a quiet interval; use a region to exclude a blinking caret or animation.
Waiting does not count as viewing pixels: capture again before coordinate use.
`snapshot` and `diff_since_snapshot` report a bounding box around all changed
pixels, which can include untouched space between changes. Its center is not
necessarily an actionable point.

## Script short, verified sequences

```python
run_steps([
    {"do": "type", "text": "Alice", "automation_id": "nameField", "replace": True},
    {"do": "click_element", "automation_id": "saveButton"},
    {"do": "wait_stable", "timeout": 3},
    {"do": "check", "expect": "changed", "region": [0, 0, 400, 200]},
    {"do": "capture"}
])
```

Supported kinds: `click`, `click_element`, `type`, `set_value`, `scroll`, `wait`,
`wait_stable`, `capture`, `check`. Unsupported gesture/key steps reject the
entire batch before any mutation. Syntax/shape validation occurs first;
runtime errors stop by default. `stop_on_error=False` records and continues.
The `performed` compatibility field counts attempted steps, including failures.

Limits: 40 steps and 60 seconds of requested waits. A coordinate click before
other mutations is guarded against the caller's prior image; after an action mutates the
UI, later predicted coordinates are not guarded. Prefer selectors and insert
`check`/`capture` checkpoints. Check compares against script start or the latest
capture/check checkpoint, not simply the immediately previous action.

`history` lists outcomes and per-step frames; `replay_frame` returns downscaled
evidence. Journals live under `%TEMP%\winauto-mcp` and retain five sessions.
`highlight` explicitly shows debug rectangles; `release_control` hides them.
`heap_snapshot`/`heap_diff` are optional .NET diagnostics requiring
`dotnet-gcdump`; a heap collection pauses the target for GC and must be assessed
separately from UI control.
