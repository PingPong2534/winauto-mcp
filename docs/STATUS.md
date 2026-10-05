# Migration status: pywinauto UIA

Verified on 2026-10-05 in `feat/pywinauto-backend`.

## Implemented

- Replaced SendInput control and uiautomation inspection with pywinauto UIA.
- Replaced shared-cursor clicks and typing with scoped semantic patterns.
- Added selector IDs, state readback, literal Unicode text replacement/append,
  numeric slider writes, COM handling per worker, and serialized shared state.
- Removed foreground takeover and global input guard from production modules.
- Kept unsupported legacy tool names as explicit refusals; no physical fallback.
- Kept screenshot/stale checks, scripts, journal, cache, overlay and heap tools.
- Refuse screen fallback that would capture a covering application's pixels.
- Updated setup/usage/contracts and archived historical input probes.

## Evidence

The current suite contains 23 tests: 22 UIA tests and one real MCP stdio test.
Live controls verify button handler invocation, Thai/emoji/literal text,
checkbox state, slider readback and list scrolling while a separate owned
window covers the target. Forbidden physical-input/focus methods are trapped;
foreground stays unchanged. Cursor equality is checked when Windows reports
no intervening external input; the suite does not pin a person's cursor.

Other cases cover duplicate selectors, disabled/read-only/canvas refusals,
incomplete-tree refusal, unsupported gestures, batch prevalidation/runtime stop, synthetic UIPI
refusal, stale images, snapshot/diff/journal replay, unavailable covered
capture, MTA MCP worker calls and STA host threads. Stdio transport verifies
initialization, 34 tools, passive keyboard status and unattached-write errors
from a temporary working directory.

Dependencies verified: Python 3.12.10, pywinauto 0.6.9, MCP 2.0.0. `pip check`
reports no broken requirements. Test journals are isolated temporary folders.

## Limits of this verification

Native owned Win32 controls were tested. This is not evidence that every
third-party app, canvas, custom provider or elevated target supports UIA.
UIPI refusal is exercised with a mocked integrity boundary, not by elevating
an application. Application providers may change focus or show popups on
semantic invocation. Optional .NET heap collection was not exercised in this
migration. Historical Notepad/input-hook probes were not executed.

## Reproduce

```powershell
.\.venv\Scripts\python.exe tests\smoke.py
.\.venv\Scripts\python.exe -m pip check
```
