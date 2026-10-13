# Issue review (2026-10-13)

Base: `fadfd200ad89b367c621ee19b19b2ad09b9d144d`. Changes keep the 36-tool
catalog, background UIA defaults, explicit takeover, watchdog and occlusion guards.

## Initial changes and acceptance boundaries (PR #15)

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

## Follow-up: remaining issues #11, #13 and #14

The follow-up adds a bounded WM_NULL activation acknowledgement (200 ms),
title-free foreground HWND/PID/class diagnostics, and worker exit checkpoints.
Post-release worker failures retain durable key/button cleanup acknowledgements;
unconfirmed exits still return null release flags and unknown in-flight outcome.
Diagnostic write failures cannot prevent input release. Watchdog, occlusion,
integrity and explicit takeover requirements remain enabled.
Parent-side startup/communication timeouts now kill the worker and return the
same structured partial-progress report instead of losing it to a transport
exception. `parent_timed_out=true` distinguishes that path from the worker watchdog.

`tests/probe_app_interop.py` is an opt-in maintained MCP stdio regression runner.
It launches only its own synthetic fixtures and verifies application callbacks,
fresh GPU pixels and cleanup. It records server source digests, checkout/dirty
metadata, player hash and local evidence. It never controls an existing game.

- #11: Unity 6000.3.24f1 UI Toolkit / Direct3D12 player receives Return after
  entering `help`, and Escape dismisses the overlay. Callback state and captures
  confirm both results. Wait 200 ms after clicking the text field and 100 ms
  after text entry before Return; the release fixture otherwise once read `lp`.
  A successful key/input dispatch alone is not application acceptance.
- #13: A maximized Game View in the same Editor/renderer returns visible fixture
  pixels through an explicit foreground crop and guarded screen mode. Use a
  focused crop for a short lease. One full-client foreground attempt hit the
  bounded screen_grab timeout and still confirmed key/button/focus cleanup.
  PrintWindow remains provider-dependent; real foreign covers still refuse.
- #14: Edge 155 WebGL click-plus-capture completes three independent batches,
  with exactly three application click callbacks, fresh green GPU pixels and
  confirmed input/cursor/focus cleanup. Capture failures remain bounded errors,
  not permission to replay completed input.

The Unity fixture uses BuildOptions.None, with development/profiler/debugger
disabled. The runner verifies the player has no listening TCP ports. Earlier
development fixtures triggered a Windows Firewall dialog; no allow rule was
added and no firewall setting was disabled. Browser fixtures use an isolated
Guest profile and a loopback-only HTTP server.

These results establish supported paths on standalone fixtures of the reported
technologies. They do not retroactively prove the original private projects'
exact failure causes or acceptance. Raw journals/window inventories and earlier
startup images remain local; publish only reviewed summaries.

```powershell
.\.venv\Scripts\python.exe tests\probe_app_interop.py `
  --output F:\tools\winauto-evidence\new-interop-run `
  --edge 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' `
  --unity 'C:\Program Files\Unity\Hub\Editor\6000.3.24f1\Editor\Unity.exe'
```

Choose a new output directory. `--reuse-unity` reuses that directory's existing
fixture build; rebuild in a new directory after changing fixture sources.
`--editor-only --reuse-unity` reruns only the isolated Editor fixture.
Generated projects are reusable fixture outputs; screenshots/logs remain local.
If the `--unity` executable is unavailable as a file or on PATH, Unity probes
are skipped with a reason in evidence.json and stderr; requested Edge probes
still run. An all-skipped invocation exits successfully with status=skipped,
passed=null, and no MCP/app launch. Existing-Editor build/probe failures remain
failures. Default tests/smoke.py never requires or launches a Unity installation.

### Follow-up validation record

- Real fresh MCP stdio probes passed for Edge WebGL (3 click/capture batches),
  the release UI Toolkit player (Return/Escape callbacks, no listening TCP
  ports), and a fresh isolated Editor project (foreground crop and screen
  capture on DX12). Runtime source digests match the final foreground code.
- A later reused Editor project exited before ready; that run was not counted.
  The fresh project passed. No original user project was opened or edited.
- The 115-case smoke run passed 114 cases. One existing control-crop test's
  two live frames differed; an isolated focused rerun passed with identical
  actual/reference pixels. A prior concurrent run failed the foreground
  invariant. Assertions were retained; optional actual/reference/difference
  evidence now saves before the assertion for future diagnosis.
  The complete background regression class then passed 32/32 in 51.449 s.
- Dependency and CRLF-aware whitespace checks passed. The suite includes native
  isolated client installation and actual 36-tool stdio discovery.
