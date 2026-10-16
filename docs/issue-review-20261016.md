# Open issue review: 2026-10-16

Base: `9c8f147783aecab76a8a16fb4cd693cf439c842c` (`master`).
Repository: `PingPong2534/winauto-mcp`. All four open issues and their comments
were read before changing code. Private applications, screenshots and session
data were not requested, copied or published.

## Decisions

| Issue | Finding | Change / disposition |
| --- | --- | --- |
| #18 | Confirmed: bound identity lookup exhaustively walks unrelated subtrees and can refuse an already observed target on an unrelated depth limit. Coordinate/name refusal itself is conservative. | Add explicit `runtime_id` selectors to click_element/read_element/type_text and correct remembered/bound identity lookup. Name/coordinate lookup remains strict. |
| #19 | Current behavior is intentional: a click-through overlay has visible borders/highlights; its entire bounding rectangle is conservatively treated as a pixel cover. The report did not establish that no visible pixels intersected. | Preserve occlusion checks and cross-session ownership. Document auto/PrintWindow workaround where DPI compatible. Keep the issue open for optional coordination capability; no claim of an app defect. |
| #20 | Confirmed in a deterministic 150% model: PrintWindow success and nonblack-content checks accept a padded image whose pixels do not match physical/UIA coordinates. | Compare target-native and physical window dimensions before PrintWindow; reject virtualization and use guarded screen fallback. Also reject geometry changes during PrintWindow. Covered scaled apps can refuse safely. No guessed image scaling. |
| #21 | Confirmed related watchdog bug: synchronous release cleanup can hang, defeating the worker deadline. The original 101-character typing timeout and persistent held state are not reproduced. | Bound cleanup grace to 250 ms; add numeric held-VK diagnostics without claiming a human source. Preserve held-input refusal and tracked-input-only cleanup. Keep #21 open for the original symptom. |

## Regression evidence

Before fixes, two bound-identity tests failed with the depth-limit exception,
the synthetic 300x150/200x100 capture test returned the corrupt bitmap, and
the 100 ms watchdog with a five-second stalled cleanup exceeded a two-second
parent deadline. These tests pass with the fixes.

Coverage includes strict duplicate/depth/node/scope checks, conflicting and
malformed identity selectors, absent/foreign IDs, native UIA textbox selection,
remembered and explicit identity writes, disabled/read-only refusals, DPI context
restoration on failure, GDI handle/resource cleanup, foreign-overlay occlusion,
held-VK diagnostics with zero dispatched input, and real subprocess watchdog
deadline/parent-death behavior. Default smoke does not inject foreground input.

Final validation:

- Default smoke: **134/134 passed**, 148.642 seconds, including 14 added
  regressions. No tests skipped. Native fixture UIA operations preserve the
  background-mode assertions; real MCP stdio discovers 36 tools and verifies
  the three new runtime-ID schemas.
- Initial full run: 133/134; the STA-read test's teardown observed a foreground
  HWND change. Cause was not established. That test passed in isolation (1/1),
  then the entire suite passed without changing the foreground assertion.
- After the final watchdog exception-path hardening, focused watchdog tests:
  **4/4 passed**. Subprocesses read the current source.
- `python -B -m pip check`: no broken requirements.
- `git -c core.whitespace=cr-at-eol diff --check`: clean.
- Full-run logs retained locally as `smoke-first.log` and `smoke-final.log` in
  the dated WinAuto validation directory; no desktop images uploaded.

## Evidence limits

Native fixtures use owned Windows controls behind a cover; they do not operate
user applications. This desktop reports system DPI 96 (100%); 150% scaling is a
deterministic modeled regression, not a native 150%/MapGIS acceptance test.
The original private registration dashboard, MapGIS app and Uno/Skia app were
not retested. No live long-text foreground injection or recovery injection was
performed. The #21 held-state origin remains unknown, and release_keyboard
remains a compatibility no-op. A watchdog exit removes hooks, not held key state.

## API basis

- [Microsoft: Runtime IDs are desktop-unique but reusable over time](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.automationelement.getruntimeid).
- [Microsoft: DPI virtualization and calling-thread context](https://learn.microsoft.com/en-us/windows/win32/hidpi/high-dpi-desktop-application-development-on-windows).
- [Microsoft: SetThreadDpiAwarenessContext and restoration](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddpiawarenesscontext).
