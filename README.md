# winauto-mcp

A Windows MCP server for inspecting and controlling an attached desktop app
with **pywinauto's UIA backend**. Actions use UI Automation patterns rather
than moving the shared cursor, injecting keys, or taking foreground focus.

## Install and run

Requires Windows and Python 3.10+ (verified with Python 3.12.10).

```powershell
cd F:\tools\winauto-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

The server uses MCP stdio. A client launches it with the absolute path to the
venv's `python.exe` and `server.py`; see [SETUP.md](SETUP.md). The backend is
`pywinauto>=0.6.9,<0.7`, with MCP SDK 2.x.

## Background control

1. `list_windows()` and `attach_window(hwnd)` select the target and show a green
   client-area outline without raising the target or taking focus. The outline
   passes mouse input through; reattaching clears previous debug highlights.
2. `get_elements()` lists names, automation IDs, client rectangles and supported actions.
3. Prefer `click_element(automation_id="...")`, `type_text(..., automation_id="...", replace=True)` and `set_value(...)`.
4. `read_element(...)` reads text, toggle/selection state and numeric values to verify results.

```python
# MCP calls shown as Python-style pseudocode; these are not a client library.
attach_window(hwnd, label="Feature: Departments - Save / Restore")
get_elements()
type_text("สวัสดี 😀", automation_id="customerName", replace=True)
read_element(automation_id="customerName")
click_element(automation_id="saveButton")
```

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
own popup or focus behavior; the server does not call focus/input APIs.

For background mutations, the server temporarily adds WS_EX_NOACTIVATE to the
target and restores that bit afterwards, including on failure. This suppresses
activation by standard Win32 UIA providers. A target already in the foreground
is left as it is. No focus-restoration loop runs; it would override a person's
window switch. Custom providers and newly opened popups may still activate.

`attach_window(hwnd, label="...")` displays a Unicode feature label at the top
of the green outline. Omit it or pass an empty string for no label. Labels are
single-line, at most 120 characters; long text is ellipsized to fit the window.
The label is visual only and is also included in the journal session name.

## Compatibility and limits

- `hover`, `drag`, `press_key`, `hotkey`, right/double/modifier clicks,
  `attach_window(take_control=True)` and `keep_foreground(True)` return an
  explicit unsupported-action error. There is no physical input fallback.
- `force=True` only bypasses the coordinate stale-image guard. It cannot enable
  unsupported input. `keep_cursor` and `stop_if_user_types` are accepted for
  compatibility and have no effect.
- `keyboard_status` reports blocking/watching off; `release_keyboard` is a no-op.
  No global keyboard/mouse hooks are installed. `release_control` hides the
  outline and debug highlights; the window stays attached. Reattach to show it again.
- Canvas controls without UIA action patterns cannot be controlled. Screenshots
  and pixel inspection remain available where the app supports capture.
- Higher-integrity writes are refused before action. Reads depend on the app's
  provider and Windows permissions. A minimized or non-responsive app may not
  provide usable UIA elements or screenshots.
- Screenshot capture uses PrintWindow first. If it fails and the target is
  covered, capture refuses to return another app's pixels; it never raises the
  target. UIA reads/actions can still be used independently of capture.

## Other tools

The 34 tools also include cropped screenshots, pixel location/cache helpers,
explicit redraw waits, snapshots/diffs, scripted steps, journal replay,
debug highlights and .NET heap snapshots. See [HOWTOUSE.md](HOWTOUSE.md) and
[docs/SPEC.md](docs/SPEC.md) for contracts. Heap tools need `dotnet-gcdump` and
force a blocking GC; they are not part of UIA control.

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

## Agent usage notes for ZiiDMS desktop testing


When using this MCP from Codex/LLM sessions for ZiiDMS desktop UI testing, follow this sequence:

1. Verify the MCP is actually exposed in the active session before falling back to ad-hoc Python calls. Search or inspect available tools for a namespace like `mcp__winauto`. A local clone of this repo is not enough by itself; the client session must be restarted or configured so the MCP server is registered.
2. Start with `list_windows`. If the target app is missing, launch the app first, wait for the top-level window, then run `list_windows` again. Do not infer failure from `Get-Process.MainWindowHandle = 0` alone; a visible attachable window may still appear in `list_windows`.
3. Attach by HWND with `attach_window`, then immediately call `capture_screen`. Use the full returned image and UIA summary as the test baseline. Do not rely on cropped screenshots from a chat transcript to decide whether buttons are missing.
4. Prefer `click_element(name)` for named buttons such as `Save`, `Delete Selected`, `Restore Selected`, `New Department`, and `Include Deleted`. For grid rows, use a unique UIA selector when available. Coordinates from a full client-area image or `locate_in_region` only work when the point resolves to a supported UIA control. Custom-drawn cells without UIA patterns are unsupported; do not fall back to physical input or eyeball resized crops.
5. For ZiiDMS launched through DmsEnv, the normal launcher may start through a hidden wrapper. If `list_windows` does not show `ZiiDMSNextGen Local`, cleanly stop the prior client PID and relaunch through the approved launcher path. If a temporary visible-wrapper workaround is used, restore the launcher file immediately after launch.
6. Use `capture_screen` or `get_elements` after each action to verify command enabled/disabled state from UIA, especially for `Save`, `Delete Selected`, and `Restore Selected`. For visual-only state such as selected rows or deleted-row styling, use the full screenshot.
7. If a click unexpectedly opens a discard/confirmation prompt, record that as behavior evidence before dismissing it. In ZiiDMS Departments testing, read-only/protected fields should not mark the detail dirty merely because typing was attempted.

For the local ZiiDMS NextGen DMSEnv Departments screen, the common launch target is:

```powershell
& 'C:\Users\Ping\.dmsenv\repos\eziidms-nextgen-dmsenv-a59a578e\run-client.ps1' -BypassLogin -ApiServer 'https://localhost:7160' -OrganizationId 7000 -DealershipId 7001 -Screen DepartmentsView
```

After launch, attach to the `ZiiDMSNextGen Local` window from `list_windows` and capture the full client area before testing.
