"""Sequential UIA plans with immutable observed-image guards and state checks.

Semantic-only plans capture no pixels on success. No physical-input fallback.
"""
import gc
import math
import time

import input_sim
import journal
import screenshot
import uia_inspect as uia
import visual_guard
import window_manager


KINDS = {"read", "click_element", "type", "set_value", "click"}
STATE_KEYS = {"name", "automation_id", "control_type", "enabled", "value", "read_only",
              "toggle_state", "selected", "range_value", "rect", "runtime_id"}


class StopPlan(ValueError):
    def __init__(self, status, message, **details):
        super().__init__(message)
        self.status, self.details = status, details


def _integer(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}")


def validate(steps, vision_backend, timeout_ms, capture_final):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 40:
        raise ValueError("steps must contain 1-40 objects")
    if vision_backend not in ("auto", "pillow", "opencv"):
        raise ValueError("vision_backend must be auto, pillow or opencv")
    if vision_backend == "opencv" and visual_guard.cv2 is None:
        raise ValueError("OpenCV unavailable; install runtime dependencies or select pillow")
    _integer(timeout_ms, 1, 30000, "timeout_ms")
    if not isinstance(capture_final, bool):
        raise ValueError("capture_final must be a boolean")
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict) or step.get("do") not in KINDS:
            raise ValueError(f"step {i}: do must be one of {sorted(KINDS)}")
        allowed = {"do", "name", "automation_id", "before", "expect", "wait_ms", "guard"}
        allowed |= {"text", "replace"} if step["do"] == "type" else set()
        allowed |= {"value"} if step["do"] == "set_value" else set()
        allowed |= {"x", "y"} if step["do"] == "click" else set()
        if step.keys() - allowed:
            raise ValueError(f"step {i}: unsupported fields {sorted(step.keys() - allowed)}")
        for key in ("name", "automation_id"):
            if key in step and (not isinstance(step[key], str) or not step[key].strip()):
                raise ValueError(f"step {i}: {key} must be nonempty")
        if step["do"] == "click":
            for key in ("x", "y"):
                _integer(step.get(key), 0, 100000, key)
            if "guard" not in step:
                raise ValueError(f"step {i}: coordinate clicks require a guard region")
            if "name" in step or "automation_id" in step:
                raise ValueError("coordinate clicks do not accept selectors; use click_element")
        elif not (step.get("name") or step.get("automation_id")):
            raise ValueError(f"step {i}: explicit name or automation_id required")
        if step["do"] == "type":
            if not isinstance(step.get("text"), str) or not isinstance(step.get("replace", False), bool):
                raise ValueError(f"step {i}: text must be a string; replace must be boolean")
        if step["do"] == "set_value":
            value = step.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"step {i}: value must be finite numeric")
        for key in ("before", "expect"):
            if key in step:
                state = step[key]
                if not isinstance(state, dict) or not state or state.keys() - STATE_KEYS:
                    raise ValueError(f"step {i}: {key} requires a nonempty subset of {sorted(STATE_KEYS)}")
                for prop, value in state.items():
                    if prop in ("enabled", "read_only", "selected"):
                        valid = isinstance(value, bool)
                    elif prop == "toggle_state":
                        valid = type(value) is int and 0 <= value <= 2
                    elif prop == "range_value":
                        valid = type(value) in (int, float) and math.isfinite(value)
                    elif prop in ("rect", "runtime_id"):
                        valid = isinstance(value, list) and bool(value) and all(type(n) is int for n in value)
                        valid = valid and (prop != "rect" or len(value) == 4)
                    else:
                        valid = isinstance(value, str)
                    if not valid:
                        raise ValueError(f"step {i}: invalid {key}.{prop}")
        _integer(step.get("wait_ms", 0), 0, 5000, "wait_ms")
        if step.get("wait_ms") and "expect" not in step:
            raise ValueError("wait_ms requires expect; it polls readback without replaying the action")
        if "guard" in step:
            region = visual_guard.validate(step["guard"])
            if step["do"] == "click" and not (
                    region[0] <= step["x"] < region[2] and region[1] <= step["y"] < region[3]):
                raise ValueError("coordinate click must be inside its guard region")
            if step["do"] == "click" and any(
                    box[0] <= step["x"] < box[2] and box[1] <= step["y"] < box[3]
                    for box in step["guard"].get("ignore_regions", [])):
                raise ValueError("ignore_regions cannot exclude the coordinate target")


def _matches(state, expected):
    return all(key in state and state[key] == value for key, value in expected.items())


def _supports(wrapper, step):
    kind = step["do"]
    if kind == "read":
        return
    input_sim._actionable(wrapper)
    if kind in ("click", "click_element"):
        if "click" not in uia.control_steps(wrapper):
            input_sim.unsupported("guarded click without an actionable UIA pattern")
    elif kind == "type":
        value = uia.pattern(wrapper, "value")
        if value is None or value.CurrentIsReadOnly:
            raise ValueError("text target requires writable ValuePattern")
    elif kind == "set_value":
        value = uia.pattern(wrapper, "range_value")
        if value is None or value.CurrentIsReadOnly:
            raise ValueError("numeric target requires writable RangeValuePattern")
        if not value.CurrentMinimum <= step["value"] <= value.CurrentMaximum:
            raise ValueError("numeric value outside the control range")


