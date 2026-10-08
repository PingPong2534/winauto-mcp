# Migration status: pywinauto UIA

Verified on 2026-10-08 in `feat/pywinauto-backend`.

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
- Attach now shows the green outline automatically and clears old highlights.
- Replaced the Tk outline with a native layered window created with no-activate
  and mouse-through styles. It follows move/resize and hides with the target.
- Screen fallback temporarily hides the decoration, restores it even on
  occlusion refusal, and preserves target/highlight state.
- Suppress standard Win32 UIA provider activation during background mutations
  with a temporary WS_EX_NOACTIVATE bit. Restore it on success and failure.
- Optional `attach_window(..., label="...")` shows a Thai/Unicode feature label
  outside the outline and includes it in the journal session name. A separate
  non-activating, click-through label window follows the frame without covering
  its interior; placement prefers above, with exterior fallback positions.
- Successful interactions automatically outline the resolved control in red;
  the latest box replaces prior boxes and keeps the feature label.

## Evidence

The current suite contains 33 tests: 25 UIA tests, seven native outline tests
and one real MCP stdio test.
Live controls verify button handler invocation, Thai/emoji/literal text,
checkbox state, slider readback and list scrolling while a separate owned
window covers the target. Forbidden physical-input/focus methods are trapped;
foreground stays unchanged. Targets are normally activatable HWNDs shown via
SW_SHOWNOACTIVATE, so tests no longer mask provider activation with a permanent
WS_EX_NOACTIVATE style. Cursor equality is checked when Windows reports
no intervening external input; the suite does not pin a person's cursor.

Outline tests confirm actual green desktop pixels, startup and repeated show/hide
without activation, layered/mouse-through styles, reattachment/highlight clearing,
retargeting, move/resize/hide/restore, detach, and clean screen fallback followed
by outline restoration. These use owned test windows, not a user's application.
Labels are verified through real desktop pixels, including Thai, long-label
clipping, exterior placement, movement, edge fallback, hiding when no exterior
space fits, clearing and hide/show persistence. Both decoration windows are
hidden during screen fallback; the label retains no-activate/mouse-through styles.
Guard tests cover style restoration
after errors, preservation of other style bits and an already-foreground target.
Live red-box tests cover clicks, logical edit selection, text/range writes,
scroll containers, script steps, unchanged markers on reads/refusals, and
label-preserving restoration after release. A failed cosmetic observer does
not report a completed invocation as failed or cause a second invocation.

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
The activation guard covers only the attached HWND during mutations, not new
popup windows or arbitrary providers that explicitly force activation. A person
clicking that background target during a mutation will not activate it until
the guard is removed. No foreground-restoration loop is installed.

## Reproduce

```powershell
.\.venv\Scripts\python.exe tests\smoke.py
.\.venv\Scripts\python.exe -m pip check
```
