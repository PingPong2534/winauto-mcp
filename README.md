# winauto-mcp

A Windows MCP server for inspecting and controlling an attached desktop app
with **pywinauto's UIA backend**. Actions use UI Automation patterns rather
than moving the shared cursor, injecting keys, or taking foreground focus.

## Install and run

Requires Windows and Python 3.10+ (verified with Python 3.12.10).

```powershell
cd F:\tools\winauto-mcp
.\install.ps1 -Client codex
# Or: .\install.ps1 -Client claude-code / -Client both
```

Installation prepares Python, installs the skill, registers/enables a native
plugin's MCP connection and installs declared required plugins. See
[SETUP.md](SETUP.md) and [bundle installation](docs/skill-installation.md).
The server uses MCP stdio with absolute paths to the venv's `python.exe` and
`server.py`. The backend is
`pywinauto>=0.6.9,<0.7`, with MCP SDK 2.x.

For agents using this MCP, the repository includes the
[WinAuto MCP skill](skills/winauto-mcp/SKILL.md), covering all 35 tools with
UIA targeting, efficient control sets, capture/cache/diff, journal and heap
diagnostics, and verified recovery. See
[skill installation](docs/skill-installation.md) for Codex installation and
Claude Code. An install request always includes runtime, skill and connection.

## Background control

1. `list_windows()` and `attach_window(hwnd)` select the target and show a green
   client-area outline without raising the target or taking focus. The outline
   passes mouse input through; reattaching clears previous debug highlights.
2. `get_elements()` lists names, automation IDs, client rectangles, exposed UIA actions and available `control_steps`.
3. Use `control_set(...)` for one control's click/type/capture sequence; use `click_element(...)`, `type_text(...)` and `set_value(...)` for individual actions.
4. `read_element(...)` reads text, toggle/selection state and numeric values to verify results.

```python
# MCP calls shown as Python-style pseudocode; these are not a client library.
attach_window(hwnd, label="Feature: Departments - Save / Restore")
get_elements()
type_text("สวัสดี 😀", automation_id="customerName", replace=True)
read_element(automation_id="customerName")
click_element(automation_id="saveButton")
```

For one enabled, writable textbox, `control_set` binds the control once, performs
its supported steps and returns a JSON report plus one cropped PNG.
Use selectors and the exact `control_type` obtained from `get_elements`.

```python
control_set(automation_id="customerName", expected_control_type="EditControl", steps=[
    {"do": "click"},
    {"do": "type", "text": "สวัสดี 😀", "replace": True},
    {"do": "capture"},
])
```

