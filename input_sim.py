"""Background UI control using pywinauto UIA patterns only.

No input injection, focus changes, global hooks, or mouse fallback.
Coordinates resolve against the attached window's own tree even if covered.
"""
import math
import threading
import time
import win32gui
import uia_inspect as uia
from window_manager import background_action, ensure_input_allowed, get_client_size, get_client_rect_screen

_targets = {}
_lock = threading.RLock()
_interaction_hook = None


def set_interaction_hook(hook):
    """Register a cosmetic observer receiving (hwnd, client_relative_rect)."""
    global _interaction_hook
    _interaction_hook = hook


def _interaction_rect(hwnd, wrapper):
    try:
        rect = wrapper.rectangle()
        left, top, right, bottom = get_client_rect_screen(hwnd)
        bounds = (max(0, rect.left - left), max(0, rect.top - top),
                  min(right - left, rect.right - left), min(bottom - top, rect.bottom - top))
        if bounds[2] > bounds[0] and bounds[3] > bounds[1]:
            return bounds
    except Exception:
        pass  # A cosmetic marker must not block an otherwise supported action.
    return None


def _show_interaction(hwnd, rect):
    if _interaction_hook is not None and rect is not None:
        try:
            _interaction_hook(hwnd, rect)
        except Exception:
            pass  # Do not invite a retry of a mutation that already succeeded.


class UnsupportedAction(ValueError):
    """The action needs real input or an unavailable UIA pattern."""


class ActionFailure(RuntimeError):
    """Mutation-aware diagnostics without control contents or automatic retries."""

    def __init__(self, cause, phase, mutation_outcome):
        super().__init__(str(cause))
        self.phase = phase
        self.mutation_outcome = mutation_outcome
        self.cause_type = type(cause).__name__


def _window_state(hwnd):
    try:
        return {"foreground_hwnd": win32gui.GetForegroundWindow(),
                "target_minimized": bool(win32gui.IsIconic(hwnd))}
    except Exception:
        return {"foreground_hwnd": None, "target_minimized": None}


def unsupported(action):
    raise UnsupportedAction(
        f"{action} is unavailable in the pywinauto UIA background backend. "
        "Nothing was sent; the mouse and foreground were not taken. "
        "Use click_element, type_text(replace=True), or set_value on a supported "
        "element from get_elements. There is no real-input fallback."
    )


def reset_target(hwnd=None):
    with _lock:
        if hwnd is None:
            _targets.clear()
        else:
            _targets.pop(hwnd, None)


def _remember(hwnd, wrapper):
    _targets[hwnd] = uia.runtime_id(wrapper)


def _find(hwnd, name=None, automation_id=None, selected=False, editable=False, target_id=None, candidates=None, scope="window"):
    if name is not None and not name.strip():
        raise ValueError("name must not be empty")
    if automation_id is not None and not automation_id.strip():
        raise ValueError("automation_id must not be empty")
    if not editable and not selected and target_id is None and name is None and automation_id is None:
        raise ValueError("provide name or automation_id from get_elements")
    matches = []
    needle = name.casefold().strip() if name is not None else None
    remembered = target_id
    if remembered is None and selected and name is None and automation_id is None:
        remembered = _targets.get(hwnd)
    if remembered is not None:
        if (not isinstance(remembered, (list, tuple)) or not remembered
                or any(type(part) is not int for part in remembered)):
            raise ValueError('runtime_id must be a nonempty array of integers from get_elements')
        remembered = tuple(remembered)
    # Runtime IDs are unique within the current desktop. Exact identity needs
    # membership in this bounded window walk, not exhaustive name uniqueness.
    # Unrelated depth/provider failures must not invalidate an observed ID.
    for wrapper in (candidates if candidates is not None else uia.walk(
            uia.root_wrapper(hwnd), strict=remembered is None, scope=scope)):
        info = wrapper.element_info
        if remembered is not None and uia.runtime_id(wrapper) != remembered:
            continue
        if automation_id is not None and info.automation_id != automation_id:
            continue
        if needle is not None and needle not in (info.name or "").casefold().strip():
            continue
        if editable and info.control_type not in ("Edit", "Document", "ComboBox"):
            continue
        if remembered is not None:
            return wrapper
        matches.append(wrapper)
    if needle is not None:
        exact = [w for w in matches if (w.element_info.name or "").casefold().strip() == needle]
        matches = exact or matches
    if not matches:
        if target_id is not None:
            raise ValueError("the bound control disappeared or was not found within the inspection limits; no replacement control was selected")
        if remembered is not None:
            raise ValueError("the selected element disappeared; select it again or supply name/automation_id")
        raise ValueError("no matching element in the attached window; check get_elements")
    if len(matches) != 1:
        raise ValueError(f"ambiguous target: {len(matches)} elements match; supply a unique automation_id or name")
    return matches[0]


