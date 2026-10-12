# Status: UIA and bounded temporary control

## Guarded hybrid batches (2026-10-12)

- Added `guarded_steps` (36 tools): shared UIA preflight, bound identities,
  fresh state/ancestry checks, optional immutable observed-image guards,
  bounded expectation polling and explicit partial-action outcomes.
- OpenCV is preferred for region comparisons when installed; Pillow provides
  capture/crop handling and an equivalent comparison fallback. No cached-image
  fallback or implicit physical-input takeover in guarded batches.
- Call-local COM cycles are collected on the caller's initialized apartment;
  Python GC state is restored after the batch.
- Full regression: 90 tests passed, including owned covered-window UIA,
  state/visual stops, real replacement/reparenting, backend parity, native
  isolated Codex/Claude installations and real stdio discovery of 36 tools.
- Latest local benchmark: three semantic actions, 7 repetitions, legacy
  run_steps(delay_ms=0) median 1038.825 ms / 4 captures; guarded_steps median
  756.971 ms / 0 captures. Wall time includes COM cleanup. Workload-specific,
  excludes AI/network latency. Run tests/benchmark_hybrid.py to remeasure.

## Earlier validation

Verified on 2026-10-11 in `feat/pywinauto-backend`. Background migration was
verified on 2026-10-08; temporary foreground control is the subsequent addition.

## Implemented

- Replaced SendInput control and uiautomation inspection with pywinauto UIA.
- Replaced shared-cursor clicks and typing with scoped semantic patterns.
- Added selector IDs, state readback, literal Unicode text replacement/append,
  numeric slider writes, COM handling per worker, and serialized shared state.
- Removed implicit foreground takeover and the legacy global input guard from
  background modules. Explicit foreground_steps now uses a short-lived gate.
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
- Added `control_set` for one control's click/type/capture steps. Inspection
  advertises `control_steps`; sets validate capabilities before mutation, bind
  the original runtime ID, verify text, and return only the current control crop.
- Removed the pixel-stability wait tool, script action and polling implementation.
  Verification uses control readback, exposed element state or fresh captures.
- Added a complete-bundle installer for Codex and Claude Code: Python dependencies,
  native plugin with the full skill and MCP connection, declared plugin dependencies,
  copied-file verification and real stdio checks before/after registration.

## Temporary foreground control evidence

- Added foreground_steps with explicit allow_takeover/reason, 3000 ms default,
  1000..8000 ms hard deadline, 400 ms screen-edge announcement, and no renewal.
- Dedicated low-level keyboard/mouse hooks pass only tagged worker input;
  physical Escape cancels. Worker timeout or parent death ends the process and
  removes its hooks. Ordinary UIA tools retain their background/refusal behavior.
- Ten foreground regressions cover prevalidation, gate/filter/cancel decisions,
  failed announcement/acquisition, partial errors, restoration failure,
  escaped foreground, Unicode layouts, native indicator and actual watchdog
  process termination/parent death. Full suite: 56 tests passed on 2026-10-09.
- The separate owned-window live probe completed click, physical Thai/emoji
  input, Backspace, hover, wheel and a fresh 280x80 crop. Readback was ไทย🙂;
  list top index advanced 0 to 3. Its focus-dependent suggestion appeared while
  editing, was present in the returned capture, and hid after focus release.
  Prior focus and cursor were restored. The measured input-gate duration was
  1375 ms with a 3000 ms budget. This is evidence for the owned fixture only.
- BlockInput was refused at medium integrity on this host. The selected hook
  gate/native UIA focus supplement worked without elevation. Mouse injection
  tags were observed truncated to 32 bits; tags use 31 bits to pass the filter.
- The server/tool catalog and native client installer checks now expose 35
  tools, including foreground_steps. Skill frontmatter validation passed.

## Evidence

### Click-drag (2026-10-11)

- Added opt-in `drag(...,allow_takeover=True,reason=...)` and foreground_steps
  drag with left/right button, straight client-pixel path and 100..1500 ms
  duration. Defaults and background run_steps still refuse physical gestures.
- Sampled path checks precede button-down; ownership/foreground are rechecked
  during movement. Tracked mouse-ups run before cursor/focus restoration on
  success/failure/Escape. Watchdog attempts release before exit; forced worker
  termination cannot confirm release and reports mouse_buttons_released=null.
