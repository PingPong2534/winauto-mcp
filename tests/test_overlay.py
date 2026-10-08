"""Live attach/outline lifecycle tests using only owned, non-activating windows."""

import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server
import screenshot
import win32api
import win32con
import win32gui
import window_manager
from test_pywinauto import TestWindow, last_input_tick


class OverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.journal_dir = tempfile.TemporaryDirectory(prefix="winauto-overlay-test-")
        cls.journal_patch = patch.object(server.journal, "ROOT", cls.journal_dir.name)
        cls.journal_patch.start()
        cls.addClassCleanup(cls.journal_dir.cleanup)
        cls.addClassCleanup(cls.journal_patch.stop)
        cls.window = TestWindow()
        cls.addClassCleanup(cls.window.close)
        cls.addClassCleanup(server.detach_window)
        foreground = win32gui.GetForegroundWindow()
        cls.overlay = server.get_overlay()
        assert win32gui.GetForegroundWindow() == foreground, "outline startup took focus"

    def wait_for(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.03)
        self.fail("overlay did not reach the expected native window state")

    def visible(self):
        return bool(self.overlay.hwnd and win32gui.IsWindowVisible(self.overlay.hwnd))

    def shown_on(self, target):
        return (self.visible() and self.overlay._tracked_hwnd == target
                and win32gui.GetWindowRect(self.overlay.hwnd)
                == window_manager.get_client_rect_screen(target))

    def green_pixels(self):
        import mss
        from PIL import Image
        left, top, right, bottom = win32gui.GetWindowRect(self.overlay.hwnd)
        with mss.MSS() as capture:
            raw = capture.grab({"left": left, "top": top,
                                "width": right - left, "height": bottom - top})
        image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        return image, sum(n for n, color in image.getcolors(image.width * image.height)
                          if color == (0, 255, 102))

    def test_attach_paints_green_outline_without_taking_input(self):
        server.release_control()
        self.wait_for(lambda: not self.visible())
        foreground = win32gui.GetForegroundWindow()
        cursor = win32api.GetCursorPos()
        input_tick = last_input_tick()
        for _ in range(3):
            server.attach_window(self.window.hwnd)
            self.wait_for(lambda: self.shown_on(self.window.hwnd))
            self.wait_for(lambda: self.green_pixels()[1] > 100)
            style = win32gui.GetWindowLong(self.overlay.hwnd, win32con.GWL_EXSTYLE)
            required = (win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TRANSPARENT
                        | win32con.WS_EX_LAYERED)
            self.assertEqual(style & required, required)
            actual = win32gui.GetForegroundWindow()
            self.assertEqual(actual, foreground,
                             f"foreground={actual} ({win32gui.GetClassName(actual)}), "
                             f"outline={self.overlay.hwnd}, before={foreground}")
            if last_input_tick() == input_tick:
                self.assertEqual(win32api.GetCursorPos(), cursor)
            proof = os.environ.get("WINAUTO_OVERLAY_PROOF")
            if proof:
                self.green_pixels()[0].save(proof)
            server.release_control()
            self.wait_for(lambda: not self.visible())
            self.assertEqual(server._state["hwnd"], self.window.hwnd)

    def test_reattach_clears_highlights_retargets_and_detach_hides(self):
        server.attach_window(self.window.hwnd)
        server.highlight([[10, 10, 90, 90]])
        self.wait_for(lambda: self.overlay._drawn == (
            window_manager.get_client_rect_screen(self.window.hwnd), ((10, 10, 90, 90),)))
        # Same HWND/geometry must still reset old boxes.
        server.attach_window(self.window.hwnd)
        self.wait_for(lambda: self.overlay._drawn == (
            window_manager.get_client_rect_screen(self.window.hwnd), ()))
        server.attach_window(self.window.cover)
        self.wait_for(lambda: self.shown_on(self.window.cover))
        server.detach_window()
        self.wait_for(lambda: not self.visible())
        self.assertIsNone(server._state["hwnd"])

    def test_outline_follows_move_resize_and_hide_restore(self):
        target = self.window.hwnd
        original = win32gui.GetWindowRect(target)
        foreground = win32gui.GetForegroundWindow()
        server.attach_window(target)
        self.wait_for(lambda: self.shown_on(target))
        try:
            left, top, right, bottom = original
            win32gui.SetWindowPos(target, 0, left + 30, top + 20,
                                 right - left + 50, bottom - top + 40,
                                 win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
            self.wait_for(lambda: self.shown_on(target))
            win32gui.ShowWindow(target, win32con.SW_HIDE)
            self.wait_for(lambda: not self.visible())
            win32gui.ShowWindow(target, win32con.SW_SHOWNOACTIVATE)
            self.wait_for(lambda: self.shown_on(target))
            self.assertEqual(win32gui.GetForegroundWindow(), foreground)
        finally:
            left, top, right, bottom = original
            win32gui.SetWindowPos(target, 0, left, top, right - left, bottom - top,
                                 win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
            win32gui.ShowWindow(target, win32con.SW_SHOWNOACTIVATE)

    def test_screen_fallback_excludes_outline_and_restores_it(self):
        # The fixture's cover is topmost and fully visible; outlining it must
        # not make the capture guard mistake our decoration for another app.
        target = self.window.cover
        foreground = win32gui.GetForegroundWindow()
        server.attach_window(target)
        self.wait_for(lambda: self.shown_on(target))
        self.wait_for(lambda: self.green_pixels()[1] > 100)
        with patch.object(screenshot, "_print_window", return_value=None):
            image = screenshot.grab_window(target)
        self.assertFalse(any(color == (0, 255, 102)
                             for _, color in image.getcolors(image.width * image.height)))
        self.wait_for(lambda: self.shown_on(target))
        self.wait_for(lambda: self.green_pixels()[1] > 100)
        self.assertEqual(win32gui.GetForegroundWindow(), foreground)


if __name__ == "__main__":
    unittest.main(verbosity=2)
