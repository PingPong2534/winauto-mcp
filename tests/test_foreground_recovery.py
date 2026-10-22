"""Reproduce owned-input races, cleanup budgeting and native focus failures."""
import pathlib
import sys
import time
import unittest
from unittest.mock import Mock, patch
import ctypes

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import foreground_control as control
import test_foreground as fixtures
from input_gate import InputGate


class RecoveryTests(unittest.TestCase):
    def native(self):
        return fixtures.ForegroundTests().native_mouse()

    def test_cleanup_does_not_race_an_inflight_native_dispatch(self):
        native = self.native()
        native.held_keys[(0, 0x1D, 8)] = None
        native.dispatch_lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, 'in flight'):
                native.release_keys(stop=True)
            native.user.SendInput.assert_not_called()
            self.assertTrue(native.held_keys)
        finally:
            native.dispatch_lock.release()
        native.release_keys(stop=True)
        self.assertFalse(native.held_keys)

    def test_short_unicode_send_releases_only_the_dispatched_chunk_possible_downs(self):
        native = self.native()
        native.keyboard_guard = Mock()
        native.user.SendInput.side_effect = [1, 4]
        with self.assertRaisesRegex(RuntimeError, 'partial'):
            native.text('abcdefgh', False)
        self.assertEqual(native.user.SendInput.call_count, 2)
        up = native.user.SendInput.call_args.args[1]
        self.assertEqual(len(up), 4)
        self.assertEqual({event.ki.wScan for event in up}, set(map(ord, 'abcd')))
        self.assertTrue(all(event.ki.dwFlags == 6 for event in up))
        self.assertFalse(native.held_keys)

    def test_text_stops_cooperatively_between_characters_and_releases(self):
        native = self.native()
        native.keyboard_guard = Mock(side_effect=[None, None, TimeoutError('cleanup reserve')])
        with self.assertRaisesRegex(TimeoutError, 'cleanup reserve'):
            native.text('abcdefgh', False)
        self.assertEqual(native.user.SendInput.call_count, 1)
        self.assertEqual(native.user.SendInput.call_args.args[0], 8)
        self.assertFalse(native.held_keys)

    def test_guard_reserves_cleanup_time_before_any_more_input(self):
        native = self.native()
        # Bind the actual guard; it must stop before window inspection/dispatch.
        native.guard = control.NativeInput.guard.__get__(native)
        native.action_deadline = time.monotonic() - 0.01
        with self.assertRaisesRegex(TimeoutError, 'cleanup reserve'):
            native.guard()
        native.user.SendInput.assert_not_called()

    def test_restore_skips_unresponsive_previous_window_without_uia(self):
        native = self.native()
        native.gate = None
        native.previous, native.hwnd, native.cursor = 999, 123, (0, 0)
        native.gui, native.api = Mock(), Mock()
        native.gui.GetForegroundWindow.return_value = 123
        native.belongs = lambda hwnd: hwnd == 123
        native.gui.IsWindow.return_value = True
        native.gui.SendMessageTimeout.side_effect = RuntimeError('unresponsive')
        native.api.GetCursorPos.return_value = (0, 0)
        with patch('uia_inspect.root_wrapper') as uia:
            report = native.restore()
        self.assertFalse(report['focus_restored'])
        self.assertIn('unresponsive', report['restore_error'])
        native.user.SetForegroundWindow.assert_not_called()
        native.gui.SetForegroundWindow.assert_not_called()
        uia.assert_not_called()

    def test_worker_exit_keeps_acknowledged_key_release_before_restore_stall(self):
        report = control.worker_exit_report({'phase': 'restoring', 'input_blocked': True,
            'keys_released': True, 'mouse_buttons_released': True, 'completed_steps': 2}, 124)
        self.assertTrue(report['keys_released'])
        self.assertTrue(report['mouse_buttons_released'])
        self.assertFalse(report['release_confirmed'])
        self.assertFalse(report['ok'])

    def test_denied_activation_never_falls_back_to_uia_or_sends_input(self):
        native = self.native()
        native.hwnd, native.pid = 123, 456
        native.gui, native.process, native.windows = Mock(), Mock(), Mock()
        native.gui.IsWindow.return_value = True
        native.gui.IsIconic.return_value = False
        native.gui.IsWindowVisible.return_value = True
        native.process.GetWindowThreadProcessId.return_value = (1, 456)
        native.gui.GetForegroundWindow.return_value = 999
        native.belongs = lambda hwnd: hwnd == 123
        native.user.SetForegroundWindow.return_value = False
        with patch('uia_inspect.root_wrapper') as uia:
            with self.assertRaisesRegex(RuntimeError, 'denied'):
                native.activate()
        uia.assert_not_called()
        native.user.SendInput.assert_not_called()

    def test_message_wait_failure_removes_both_hooks_and_refuses_control(self):
        gate = InputGate(123)
        user, kernel = Mock(), Mock()
        user.SetWindowsHookExW.side_effect = [101, 102]
        user.MsgWaitForMultipleObjectsEx.return_value = 0xFFFFFFFF
        kernel.GetModuleHandleW.return_value = 1
        with patch.object(ctypes, 'WinDLL', side_effect=[user, kernel]), \
             patch('win32gui.PumpWaitingMessages'):
            gate._run()
        self.assertFalse(gate.active)
        self.assertTrue(gate.error)
        self.assertEqual([call.args[0] for call in user.UnhookWindowsHookEx.call_args_list], [102, 101])
        self.assertFalse(gate.handles)

    def test_fixed_waits_cannot_spend_the_cleanup_reserve(self):
        with self.assertRaisesRegex(ValueError, 'no time'):
            control.validate([{'do': 'wait', 'ms': 500}], 'test', 1000, True)

    def test_restoration_probe_does_not_override_an_external_focus_change(self):
        native = self.native()
        native.gate = None
        native.previous, native.hwnd, native.cursor = 999, 123, (0, 0)
        native.gui, native.api = Mock(), Mock()
        native.gui.GetForegroundWindow.side_effect = [123, 123, 777]
        native.gui.IsWindow.return_value = True
        native.belongs = lambda hwnd: hwnd == 123
        native.api.GetCursorPos.return_value = (0, 0)
        report = native.restore()
        self.assertFalse(report['focus_restored'])
        native.user.SetForegroundWindow.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