def _at_point(hwnd, x, y, interactive=False):
    width, height = get_client_size(hwnd)
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError(f"({x}, {y}) is outside the {width}x{height} client area")
    from window_manager import get_client_rect_screen
    origin_x, origin_y, _, _ = get_client_rect_screen(hwnd)
    candidates = []
    for wrapper in uia.walk(uia.root_wrapper(hwnd), strict=True):
        rect = wrapper.rectangle()
        left, top = rect.left - origin_x, rect.top - origin_y
        right, bottom = rect.right - origin_x, rect.bottom - origin_y
        if left <= x < right and top <= y < bottom:
            # A label inside a WPF/WinUI button may be the smallest rectangle.
            # Resolve clicks to its enclosing control, not that Text leaf.
            if interactive and wrapper.element_info.control_type not in uia.INTERACTIVE_TYPES:
                continue
            if interactive and wrapper.element_info.control_type in ("ListItem", "TreeItem") \
                    and not any(uia.pattern(wrapper, name) is not None for name in
                                ("selection_item", "invoke", "expand_collapse", "toggle")):
                continue
            candidates.append(((right - left) * (bottom - top), wrapper))
    if not candidates:
        if interactive:
            unsupported("clicking a point without an interactive UIA control")
        raise ValueError("no UIA element at this point in the attached window")
    # Desktop.from_point() would find the covering app instead.
    # Preorder visits the child after its container; equal bounds must activate
    # that nested control rather than the ListItem's selection pattern.
    if interactive:
        return min(reversed(candidates), key=lambda item: item[0])[1]
    return min(candidates, key=lambda item: item[0])[1]


def _actionable(wrapper):
    if not wrapper.is_enabled():
        raise ValueError("target element is disabled; nothing was performed")


def _activate(hwnd, wrapper):
    _actionable(wrapper)
    rect = _interaction_rect(hwnd, wrapper)
    kind = wrapper.element_info.control_type
    if kind in ("Edit", "Document"):
        if uia.pattern(wrapper, "value") is None:
            unsupported("selecting a text control without ValuePattern")
        _remember(hwnd, wrapper)
        _show_interaction(hwnd, rect)
        return "selected text target (Windows focus unchanged)"
    toggle = uia.pattern(wrapper, "toggle")
    selection = uia.pattern(wrapper, "selection_item")
    invoke = uia.pattern(wrapper, "invoke")
    expand = uia.pattern(wrapper, "expand_collapse")
    if kind == "CheckBox" and toggle is not None:
        toggle.Toggle()
        action = "toggled"
    elif kind in ("RadioButton", "ListItem", "TreeItem", "TabItem") and selection is not None \
            and not (kind == "ListItem" and invoke is not None):
        selection.Select()
        action = "selected"
    elif invoke is not None:
        wrapper.invoke()
        action = "invoked"
    elif selection is not None:
        selection.Select()
        action = "selected"
    elif expand is not None:
        if expand.CurrentExpandCollapseState == 1:
            expand.Collapse()
            action = "collapsed"
        else:
            expand.Expand()
            action = "expanded"
    elif toggle is not None:
        toggle.Toggle()
        action = "toggled"
    else:
        unsupported(f"activating {kind}")
    _show_interaction(hwnd, rect)
    return action


