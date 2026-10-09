# Batches and diagnostics

## Multi-control scripts

Use `run_steps` for short sequences spanning multiple known controls. Use
`control_set` for a single control with a required fresh final crop.

```python
# IDs must come from the attached app's current get_elements() result.
run_steps(
    steps=[
        {"do": "type", "automation_id": "customerName", "text": "Alice", "replace": True},
        {"do": "type", "automation_id": "customerCity", "text": "Bangkok", "replace": True},
        {"do": "click_element", "automation_id": "saveButton"},
    ],
    stop_on_error=True,
)
# Inspect the summary and every step, then verify the app's save result.
read_element(automation_id="saveStatus")
```

- Supported `do` kinds: `click`, `click_element`, `type`, `set_value`, `scroll`,
  `wait`, `capture`, `check`. Step syntax is prevalidated before mutation;
  control capabilities, uniqueness and permissions are checked at execution.
- Limit: 40 steps, at most 60,000 ms of requested `wait` steps (`ms`). Action
  durations and `delay_ms` are outside that wait budget. Prefer observations
  of expected state over fixed waits. `delay_ms` defaults to 120 and is clamped
  to 0–3000 ms.
- Keep `stop_on_error=True` unless continuing independent failures is required.
  `stop_if_user_types` and `keep_cursor` are compatibility arguments without
  effect; background scripts do not watch or block user input. For a permitted
  focus-dependent sequence, use foreground_steps as described in
  [temporary-control.md](temporary-control.md); do not mix two control modes in
  one script or hold input while planning the next action.
- Check summary `ok`, `stopped_at_step` and each `steps[].ok`. `performed`
  counts attempted steps, including failures. Completed actions are not
  rolled back; inspect state before retrying mutations.
- The initial coordinate click before mutations can use the stale-image guard.
  Later predicted coordinates after mutations are not guarded. Prefer selectors
  and reinspect when layout changes.

## Evidence limits

- A `capture` step optionally accepts `region=[x1,y1,x2,y2]`. Images follow the
  report in the order listed by `images`. A runtime error may add a full-client
  stop image.
- **A run_steps capture may reuse the previous script frame when a fresh grab
  fails, without marking that fallback in the report.** For evidence requiring
  current pixels, call `screenshot`, `capture_region` or `control_set` separately;
  these fail when fresh pixels are unavailable.
- A `check` step accepts `expect="changed"` or `"unchanged"`, optional `region`
  and `threshold`. It compares pixels against script start or the latest
  capture/check checkpoint, not necessarily the preceding action. A pixel
  difference does not prove the app saved data or reached the intended state.
- `snapshot`/`diff_since_snapshot` are pixel diagnostics. The returned bounding
  box can include space between disconnected changes; its center need not be
  a usable action point.
- `remember_location`/`recall_location` cache and recheck pixel locations. They
  do not identify a unique semantic control or enable canvas automation.

## Failure diagnosis

1. Inspect the returned batch report first. It includes full step results.
2. Use `history(last=20)` and, when needed, `replay_frame(seq, which="after")`
   to inspect recorded behavior. Aggregate records can say `ok=true` because
   the tool returned a report; read `script:`/`control:` step records and the
   actual report for action failures.
3. `run_steps` attempts before/after frames per executed step. `control_set`
   records click/type results as text and an after-frame for successful capture;
   do not expect before/after images for its mutations.
4. Replay images are downscaled history, not current coordinate references.
   Journals truncate arguments/results, retain five session folders under
   `%TEMP%\winauto-mcp`, and keep 300 records in memory.
5. If the target closes or is replaced, list windows and attach the correct
   current HWND. If a write succeeded but capture failed, read the control
   and obtain a fresh capture instead of replaying the write.

For optional .NET memory investigation, load the Managed heap section in
[tools.md](tools.md); it covers snapshot labels, repeated-cycle comparison,
collection side effects and interpretation limits.
