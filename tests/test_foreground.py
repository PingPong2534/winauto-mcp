"""Bounded takeover lifecycle, native indicator and watchdog tests.

Default discovery never blocks human input or changes foreground focus.
The separately invoked live probe uses only owned windows.
"""
import copy
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import foreground_control as control
import win32api
import win32con
import win32gui
from takeover_indicator import ScreenIndicator
from input_gate import InputGate, _KeyEvent, _MouseEvent
from test_pywinauto import TestWindow


class ForegroundTests(unittest.TestCase):
    def test_gate_suppresses_human_allows_only_tagged_injection_and_escape_cancels(self):
        gate = InputGate(123456)
        self.assertEqual(gate.decision(0, 0x100, _KeyEvent(vkCode=65), True), 1)
        self.assertEqual(gate.decision(0, 0x200, _MouseEvent(), False), 1)
        self.assertIsNone(gate.decision(-1, 0x100, _KeyEvent(), True))
        self.assertIsNone(gate.decision(0, 0x100, _KeyEvent(flags=0x10, dwExtraInfo=123456), True))
        self.assertIsNone(gate.decision(0, 0x200, _MouseEvent(flags=1, dwExtraInfo=123456), False))
        self.assertEqual(gate.decision(0, 0x100, _KeyEvent(flags=0x10, dwExtraInfo=654321), True), 1)
        # Injected Escape does not cancel; a physical Escape does.
        self.assertIsNone(gate.decision(0, 0x100, _KeyEvent(vkCode=27, flags=0x10, dwExtraInfo=123456), True))
        self.assertFalse(gate.cancelled.is_set())
        self.assertEqual(gate.decision(0, 0x100, _KeyEvent(vkCode=27), True), 1)
        self.assertTrue(gate.cancelled.is_set())
        self.assertIsNone(gate.decision(0, 0x100, _KeyEvent(vkCode=65), True))
        with self.assertRaisesRegex(RuntimeError, 'Escape'):
            gate.check()

    def test_unresponsive_gate_refuses_more_input(self):
        gate = InputGate(123456)
        gate.active = True
        gate.last_pump = time.monotonic() - 1
        with self.assertRaisesRegex(RuntimeError, 'message pump'):
            gate.check()

    def request(self):
        return {'reason': 'Test suggestion box', 'timeout_ms': 3000, 'folder': '.',
                'steps': [{'do': 'click', 'x': 25, 'y': 30}, {'do': 'type', 'text': 'ไทย🙂'},
                          {'do': 'key', 'keys': ['down']}, {'do': 'key', 'keys': ['enter']}]}

    def execute(self, native=None, indicator=None):
        native, indicator = native or Mock(), indicator or Mock()
        snapshots = []
        result = control.execute(self.request(), native, indicator,
                                 lambda r: snapshots.append(copy.deepcopy(r)))
        return result, native, indicator, snapshots

    def test_explicit_opt_in_and_entire_batch_validation(self):
        base = self.request()['steps']
        for steps, reason, ms, opt in ((base, 'test', 3000, False), (base, '', 3000, True),
                (base, 'test', 8001, True), ([{'do': 'capture'}, {'do': 'key', 'keys': ['enter']}], 'test', 3000, True),
                ([{'do': 'wait', 'ms': 1500}, {'do': 'wait', 'ms': 1500}], 'test', 3000, True),
                ([{'do': 'key', 'keys': ['win']}], 'test', 3000, True),
                ([{'do': 'type', 'text': 'a', 'replace': 'yes'}], 'test', 3000, True)):
            with self.subTest(steps=steps, reason=reason, ms=ms, opt=opt), self.assertRaises(ValueError):
                control.validate(steps, reason, ms, opt)
        control.validate(base, 'suggestion box', 3000, True)

    def test_announcement_and_lock_precede_focus_and_input(self):
        calls = Mock()
        native, indicator = Mock(), Mock()
        native.restore.return_value = {'focus_restored': True, 'cursor_restored': True}
        calls.attach_mock(native, 'native')
        calls.attach_mock(indicator, 'indicator')
        report, _, _, _ = self.execute(native, indicator)
        names = [c[0] for c in calls.mock_calls]
        self.assertLess(names.index('indicator.show'), names.index('native.block'))
        self.assertLess(names.index('indicator.wait'), names.index('native.block'))
        self.assertLess(names.index('native.block'), names.index('native.activate'))
        self.assertLess(names.index('native.restore'), names.index('native.unblock'))
        self.assertLess(names.index('native.unblock'), names.index('indicator.close'))
        self.assertEqual(native.activate.call_count, 1)  # no focus restoration between steps
        self.assertEqual(report['completed_steps'], 4)
        self.assertTrue(report['ok'])
        self.assertFalse(report['input_blocked'])

    def test_indicator_failure_or_block_refusal_never_takes_focus(self):
        for failure in ('show', 'block'):
            native, indicator = Mock(), Mock()
            (indicator.show if failure == 'show' else native.block).side_effect = PermissionError('refused')
            report, _, _, _ = self.execute(native, indicator)
            self.assertFalse(report['ok'])
            native.activate.assert_not_called()
            native.text.assert_not_called()
            native.restore.assert_not_called()
            native.unblock.assert_not_called()
            indicator.close.assert_called_once()

    def test_partial_failure_preserves_progress_and_releases_even_if_restore_fails(self):
        native = Mock()
        native.text.side_effect = RuntimeError('provider failed')
        native.restore.side_effect = RuntimeError('old window closed')
        report, _, _, snapshots = self.execute(native)
        self.assertFalse(report['ok'])
        self.assertEqual(report['completed_steps'], 1)
        self.assertEqual(report['stopped_at_step'], 1)
        self.assertEqual(snapshots[-2]['stopped_at_step'], 1)
        self.assertIn('restore_error', report)
        native.unblock.assert_called_once()
        native.key.assert_not_called()
        self.assertEqual(report['phase'], 'released')

    def test_foreground_escape_stops_without_reacquiring_and_unlocks(self):
        native = Mock()
        native.guard.side_effect = RuntimeError('foreground escaped')
        native.restore.return_value = {'focus_restored': False}
        report, _, _, _ = self.execute(native)
        self.assertFalse(report['ok'])
        native.point.assert_not_called()
        native.unblock.assert_called_once()
        self.assertEqual(native.activate.call_count, 1)

    def test_watchdog_terminates_stuck_worker_and_detects_parent_exit(self):
        script = ('import foreground_control as f,threading,time; '
                  'f.threading.Thread(target=f.watchdog,args=(int(__import__("sys").argv[1]),500,threading.Event()),daemon=True).start(); '
                  'time.sleep(30)')
        started = time.monotonic()
        with subprocess.Popen([sys.executable, '-c', script, str(os.getpid())], cwd=str(Path(control.__file__).parent),
                              creationflags=subprocess.CREATE_NO_WINDOW) as worker:
            self.assertEqual(worker.wait(timeout=4), 124)
        self.assertLess(time.monotonic() - started, 4)
        with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], creationflags=subprocess.CREATE_NO_WINDOW) as parent:
            child_script = script.replace(',500,', ',8000,')
            with subprocess.Popen([sys.executable, '-c', child_script, str(parent.pid)], cwd=str(Path(control.__file__).parent),
                                  creationflags=subprocess.CREATE_NO_WINDOW) as worker:
                time.sleep(0.3)
                parent.kill()
                parent.wait(timeout=2)
                self.assertEqual(worker.wait(timeout=3), 125)

    def test_sendinput_layout_and_unicode_pairs_without_real_input(self):
        self.assertEqual(ctypes.sizeof(control._Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        native = object.__new__(control.NativeInput)
        native.send = Mock()
        native.keyboard_guard = Mock()
        native.text('ไทย🙂', False)
        events = native.send.call_args.args[0]
        self.assertEqual(len(events), 10)  # three Thai units + surrogate pair, down/up each
        units = [e.ki.wScan for e in events[::2]]
        encoded = b''.join(n.to_bytes(2, 'little') for n in units)
        self.assertEqual(encoded.decode('utf-16-le'), 'ไทย🙂')
        self.assertTrue(all(e.ki.dwFlags == 6 for e in events[1::2]))

    def test_native_screen_edge_indicator_is_visible_without_focus_or_cursor_change(self):
        owned = TestWindow()
        indicator = ScreenIndicator(owned.hwnd, 'ทดสอบ suggestion', 3000)
        original_focus, original_cursor = win32gui.GetForegroundWindow(), win32api.GetCursorPos()
        try:
            indicator.show('ขอควบคุมชั่วคราว')
            self.assertEqual(len(indicator.windows), 4 * len(win32api.EnumDisplayMonitors()) + 1)
            required = win32con.WS_EX_TRANSPARENT | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOPMOST
            self.assertTrue(all(win32gui.IsWindowVisible(w) for w in indicator.windows))
            self.assertTrue(all(win32gui.GetWindowLong(w, win32con.GWL_EXSTYLE) & required == required for w in indicator.windows))
            self.assertEqual(win32gui.GetForegroundWindow(), original_focus)
            self.assertEqual(win32api.GetCursorPos(), original_cursor)
            handles = list(indicator.windows)
        finally:
            indicator.close()
            owned.close()
        self.assertTrue(all(not win32gui.IsWindow(w) for w in handles))


if __name__ == '__main__':
    unittest.main()