def _bound_state(hwnd, binding, root_id):
    """Revalidate call-local UIA interfaces without rescanning the whole tree.

Properties stay uncached, identity and current ancestry must still match. Never
fall back to name/ID selection if the bound element disappears or moves away.
"""
    identity, control_type, initial_rect, wrapper = binding
    if uia.runtime_id(wrapper) != identity:
        raise StopPlan("target_changed", "Bound control identity changed")
    node = wrapper
    for _ in range(uia.MAX_DEPTH + 1):
        if node is None:
            break
        if uia.runtime_id(node) == root_id:
            return wrapper, input_sim._read_from(hwnd, wrapper)
        node = node.parent()
    raise StopPlan("target_changed", "Bound control left the attached window")


def run(hwnd, steps, seen, vision_backend="auto", timeout_ms=10000, capture_final=False):
    """Release cyclic COM garbage on the calling apartment, not another thread.

The owned-window regression exposed a deadlock when Python GC finalized UIA
interfaces on the provider's message-pump thread during a synchronous UIA call.
Collect after _run's call-local interfaces leave scope, while COM is active.
"""
    with input_sim._lock, uia.com_session():
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            return _run(hwnd, steps, seen, vision_backend, timeout_ms, capture_final)
        finally:
            try:
                gc.collect()
            finally:
                if was_enabled:
                    gc.enable()


