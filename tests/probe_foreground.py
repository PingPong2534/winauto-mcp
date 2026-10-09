"""Explicit live takeover probe: at most 3 seconds, owned windows only.

Not discovered by smoke.py. Run manually to test this host's input-gate/focus
capability. A refusal is reported honestly rather than bypassed with unlocked input.
"""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import win32con
import win32gui
from test_pywinauto import TestWindow


class SuggestionWindow(TestWindow):
    """Owned transient suggestion, shown on focused edit and hidden on focus loss."""
    def __init__(self):
        self.popup = None
        self.suggestions_shown = 0
        super().__init__()

    def _proc(self, hwnd, msg, wp, lp):
        if msg == win32con.WM_COMMAND and (wp & 0xFFFF) == 101 and (wp >> 16) == win32con.EN_CHANGE:
            if win32gui.GetFocus() == self.controls.get(101):
                if not self.popup:
                    x, y = win32gui.ClientToScreen(hwnd, (10, 55))
                    self.popup = win32gui.CreateWindowEx(win32con.WS_EX_NOACTIVATE, 'STATIC',
                        'Suggestion: selected customer', win32con.WS_POPUP | win32con.WS_BORDER,
                        x, y, 280, 35, hwnd, 0, 0, None)
                win32gui.ShowWindow(self.popup, win32con.SW_SHOWNOACTIVATE)
                self.suggestions_shown += 1
        if msg == win32con.WM_ACTIVATE and (wp & 0xFFFF) == win32con.WA_INACTIVE and self.popup:
            win32gui.ShowWindow(self.popup, win32con.SW_HIDE)
        return super()._proc(hwnd, msg, wp, lp)


if __name__ == '__main__':
    window = SuggestionWindow()
    try:
        win32gui.ShowWindow(window.cover, win32con.SW_HIDE)
        server.attach_window(window.hwnd, label='Owned temporary-control test')
        initial_top = window.call_ui(lambda: win32gui.SendMessage(window.controls[401], win32con.LB_GETTOPINDEX, 0, 0))
        result = server.foreground_steps(reason='ทดสอบ textbox ในหน้าต่างทดสอบ',
            timeout_ms=3000, allow_takeover=True, steps=[
                {'do': 'click', 'x': 80, 'y': 35},
                {'do': 'type', 'text': 'ไทย🙂X', 'replace': True},
                {'do': 'key', 'keys': ['backspace']},
                {'do': 'hover', 'x': 60, 'y': 120, 'dwell_ms': 100},
                {'do': 'scroll', 'x': 400, 'y': 100, 'clicks': -1},
                {'do': 'capture', 'region': [10, 20, 290, 100]}])
        report = json.loads(result[0])
        actual = json.loads(server.read_element(automation_id='101'))
        final_top = window.call_ui(lambda: win32gui.SendMessage(window.controls[401], win32con.LB_GETTOPINDEX, 0, 0))
        popup_visible = bool(window.popup and win32gui.IsWindowVisible(window.popup))
        proof = os.environ.get('WINAUTO_FOREGROUND_PROOF')
        if report['ok'] and proof:
            Path(proof).write_bytes(result[1].data)
        print(json.dumps({'report': report, 'textbox_readback': actual,
                          'image_returned': len(result) == 2,
                          'list_top_index': [initial_top, final_top],
                          'suggestions_shown_while_focused': window.suggestions_shown,
                          'suggestion_visible_after_release': popup_visible}, ensure_ascii=True, indent=2))
        if report['ok'] and actual.get('value') != 'ไทย🙂':
            raise AssertionError('real input did not produce expected textbox value')
        if report['ok'] and (final_top <= initial_top or window.suggestions_shown == 0):
            raise AssertionError('scroll or focused suggestion behavior was not exercised')
    finally:
        server.detach_window()
        window.close()