def _click_options(button, double, modifiers=None):
    if button not in ("left", "right"):
        raise ValueError("button must be 'left' or 'right'")
    if button != "left" or double or modifiers:
        unsupported("right/double/modifier click")


def click_in_window(hwnd, client_x, client_y, button="left", double=False,
                    modifiers=None, keep_cursor=False):
    ensure_input_allowed(hwnd)
    _click_options(button, double, modifiers)
    with _lock, background_action(hwnd), uia.com_session():
        return _activate(hwnd, _at_point(hwnd, client_x, client_y, interactive=True))


def click_element(hwnd, name=None, automation_id=None, button="left", double=False, scope="window", runtime_id=None):
    phase, outcome = "preflight", "not_started"
    before = _window_state(hwnd)
    try:
        ensure_input_allowed(hwnd)
        _click_options(button, double)
        with _lock, background_action(hwnd), uia.com_session():
            phase = "target_resolution"
            wrapper = _find(hwnd, name, automation_id, scope=scope, target_id=runtime_id)
            phase = "description"
            desc = uia.describe(wrapper, hwnd)
            phase, outcome = "activation", "unknown; inspect target before retrying"
            action = _activate(hwnd, wrapper)
            phase, outcome = "post_action", "completed; application state not verified"
            result = {"action": action, "element": desc, "backend": "pywinauto-uia", "scope": scope}
        after = _window_state(hwnd)
        result.update(window_state_before=before, window_state_after=after,
                      foreground_changed=(before["foreground_hwnd"] != after["foreground_hwnd"]
                                          if None not in (before["foreground_hwnd"], after["foreground_hwnd"]) else None))
        return result
    except Exception as exc:
        exc.phase = phase
        exc.mutation_outcome = outcome
        exc.window_state_before = before
        exc.window_state_after = _window_state(hwnd)
        raise


def _type_into(hwnd, wrapper, text, replace=False):
    """Write to an already resolved wrapper in the caller's guarded COM session."""
    _actionable(wrapper)
    value = uia.pattern(wrapper, "value")
    if value is None:
        unsupported("text input without ValuePattern")
    if value.CurrentIsReadOnly:
        raise ValueError("target text control is read-only")
    desired = text if replace else value.CurrentValue + text
    rect = _interaction_rect(hwnd, wrapper)
    try:
        if wrapper.element_info.control_type == "Edit":
            wrapper.set_edit_text(desired)
        else:
            value.SetValue(desired)
    except Exception as exc:
        raise ActionFailure(exc, "write", "unknown; inspect target before retrying") from exc
    try:
        # Read only: never repeat the write, restore the window or select a replacement.
        deadline = time.monotonic() + 0.3
        while value.CurrentValue != desired:
            if time.monotonic() >= deadline:
                raise RuntimeError("text was set but readback differs; inspect the target before retrying; "
                                   "a minimized provider may defer updates until the user restores it")
            time.sleep(0.025)
    except Exception as exc:
        raise ActionFailure(exc, "readback", "write_returned; value_unverified; do not replay") from exc
    _remember(hwnd, wrapper)
    _show_interaction(hwnd, rect)
    return {"action": "replaced" if replace else "appended", "characters": len(text),
            "automation_id": wrapper.element_info.automation_id, "verified": True,
            "backend": "pywinauto-uia"}


def type_text(text, hwnd=None, name=None, automation_id=None, replace=False, runtime_id=None):
    if hwnd is None:
        raise ValueError("hwnd is required; background typing never uses the foreground window")
    ensure_input_allowed(hwnd)
    with _lock, background_action(hwnd), uia.com_session():
        wrapper = _find(hwnd, name, automation_id, selected=True, editable=True, target_id=runtime_id)
        return _type_into(hwnd, wrapper, text, replace)