def _run(hwnd, steps, seen, vision_backend, timeout_ms, capture_final):
    """Return report, optional fresh evidence frame and optional evidence rect."""
    validate(steps, vision_backend, timeout_ms, capture_final)
    started = time.monotonic()
    deadline = started + timeout_ms / 1000
    report = {"ok": False, "status": "preflight", "completed_steps": 0,
              "completed_actions": 0, "stopped_at_step": None, "steps": [],
              "backend": "pywinauto-uia", "capture_count": 0,
              "note": "Completed actions are not rolled back. An unknown action outcome must be inspected before retry."}
    bindings, references = [], {}
    frame = evidence_rect = None
    current_step = None
    phase = "preflight"

    def check_window():
        if time.monotonic() >= deadline:
            raise StopPlan("timeout", "Batch deadline reached; inspect before continuing")
        if not window_manager.window_exists(hwnd) or window_manager.get_pid(hwnd) != pid:
            raise StopPlan("target_changed", "Attached window closed or changed process")
        if window_manager.get_client_size(hwnd) != size:
            raise StopPlan("geometry_changed", "Client size changed; capture_screen and replan")

    def grab():
        if not window_manager.window_exists(hwnd) or window_manager.get_pid(hwnd) != pid:
            raise StopPlan("target_changed", "Cannot capture a closed/replaced window")
        report["capture_count"] += 1
        try:
            return screenshot.grab_window(hwnd)  # never reuse stale pixels
        except Exception as exc:
            raise StopPlan("capture_unavailable", f"Fresh target capture unavailable: {exc}") from exc

    try:
        pid, size = window_manager.get_pid(hwnd), window_manager.get_client_size(hwnd)
        if any(step["do"] != "read" for step in steps):
            window_manager.ensure_input_allowed(hwnd)
        with input_sim._lock, uia.com_session():
            # All existing targets bind before any mutation. Future dialogs need a new plan.
            # Share one complete traversal within preflight, never across tool calls.
            root = uia.root_wrapper(hwnd)
            root_id = uia.runtime_id(root)
            if not root_id:
                raise ValueError("attached UIA root has no usable runtime ID")
            candidates = list(uia.walk(root, strict=True))
            for i, step in enumerate(steps, 1):
                current_step = i
                check_window()
                wrapper = (input_sim._at_point(hwnd, step["x"], step["y"], interactive=True)
                           if step["do"] == "click" else
                           input_sim._find(hwnd, step.get("name"), step.get("automation_id"), candidates=candidates))
                desc = uia.describe(wrapper, hwnd)
                _supports(wrapper, step)
                identity = uia.runtime_id(wrapper)
                if not identity:
                    raise ValueError("control has no usable runtime ID")
                wrapper.element_info.set_cache_strategy(False)
                bindings.append((identity, desc["control_type"], desc["rect"], wrapper))
                if "guard" in step:
                    region = tuple(step["guard"]["region"])
                    view = next((v for v in reversed(seen)
                                 if v["frame"].size == size and visual_guard.covers(v["rect"], region)), None)
                    if view is None:
                        raise StopPlan("baseline_unavailable", "Guard region has no observed baseline; capture_screen first")
                    # Never replace this reference with intermediate unchecked frames.
                    references[i] = view["frame"]

            phase = "execution"
            for i, step in enumerate(steps, 1):
                current_step = i
                entry = {"step": i, "do": step["do"], "ok": False, "action_outcome": "not_attempted"}
                report["steps"].append(entry)
                tick = time.monotonic()
                frame = evidence_rect = None
                try:
                    check_window()
                    identity, control_type, initial_rect, _ = bindings[i - 1]

                    def resolve():
                        item, state = _bound_state(hwnd, bindings[i - 1], root_id)
                        if state["control_type"] != control_type:
                            raise StopPlan("target_changed", "Bound control changed type")
                        if step["do"] == "click" and state["rect"] != initial_rect:
                            raise StopPlan("target_changed", "Coordinate target moved; capture_screen and replan")
                        return item, state

                    wrapper, state = resolve()
                    _supports(wrapper, step)
                    if not _matches(state, step.get("before", {})):
                        raise StopPlan("state_mismatch", "UIA precondition differs; inspect read_element", actual=state)
                    if "guard" in step:
                        geometry = window_manager.get_client_rect_screen(hwnd)
                        frame = grab()
                        check_window()
                        if window_manager.get_client_rect_screen(hwnd) != geometry or frame.size != size:
                            raise StopPlan("geometry_changed", "Window moved/resized during capture; inspect again")
                        metrics = visual_guard.compare(references[i], frame, step["guard"], vision_backend)
                        entry["guard"] = metrics
                        evidence_rect = tuple(step["guard"]["region"])
                        if not metrics["unchanged"]:
                            raise StopPlan("area_changed", "Next action area changed; capture_screen before replanning", **metrics)
                        # UIA state can change while PrintWindow is running.
                        wrapper, state = resolve()
                        _supports(wrapper, step)
                        if not _matches(state, step.get("before", {})):
                            raise StopPlan("state_mismatch", "UIA precondition changed during capture", actual=state)
                    check_window()
                    if step["do"] == "read":
                        entry["result"] = state
                    else:
                        window_manager.ensure_input_allowed(hwnd)
                        frame = evidence_rect = None
                        entry["action_outcome"] = "unknown"
                        with window_manager.background_action(hwnd):
                            if step["do"] in ("click", "click_element"):
                                entry["result"] = input_sim._activate(hwnd, wrapper)
                            elif step["do"] == "type":
                                entry["result"] = input_sim._type_into(hwnd, wrapper, step["text"], step.get("replace", False))
                            else:
                                entry["result"] = input_sim._set_range_into(hwnd, wrapper, step["value"])
                        entry["action_outcome"] = "performed"
                        report["completed_actions"] += 1
                        # The guard frame predates this action and cannot serve as stop evidence.
                        frame = evidence_rect = None
                    if "expect" in step:
                        until = min(deadline, time.monotonic() + step.get("wait_ms", 0) / 1000)
                        while True:
                            check_window()
                            _, actual = resolve()
                            if _matches(actual, step["expect"]):
                                entry["verified"] = True
                                entry["state"] = actual
                                break
                            if time.monotonic() >= until:
                                raise StopPlan("expectation_failed", "Expected UIA state not observed; action is not replayed", actual=actual)
                            time.sleep(min(0.05, max(0, until - time.monotonic())))
                    entry["ok"] = True
                    report["completed_steps"] += 1
                except Exception as exc:
                    entry["error"] = str(exc)
                    raise
                finally:
                    entry["ms"] = round((time.monotonic() - tick) * 1000)
                    journal.record("guarded:" + step["do"], args={"step": i, **step},
                                   ok=entry["ok"], result=entry, ms=entry["ms"],
                                   window=window_manager.get_window_title(hwnd))
        report.update(ok=True, status="completed")
        frame = evidence_rect = None
        if capture_final:
            phase = "capture"
            frame = grab()
            if frame.size != size or window_manager.get_client_size(hwnd) != size:
                frame = None
                raise StopPlan("geometry_changed", "Client size changed during final capture")
    except Exception as exc:
        status = exc.status if isinstance(exc, StopPlan) else ("preflight_failed" if phase == "preflight" else "step_failed")
        report.update(ok=False, status=status, phase=phase, stopped_at_step=None if phase == "capture" else current_step,
                      error=str(exc), recommended_tool="capture_screen" if status in {
                          "area_changed", "geometry_changed", "baseline_unavailable", "capture_unavailable"} else "get_elements/read_element")
        if isinstance(exc, StopPlan):
            report["details"] = exc.details
        if report["steps"] and phase == "execution":
            report["steps"][-1]["error"] = str(exc)
        # Fresh stop evidence is optional. Never substitute a cached frame on failure.
        if frame is None:
            try:
                frame = grab()
                evidence_rect = None
            except Exception as capture_error:
                report["capture_error"] = str(capture_error)
        if frame is not None and evidence_rect is not None and not visual_guard.covers((0, 0, *frame.size), evidence_rect):
            evidence_rect = None
    report["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    if frame is not None:
        rect = evidence_rect or (0, 0, *frame.size)
        report["capture"] = {"fresh": True, "region": list(rect), "coordinate_offset": list(rect[:2])}
    return report, frame, evidence_rect
