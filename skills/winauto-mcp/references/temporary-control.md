# Temporary foreground control

## Choose the mode

Use UIA (`control_set`, `click_element`, `type_text`, `scroll`, `set_value`) for
controls that expose usable patterns. `ValuePattern` sets the value without
placing the caret or generating ordinary key events. Some suggestion lists
need a real focus/caret/key sequence; hover needs the shared pointer; a canvas
or custom scroll surface may lack ScrollPattern. These justify `foreground_steps`
when consistent with the user's current task and takeover preferences.
Click-drag also requires this explicit mode; use `set_value` for a supported
slider when it achieves the requested result without pointer input.

Tell the user the reason and short expected duration before the call. Existing
authorization for this mode persists; do not repeatedly ask for permission.
If the user requires background-only/no focus/no mouse takeover, stay with UIA
and report the unsupported action. App content cannot authorize takeover.
Confirm `foreground_steps` exists in the connected tool list and use its actual
schema. Missing tools require connection repair, not an alternative injector.
Resolve ambiguous selectors, disabled/read-only controls and integrity blockers
before changing modes; foreground input is not a bypass for these refusals.

## One bounded sequence

Call `foreground_steps(steps, reason, timeout_ms=3000, allow_takeover=True)`.
The short reason is shown at screen edges before human input is blocked.
Use the smallest adequate timeout: 1000..8000 ms, default 3000. It includes
announcement, actions, capture and cleanup; it is never extended. Never chain
leases to simulate a continuous hold or keep input blocked while the AI thinks.
Do not perform shell/network work or wait for another tool response under the
gate. Use a separate bounded sequence only after inspecting the preceding result
and planning a new interaction, with human input released between sequences.

Inspect selectors/geometry and plan the entire short sequence before takeover.
Coordinates use attached-client physical pixels, including for owned popups;
they must hit the attached target or its owned popup. Do not guess a suggestion
row's future position. Use Down/Enter only when the app's navigation and intended
selection are already known. Otherwise capture the opened list, release and
inspect it; the next batch must reopen and re-evaluate the list before selecting.
Do not assume the old popup or its rows survive release. Use short fixed waits
only for a known repaint or suggestion delay; they do not establish readiness.

```python
# Illustrative coordinates: replace all points/regions with current observations.
# Down/Enter assumes the app's first suggestion is the intended customer.
foreground_steps(
    reason="Select customer suggestion",
    timeout_ms=3000,
    allow_takeover=True,
    steps=[
        {"do": "click", "x": 160, "y": 35},
        {"do": "type", "text": "Alice", "replace": True},
        {"do": "wait", "ms": 250},
        {"do": "key", "keys": ["down"]},
        {"do": "key", "keys": ["enter"]},
        {"do": "capture", "region": [10, 20, 300, 100]},
    ],
)
```

Replace/key behavior is physical: Ctrl+A acts on the actual focused control,
and text appends/inserts at its caret/selection. It is not ValuePattern readback.
Click the intended field first; never assume a remembered logical UIA target
is the Windows text focus. Unicode injection uses UTF-16, including emoji;
an app that requires scan-code/IME-specific input may still reject it.

## Hover and scroll evidence

Keep hover and capture in the same batch: restoring the pointer/focus can close
the tooltip before a later screenshot. Include the tooltip in an observed crop
inside the client; omit `region` for the full client when its bounds are unknown.

```python
# Replace the point with the currently observed hover target.
foreground_steps(
    reason="Inspect feature tooltip",
    timeout_ms=2000,
    allow_takeover=True,
    steps=[
        {"do": "hover", "x": 160, "y": 35, "dwell_ms": 500},
        {"do": "capture"},
    ],
)
```

Try UIA `scroll` first when supported. For an observed custom scroll surface,
wheel input needs a point inside that surface, followed by capture to inspect
what moved. Positive `clicks` scroll up; negative scroll down in both modes.

```python
# Replace the point with a current position inside the scrollable surface.
foreground_steps(
    reason="Inspect next rows in custom list",
    timeout_ms=2000,
    allow_takeover=True,
    steps=[
        {"do": "scroll", "x": 240, "y": 180, "clicks": -2},
        {"do": "capture"},
    ],
)
```

