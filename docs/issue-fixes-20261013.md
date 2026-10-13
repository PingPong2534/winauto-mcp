# Open issue review (2026-10-13)

Base: `fadfd200ad89b367c621ee19b19b2ad09b9d144d`. Changes keep the 36-tool
catalog, background UIA defaults, explicit takeover, watchdog and occlusion guards.

## Changes and acceptance boundaries

| Issue | Change | Verification / remaining scope |
| --- | --- | --- |
| #9 | Foreground click/drag accept distinct ctrl/shift/alt modifiers held through button-up. Track keys before SendInput and release possible partial downs on success, failure, cancellation and watchdog cleanup. | Deterministic partial-send/cancel tests and real owned-window Shift+click/Ctrl+Shift+drag receipt. |
| #10 | `get_elements`, `read_element`, `click_element` accept `scope="non_document"`. Exclude all Document roots/subtrees, preserving strict uniqueness and limits throughout the remaining tree. | Deep synthetic page, duplicate toolbar controls, excluded document target, non-document depth/node limits and stdio schema. Default whole-window refusal retained. |
| #11 | Key steps use scan codes with `hold_ms=80` (20..500 ms), separate down/up dispatch and tracked cleanup. | Owned native window received Return/Escape scan codes and separate down/up messages with the requested 120 ms interval. Unity UI Toolkit callback acceptance remains unverified; keep issue open. |
| #12 | Release borrowed GetWindowDC with ReleaseDC, restore selected bitmap and attempt all cleanup. Optional journal capture exceptions cannot mask completed mutations. | Real native title-bar Close invocation returned success after destruction. Thirty repeated captures did not accumulate GDI handles. Injected DeleteDC/journal failures preserve the action result. |
| #13 | Explicit `capture_mode="screen"` on capture_screen/screenshot/capture_region bypasses partial PrintWindow output while retaining occlusion/geometry guards. Cover diagnostics use HWND/PID/class without titles; ignore DWM-cloaked windows. | Synthetic visible/covered/cloaked/geometry regressions. A foreign WinAuto overlay correctly blocked a live probe; it was neither bypassed nor hidden. Unity Editor DX12 Game View remains unverified; keep issue open. |
| #14 | Preload capture imports before blocking input; bound screen-read/PNG work to 1 s and reserve 300 ms from the original lease for cleanup. Preserve capture_phase and completed input. Keep activation COM cycles on the main initialized apartment until after input release. | A deliberately stalled DwmFlush returns a bounded error with no late PNG. Successful native input + capture returned 5 completed steps and confirmed cleanup. One shared-host cold-start attempt timed out before input. Exact Edge WebGL reproduction remains unverified; keep issue open. |

## Native probe

`tests/probe_modifiers_keys.py` is a maintained regression fixture, excluded from
default discovery. It creates its own window and calls the real foreground worker.
The final successful run used the default 3000 ms maximum lease, completed all five steps,
returned a fresh 190x90 crop and reported input_blocked_ms=1375,
mouse_buttons_released=true, keys_released=true, focus_restored=true and
cursor_restored=true. Native message logs confirmed both modifiers throughout
drag motion/button-up, Return scan=0x1C and Escape scan=0x01, with down/up intervals
at least 90 ms for requested hold_ms=120. Async input state was clear afterwards.

An earlier crop intersected an unrelated WinAuto instance's overlay and was
correctly refused. Only the owned fixture/crop was moved for the successful run.
No user application was controlled, no unrelated overlay was hidden, and no
capture/input guard was disabled. Imported-module I/O delays were also observed;
these are not proof of the original Edge capture failure's root cause.

## Reproduce validation

Final full suite: 110 tests passed in 136.763 s, zero failures/skips. Dependency
check, skill frontmatter validation and CRLF-aware diff check passed. The real
stdio test discovered all 36 tools and verified new scope/capture-mode schemas.
The final foreground-only regression run passed 22/22 cases.

```powershell
.\.venv\Scripts\python.exe tests\smoke.py
.\.venv\Scripts\python.exe tests\probe_modifiers_keys.py
.\.venv\Scripts\python.exe -m pip check
git -c core.whitespace=cr-at-eol diff --check
```

The smoke suite uses owned windows, synthetic provider failures, isolated native
client installations and actual MCP stdio discovery. Shared-desktop focus/cursor
changes can invalidate its no-activation assertions; retain and inspect those
failures rather than weakening the assertions. Native probe results establish
fixture behavior, not Unity/Edge application-level acceptance.
