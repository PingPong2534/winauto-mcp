# Hybrid automation planning

Load for multi-target plans, speed-sensitive workflows, conditional actions,
or choosing UIA versus pixel verification. Use each tool only where it provides
necessary evidence; do not run the whole catalog for every action.

## Route by the missing information

| Situation | Inspect / execute / verify |
| --- | --- |
| New target window | `list_windows`, `attach_window`; `get_elements` for controls or `capture_screen` for visual context plus tree |
| Known semantic control | Unique ID/name, UIA action, `read_element`; use `control_set` if a final control crop is required |
| Several existing controls | `guarded_steps`: bind identities before mutations, revalidate fresh state/ancestry at each step, check `before` and `expect` |
| Semantic control with important visual context | Add a `guard` covering that context; keep UIA state checks too |
| Known coordinates | Obtain source coordinates with `locate_in_region` or current UIA rect; guarded `click` still requires UIA action support |
| Reused location | `recall_location` verifies the cache; never treat `remember_location` as readiness or identity proof |
| Only fresh pixels needed | `screenshot`; use `capture_region` for a known smaller area; avoid another full UIA traversal |
| Slow change on an existing control | `expect` plus bounded `wait_ms`; no repeated mutation or fixed sleep |
| Navigation / new or replaced controls / new top-level dialog | End the batch, inspect the resulting UI, and plan again; attach a separate dialog HWND when necessary |
| Canvas / missing UIA patterns / focus-dependent behavior | Pixels locate and verify; only explicitly permitted `foreground_steps` can perform physical actions. Load temporary-control.md. Keep it short with final capture; no automatic switch from UIA |
| Scroll surface | Prefer `scroll` / legacy `run_steps` with ScrollPattern; inspect newly visible controls afterward; permitted foreground scroll only when required |
| Pixel diagnostics | `snapshot`, `diff_since_snapshot`; they detect changes, not intended outcome |
| Partial failure | Read the batch first, then `read_element`, fresh pixels, `history` / `replay_frame` as needed; never aim from replay |
| Memory growth investigation | `heap_snapshot` / `heap_diff` only for an authorized .NET diagnostic task; not in the action loop |
| Finish | `release_control` to hide decoration or `detach_window` to end attachment |

`highlight` is an optional targeting aid, not an execution or verification step.
`keyboard_status` is diagnostic; `release_keyboard` does not cancel a foreground
batch. Retired hover/key/hotkey and foreground-hold tools do not provide a
fallback; use the explicitly opted-in bounded foreground API instead.

## Guarded batch contract

```python
# IDs and expected state come from current inspection of the attached app.
guarded_steps(steps=[
    {"do": "type", "automation_id": "customerName", "text": "Alice",
     "replace": True, "before": {"enabled": True}, "expect": {"value": "Alice"}},
    {"do": "type", "automation_id": "customerCity", "text": "Bangkok",
     "replace": True, "expect": {"value": "Bangkok"}},
    {"do": "click_element", "automation_id": "saveButton", "before": {"enabled": True}},
    {"do": "read", "automation_id": "saveStatus",
     "expect": {"name": "Saved"}, "wait_ms": 1000},
])
```

All targets must already exist at preflight, including a status control that
will change after Save. Navigation that destroys targets needs a new plan.
Do not claim Save succeeded from a successful invocation alone.

- `do`: `read`, `click_element`, `type` (text, replace=False), `set_value`
  (numeric value), `click` (x,y). Every non-coordinate step needs explicit
  `name` and/or `automation_id`. Syntax and initial capabilities of every target
  are checked before any mutation; control identity is then bound by runtime ID.
  One complete UIA traversal is shared during preflight. Call-local UIA interfaces
  use uncached properties and fresh ancestry/identity checks; no wrapper survives
  into another tool call and no replacement is selected after disappearance.
- `before` / `expect`: exact subsets of `name`, `automation_id`, `control_type`,
  `enabled`, `value`, `read_only`, `toggle_state`, `selected`, `range_value`,
  `rect`, `runtime_id`. Missing properties fail rather than passing silently.
  Password value is unavailable. Put observed `runtime_id` in `before` if identity
  must also match the earlier planning observation.
- `wait_ms`: 0-5000, only with `expect`. Polls bound control state at up to 50 ms
  intervals, never repeats the action. The target must remain the same control.
- `timeout_ms`: default 10000, range 1-30000. Cooperative deadline between
  synchronous UIA/capture calls; it cannot interrupt a hung provider call.
- `capture_final=False`: semantic-only success captures nothing. Opt in when
  final visual evidence is needed. A failed final capture does not undo actions.
- `vision_backend`: `auto`, `pillow`, `opencv`. Auto prefers OpenCV when installed,
  based on the local benchmark of small and large ROIs; missing OpenCV falls
  back to Pillow. Pillow also handles capture/crops and can be explicitly selected
  for comparison. An explicitly requested unavailable OpenCV fails preflight.
  Backends share max-RGB-channel threshold semantics, unlike the legacy
  luminance-based `check` / `diff_since_snapshot`.

## Visual guards

After observing a current screenshot/crop, add to an action:

```python
"guard": {
    "region": [420, 310, 620, 370],
    "threshold": 10,
    "max_changed_ratio": 0,
    "ignore_regions": []
}
```

The region must be fully covered by one caller-observed view with the same
client size. A diagnostic snapshot or journal frame is not an observed view.
All guard references are fixed before the batch. Internal captures/checks
never advance them. Fresh capture is required at every guarded step; no
cached-image fallback. Window movement during capture or client resizing stops.
Coordinate targets must remain in their original UIA rectangle and inside the
unmasked guard. Image equality cannot enable unsupported canvas actions.

Compare pixels where the largest RGB-channel difference exceeds threshold
(0-255); the changed-pixel fraction must be <= max_changed_ratio (default 0).
Masks use absolute client rectangles and cannot cover the entire region.
Use small masks only for observed irrelevant animation, such as a caret. Avoid
high tolerances or masks over meaningful labels/state. Include relevant modal
or navigation context in guards or UIA conditions: unchanged target pixels alone
cannot rule out a modal elsewhere, stale application rendering or semantic failure.
If an earlier action intentionally changes this region, use an explicit semantic
condition or end the batch for new observation; do not raise tolerance to bypass it.

## Results and recovery

Read `ok`, `status`, `completed_steps`, `completed_actions`, `stopped_at_step`,
and `steps[].action_outcome`:

- `not_attempted`: stopped before dispatching the mutation.
- `performed`: mutation returned successfully; an expectation may still fail.
- `unknown`: provider call raised after dispatch; its effect may have occurred.

On `area_changed`, the next action is not attempted. The report includes region,
changed bbox/fraction/backend, recommends `capture_screen`, and returns a fresh
crop plus coordinate offset when available. Inspect that crop if enough context
is present; otherwise capture the full screen. Returned evidence counts as an
observed view only after the batch returns, never during automatic continuation.

Other stops include missing baseline, geometry changes, capture unavailable,
preflight failure, state mismatch, expectation failure or deadline. Failed
evidence capture returns its error without a stale image. Never replay completed
or unknown mutations blindly. Plan remaining actions from the observed current
state. Pixel equality and UIA invocation acceptance alone are not business proof.

Use `capture_count` and core `elapsed_ms` to inspect workflows; elapsed_ms excludes
outer COM/GC cleanup and transport. Run `tests/benchmark_hybrid.py` from the server
checkout for a repeatable complete-call owned-window benchmark; do not
promise a universal speed multiplier from a synthetic image comparison.
