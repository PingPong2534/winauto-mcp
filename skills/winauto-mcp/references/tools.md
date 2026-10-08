# Tool reference

This catalog covers all 34 tools in the UIA backend. Names are unqualified MCP
tool names; resolve the client's namespace. Signatures show defaults, not
executable client functions. Omitted selectors default to `None`.
Use current app observations for handles, IDs and coordinates.

## Attachment

### `list_windows`

`list_windows()` returns a JSON list of visible, non-minimized, titled top-level
windows: `hwnd`, `title`, `process`. Blocked targets additionally include
`input_blocked=true` and integrity information. It does not return rectangles.
Use this to choose/recover the intended target, not the foreground app by assumption.

### `attach_window`

`attach_window(hwnd, take_control=False, label="")` selects one HWND per server
process. It shows a green client outline without raising the app. The optional
single-line Unicode label is at most 120 characters, excludes CR/LF/NUL, sits
outside the frame and is included in the journal session name. An empty label
clears it on attach. Long display text is ellipsized; the label hides if no
exterior screen space is available. `take_control=True` is refused.
Reattaching resets logical targeting, seen regions, pixel snapshot, heap labels
and the journal session. It also clears red highlights.

### `detach_window`

`detach_window()` clears the attachment and logical text target, seen regions,
pixel snapshot and in-memory heap labels, and hides decorations. Use it when
ending an app session; inspect/list/attach again before further app operations.

## Inspection and capture

### `get_elements`

`get_elements()` returns JSON for interactive controls inside the attached HWND:
`name`, `automation_id`, `control_type`, `enabled`, client `rect`, `runtime_id`,
`actions`, `control_steps` and category. Inspection is capped at 150 results,
depth 15 and 3,000 nodes. Use it for unique selectors and capabilities, not to
infer that unlisted controls are absent. `actions` lists exposed patterns;
disabled/read-only status may still prohibit mutation. Strict action resolution
refuses ambiguity or an incomplete search instead of picking a partial match.

### `read_element`

`read_element(name=None, automation_id=None)` returns the selected control's
description plus available `value`, `read_only`, `toggle_state`, `selected`,
`range_value`. Password text is omitted. Use a unique ID/name to verify state
before toggles or after asynchronous app updates; missing pattern properties
are omitted rather than invented. Names match case-insensitive exact before
substring; automation IDs match exactly. Both selectors may be combined.

### `capture_screen`

`capture_screen()` returns text with a UIA summary and `elements_json`, plus a
fresh full-client PNG. Use for initial visual context with controls. A canvas
may have pixels but no actionable UIA entries. It marks the full client as seen.

### `screenshot`

`screenshot()` returns a fresh full-client PNG without walking the UIA tree.
Use for visual refresh when selectors are already known. It marks the full
client as seen. It is cheaper than `capture_screen` for pixels alone.

### `capture_region`

`capture_region(x1,y1,x2,y2)` returns a JSON header and fresh cropped PNG. The
client rectangle is clipped to the current image; an empty intersection errors.
The header provides `region`, `crop_size`, `coordinate_offset`. Only that region
counts as seen. Crop origin `(0,0)` maps to the returned offset, not client origin.
Prefer a known control/status rectangle when only one result matters.

All screenshot tools try PrintWindow without activation. A screen fallback is
allowed only when the target is unobscured; covered/minimized/unavailable pixels
can fail. UIA operations can still succeed without screenshots. Overlays and
labels are excluded from app captures; internal journal frames may cover more
than the returned crop. Never infer coordinates from client-resized images.

## Semantic actions

### `click_element`

`click_element(name=None, button="left", double=False, automation_id=None)`
activates one uniquely selected, enabled control. Returns JSON with `action`,
`element` and `backend`. Prefer this over coordinates. Buttons use InvokePattern,
checkboxes TogglePattern, items/radios/tabs SelectionItemPattern, expandable
controls ExpandCollapsePattern. Read state before toggling/expanding; an action
may reverse the current state. Edit/Document with ValuePattern select a logical
text target without changing the caret. Right/double clicks are refused.

### `click`

`click(x,y,button="left",double=False,modifiers=None,force=False,keep_cursor=False)`
resolves a containing UIA control in the attached tree and activates it; it
does not send a physical click. Coordinates are client pixels. A stale-image
refusal can return a reason and fresh image normally through MCP; inspect the
response before assuming activation. `force=True` only skips that comparison.
Right/double/modifier clicks are refused; `keep_cursor` has no effect.

### `type_text`

`type_text(text,name=None,automation_id=None,replace=False)` writes literal
Unicode through ValuePattern and verifies readback. Returns action
`appended`/`replaced`, input character count, automation ID and `verified`.
Default appends to the end of the entire value; `replace=True` sets the whole
value. It does not send Enter/Tab or shortcut syntax. Explicit selectors are
preferred. Without one, it uses a logically selected control or requires exactly
one Edit/Document/ComboBox candidate. Disabled/read-only fields refuse writes.
A readback error can occur after a write; inspect state before repeating it.

