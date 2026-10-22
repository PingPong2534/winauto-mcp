# Issue review: 2026-10-22

Baseline: clean master a94cfaf, matching origin/master. Reviewed all 13 open
issues and their comments. No private application or account was accessed.

| Issue | Disposition | Change or supported procedure |
| --- | --- | --- |
| #19 | Intentional foreign-window guard; coordination remains open | Do not exempt overlays by class name or dismiss another instance. Inspect with background capture_mode=auto when supported. |
| #21 | Unresolved worker/held-state interoperability | Existing worker tracks its own possible downs and attempts bounded cleanup. Async VK 231 does not prove ownership; no blanket release or guard relaxation. |
| #23 | Unresolved restoration/target acceptance | completed_steps proves dispatch, not text/navigation. Inspect worker_exit_phase, release_confirmed, current readback and target state before continuing. |
| #24 | Unreproduced app keyboard/focus symptom | Scan-code dispatch already preserves extended flags. Prefer a supported ValuePattern write and full readback over unverified caret chords. |
| #25 | Provider limitation; remains open | An empty UIA result does not establish why controls are absent. No authenticated WeCom fixture or supported accessibility activation route was validated. |
| #26 | Provider limitation; remains open | Enabled EditControl without writable ValuePattern remains unsupported. Use the application's documented API when available; no hidden physical-input fallback. |
| #27 | Unresolved activation/native stall | Watchdog and focus ownership remain enforced. Existing phase checkpoints distinguish activating/restoring, but do not distinguish Windows denial from a stalled provider. |
| #28 | Defensive handling added; original page unverified | Null control type describes as UnknownControl. TypeError from child enumeration becomes actionable provider diagnostic; strict targeting never consumes a partial tree as unique. |
| #29 | Provider/traversal boundary; remains open | Visible pixels do not establish an invokable primary action. Existing bounded authorized foreground path remains explicit; no unsafe coordinate fallback. |
| #30 | Requested observability implemented | click_element reports before/after foreground HWND, minimized state and observed foreground_changed; errors retain samples. No automatic focus restoration or causal claim. |
| #31 | Transport diagnostics fixed | Explicit MCP error results expose cause, phase and mutation uncertainty. click_element distinguishes target resolution, description, activation and post-action failure. |
| #32 | Bounded readback and uncertainty implemented | Poll same provider value up to 300 ms without another write. Persistent mismatch reports readback phase and write_returned/value_unverified; user-controlled restoration may still be necessary. |
| #33 | Missing key implemented | Insert VK 0x2D uses the existing extended scan-code path and owned-key cleanup. Existing approval, bounds and ownership checks remain. |

## Evidence scope

Maintained regressions cover disappearing metadata, enumeration diagnostics,
focus observations, invocation uncertainty, delayed/persistent readback, MCP
error content, and Insert mapping. Model tests do not establish Edge/TikTok,
Electron/Grok, WeCom, Uno or ArcGIS acceptance. Native smoke fixtures are owned
windows; no user document or application is mutated for reproduction.

Baseline foreground tests: 29/30 passed; indicator cursor-preservation assertion
observed (130,172) becoming (139,168), consistent with concurrent desktop input.
The unchanged test is retained. Broad smoke suite passed 139/139, including the
same indicator assertion, all five new issue regressions, isolated client-home
installation checks and real stdio discovery of 36 tools. pip check and whitespace
checks passed. Final transport tests also verify structured error content over
the actual stdio connection while retaining the existing output schema.

No installation requested. Repository changes do not establish that an existing
client process has loaded the new server or skill bundle.
