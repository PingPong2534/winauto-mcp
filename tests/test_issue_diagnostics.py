"""Sanitized regressions for issue diagnostics; no user application access."""
import asyncio
from contextlib import nullcontext
import json
import pathlib
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, PropertyMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server
import input_sim
import uia_inspect as uia
import foreground_control as foreground


class IssueDiagnosticsTests(unittest.TestCase):
    def test_insert_extended_scan_code_and_owned_release(self):
        foreground.validate([{'do': 'key', 'keys': ['shift', 'insert']}], 'test', 3000, True)
        native = object.__new__(foreground.NativeInput)
        native.user = Mock()
        native.user.MapVirtualKeyW.return_value = 0xE052
        down, up = native._key_event('insert'), native._key_event('insert', up=True)
        native.user.MapVirtualKeyW.assert_called_with(0x2D, 4)
        self.assertEqual((down.ki.wScan, down.ki.dwFlags), (0x52, 9))
        self.assertEqual((up.ki.wScan, up.ki.dwFlags), (0x52, 11))
        with self.assertRaises(ValueError):
            foreground.validate([{'do': 'key', 'keys': ['shift', 'insert']}], 'test', 3000, False)

    def test_disappearing_control_metadata_is_safe(self):
        wrapper = SimpleNamespace(element_info=SimpleNamespace(control_type=None, name=None,
            automation_id=None, runtime_id=(1,)), rectangle=lambda: SimpleNamespace(
                left=0, top=0, right=1, bottom=1), is_enabled=lambda: False)
        with patch.object(uia, 'get_client_rect_screen', return_value=(0, 0, 10, 10)), \
             patch.object(uia, 'pattern', return_value=None):
            self.assertEqual(uia.describe(wrapper, 1)['control_type'], 'UnknownControl')
        root = SimpleNamespace(children=Mock(side_effect=TypeError('invalid provider metadata')))
        with self.assertRaisesRegex(RuntimeError, 'provider.*enumeration'):
            list(uia.walk(root, strict=True))

    def test_click_reports_focus_transition_and_uncertain_invocation(self):
        before = {'foreground_hwnd': 1, 'target_minimized': True}
        after = {'foreground_hwnd': 2, 'target_minimized': True}
        with patch.object(input_sim, 'ensure_input_allowed'), \
             patch.object(input_sim, 'background_action', return_value=nullcontext()), \
             patch.object(uia, 'com_session', return_value=nullcontext()), \
             patch.object(input_sim, '_find'), patch.object(uia, 'describe', return_value={}), \
             patch.object(input_sim, '_window_state', side_effect=[before, after]), \
             patch.object(input_sim, '_activate', return_value='invoked'):
            result = input_sim.click_element(2, name='test')
        self.assertTrue(result['foreground_changed'])
        self.assertTrue(result['window_state_after']['target_minimized'])
        with patch.object(input_sim, 'ensure_input_allowed'), \
             patch.object(input_sim, 'background_action', return_value=nullcontext()), \
             patch.object(uia, 'com_session', return_value=nullcontext()), \
             patch.object(input_sim, '_find'), patch.object(uia, 'describe', return_value={}), \
             patch.object(input_sim, '_window_state', return_value=before), \
             patch.object(input_sim, '_activate', side_effect=RuntimeError('provider failed')):
            with self.assertRaises(RuntimeError) as raised:
                input_sim.click_element(2, name='test')
        self.assertEqual(raised.exception.phase, 'activation')
        self.assertIn('unknown', raised.exception.mutation_outcome)

    def test_delayed_readback_does_not_repeat_write(self):
        for delayed in (True, False):
            with self.subTest(delayed=delayed):
                value = Mock(CurrentIsReadOnly=False)
                type(value).CurrentValue = PropertyMock(side_effect=['stale', 'text'] if delayed else None,
                                                         return_value='stale')
                wrapper = Mock(element_info=SimpleNamespace(control_type='Edit', automation_id='test'))
                with patch.object(uia, 'pattern', return_value=value), \
                     patch.object(input_sim, '_interaction_rect', return_value=None), \
                     patch.object(input_sim, '_remember'), \
                     patch.object(input_sim.time, 'monotonic', side_effect=[0, 0.4]), \
                     patch.object(input_sim.time, 'sleep'):
                    if delayed:
                        # Allow one stale sample and then the expected value.
                        with patch.object(input_sim.time, 'monotonic', side_effect=[0, 0.1]):
                            self.assertTrue(input_sim._type_into(1, wrapper, 'text', True)['verified'])
                    else:
                        with self.assertRaises(input_sim.ActionFailure) as raised:
                            input_sim._type_into(1, wrapper, 'text', True)
                        self.assertEqual(raised.exception.phase, 'readback')
                        self.assertIn('do not replay', raised.exception.mutation_outcome)
                wrapper.set_edit_text.assert_called_once_with('text')

    def test_mcp_error_content_survives_transport(self):
        async def scenario():
            with patch.object(server, '_require_attached', side_effect=ValueError('no window attached')):
                result = await server.mcp.call_tool('click_element', {'name': 'test'})
            self.assertTrue(result.is_error)
            data = json.loads(next(b.text for b in result.content if b.type == 'text'))
            self.assertEqual(data['error'], 'no window attached')
            self.assertEqual(data['error_type'], 'ValueError')
            self.assertFalse(data['ok'])
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main(verbosity=2)
