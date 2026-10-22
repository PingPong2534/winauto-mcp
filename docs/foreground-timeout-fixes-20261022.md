# Foreground timeout fixes: #21, #23, #27

Baseline: master 94bccde, clean. Original issue reports and all comments were
reviewed. The original private Uno/Edge/ArcGIS environments were not replayed.

## Confirmed defects

- The hook message pump slept after every pass, delaying each synchronous hook
  callback by a scheduler tick. Owned native text delivery reached only 88 units
  before a 4 s lease's action deadline in the intermediate reproduction. This
  timing is independent of the fixture's initial no-horizontal-scroll readback
  limit; the final fixture enables ES_AUTOHSCROLL at creation.
- Key cleanup could fail to acquire the dispatch lock and nevertheless emit ups
  concurrently with an in-flight send. This can place an up before a late down.
- Text had no local finally for partial dispatch and no cooperative action cutoff
  reserving cleanup time. The watchdog could terminate it mid-chunk.
- Activation/restoration called provider-dependent UIA SetFocus without a bounded
  completion contract while holding the input gate. Native call subphases were
  not durably distinguished. A restore-stage exit erased already acknowledged
  key/button release fields.

Six focused regressions failed before the patch. Additional refusal/cleanup
boundary regressions were added after that reproduction.

## Changes

- Wake the hook thread using MsgWaitForMultipleObjectsEx, with a 50 ms idle
  heartbeat/shutdown bound, instead of per-event sleep. Wait failures unhook both
  hooks and stop control; foreign events and physical Escape retain their policy.
- Reserve 500 ms inside the original worker lease in preflight and action checks.
  Type in scalar-aligned chunks of four Python characters, check keyboard/focus
  ownership between chunks, and release owned possible downs in finally.
- Refuse cleanup that cannot acquire the dispatch lock. Never infer ownership from
  async VK 231 or issue blanket key-ups. A genuinely stuck SendInput can still
  leave release unknown; the hard watchdog and held-input refusal remain.
- Use native foreground/cursor requests through ctypes. Probe responsiveness
  before activation (200 ms) and restoration (100 ms); acknowledge asynchronous
  activation with a second bounded WM_NULL. Remove unbounded UIA focus fallback.
- Preserve original failure subphase, native elapsed time, injection progress,
  cooperative expiry and separate key/button/gate release acknowledgement.

## Native evidence

Windows 11 build 26200, owned Win32 Edit fixtures at 96 DPI. Every batch used the
real disposable foreground worker, visible indicator and tagged hook gate.

| Case | Observed outcome |
| --- | --- |
| 101 ASCII characters, 4 s lease | Full exact readback; keys/buttons released; no async held keys. First successful run blocked input for 718 ms. |
| 180 mixed Thai/ASCII characters, 6 s lease | Full exact readback; keys/buttons released; no async held keys. First successful run blocked input for 750 ms. |
| 2000 Thai characters, 1.5 s lease | Cooperative stop during typing; 76-character exact prefix in the final pre-smoke run; keys/buttons released; no async held keys. No text replay. Prefix length depends on host timing. |
| Following click-only batch | Acquired and released successfully; no replacement/append retry. |
| Owned nonresponsive target UI thread, 2 s lease | Refused at activation_probe (native error 1460); zero dispatched steps; keys/buttons/gate released, 250 ms blocked. |

Maintained reproduction: `.venv/Scripts/python.exe -B tests/probe_timeout_recovery.py`.
The fixture owns its content/windows and enables horizontal scrolling for long
input. An initial fixture without that style capped visible-width input at 34
characters and was corrected rather than interpreted as a production defect.

## Scope

Validation: 149/149 smoke tests passed, including ten new focused regressions,
real stdio discovery of 36 tools and isolated Codex/Claude client-home installer
checks. The existing foreground native probe also passed before/after the changes.
pip check and whitespace checks passed. The final native recovery rerun again
verified full 101/180-character readbacks, a clean cooperative stop at an
80-character prefix, an immediately usable following batch and the bounded hung
target refusal; all reported release_confirmed=true and no held keys afterward.
Native recovery proof runs separately
from smoke so no concurrent fixtures compete for desktop input.

These changes fix reproduced input-pump latency, cleanup races, action budgeting,
unbounded focus fallback and reporting defects. They do not prove every original
private-app symptom has the same cause. Original persistent VK_PACKET origin and
every third-party focus/IME/provider behavior remain outside this fixture proof.
Unknown input stays refused; no guard is weakened or bypassed. No client reinstall
or Unity application probe is part of this change.