### `set_value`

`set_value(value,name=None,automation_id=None)` uses RangeValuePattern for
numeric controls such as sliders. Send a finite value within the app's range;
disabled, read-only or missing-pattern targets are refused. Returns readback
`value`, `verified=true`, backend. Use instead of dragging a slider; an error
after setting can mean the app rounded/changed the value, so read state first.

### `scroll`

`scroll(x,y,clicks,keep_cursor=False)` finds a vertically scrollable ScrollPattern
at the current client point or an ancestor within the target. Integer clicks
range from -100 to 100: positive up, negative down, zero no-op. Distance is
provider-defined small steps. The return text describes scrolling, not an
exact resulting position; inspect the newly visible content. `keep_cursor` is ignored.

## Sets, scripts and presence

### `control_set`

`control_set(steps,name=None,automation_id=None,expected_control_type=None,delay_ms=120)`
binds a single original control runtime ID. All capabilities and step syntax
are checked before mutation. Use the exact observed `control_type` when checking
type. `delay_ms` must be an integer 0–3000. Allows 1–20 steps with only these shapes:

```python
{"do": "click"}
{"do": "type", "text": "Alice", "replace": True}  # replace defaults to False
{"do": "capture"}  # required exactly once, last
```

No other step fields, coordinates, regions or per-step selectors are accepted.
`control_steps` advertises click/type/capture for writable Edit/Document;
click/capture for read-only text or supported activation controls; capture only
for other/disabled controls. Capture still requires usable bounds/pixels.
Standalone ComboBox typing is not supported as a set type step.
Success returns a report plus one fresh control-only PNG with crop metadata.
Preflight errors run no steps; runtime errors return `ok=false`, completed step
details and no image, with no rollback. Never blindly replay successful writes.
See SKILL.md for the textbox sequence and report inspection.

### `run_steps`

`run_steps(steps,delay_ms=120,stop_on_error=True,stop_if_user_types=False)` batches
up to 40 steps spanning controls. Total requested fixed waits must not exceed
60,000 ms. Syntax is prevalidated; capabilities/uniqueness checked at execution.

| `do` | Fields to use |
| --- | --- |
| `click` | required `x`, `y`; optional `force`; use left/single/no modifiers |
| `click_element` | `name` and/or `automation_id`; use left/single |
| `type` | required `text`; explicit `name`/`automation_id`, optional `replace` |
| `set_value` | required `value`; explicit `name`/`automation_id` |
| `scroll` | required `x`, `y`, `clicks` |
| `wait` | required `ms` (nonnegative integer) |
| `capture` | optional `region=[x1,y1,x2,y2]` |
| `check` | optional `expect="changed"`/`"unchanged"`, `region`, `threshold=10` |

The JSON summary includes `ok`, attempted count `performed`, `of`,
`stopped_at_step`, per-step results and ordered image labels. Inspect it even if
MCP returns normally. Completed mutations persist. `capture` can reuse a prior
frame when fresh capture fails; use standalone capture or `control_set` for
fresh evidence. `check` compares to script start/latest checkpoint, not app
business success. `stop_if_user_types` is ignored. See
[batches-and-diagnostics.md](batches-and-diagnostics.md) for planning/recovery.

### `wait_for`

`wait_for(name,timeout=10.0,interval=0.5)` polls the bounded interactive UIA list
for case-insensitive exact/substring name presence, with times in seconds.
Use positive intervals and bounded timeouts. Returns found name/rect and a
multiple-match note, or raises TimeoutError. It does not check enabled state,
unique action selection, text value or stable pixels. Follow with read/capture.

## Pixel location, cache and diffs

### `locate_in_region`

`locate_in_region(x1,y1,x2,y2,threshold=30)` scans a fresh frame in a small client
region around one item, at most 500,000 pixels. Requires positive area. It finds
pixels differing from the dominant background and returns JSON `bbox`, `center`
in client coordinates, or text saying no content was found. It marks that region
seen. Use threshold 0–255: lower detects faint contrast, higher suppresses weak
differences. Keep the region inside the client and away from neighboring items.
The detected center is not semantic identification; UIA activation must still
resolve to the intended supported control.

### `remember_location`

`remember_location(label,x1,y1,x2,y2)` saves a measured bbox under a short semantic
label such as `save_button`, keyed by process name/client size/normalized label.
Pass bounds from current UIA/pixel inspection, with positive area. Returns
confirmation. Persistence is in `.location_cache.json` beside the server module;
it survives attach/session resets, unlike the in-memory snapshot. It stores
coordinates, not a screenshot or proof of control identity.

### `recall_location`

`recall_location(label,margin=15,threshold=30)` re-scans live pixels around the
cached box. On success returns `cache_hit=true`, refreshed `bbox` and `center`;
use that current center. On miss returns `cache_hit=false` with a reason and
drops a stale entry. Reinspect/locate/remember again. Use a small positive margin
(e.g. 5–10) for dense toolbars to avoid merging adjacent content. Reverification
checks geometric contrast similarity, not a button's name or authorization.

### `snapshot`

