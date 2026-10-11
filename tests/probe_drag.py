"""Explicit live drag regression on an owned window; never a user's application.

Not discovered by smoke.py. Exercises the actual foreground worker/input gate,
continuous held-button motion, drop receipt, capture and mouse/focus restoration.
Keep this probe for drag regressions. WINAUTO_DRAG_PROOF optionally saves PNG.
"""
import json
import ctypes
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import win32api
import win32con
import win32gui
from test_pywinauto import TestWindow


class DragWindow(TestWindow):
    def __init__(self):
        self.drag_start = None
        self.drag_end = None
        self.moves = []
        self.dragging = False
        super().__init__()

    def _proc(self, hwnd, msg, wp, lp):
        point = ((ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value)
                 if msg in (win32con.WM_LBUTTONDOWN, win32con.WM_MOUSEMOVE, win32con.WM_LBUTTONUP) else None)
        if msg == win32con.WM_LBUTTONDOWN:
            self.drag_start = point
            self.dragging = True
            win32gui.SetCapture(hwnd)
            return 0
        if msg == win32con.WM_MOUSEMOVE and self.dragging:
            self.moves.append((point, bool(wp & win32con.MK_LBUTTON)))
            return 0
        if msg == win32con.WM_LBUTTONUP and self.dragging:
            self.drag_end = point
            self.dragging = False
            win32gui.ReleaseCapture()
            dc = win32gui.GetDC(hwnd)
            try:
                win32gui.MoveToEx(dc, *self.drag_start)
                win32gui.LineTo(dc, *point)
                win32gui.DrawText(dc, f'Drag received: {self.drag_start} -> {point}; moves={len(self.moves)}',
                                 -1, (20, 415, 640, 440), win32con.DT_SINGLELINE)
            finally:
                win32gui.ReleaseDC(hwnd, dc)
            return 0
        return super()._proc(hwnd, msg, wp, lp)


if __name__ == '__main__':
    window = DragWindow()
    try:
        win32gui.ShowWindow(window.cover, win32con.SW_HIDE)
        server.attach_window(window.hwnd, label='Owned click-drag regression')
        result = server.foreground_steps(reason='ทดสอบคลิกลากในหน้าต่างทดสอบ',
            timeout_ms=3000, allow_takeover=True, steps=[
                {'do': 'drag', 'x1': 40, 'y1': 450, 'x2': 240, 'y2': 480, 'duration_ms': 500},
                {'do': 'capture', 'region': [10, 410, 650, 500]}])
        report = json.loads(result[0])
        actual = window.call_ui(lambda: {'start': window.drag_start, 'end': window.drag_end,
            'moves': len(window.moves), 'held_during_moves': all(held for _, held in window.moves),
            'dragging': window.dragging})
        buttons_up = not any(win32api.GetAsyncKeyState(key) & 0x8000 for key in (1, 2))
        print(json.dumps({'report': report, 'actual': actual, 'buttons_up': buttons_up,
                          'image_returned': len(result) == 2}, indent=2))
        assert report['ok'], report
        assert actual['start'] == (40, 450) and actual['end'] == (240, 480), actual
        assert actual['moves'] > 2 and actual['held_during_moves'] and not actual['dragging'], actual
        assert report['mouse_buttons_released'] and buttons_up
        assert len(result) == 2
        if os.environ.get('WINAUTO_DRAG_PROOF'):
            Path(os.environ['WINAUTO_DRAG_PROOF']).write_bytes(result[1].data)
    finally:
        server.detach_window()
        window.close()