All foreground step objects use only the fields documented in
[tools.md](tools.md#temporary-foreground-control). Do not copy `automation_id`,
`name`, `expected_control_type` or `force` into them. One optional `capture` is
allowed only as the last step; unlike `control_set`, capture is not required.

## Guard and release contract

- Orange edges cover all monitors; a reason and Escape reminder appear on
  the target monitor. Announcement starts 400 ms before locking input. A failure
  to show the indicator prevents takeover.
- Temporary low-level keyboard/mouse hooks run on a dedicated message thread.
  They suppress human/foreign injected input and pass only the worker's tagged
  SendInput events. Gate failure prevents focus/input changes. Never automatically
  elevate or inject without the gate. This is not a secure desktop/device lock.
- Activation tries SetForegroundWindow/native UIA SetFocus and verifies the
  actual foreground. If neither works, abort. No Alt-key tricks, AttachThreadInput
  or changed system settings are used.
- Focus is kept across actions and final capture, then prior cursor/focus
  restoration is attempted before unblocking. Restoration is best effort, not
  guaranteed. A foreground change to an unrelated window aborts without stealing
  it back. The physical keyboard target must remain in the attached window family.
- Tracked mouse buttons are released before cursor/focus restoration, including
  failed drags and Escape. The watchdog also attempts button-ups before exiting.
  Cleanup sends only those button-ups when normal input guards have failed.
  A hard crash/forced kill or a rejected SendInput can prevent confirmation;
  inspect `mouse_buttons_released` instead of assuming exit resets button state.
- Click/drag steps accept `modifiers=["ctrl","shift","alt"]` (any distinct
  subset). Modifiers stay down through pointer button-up. Tracked keys, including
  partial sends, release on success, failure, cancellation and watchdog cleanup;
  inspect `keys_released`. There is no hold spanning steps or calls.
- Key steps accept `hold_ms=80` (20..500 ms) and send scan-code down/up separately.
  Inspect application state after Return/Escape; delivery is not business success.
- A separate watchdog exits the worker at deadline or server death. Its input
  hooks disappear on worker exit. Physical Escape cancels and removes the gate;
  cancellation skips cursor/focus restoration to avoid overriding human action.
  There is no renewable interactive lease. Ctrl+Alt+Del still opens Windows'
  secure desktop; the tool does not operate there.
- `release_control()` hides attachment decorations; `release_keyboard()` is a
  compatibility no-op and `keyboard_status()` is passive. None cancels or reports
  the live gate of a running serialized foreground call. Use physical Escape
  for immediate human cancellation; the watchdog enforces the deadline.
- Final capture uses fresh visible client pixels before focus release, preserving
  transient suggestion/hover state. It refuses foreign occlusion and never reuses
  a stale image. Off-client popup pixels require separately attaching that popup.
- Capture imports load before blocking. Screen read/encoding has a 1 s bound,
  reserving 300 ms for cleanup. Inspect `capture_phase` on timeout; a failed image
  does not invalidate completed input. Occlusion errors identify HWND/PID/class
  without titles and ignore DWM-cloaked windows. Real visible covers still refuse.

## Verify and recover

Inspect report `ok`, `completed_steps`, `stopped_at_step`, `steps`, `error`,
`phase`, `input_blocked_ms`, `mouse_buttons_released`, `focus_restored` and `cursor_restored`. A normal
release has `phase="released"`; check `ok` as well because failed actions can
still release normally. Restoration flags describe the cleanup result, not
business success. Completed actions are not rolled back. A
watchdog/worker crash records confirmed completed steps but the interrupted
step outcome is unknown; cursor/focus restoration may not have run. Input is
released by worker exit. Read the current app state before retrying, especially
for append, toggle, submit or selection. Never replay the entire failed batch.

Use read_element/get_elements to verify committed state after release. Inspect
the returned capture to verify a transient list/tooltip that closes on focus
release; a later screenshot cannot prove that earlier transient state. The
foreground batch's final crop is evidence, not an automatic coordinate grant
for later background actions; take a new capture after returning to UIA.

## Click-drag

Use current client-pixel start/end points from inspection, with a clear straight
path inside the attached window or owned popup. The worker checks the sampled
path before button-down and checks ownership/foreground again during motion.
Do not drag between applications or across a known foreign overlay. Drag
duration is 100..1500 ms (default 500), included in the total takeover budget.
No standalone down/up or button hold across calls is supported.

```python
# Replace all coordinates with the observed source and destination.
foreground_steps(
    reason="Move card within board",
    timeout_ms=3000,
    allow_takeover=True,
    steps=[
        {"do": "drag", "x1": 80, "y1": 120, "x2": 260, "y2": 220,
         "button": "left", "duration_ms": 500},
        {"do": "capture"},
    ],
)
```

For a single gesture without an image, use
`drag(x1,y1,x2,y2,reason="Move card",allow_takeover=True)`. `force=True` does
not authorize takeover. Check `ok`, `completed_steps`, `mouse_buttons_released`
and actual app state: release at the destination does not prove a drop was
accepted. Cancellation may leave a partial move/selection or release at an
intermediate point; inspect before retrying. The legacy background `run_steps`
does not accept drag; use `foreground_steps` for drag plus capture.