All steps are validated before mutation; each uses the original control's runtime
ID. The final image includes only the control's visible rectangle and supplies
its client-coordinate offset. Runtime errors report completed steps and return
no image. Preflight validation failures raise a tool error before any action.
Inspect the report's `ok` and `completed_steps` before retrying a runtime failure.
See [HOWTOUSE.md](HOWTOUSE.md#one-control-multiple-steps) for limits.

`click(x,y)` searches the attached window's own UIA tree, including when a
separate window covers it. It activates the supported control enclosing that
point. Selecting an edit only remembers a logical text target; it does not
place the real caret or change Windows focus. `type_text` appends to the end
by default; `replace=True` replaces the whole value. Text and numeric writes
verify their value by reading it back. Duplicate names/IDs are refused.

Checkboxes use TogglePattern, items/tabs use SelectionItemPattern, buttons use
InvokePattern, expandable controls use ExpandCollapsePattern, text uses
ValuePattern, sliders use RangeValuePattern, and scrolling uses ScrollPattern.
The application must expose the relevant pattern. Its provider can have its
own popup or focus behavior; the background tools do not call focus/input APIs.

For background mutations, the server temporarily adds WS_EX_NOACTIVATE to the
target and restores that bit afterwards, including on failure. This suppresses
activation by standard Win32 UIA providers. A target already in the foreground
is left as it is. No focus-restoration loop runs; it would override a person's
window switch. Custom providers and newly opened popups may still activate.

`attach_window(hwnd, label="...")` displays a Unicode feature label outside,
above the green outline. It moves below or beside the frame when needed and
hides if no exterior screen space is available. Omit it or pass an empty string
for no label. Labels are single-line, at most 120 characters; long text is
ellipsized to fit the window.
The label is visual only and is also included in the journal session name.

Successful clicks, text writes, numeric writes and scrolls automatically show a
red rectangle around the last control acted on, including `run_steps` and
`control_set` actions.
Each interaction replaces the previous rectangle and keeps the feature label.
Reads and refused/failed actions leave it unchanged. The rectangle is a snapshot
of the control's bounds at the interaction, not a live UIA tracker after layout changes.

## Compatibility and limits

- `hover`, `drag`, `press_key`, `hotkey`, right/double/modifier clicks,
  `attach_window(take_control=True)` and `keep_foreground(True)` return an
  explicit unsupported-action error. Background tools never switch to real input.
- `force=True` only bypasses the coordinate stale-image guard. It cannot enable
  unsupported input. `keep_cursor` and `stop_if_user_types` are accepted for
  compatibility and have no effect.
- `keyboard_status` reports blocking/watching off; `release_keyboard` is a no-op.
  Background tools install no keyboard/mouse hooks. `release_control` hides the
  outline and debug highlights; the window stays attached. Reattach or perform a
  successful interaction to show it again.
- Canvas controls without UIA action patterns cannot be controlled through UIA. Screenshots
  and pixel inspection remain available where the app supports capture.
- Writes to a known higher-integrity target are refused before action when the
  server has no UIAccess. Unknown levels do not themselves block a write. Reads
  depend on the app's provider and Windows permissions. A minimized or
  non-responsive app may not provide usable UIA elements or screenshots.
- Screenshot capture uses PrintWindow first. If it fails and the target is
  covered, capture refuses to return another app's pixels; it never raises the
  target. UIA reads/actions can still be used independently of capture.

## Temporary foreground control

For focus-dependent suggestion boxes, pointer hover, real scrolling or keys,
use the explicit `foreground_steps` batch. UIA remains the default; the existing
background tools never switch automatically. The batch announces its reason
at orange screen edges 400 ms before blocking human input, keeps focus through
the sequence and final capture, then attempts to restore prior cursor/focus
before releasing input. Default duration is 3000 ms, hard limit 8000 ms with
no renewal. A separate worker watchdog terminates on deadline or server death;
Windows removes the temporary hooks when the worker exits. Physical Esc also
cancels/releases control. Failed locking/activation aborts, with no unprotected fallback.

```python
foreground_steps(reason="Customer suggestions", allow_takeover=True, steps=[
    {"do": "click", "x": 160, "y": 35},  # observed textbox position
    {"do": "type", "text": "Alice", "replace": True},
    {"do": "wait", "ms": 250},
    {"do": "key", "keys": ["down"]},
    {"do": "key", "keys": ["enter"]},
    {"do": "capture", "region": [10, 20, 300, 100]},
])
```

Inspect `ok` and confirmed completed steps before retrying. Physical text uses
the actual caret/selection, not the logical UIA target. Capture is fresh visible
client pixels before focus release. A blocked/hung worker may leave focus or
cursor changed even though its input gate is released. The gate uses dedicated
low-level keyboard/mouse hooks, avoiding this host's BlockInput permission
failure. Activation uses SetForegroundWindow/native UIA SetFocus with verification;
installation never elevates automatically.
See [temporary-control usage](skills/winauto-mcp/references/temporary-control.md)
and [research/design](docs/temporary-control.md).

## Other tools

The 35 tools also include cropped screenshots, pixel location/cache helpers,
element-presence waits, snapshots/diffs, scripted steps, journal replay,
debug highlights and .NET heap snapshots. See [HOWTOUSE.md](HOWTOUSE.md) and
[docs/SPEC.md](docs/SPEC.md) for contracts. Heap tools need `dotnet-gcdump` and
force a blocking GC; they are not part of UIA control.

`run_steps` is a multi-control script with syntax prevalidation; control-specific
capabilities are checked as each action runs. Its capture step may reuse the
previous script frame if a new capture fails. Use `control_set`, `screenshot`
or `capture_region` when a fresh image is required. Verify each action through
control readback, an exposed element or a fresh screenshot.

## Verification

```powershell
.\.venv\Scripts\python.exe tests\smoke.py
.\.venv\Scripts\python.exe -m pip check
```

The current suite owns its native controls and covering window, traps real
input/focus calls, verifies actual control handlers and readback, initializes
COM on both MCP workers and a host STA thread, and connects to the real stdio
server from another working directory. It does not launch Notepad or edit a
user application. Test journals use isolated temporary folders.

Historical SendInput probes and the retired input guard are in
[tests/legacy](tests/legacy/README.md), excluded from current discovery.
See [docs/STATUS.md](docs/STATUS.md) for the verified migration scope.

## App-specific testing

Load [docs/ziidms-testing.md](docs/ziidms-testing.md) only when testing ZiiDMS
Departments through DmsEnv. Its launcher path and app behavior are environment
notes, separate from the server's verified UIA contract.