def read_element(hwnd, name=None, automation_id=None, scope="window", runtime_id=None):
    with _lock, uia.com_session():
        wrapper = _find(hwnd, name, automation_id, scope=scope, target_id=runtime_id)
        return _read_from(hwnd, wrapper)


def _read_from(hwnd, wrapper):
    """Read a resolved control; callers own the lock and COM session."""
    result = uia.describe(wrapper, hwnd)
    value = uia.pattern(wrapper, "value")
    if value is not None:
        if not wrapper.element_info.element.CurrentIsPassword:
            result["value"] = value.CurrentValue
        result["read_only"] = bool(value.CurrentIsReadOnly)
    for iface_name, prop, key in (
        ("toggle", "CurrentToggleState", "toggle_state"),
        ("selection_item", "CurrentIsSelected", "selected"),
        ("range_value", "CurrentValue", "range_value"),
    ):
        iface = uia.pattern(wrapper, iface_name)
        if iface is not None:
            result[key] = getattr(iface, prop)
    return result


def set_value(hwnd, value, name=None, automation_id=None):
    ensure_input_allowed(hwnd)
    with _lock, background_action(hwnd), uia.com_session():
        wrapper = _find(hwnd, name, automation_id)
        return _set_range_into(hwnd, wrapper, value)


def _set_range_into(hwnd, wrapper, value):
    """Set a resolved range; callers own input permission, lock and COM session."""
    _actionable(wrapper)
    iface = uia.pattern(wrapper, "range_value")
    if iface is None:
        unsupported("setting a numeric value without RangeValuePattern")
    numeric = float(value)
    if not math.isfinite(numeric) or not iface.CurrentMinimum <= numeric <= iface.CurrentMaximum:
        raise ValueError(f"value must be between {iface.CurrentMinimum} and {iface.CurrentMaximum}")
    if iface.CurrentIsReadOnly:
        raise ValueError("target numeric control is read-only")
    rect = _interaction_rect(hwnd, wrapper)
    iface.SetValue(numeric)
    actual = iface.CurrentValue
    if actual != numeric:
        raise RuntimeError(f"value readback differs: expected {numeric}, got {actual}")
    _show_interaction(hwnd, rect)
    return {"value": actual, "verified": True, "backend": "pywinauto-uia"}


def scroll_in_window(hwnd, client_x, client_y, clicks, keep_cursor=False):
    ensure_input_allowed(hwnd)
    if not isinstance(clicks, int) or not -100 <= clicks <= 100:
        raise ValueError("clicks must be an integer between -100 and 100")
    if clicks == 0:
        return "no scroll requested"
    with _lock, background_action(hwnd), uia.com_session():
        wrapper = _at_point(hwnd, client_x, client_y)
        root_id = uia.runtime_id(uia.root_wrapper(hwnd))
        for _ in range(uia.MAX_DEPTH + 1):
            _actionable(wrapper)
            iface = uia.pattern(wrapper, "scroll")
            if iface is not None and iface.CurrentVerticallyScrollable:
                rect = _interaction_rect(hwnd, wrapper)
                wrapper.scroll("up" if clicks > 0 else "down", "line",
                               count=abs(clicks), retry_interval=0.01)
                _show_interaction(hwnd, rect)
                return "scrolled through ScrollPattern"
            if uia.runtime_id(wrapper) == root_id:
                break
            wrapper = wrapper.parent()
            if wrapper is None:
                break
        unsupported("scrolling without a vertically scrollable ScrollPattern")


def press_key(key, hwnd=None):
    if hwnd is not None:
        ensure_input_allowed(hwnd)
    unsupported(f"press_key({key!r})")


def press_keys(keys, hwnd=None):
    if hwnd is not None:
        ensure_input_allowed(hwnd)
    unsupported(f"hotkey({keys!r})")


def drag_in_window(hwnd, x1, y1, x2, y2, button="left", **kwargs):
    ensure_input_allowed(hwnd)
    unsupported("coordinate drag; use set_value for a UIA slider")


def move_to(screen_x, screen_y):
    unsupported("mouse movement/hover")