- Five new foreground regressions cover drag timing/budget/opt-in, held-button
  motion and cancellation, covered paths, partial SendInput, release failure
  and watchdog cleanup. Transport checks the actual drag MCP opt-in schema.
  Full smoke suite: 61 tests passed in 112.125 seconds on 2026-10-11, including
  native Codex/Claude installation tests in isolated client homes.
- `tests/probe_drag.py` exercised the real worker on an owned HWND: start
  (40,450), end (240,480), 25 held-button move messages, received mouse-up,
  fresh 640x90 capture, both physical button states up, cursor/focus restored.
  Gate duration was 1750 ms with a 3000 ms budget. This verifies the fixture;
  acceptance of a drop in a user's application remains app-dependent.

### Background and installer evidence

The UIA/outline/transport suite contains 40 tests: 32 UIA tests, seven native
outline tests and one real MCP stdio test. Six installer regression tests additionally
exercise native clients and partial failures in isolated temporary client homes.
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

Control-set tests verify Thai/literal text and append/replace under a covering
window, native textbox-frame crop pixels and dimensions, crop-only seen state,
prevalidation, wrong-type/read-only/ambiguous refusals, same-selector native
control replacement, fresh bounds after movement, capture/layout failures after
successful writes, per-step journals and MCP worker image serialization.

Other cases cover duplicate selectors, disabled/read-only/canvas refusals,
incomplete-tree refusal, unsupported gestures, batch prevalidation/runtime stop, synthetic UIPI
refusal, stale images, snapshot/diff/journal replay, unavailable covered
capture, MTA MCP worker calls and STA host threads. Stdio transport verifies
initialization, 35 tools including control_set/foreground_steps, absence of the retired wait tool,
passive keyboard status and unattached-write errors
from a temporary working directory.

Dependencies verified: Python 3.12.10, pywinauto 0.6.9, MCP 2.0.0. `pip check`
reports no broken requirements. Test journals are isolated temporary folders.

Installer evidence: native Codex and Claude Code (2.1.168) plugin CLIs installed
the complete skill/MCP bundle and a previously absent local prerequisite plugin.
Reinstallation updated both native skill caches after source changes, kept one
plugin ID, preserved unrelated settings and verified each installed connector's
35-tool stdio handshake. The PowerShell entry point also completed with existing
Python/venv dependencies. Required-plugin failure blocks main registration;
preflight failure clears a previous success report.
Relocated checkouts receive a new connector/cache revision, and a foreign local
marketplace using the same name is refused without changing its registration.

## Limits of this verification

The user's actual suggestion app, IME-dependent typing, elevated targets and
secure/locked desktops have not been verified in temporary mode. Hook callback
cancellation is covered deterministically; physical Escape has not been manually
pressed during the live probe. Hook gates are not raw-input/touch/device locks.
Focus/cursor restoration remains best effort on refusal, cancellation or worker
termination. See [temporary-control.md](temporary-control.md) for the contract.

Native owned Win32 controls were tested. This is not evidence that every
third-party app, canvas, custom provider or elevated target supports UIA.
UIPI refusal is exercised with a mocked integrity boundary, not by elevating
an application. Application providers may change focus or show popups on
semantic invocation. Optional .NET heap collection was not exercised in this
migration. Historical Notepad/input-hook probes were not executed.
Fresh-machine WinGet Python/.NET provisioning and heap-tool installation were
not exercised. Claude Desktop/Cowork/cloud full-bundle installation is outside
the current installer; native-client tests do not prove post-restart UI discovery.
The activation guard covers only the attached HWND during mutations, not new
popup windows or arbitrary providers that explicitly force activation. A person
clicking that background target during a mutation will not activate it until
the guard is removed. No foreground-restoration loop is installed.

## Documentation review

Reviewed against source commit 53ef7ca plus removal of the pixel-stability wait
on 2026-10-08, when the server exposed 34 tools. On 2026-10-09 the tool catalog,
skill and examples were extended for foreground_steps (35 tools). Python
examples were checked against their current signatures.
Controlled in-memory probes confirm that run_steps can reuse a prior frame when
capture fails. HOWTOUSE and
SPEC now describe these limits, batch error/report distinctions, journal frame
availability, control_steps and coordinate/state conventions. ZiiDMS launcher
notes are separate and remain outside the native-control verification scope.

## Reproduce

```powershell
.\.venv\Scripts\python.exe tests\smoke.py
.\.venv\Scripts\python.exe -m pip check
```
