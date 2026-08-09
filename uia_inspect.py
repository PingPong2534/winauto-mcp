"""Walk the UI Automation tree of a window to list interactive elements."""

import comtypes
import uiautomation as auto

# uiautomation uses COM (via comtypes), which requires CoInitialize on
# whichever thread calls into it. The MCP SDK dispatches each sync tool call
# onto an anyio worker thread, so this must run per-call, not just at import.
def _ensure_com():
    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # already initialized on this thread

from window_manager import get_client_rect_screen, get_window_title

# Control types worth surfacing to a caller deciding where to click/type.
INTERACTIVE_TYPES = {
    "ButtonControl": "ปุ่ม",
    "EditControl": "ช่อง input",
    "ComboBoxControl": "dropdown",
    "CheckBoxControl": "checkbox",
    "RadioButtonControl": "radio",
    "MenuItemControl": "เมนู",
    "TabItemControl": "แท็บ",
    "HyperlinkControl": "ลิงก์",
    "ListItemControl": "รายการ",
    "SliderControl": "slider",
}

MAX_DEPTH = 15
MAX_NODES = 3000
MAX_RESULTS = 150


def get_elements(hwnd, max_depth=MAX_DEPTH, max_nodes=MAX_NODES, max_results=MAX_RESULTS):
    """Return interactive elements as a list of dicts with client-relative rects.

    Returns [] if the UIA tree yields nothing useful (e.g. a canvas-rendered
    app like a game) -- callers should treat that as "fall back to vision".
    """
    _ensure_com()
    root = auto.ControlFromHandle(hwnd)
    if root is None:
        return []

    origin_left, origin_top, _, _ = get_client_rect_screen(hwnd)

    elements = []
    visited = 0

    def walk(ctrl, depth):
        nonlocal visited
        if depth > max_depth or visited > max_nodes or len(elements) >= max_results:
            return
        visited += 1
        try:
            control_type = ctrl.ControlTypeName
            if control_type in INTERACTIVE_TYPES:
                rect = ctrl.BoundingRectangle
                if rect.width() > 0 and rect.height() > 0:
                    elements.append(
                        {
                            "name": ctrl.Name or "",
                            "control_type": control_type,
                            "category": INTERACTIVE_TYPES[control_type],
                            "enabled": bool(ctrl.IsEnabled),
                            # client-relative, matches capture_screen's image pixel space
                            "rect": [
                                rect.left - origin_left,
                                rect.top - origin_top,
                                rect.right - origin_left,
                                rect.bottom - origin_top,
                            ],
                        }
                    )
        except Exception:
            pass

        try:
            children = ctrl.GetChildren()
        except Exception:
            return
        for child in children:
            if visited > max_nodes or len(elements) >= max_results:
                break
            walk(child, depth + 1)

    walk(root, 0)
    return elements


def summarize(hwnd, elements=None):
    """Human-readable Thai summary grouped by control category."""
    title = get_window_title(hwnd)
    if elements is None:
        elements = get_elements(hwnd)

    if not elements:
        return (
            f'กำลังแสดงหน้าต่าง "{title}" แต่ไม่พบ element ที่อ่านได้ผ่าน UI Automation '
            "(อาจเป็น canvas/custom-render เช่นเกม) — ใช้ภาพ screenshot แทนเพื่อดูตำแหน่งเอง"
        )

    by_category = {}
    for el in elements:
        by_category.setdefault(el["category"], []).append(el)

    lines = [f'กำลังแสดงหน้าต่าง "{title}" พบ element ดังนี้:']
    for category, items in by_category.items():
        names = [it["name"] for it in items if it["name"].strip()]
        if names:
            lines.append(f"- {category} ({len(items)}): " + ", ".join(names[:20]))
        else:
            lines.append(f"- {category} ({len(items)}): (ไม่มีชื่อ)")
    return "\n".join(lines)