`snapshot()` stores one fresh client frame as a reference for later diffs and
returns confirmation, not an image. Take it before the action being compared.
It replaces the previous reference; attachment resets it. It does not itself
mark pixels as viewed for coordinate actions.

### `diff_since_snapshot`

`diff_since_snapshot(threshold=10,region=None)` captures fresh pixels and compares
to the stored snapshot. It returns `changed_bbox` JSON or text `no change detected`;
errors when no snapshot exists. Optional region is `[x1,y1,x2,y2]` in client pixels.
Threshold 0–255 filters grayscale difference; lower catches subtle changes.
A resized client reports the whole current frame as changed. The reference is
not advanced by the diff. Do not click a diff box center or equate visual change
with a successful save; inspect state or the specific changed area.

## Overlays and journal

### `highlight`

`highlight(rects)` accepts client rectangles `[[x1,y1,x2,y2], ...]`, shows manual
red debug boxes and enables decoration tracking. Use inspected rectangles to
explain the target to a person. `highlight([])` hides the decoration, including
the attach frame/label. It has no effect on UIA targeting. The next successful
mutation replaces the manual boxes with its own last-interaction rectangle.
Read/failed actions leave existing boxes unchanged; bounds are not live trackers.

### `release_control`

`release_control()` hides the outline, label and highlights while keeping the
window attached and other state. It does not release an input lock: none exists.
The next successful interaction or reattach shows decorations again.

### `history`

`history(last=20,tool_name=None,failures_only=False)` returns session information
and recent records oldest first. Each record identifies its sequence, tool,
arguments, outcome, duration and available frames. `tool_name` is a substring
filter (e.g. `"control:"`); `failures_only` filters recorded `ok=false`.
Records are capped at 300 in memory and journal data is truncated. Aggregate
batch `ok` can only mean a report was returned; inspect its report/step records.

### `replay_frame`

`replay_frame(seq,which="after")` returns a JSON metadata header and downscaled
JPEG evidence for the selected record. Get `seq` and frame availability from
`history`; `which` must be `"before"` or `"after"`. Missing records/frames error.
Metadata includes scale, time, tool and result. Use to diagnose past behavior;
take a fresh capture/measurement before acting. Session files retain five folders
under `%TEMP%\winauto-mcp`; reattach starts a new active history.

## Managed heap

### `heap_snapshot`

`heap_snapshot(label)` collects the attached process's .NET heap with
`dotnet-gcdump`, forces a blocking GC and stores parsed live-object counts under
that label. Requires a suitable .NET process and the external tool; native apps
have no managed heap. Returns `label`, `pid`, `live_objects`, `heap_bytes`,
`distinct_types`, `dump_file`, `labels_held`. Use distinct labels for baseline and
each round; collection can take time/pause the app. Missing tooling/process or
collection failure is a diagnostic blocker, not a reason for physical input.

### `heap_diff`

`heap_diff(before,after,top=25)` compares two labels held by the current attachment.
Use a positive `top`. Returns count growth sorted by delta in `grew`, total
growth type count and object totals. `approx_bytes_each` is an estimate per
object, not a reliable total byte delta. Missing labels error; reattach clears them.

For a requested leak investigation: warm up the feature, snapshot `baseline`,
repeat the same open/close or allocation/release cycle 5+ times, snapshot each
round (`round1`, `round2`, ...), then compare consecutive rounds and baseline.
Look for repeated growth of relevant app types; a single pair includes runtime
initialization/collection noise and does not establish a leak. The shipped
noise-floor note is a measurement from another process, not a universal threshold.

## Unsupported and compatibility tools

These names remain registered to explain older-client requests. Do not call
them as an attempted workaround for missing semantic patterns.

### `hover`

`hover(x,y,dwell_ms=700,force=False)` always refuses real hover; no cursor moves.
Use exposed UIA state/help text if available; pointer-triggered tooltips may
have no supported equivalent. `force` cannot enable hover.

### `drag`

`drag(x1,y1,x2,y2,button="left",force=False,keep_cursor=False)` refuses gestures.
Use `set_value` for a supported numeric range; arbitrary drag/drop is unavailable.

### `press_key`

`press_key(key)` refuses keyboard injection. Use the app's exposed UIA button or
command for Enter/Escape/Tab-like intent when an equivalent exists.

### `hotkey`

`hotkey(keys)` refuses shortcut injection, including a list such as `['ctrl','s']`.
Use an exposed Save command; an app with only a keyboard shortcut is unsupported.

### `keep_foreground`

`keep_foreground(enabled)` accepts False as a background-mode confirmation;
True errors. It never establishes a foreground hold or restores prior focus.

### `keyboard_status`

`keyboard_status()` returns passive backend/status JSON: blocking, watching and
blocking_enabled are False, lease is zero, human-key counts/times are unavailable.
It does not observe the person's typing or make scripts pause for human input.

### `release_keyboard`

`release_keyboard(enable_blocking=True)` is a compatibility no-op returning
that blocking is off. Either argument value leaves it off; no input hold exists.
