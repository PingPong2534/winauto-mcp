"""Maintained native probe for issue #9/#11; creates and controls only its fixture.

Not discovered by smoke.py. Uses the real foreground worker, bounded hook gate,
modifier gestures, scan-code keyboard intervals, capture and cleanup reports.
"""
import ctypes
import faulthandler
import json
from pathlib import Path
import sys
import tempfile
import time

faulthandler.enable()
faulthandler.dump_traceback_later(20)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import win32api
import win32con
import win32gui
from test_pywinauto import TestWindow


class InputWindow(TestWindow):
    def __init__(self):
        self.pointer = []
        self.keys = []
        self.dragging = False
        super().__init__()

    def _proc(self, hwnd, msg, wp, lp):
        if msg in (win32con.WM_LBUTTONDOWN, win32con.WM_LBUTTONUP):
            self.pointer.append({'message': msg, 'shift': bool(wp & win32con.MK_SHIFT),
                                 'ctrl': bool(wp & win32con.MK_CONTROL)})
            if msg == win32con.WM_LBUTTONDOWN:
                win32gui.SetFocus(hwnd)
                self.dragging = True
                win32gui.SetCapture(hwnd)
            else:
                self.dragging = False
                win32gui.ReleaseCapture()
            return 0
        if msg == win32con.WM_MOUSEMOVE and self.dragging:
            self.pointer.append({'message': msg, 'shift': bool(wp & win32con.MK_SHIFT),
                                 'ctrl': bool(wp & win32con.MK_CONTROL)})
            return 0
        if msg in (win32con.WM_KEYDOWN, win32con.WM_KEYUP):
            self.keys.append({'message': msg, 'vk': wp, 'scan': (lp >> 16) & 255,
                              'time': time.monotonic()})
            return 0
        return super()._proc(hwnd, msg, wp, lp)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='winauto-modifier-proof-') as journal_dir:
        server.journal.ROOT = journal_dir
        window = InputWindow()
        try:
            # Keep the proof crop in the primary monitor's left strip, away
            # from unrelated full-size app overlays in this shared desktop.
            window.call_ui(lambda: win32gui.SetWindowPos(window.hwnd, 0, 0, 100, 740, 560,
                win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER))
            win32gui.ShowWindow(window.cover, win32con.SW_HIDE)
            server.attach_window(window.hwnd, label='Owned modifier and key regression')
            result = server.foreground_steps(reason='ทดสอบ modifier และปุ่มในหน้าต่างทดสอบ',
                timeout_ms=3000, allow_takeover=True, steps=[
                    {'do': 'click', 'x': 40, 'y': 450, 'modifiers': ['shift']},
                    {'do': 'drag', 'x1': 40, 'y1': 450, 'x2': 240, 'y2': 480,
                     'duration_ms': 200, 'modifiers': ['ctrl', 'shift']},
                    {'do': 'key', 'keys': ['enter'], 'hold_ms': 120},
                    {'do': 'key', 'keys': ['escape'], 'hold_ms': 120},
                    {'do': 'capture', 'region': [10, 410, 200, 500]}])
            report = json.loads(result[0])
            actual = window.call_ui(lambda: {'pointer': window.pointer, 'keys': window.keys})
            keys_up = not any(win32api.GetAsyncKeyState(key) & 0x8000 for key in (1, 2, 13, 16, 17, 18, 27))
            print(json.dumps({'report': report, 'actual': actual,
                              'inputs_up': keys_up, 'image_returned': len(result) == 2}, indent=2))
            assert report['ok'], report
            assert report['mouse_buttons_released'] and report['keys_released'] and keys_up
            assert len(result) == 2
            pointer = actual['pointer']
            assert len(pointer) > 5 and all(p['shift'] for p in pointer), pointer
            assert all(p['ctrl'] for p in pointer[2:]), pointer
            for vk, scan in ((13, 0x1C), (27, 1)):
                events = [k for k in actual['keys'] if k['vk'] == vk]
                assert [k['message'] for k in events] == [win32con.WM_KEYDOWN, win32con.WM_KEYUP], events
                assert all(k['scan'] == scan for k in events), events
                assert events[1]['time'] - events[0]['time'] >= 0.09, events
        finally:
            server.detach_window()
            window.close()
            faulthandler.cancel_dump_traceback_later()
