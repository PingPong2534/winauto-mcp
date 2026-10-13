"""Capture cleanup, guarded visible pixels and bounded driver failure regressions."""
import contextlib
import ctypes
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server
import screenshot
import win32ui
from takeover_indicator import ScreenIndicator
from PIL import Image
from test_pywinauto import TestWindow


class CaptureTests(unittest.TestCase):
    def test_native_repeated_printwindow_capture_does_not_leak_gdi_handles(self):
        window = TestWindow()
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        user.GetGuiResources.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        user.GetGuiResources.restype = ctypes.c_ulong
        try:
            process = kernel.GetCurrentProcess()
            self.assertIsNotNone(screenshot._print_window(window.hwnd))
            before = user.GetGuiResources(process, 0)
            for _ in range(30):
                self.assertIsNotNone(screenshot._print_window(window.hwnd))
            self.assertLessEqual(user.GetGuiResources(process, 0), before + 2)
        finally:
            window.close()

    def test_native_close_invocation_returns_success_after_window_destruction(self):
        window = TestWindow()
        with tempfile.TemporaryDirectory() as folder, patch.object(server.journal, 'ROOT', folder):
            try:
                server.attach_window(window.hwnd)
                result = server.click_element(name='Close')
                self.assertEqual(__import__('json').loads(result)['action'], 'invoked')
                self.assertTrue(window.done.wait(3))
            finally:
                server.detach_window()
                window.close()

    def test_journal_capture_gdi_failure_cannot_mask_or_skip_a_mutation(self):
        with patch.dict(server._state, hwnd=123), \
             patch.object(server.window_manager, 'window_exists', return_value=True), \
             patch.object(server.window_manager, 'get_window_title', return_value='test'), \
             patch.object(server, 'grab_window', side_effect=win32ui.error('DeleteDC failed')), \
             patch.object(server.journal, 'record') as record:
            invocation = Mock(return_value='invoked')
            invocation.__name__ = 'click_element'
            result = server.journaled(invocation)()
            self.assertEqual(result, 'invoked')
            invocation.assert_called_once()
            self.assertTrue(record.call_args.kwargs['ok'])
            self.assertIsNone(record.call_args.kwargs['after'])
            self.assertIsNone(server._state['pre_frame'])

    def test_printwindow_cleans_every_resource_and_does_not_delete_borrowed_dc(self):
        src, mem, bmp = Mock(), Mock(), Mock()
        src.CreateCompatibleDC.return_value = mem
        mem.DeleteDC.side_effect = win32ui.error('DeleteDC failed')
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(screenshot.win32gui, 'GetWindowRect', return_value=(0, 0, 200, 100)))
            stack.enter_context(patch.object(screenshot.win32gui, 'GetWindowDC', return_value=42))
            stack.enter_context(patch.object(screenshot.win32ui, 'CreateDCFromHandle', return_value=src))
            stack.enter_context(patch.object(screenshot.win32ui, 'CreateBitmap', return_value=bmp))
            stack.enter_context(patch.object(screenshot.ctypes.windll.user32, 'PrintWindow', return_value=0))
            delete = stack.enter_context(patch.object(screenshot.win32gui, 'DeleteObject', side_effect=win32ui.error('gone')))
            release = stack.enter_context(patch.object(screenshot.win32gui, 'ReleaseDC'))
            self.assertIsNone(screenshot._print_window(1))
        delete.assert_called_once()
        mem.DeleteDC.assert_called_once()
        src.DeleteDC.assert_not_called()
        release.assert_called_once_with(1, 42)
        self.assertEqual(mem.SelectObject.call_count, 2)

    def test_screen_mode_skips_printwindow_but_preserves_guard(self):
        image = Image.new('RGB', (100, 100), 'white')
        with patch.object(screenshot.win32gui, 'IsIconic', return_value=False), \
             patch.object(screenshot.win32gui, 'IsWindowVisible', return_value=True), \
             patch.object(screenshot, '_print_window') as printwindow, \
             patch.object(screenshot, 'hidden_for_capture', contextlib.nullcontext), \
             patch.object(screenshot, '_uncovered_screen_grab', return_value=image) as guard:
            self.assertIs(screenshot.grab_window(1, allow_occluded=False), image)
            printwindow.assert_not_called()
            guard.assert_called_once_with(1)
            guard.side_effect = ValueError('covered')
            with self.assertRaisesRegex(ValueError, 'covered'):
                screenshot.grab_window(1, allow_occluded=False)
        with self.assertRaisesRegex(ValueError, 'capture_mode'):
            server._capture_mode(1, 'unsafe')

    def test_visible_capture_refuses_geometry_change_or_new_cover_after_grab(self):
        for moved in (False, True):
            rects = [(0, 0, 100, 100), (0, 0, 120, 100) if moved else (0, 0, 100, 100)]
            with patch.object(screenshot, 'get_client_rect_screen', side_effect=rects), \
                 patch.object(screenshot, 'ensure_uncovered', side_effect=None if moved else [None, ValueError('covered')]), \
                 patch.object(screenshot, '_screen_grab', return_value=Image.new('RGB', (100, 100))):
                with self.assertRaisesRegex(ValueError, 'geometry|covered'):
                    screenshot._uncovered_screen_grab(1)

    def test_cloaked_windows_skipped_but_visible_foreign_cover_identified_without_title(self):
        for cloaked in (False, True):
            with contextlib.ExitStack() as stack:
                for name, value in (('GetTopWindow', 2), ('IsWindowVisible', True), ('IsIconic', False),
                                    ('GetWindowRect', (0, 0, 100, 100)), ('GetWindow', 1),
                                    ('GetClassName', 'SyntheticCover')):
                    stack.enter_context(patch.object(screenshot.win32gui, name, return_value=value))
                title = stack.enter_context(patch.object(screenshot.win32gui, 'GetWindowText'))
                stack.enter_context(patch.object(screenshot, '_cloaked', return_value=cloaked))
                stack.enter_context(patch('win32process.GetWindowThreadProcessId', return_value=(7, 8)))
                if cloaked:
                    screenshot.ensure_uncovered(1, (0, 0, 100, 100))
                else:
                    with self.assertRaisesRegex(ValueError, 'covering_hwnd=2, pid=8, class='):
                        screenshot.ensure_uncovered(1, (0, 0, 100, 100))
                title.assert_not_called()

    def indicator(self):
        indicator = object.__new__(ScreenIndicator)
        indicator.target, indicator.windows = 1, []
        indicator.deadline = time.monotonic() + 5
        indicator.show = Mock()
        native = Mock()
        native.windows.get_client_rect_screen.return_value = (0, 0, 100, 100)
        return indicator, native

    def test_stalled_driver_capture_returns_bounded_error_and_cannot_write_late_png(self):
        indicator, native = self.indicator()
        release = threading.Event()
        phases = []
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(screenshot, 'ensure_uncovered'), \
             patch.object(screenshot.ctypes.windll.dwmapi, 'DwmFlush', side_effect=lambda: release.wait(3)), \
             patch('mss.mss') as capture:
            raw = Mock(size=(100, 100), bgra=bytes([255, 255, 255, 255]) * 10000)
            capture.return_value.__enter__.return_value.grab.return_value = raw
            started = time.monotonic()
            try:
                with self.assertRaisesRegex(TimeoutError, 'dwm_flush'):
                    indicator.capture(native, None, folder, phases.append)
                self.assertLess(time.monotonic() - started, 1.5)
                self.assertIn('dwm_flush', phases)
            finally:
                release.set()
            time.sleep(0.1)
            self.assertFalse(pathlib.Path(folder, 'capture.png').exists())
            indicator.show.assert_called_once()

    def test_capture_reports_stages_and_returns_fresh_crop(self):
        indicator, native = self.indicator()
        phases = []
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(screenshot, 'ensure_uncovered') as guard, \
             patch.object(screenshot.ctypes.windll.dwmapi, 'DwmFlush'), \
             patch('mss.mss') as capture:
            capture.return_value.__enter__.return_value.grab.return_value = Mock(
                size=(20, 30), bgra=bytes([20, 30, 40, 255]) * 600)
            report = indicator.capture(native, [10, 10, 30, 40], folder, phases.append)
            with Image.open(pathlib.Path(folder, 'capture.png')) as image:
                self.assertEqual(image.size, (20, 30))
                self.assertEqual(image.getpixel((0, 0)), (40, 30, 20))
            self.assertEqual(report['coordinate_offset'], [10, 10])
            self.assertEqual(guard.call_count, 2)
            self.assertEqual(phases, ['occlusion_check', 'dwm_flush', 'screen_grab', 'png_encode',
                                      'verify_and_save', 'complete'])


if __name__ == '__main__':
    unittest.main()
