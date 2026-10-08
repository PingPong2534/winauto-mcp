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

    def label_visible(self):
        return bool(win32gui.IsWindowVisible(self.overlay.label_hwnd))

    def drawn_label(self, label):
        state = self.overlay._drawn
        return state is not None and state[2] == label

    def label_outside(self):
        if not self.label_visible() or not self.visible():
            return False
        left, top, right, bottom = win32gui.GetWindowRect(self.overlay.hwnd)
        x1, y1, x2, y2 = win32gui.GetWindowRect(self.overlay.label_hwnd)
        return x2 <= left or x1 >= right or y2 <= top or y1 >= bottom

    def desktop_pixels(self, rect):
        import mss
        from PIL import Image
        left, top, right, bottom = rect
        with mss.MSS() as capture:
            raw = capture.grab({"left": left, "top": top,
                                "width": right - left, "height": bottom - top})
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    def decoration_pixels(self):
        rects = [win32gui.GetWindowRect(self.overlay.hwnd)]
        if self.label_visible():
            rects.append(win32gui.GetWindowRect(self.overlay.label_hwnd))
        return self.desktop_pixels((min(r[0] for r in rects) - 4,
                                    min(r[1] for r in rects) - 4,
                                    max(r[2] for r in rects) + 4,
                                    max(r[3] for r in rects) + 4))

    def green_pixels(self):
        image = self.desktop_pixels(win32gui.GetWindowRect(self.overlay.hwnd))
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
            window_manager.get_client_rect_screen(self.window.hwnd), ((10, 10, 90, 90),), ""))
        # Same HWND/geometry must still reset old boxes.
        server.attach_window(self.window.hwnd)
        self.wait_for(lambda: self.overlay._drawn == (
            window_manager.get_client_rect_screen(self.window.hwnd), (), ""))
        server.attach_window(self.window.cover)
        self.wait_for(lambda: self.shown_on(self.window.cover))
        server.detach_window()
        self.wait_for(lambda: not self.visible())
        self.assertIsNone(server._state["hwnd"])

    def test_outline_follows_move_resize_and_hide_restore(self):
        target = self.window.hwnd
        original = win32gui.GetWindowRect(target)
        foreground = win32gui.GetForegroundWindow()
        server.attach_window(target, label="Feature: Move / Resize")
        self.wait_for(lambda: self.shown_on(target) and self.label_outside())
        try:
            left, top, right, bottom = original
            win32gui.SetWindowPos(target, 0, left + 30, top + 20,
                                 right - left + 50, bottom - top + 40,
                                 win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
            self.wait_for(lambda: self.shown_on(target) and self.label_outside()
                          and win32gui.GetWindowRect(self.overlay.label_hwnd)[3]
                          == window_manager.get_client_rect_screen(target)[1] - 4)
            win32gui.ShowWindow(target, win32con.SW_HIDE)
            self.wait_for(lambda: not self.visible() and not self.label_visible())
            win32gui.ShowWindow(target, win32con.SW_SHOWNOACTIVATE)
            self.wait_for(lambda: self.shown_on(target) and self.label_outside())
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
        server.attach_window(target, label="Feature: Capture")
        self.wait_for(lambda: self.shown_on(target) and self.label_outside())
        self.wait_for(lambda: self.green_pixels()[1] > 100)
        original_capture = screenshot._uncovered_screen_grab

        def capture_without_decoration(*args, **kwargs):
            self.assertFalse(self.visible())
            self.assertFalse(self.label_visible())
            return original_capture(*args, **kwargs)

        with patch.object(screenshot, "_print_window", return_value=None), \
                patch.object(screenshot, "_uncovered_screen_grab", side_effect=capture_without_decoration):
            image = screenshot.grab_window(target)
        self.assertFalse(any(color == (0, 255, 102)
                             for _, color in image.getcolors(image.width * image.height)))
        self.wait_for(lambda: self.shown_on(target) and self.label_outside())
        self.wait_for(lambda: self.green_pixels()[1] > 100)
        self.assertEqual(win32gui.GetForegroundWindow(), foreground)

    def test_attach_label_paints_thai_and_clears_without_taking_focus(self):
        foreground = win32gui.GetForegroundWindow()
        label = "กำลังทดสอบ: บันทึกข้อมูลลูกค้า & Restore"
        server.attach_window(self.window.cover, label=label)
        self.wait_for(lambda: self.shown_on(self.window.cover)
                      and self.drawn_label(label))

        def label_visible():
            if not self.label_outside():
                return False
            image = self.desktop_pixels(win32gui.GetWindowRect(self.overlay.label_hwnd))
            colors = dict((color, n) for n, color in image.getcolors(image.width * image.height))
            return colors.get((21, 48, 38), 0) > 100 and colors.get((255, 255, 255), 0) > 10

        self.wait_for(label_visible)
        style = win32gui.GetWindowLong(self.overlay.label_hwnd, win32con.GWL_EXSTYLE)
        required = win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_LAYERED
        self.assertEqual(style & required, required)
        self.assertEqual(win32gui.SendMessage(self.overlay.label_hwnd, win32con.WM_MOUSEACTIVATE, 0, 0),
                         win32con.MA_NOACTIVATE)
        self.assertIn(label, server.journal.session_info()["label"])
        self.assertEqual(win32gui.GetForegroundWindow(), foreground)
        proof = os.environ.get("WINAUTO_LABEL_PROOF")
        if proof:
            self.decoration_pixels().save(proof)
        server.release_control()
        self.wait_for(lambda: not self.visible() and not self.label_visible())
        server.highlight([[40, 40, 80, 80]])
        self.wait_for(lambda: self.shown_on(self.window.cover)
                      and self.drawn_label(label))
        self.wait_for(label_visible)
        server.attach_window(self.window.cover, label="W" * 120)
        self.wait_for(lambda: self.drawn_label("W" * 120))
        self.wait_for(label_visible)
        server.attach_window(self.window.cover)
        self.wait_for(lambda: self.drawn_label("") and not self.label_visible())
        self.assertEqual(win32gui.GetForegroundWindow(), foreground)
        for invalid in ("line\nbreak", "x" * 121, "nul\x00"):
            with self.subTest(label=invalid), self.assertRaises(ValueError):
                server.attach_window(self.window.hwnd, label=invalid)
        self.assertEqual(server._state["hwnd"], self.window.cover)

    def test_label_uses_exterior_fallback_and_hides_when_no_space_fits(self):
        target = self.window.cover
        original = win32gui.GetWindowRect(target)
        foreground = win32gui.GetForegroundWindow()
        monitor = win32api.GetMonitorInfo(win32api.MonitorFromWindow(
            target, win32con.MONITOR_DEFAULTTONEAREST))["Monitor"]
        ml, mt, mr, mb = monitor
        try:
            # This fixture is a borderless popup: client top equals window top.
            win32gui.SetWindowPos(target, 0, ml + 60, mt, 500, 150,
                                 win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
            server.attach_window(target, label="Feature: Exterior placement")
            self.wait_for(lambda: self.shown_on(target) and self.label_outside())
            self.assertEqual(win32gui.GetWindowRect(self.overlay.label_hwnd)[1],
                             window_manager.get_client_rect_screen(target)[3] + 4)
            win32gui.SetWindowPos(target, 0, ml + 60, mt, 500, mb - mt,
                                 win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
            self.wait_for(lambda: self.shown_on(target) and self.label_outside()
                          and win32gui.GetWindowRect(self.overlay.label_hwnd)[0]
                          == window_manager.get_client_rect_screen(target)[2] + 4)
            win32gui.SetWindowPos(target, 0, ml, mt, mr - ml, mb - mt,
                                 win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
            self.wait_for(lambda: self.shown_on(target) and not self.label_visible())
            win32gui.SetWindowPos(target, 0, ml + 60, mt + 80, 500, 150,
                                 win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)
            self.wait_for(lambda: self.shown_on(target) and self.label_outside())
            server.detach_window()
            self.wait_for(lambda: not self.visible() and not self.label_visible())
            self.assertEqual(win32gui.GetForegroundWindow(), foreground)
        finally:
            left, top, right, bottom = original
            win32gui.SetWindowPos(target, 0, left, top, right - left, bottom - top,
                                 win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)

    def test_latest_successful_interaction_moves_box_and_preserves_label(self):
        label = "Feature: Last interaction"
        foreground = win32gui.GetForegroundWindow()
        server.attach_window(self.window.hwnd, label=label)

        def expect_control(ident):
            left, top = win32gui.ClientToScreen(self.window.hwnd, (0, 0))
            x1, y1, x2, y2 = win32gui.GetWindowRect(self.window.controls[ident])
            rect = (x1 - left, y1 - top, x2 - left, y2 - top)
            self.wait_for(lambda: self.overlay._drawn == (
                window_manager.get_client_rect_screen(self.window.hwnd), (rect,), label))
            return rect

        server.click_element("Run")
        expect_control(201)
        self.wait_for(lambda: any(color == (255, 51, 85)
                      for _, color in self.green_pixels()[0].getcolors(1000000)))
        proof = os.environ.get("WINAUTO_INTERACTION_PROOF")
        if proof:
            self.green_pixels()[0].save(proof)
        server.click_element(automation_id="101")
        expect_control(101)
        server.type_text("latest", automation_id="101", replace=True)
        expect_control(101)
        server.set_value(37, automation_id="301")
        expect_control(301)
        server.scroll(*self.window.center(401), -2)
        expect_control(401)
        server.click(*self.window.center(201), force=True)
        rect = expect_control(201)
        server.read_element(automation_id="101")
        with self.assertRaises(ValueError):
            server.click_element("Duplicate")
        with self.assertRaises(ValueError):
            server.type_text("refused", automation_id="102", replace=True)
        self.assertEqual(self.overlay._drawn[1], (rect,))
        report = server.run_steps([
            {"do": "type", "text": "script", "automation_id": "101", "replace": True},
            {"do": "click_element", "automation_id": "202"},
        ], delay_ms=0)
        self.assertIn('"ok": true', report[0])
        expect_control(202)
        server.release_control()
        self.wait_for(lambda: not self.visible())
        server.read_element(automation_id="101")
        self.assertFalse(self.visible())
        server.type_text(" after release", automation_id="101")
        expect_control(101)
        self.assertEqual(win32gui.GetForegroundWindow(), foreground)


if __name__ == "__main__":
    unittest.main(verbosity=2)
