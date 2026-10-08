"""Inspect an attached window through pywinauto's UIA backend.

COM is initialized on each MCP worker. Wrappers stay inside their creating
call; only runtime IDs, never COM objects, are kept between calls.
"""
import contextlib
from pywinauto.controls.uiawrapper import UIAWrapper
from pywinauto.uia_element_info import UIAElementInfo
from pywinauto.uia_defines import NoPatternInterfaceError
import comtypes
from window_manager import get_client_rect_screen, get_window_title

INTERACTIVE_TYPES = {
    "Button": "ปุ่ม", "Edit": "ช่อง input", "Document": "เอกสาร",
    "ComboBox": "dropdown", "CheckBox": "checkbox", "RadioButton": "radio",
    "MenuItem": "เมนู", "TabItem": "แท็บ", "Hyperlink": "ลิงก์",
    "ListItem": "รายการ", "TreeItem": "รายการต้นไม้", "Slider": "slider",
}
MAX_DEPTH = 15
MAX_NODES = 3000
MAX_RESULTS = 150


@contextlib.contextmanager
def com_session():
    initialized = False
    try:
        comtypes.CoInitializeEx(0)
        initialized = True
    except OSError as exc:
        code = getattr(exc, "winerror", 0) or 0
        if code & 0xFFFFFFFF != 0x80010106:
            raise  # Only tolerate RPC_E_CHANGED_MODE (host already uses STA).
    try:
        yield
    finally:
        if initialized:
            comtypes.CoUninitialize()


def root_wrapper(hwnd):
    return UIAWrapper(UIAElementInfo(int(hwnd)))


def walk(root, max_depth=MAX_DEPTH, max_nodes=MAX_NODES, strict=False):
    """Bounded walk of this window only, including non-interactive ancestors."""
    pending = [(root, 0)]
    visited = 0
    while pending and visited < max_nodes:
        wrapper, depth = pending.pop()
        visited += 1
        yield wrapper
        try:
            children = wrapper.children()
        except (comtypes.COMError, RuntimeError):
            if strict:
                raise
            continue
        if depth >= max_depth:
            if strict and children:
                raise ValueError("UIA tree exceeds the depth limit; target uniqueness cannot be verified")
            continue
        pending.extend((child, depth + 1) for child in reversed(children))
    if strict and pending:
        raise ValueError("UIA tree exceeds the node limit; target uniqueness cannot be verified")


def pattern(wrapper, name):
    """Return a supported pattern; never substitute real input."""
    try:
        return getattr(wrapper, "iface_" + name)
    except NoPatternInterfaceError:
        return None


def runtime_id(wrapper):
    return tuple(wrapper.element_info.runtime_id)


def control_steps(wrapper):
    """Advertise capabilities for a set bound to this control, never real input."""
    steps = []
    if wrapper.is_enabled():
        if wrapper.element_info.control_type in ("Edit", "Document"):
            value = pattern(wrapper, "value")
            if value is not None:
                steps.append("click")
                if not value.CurrentIsReadOnly:
                    steps.append("type")
        elif any(pattern(wrapper, name) is not None for name in
                 ("invoke", "toggle", "selection_item", "expand_collapse")):
            steps.append("click")
    return [*steps, "capture"]


def describe(wrapper, hwnd):
    info = wrapper.element_info
    rect = wrapper.rectangle()
    left, top, _, _ = get_client_rect_screen(hwnd)
    actions = [action for action, iface in (
        ("invoke", "invoke"), ("toggle", "toggle"), ("select", "selection_item"),
        ("expand", "expand_collapse"), ("set_text", "value"),
        ("set_value", "range_value"), ("scroll", "scroll"),
    ) if pattern(wrapper, iface) is not None]
    return {
        "name": info.name or "", "automation_id": info.automation_id or "",
        "control_type": info.control_type + "Control",
        "category": INTERACTIVE_TYPES.get(info.control_type, info.control_type),
        "enabled": bool(wrapper.is_enabled()),
        "rect": [rect.left - left, rect.top - top, rect.right - left, rect.bottom - top],
        "runtime_id": list(runtime_id(wrapper)), "actions": actions,
        "control_steps": control_steps(wrapper),
    }


def get_elements(hwnd, max_depth=MAX_DEPTH, max_nodes=MAX_NODES, max_results=MAX_RESULTS):
    elements = []
    with com_session():
        for wrapper in walk(root_wrapper(hwnd), max_depth, max_nodes):
            try:
                if wrapper.element_info.control_type not in INTERACTIVE_TYPES:
                    continue
                entry = describe(wrapper, hwnd)
                x1, y1, x2, y2 = entry["rect"]
                if x2 > x1 and y2 > y1:
                    elements.append(entry)
                    if len(elements) >= max_results:
                        break
            except comtypes.COMError:
                continue
    return elements


def summarize(hwnd, elements=None):
    title = get_window_title(hwnd)
    elements = get_elements(hwnd) if elements is None else elements
    if not elements:
        return (f'หน้าต่าง "{title}" ไม่มี element ที่อ่านได้ผ่าน pywinauto UIA; '
                "ดู screenshot ได้ แต่ควบคุม canvas ด้วยเมาส์จริงไม่ได้ใน backend นี้")
    grouped = {}
    for element in elements:
        grouped.setdefault(element["category"], []).append(element)
    lines = [f'หน้าต่าง "{title}" พบ element ผ่าน pywinauto UIA:']
    for category, items in grouped.items():
        names = [item["name"] for item in items if item["name"].strip()]
        lines.append(f"- {category} ({len(items)}): " + (", ".join(names[:20]) or "(ไม่มีชื่อ)"))
    return "\n".join(lines)
