"""Control-scoped UIA steps with identity binding and a final control-only crop."""

import time

import input_sim
import journal
import screenshot
import uia_inspect as uia
import window_manager


MAX_CONTROL_STEPS = 20
_FIELDS = {"click": {"do"}, "type": {"do", "text", "replace"}, "capture": {"do"}}


def _validate(steps, delay_ms, expected_control_type):
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_CONTROL_STEPS:
        raise ValueError("steps must contain 1–20 control step objects")
    if isinstance(delay_ms, bool) or not isinstance(delay_ms, int) or not 0 <= delay_ms <= 3000:
        raise ValueError("delay_ms must be an integer between 0 and 3000")
    if expected_control_type is not None and (
            not isinstance(expected_control_type, str) or not expected_control_type.strip()):
        raise ValueError("expected_control_type must be a non-empty control_type from get_elements")
    plan = []
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise ValueError(f"step {i} must be an object")
        kind = step.get("do")
        if not isinstance(kind, str) or kind not in _FIELDS:
            raise ValueError(f"step {i}: do must be click, type or capture")
        extra = step.keys() - _FIELDS[kind]
        if extra:
            raise ValueError(f"step {i}: unsupported fields {sorted(extra)}; all steps use the bound control")
        if kind == "type":
            if not isinstance(step.get("text"), str):
                raise ValueError(f"step {i}: text must be a string")
            if not isinstance(step.get("replace", False), bool):
                raise ValueError(f"step {i}: replace must be a boolean")
        if kind == "capture" and i != len(steps):
            raise ValueError("capture must appear exactly once, as the final step")
        plan.append(dict(step))
    if plan[-1]["do"] != "capture":
        raise ValueError("the final step must be capture")
    return plan


def _region(bounds, size):
    width, height = size
    left, top, right, bottom = bounds
    rect = (max(0, left), max(0, top), min(width, right), min(height, bottom))
    if right <= left or bottom <= top or rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise ValueError("the control has no capturable rectangle inside the attached client area")
    return rect


def _supports(wrapper, kind):
    if kind != "capture":
        input_sim._actionable(wrapper)
    if kind not in uia.control_steps(wrapper):
        if kind == "type":
            value = uia.pattern(wrapper, "value")
            if value is not None and value.CurrentIsReadOnly:
                raise ValueError("target text control is read-only")
        input_sim.unsupported(f"control step {kind} on {wrapper.element_info.control_type}")


def _capture(hwnd, target_id):
    wrapper = input_sim._find(hwnd, target_id=target_id)
    before = uia.describe(wrapper, hwnd)
    client = window_manager.get_client_rect_screen(hwnd)
    frame = screenshot.grab_window(hwnd)
    # Do not associate pixels with a box that moved or a replacement field.
    wrapper = input_sim._find(hwnd, target_id=target_id)
    after = uia.describe(wrapper, hwnd)
    if (window_manager.get_client_rect_screen(hwnd) != client
            or frame.size != (client[2] - client[0], client[3] - client[1])
            or before["rect"] != after["rect"]
            or before["control_type"] != after["control_type"]):
        raise ValueError("control/window geometry changed during capture; no image returned")
    rect = _region(after["rect"], frame.size)
    return frame, rect, after


def run(hwnd, steps, name=None, automation_id=None, expected_control_type=None, delay_ms=120):
    """Return (report, full_internal_frame, crop_rect); no full image on failure."""
    plan = _validate(steps, delay_ms, expected_control_type)
    mutates = any(step["do"] != "capture" for step in plan)
    if mutates:
        window_manager.ensure_input_allowed(hwnd)
    with input_sim._lock, uia.com_session():
        wrapper = input_sim._find(hwnd, name, automation_id)
        element = uia.describe(wrapper, hwnd)
        if expected_control_type is not None and element["control_type"] != expected_control_type:
            raise ValueError(f"expected {expected_control_type}, found {element['control_type']}; nothing performed")
        for step in plan:
            _supports(wrapper, step["do"])
        _region(element["rect"], window_manager.get_client_size(hwnd))
        target_id = uia.runtime_id(wrapper)
        if not target_id:
            raise ValueError("the control has no usable runtime ID; cannot bind a control set")
        initial_type = element["control_type"]
        title = window_manager.get_window_title(hwnd)
        report = {"ok": True, "element": element, "completed_steps": 0,
                  "of": len(plan), "stopped_at_step": None, "steps": [], "capture": None,
                  "backend": "pywinauto-uia"}
        frame = rect = None
        for i, step in enumerate(plan, 1):
            kind = step["do"]
            entry = {"step": i, "do": kind, "ok": True}
            started = time.monotonic()
            try:
                wrapper = input_sim._find(hwnd, target_id=target_id)
                if wrapper.element_info.control_type + "Control" != initial_type:
                    raise ValueError("the bound control changed type; no replacement selected")
                _supports(wrapper, kind)
                if kind == "capture":
                    frame, rect, current = _capture(hwnd, target_id)
                    report["element"] = current
                    entry["result"] = {"region": list(rect),
                                       "crop_size": [rect[2] - rect[0], rect[3] - rect[1]],
                                       "coordinate_offset": list(rect[:2])}
                    report["capture"] = entry["result"]
                else:
                    window_manager.ensure_input_allowed(hwnd)
                    with window_manager.background_action(hwnd):
                        if kind == "click":
                            entry["result"] = {"action": input_sim._activate(hwnd, wrapper)}
                        else:
                            entry["result"] = input_sim._type_into(
                                hwnd, wrapper, step["text"], step.get("replace", False))
                    if delay_ms:
                        time.sleep(delay_ms / 1000)
                report["completed_steps"] += 1
            except Exception as exc:
                entry.update(ok=False, result=f"{type(exc).__name__}: {exc}")
                report.update(ok=False, stopped_at_step=i,
                              note="Completed steps are not rolled back. Inspect them before retrying writes.")
                frame = rect = None
            entry["ms"] = round((time.monotonic() - started) * 1000)
            journal.record(f"control:{kind}",
                           args={"step": i, "runtime_id": list(target_id), **step},
                           ok=entry["ok"], result=entry["result"], ms=entry["ms"],
                           window=title, after=frame if kind == "capture" else None)
            report["steps"].append(entry)
            if not entry["ok"]:
                break
        return report, frame, rect
