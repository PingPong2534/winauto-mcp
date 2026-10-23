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
[WinAuto MCP skill](skills/winauto-mcp/SKILL.md), covering all 45 tools with
UIA targeting, efficient control sets, capture/cache/diff, journal and heap
diagnostics, and verified recovery. See
[skill installation](docs/skill-installation.md) for Codex installation and
Claude Code. An install request always includes runtime, skill and connection.

## Persistent browser control

For an already authenticated Edge tab, the bundle also includes Microsoft's
official Playwright Extension mode. The installer pins `@playwright/mcp` 0.0.83
and registers a separate `playwright-extension` MCP server (25 upstream tools).
Install the [official extension](https://github.com/microsoft/playwright/blob/main/packages/extension/README.md),
then approve/select the intended tab through its normal connection page. It
reuses the browser's existing session without copying cookies. No approval-bypass
token is configured. See [Extension mode](skills/winauto-mcp/references/browser-extension.md).
The 45-tool WinAuto server and its owned-profile mode below remain separate.

The nine `browser_*` MCP tools use Playwright on a separate worker. Open a visible
installed Edge/Chrome with a dedicated persistent profile, sign in manually once,
and reuse that profile across browser and MCP restarts. Sites may expire sessions.
Personal browser profiles are never attached or copied. No public debugging port,
arbitrary JavaScript, forced actions or automatic publishing is exposed.

Use `browser_open` -> `browser_snapshot` -> `browser_actions`. Actions use refs
from that snapshot, verify form writes and report partial completion. File uploads
use Playwright directly. `browser_close` retains the profile. See the complete
[profile/login/action contract](skills/winauto-mcp/references/browser.md).

`install.ps1` installs Playwright's Python package and driver. The default `msedge`
route uses installed Microsoft Edge; it does not download another browser. Using
`browser="chromium"` requires a separate `python -m playwright install chromium`.
Profiles live outside the checkout under `%LOCALAPPDATA%/winauto-mcp/browser-profiles`.

Run `tests/test_browser_integration.py` for an isolated local login fixture;
`tests/probe_browser_session.py` opens a dedicated TikTok profile for manual login
and read-only persistence/resume verification. Neither probe publishes content.

## Background control

The skill maintains compact current guides per app/site from verified use,
including relevant UI structure, semantic anchors, steps and recovery boundaries.
It checks live state before reuse and invalidates/repairs contradicted recipes.
Guides live under `%LOCALAPPDATA%/winauto-mcp/guides/v1`, outside Git and plugin
caches, with one latest record per target and no history or user/session data.
See [app/site guide learning](skills/winauto-mcp/references/app-guides.md).

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

For unnamed or duplicate webpage controls, use the exact `runtime_id` returned
by current `get_elements()`/`capture_screen()` inspection with `click_element`,
`read_element` or `type_text`. Identity lookup stays inside the attached window
and its depth/node bounds, but does not require an exhaustive name-uniqueness
scan of unrelated deep branches. Missing identities refuse without selecting a
replacement. Reinspect after navigation/replacement; runtime IDs can be reused.
Coordinate clicks and name/automation-ID lookups retain strict traversal guards.

`auto` capture checks the target's DPI render context before using PrintWindow.
If native and physical window dimensions differ, it uses guarded visible pixels
instead of returning a successful bitmap with scaled content and black padding.
That fallback still refuses covered windows. A foreign WinAuto overlay can paint
outline/highlight pixels and is treated as a cover; no cross-instance overlay is
hidden or exempted by class name. For covered targets, `capture_region` with
`capture_mode="auto"` can use PrintWindow only when that path is DPI compatible.
See [issue review and verification](docs/issue-review-20261016.md).

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

- `hover`, `press_key`, `hotkey`, right/double/modifier clicks,
  `attach_window(take_control=True)` and `keep_foreground(True)` return an
  explicit unsupported-action error. Background tools never switch to real input.
- `drag` now supports explicit temporary takeover with `allow_takeover=True` and
  a visible `reason`. Or use a `foreground_steps` drag plus final capture. The
  straight path must stay in the attached window/owned popup; drag duration is
  100..1500 ms. Buttons are released before restoring cursor/focus, including
  failed drags. Defaults and background `run_steps` still refuse gestures. See
  [drag workflow](skills/winauto-mcp/references/temporary-control.md#click-drag).
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

## Conditional hybrid automation

For browser toolbars blocked by a deep webpage tree, inspect with
`get_elements(scope="non_document")` and use that scope on `click_element` or
`read_element`. Document roots/subtrees are excluded; uniqueness and traversal
limits remain enforced within the scope.

For partially black GPU surfaces, `screenshot(capture_mode="screen")` (also
available on `capture_screen`/`capture_region`) uses visible pixels with foreign
occlusion/geometry guards. Default `auto` still prefers PrintWindow. Occlusion
refusals identify covering HWND/PID/class without exposing window titles.

Use `guarded_steps` for several existing controls. The server binds their UIA
identities before mutations, checks `before`/`expect` state, and can compare each
action's region with an observed baseline through Pillow or OpenCV. Semantic-only
success takes no screenshots; visual guards use fresh images and stop before a
changed target. Results distinguish completed actions from unknown outcomes.
No automatic physical-input fallback occurs. Navigation requires a new plan.

See [hybrid planning and examples](skills/winauto-mcp/references/hybrid-automation.md).
Run `.venv\Scripts\python.exe tests\benchmark_hybrid.py` for repeatable local
pixel and owned-window batch measurements. These exclude AI/network round trips.

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
failure. Activation probes responsiveness and uses native SetForegroundWindow
with verification; no unbounded UIA focus call runs under the input gate.
installation never elevates automatically.
See [temporary-control usage](skills/winauto-mcp/references/temporary-control.md)
and [research/design](docs/temporary-control.md).
See the [#21/#23/#27 follow-up](docs/foreground-timeout-fixes-20261022.md) for
event-driven hook dispatch, the 500 ms cleanup reserve and native recovery proof.

## Other tools

Foreground click/drag steps accept `modifiers=["ctrl","shift"]` (distinct subsets
of ctrl/shift/alt), held through button-up. Key steps accept `hold_ms=80`
(20..500 ms) and send scan-code down/up separately. Inspect `keys_released` and
`mouse_buttons_released` after failures. Optional capture work is bounded to 1 s
and reserves 300 ms for cleanup; `capture_phase` identifies the stalled phase.
Completed input persists when capture fails. Unity/Edge-specific response still
requires target-app verification.

Worker failures expose `worker_exit_phase`; `release_confirmed=true` retains
cleanup acknowledgements if the process fails only after release. Lost-focus
diagnostics include HWND/PID/class without titles. Activation waits up to 200 ms
for the target's cross-thread acknowledgement without retrying lost focus.
The opt-in `tests/probe_app_interop.py` tests isolated Edge WebGL and Unity
UI Toolkit/DX12 fixtures through real MCP stdio. Its release player disables
profiler/debugger listeners, avoiding Development Player firewall prompts.
If the requested Unity Editor executable is absent (both file path and PATH),
the runner reports Unity as skipped and continues requested Edge probes. An
all-skipped run returns status=skipped and passed=null, without starting MCP or
installing Unity. The default smoke.py suite never launches Unity.

The 45 tools also include cropped screenshots, pixel location/cache helpers,
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
See [the 2026-10-22 issue review](docs/issue-review-20261022.md) for current
diagnostic changes, provider limitations and unresolved foreground reports.

## App-specific testing

Load [docs/ziidms-testing.md](docs/ziidms-testing.md) only when testing ZiiDMS
Departments through DmsEnv. Its launcher path and app behavior are environment
notes, separate from the server's verified UIA contract.
