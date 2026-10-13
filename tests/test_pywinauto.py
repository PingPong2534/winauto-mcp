"""Live UIA/MCP regression tests using owned windows shown without activation."""

import asyncio
import contextlib
import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import pathlib
import queue
import sys
import threading
import tempfile
import time
import unittest
from unittest.mock import patch
from concurrent.futures import Future

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server  # DPI awareness must be set before creating test windows.
import control_sets
import input_sim
import integrity
import screenshot
import uia_inspect
import window_manager
import win32api
import win32con
import win32gui


def last_input_tick():
    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
    value = LastInput()
    value.cbSize = ctypes.sizeof(value)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(value)):
        raise ctypes.WinError()
    return value.dwTime


class TestWindow:
    """Normally activatable controls shown without activation or cursor movement."""

    def __init__(self):
        self.ready = threading.Event()
        self.done = threading.Event()
        self.hwnd = None
        self.cover = None
        self.controls = {}
        self.invocations = 0
        self.error = None
        self.commands = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.ready.wait(10):
            raise RuntimeError("test window did not start")
        if self.error:
            raise self.error

    def _proc(self, hwnd, msg, wp, lp):
        if msg == win32con.WM_APP:
            callback, future = self.commands.get_nowait()
            try:
                future.set_result(callback())
            except Exception as exc:
                future.set_exception(exc)
            return 0
        if msg == win32con.WM_COMMAND and (wp & 0xFFFF) == 201:
            self.invocations += 1
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wp, lp)

    def _run(self):
        try:
            ctypes.windll.comctl32.InitCommonControls()
            cls = f"winauto_uia_test_{id(self)}"
            wc = win32gui.WNDCLASS()
            wc.lpfnWndProc = self._proc
            wc.lpszClassName = cls
            wc.hbrBackground = win32con.COLOR_WINDOW + 1
            win32gui.RegisterClass(wc)
            self.hwnd = win32gui.CreateWindowEx(
                0, cls, "winauto UIA owned test window",
                win32con.WS_OVERLAPPEDWINDOW, 200, 200, 740, 560, 0, 0, 0, None,
            )
            controls = [
                (101, "EDIT", "", win32con.WS_BORDER, 10, 20, 280, 30),
                (102, "EDIT", "readonly", win32con.ES_READONLY, 10, 60, 280, 30),
                (201, "BUTTON", "Run", win32con.BS_PUSHBUTTON, 10, 105, 100, 30),
                (202, "BUTTON", "Enabled", win32con.BS_AUTOCHECKBOX, 10, 150, 140, 30),
                (203, "BUTTON", "Duplicate", 0, 10, 195, 100, 30),
                (204, "BUTTON", "Duplicate", 0, 120, 195, 100, 30),
                (205, "BUTTON", "Disabled", 0, 10, 235, 100, 30),
                (301, "msctls_trackbar32", "", 0, 10, 290, 280, 40),
                (401, "LISTBOX", "", win32con.WS_VSCROLL | win32con.LBS_NOTIFY,
                 340, 20, 300, 400),
                (501, "STATIC", "Canvas without UIA action", 0, 10, 355, 250, 30),
            ]
            for ident, kind, text, extra, x, y, width, height in controls:
                self.controls[ident] = win32gui.CreateWindow(
                    kind, text, win32con.WS_CHILD | win32con.WS_VISIBLE | extra,
                    x, y, width, height, self.hwnd, ident, 0, None,
                )
            win32gui.EnableWindow(self.controls[205], False)
            for i in range(80):
                win32gui.SendMessage(self.controls[401], win32con.LB_ADDSTRING, 0, f"Item {i}")
            win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
            win32gui.UpdateWindow(self.hwnd)
            # All test HWNDs belong to the message-pump thread. Creating a
            # cover on the UIA client thread can deadlock FindAll's Win32
            # provider, because that thread is blocked in the COM call.
            self.cover = win32gui.CreateWindowEx(
                win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOPMOST,
                "STATIC", "winauto test cover", win32con.WS_POPUP,
                200, 200, 740, 560, 0, 0, 0, None,
            )
            win32gui.ShowWindow(self.cover, win32con.SW_SHOWNOACTIVATE)
            win32gui.UpdateWindow(self.cover)
            self.ready.set()
            win32gui.PumpMessages()
        except Exception as exc:
            self.error = exc
            self.ready.set()
        finally:
            if self.cover and win32gui.IsWindow(self.cover):
                win32gui.DestroyWindow(self.cover)
            if self.hwnd and win32gui.IsWindow(self.hwnd):
                win32gui.DestroyWindow(self.hwnd)
            self.done.set()

    def close(self):
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        if not self.done.wait(5):
            raise RuntimeError("test window was not cleaned up")

    def center(self, ident):
        left, top, right, bottom = win32gui.GetWindowRect(self.controls[ident])
        origin_x, origin_y = win32gui.ClientToScreen(self.hwnd, (0, 0))
        return ((left + right) // 2 - origin_x, (top + bottom) // 2 - origin_y)

    def call_ui(self, callback):
        future = Future()
        self.commands.put((callback, future))
        win32gui.PostMessage(self.hwnd, win32con.WM_APP, 0, 0)
        return future.result(timeout=4)


class BackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.journal_dir = tempfile.TemporaryDirectory(prefix="winauto-uia-test-")
        cls.journal_patch = patch.object(server.journal, "ROOT", cls.journal_dir.name)
        cls.journal_patch.start()
        cls.addClassCleanup(cls.journal_dir.cleanup)
        cls.addClassCleanup(cls.journal_patch.stop)
        cls.window = TestWindow()
        cls.cover = cls.window.cover

    @classmethod
    def tearDownClass(cls):
        server.detach_window()
        cls.window.close()
        assert not win32gui.IsWindow(cls.window.hwnd), "test leaked a window"

    def setUp(self):
        server.attach_window(self.window.hwnd)
        win32gui.SendMessage(self.window.controls[101], win32con.WM_SETTEXT, 0, "")
        self.desktop = (win32api.GetCursorPos(), win32gui.GetForegroundWindow())
        self.last_input = last_input_tick()
        self.patches = contextlib.ExitStack()
        # Fail even if an input path attempts to steal then restore focus.
        for module, names in ((win32gui, ("SetForegroundWindow", "BringWindowToTop", "SetFocus")),
                              (win32api, ("SetCursorPos", "mouse_event", "keybd_event"))):
            for name in names:
                self.patches.enter_context(patch.object(module, name,
                                                        side_effect=AssertionError("real input/focus attempted")))
        self.patches.enter_context(patch("pywinauto.base_wrapper.BaseWrapper.click_input",
                                        side_effect=AssertionError("real click attempted")))
        self.patches.enter_context(patch("pywinauto.base_wrapper.BaseWrapper.type_keys",
                                        side_effect=AssertionError("real typing attempted")))
        self.patches.enter_context(patch("pywinauto.controls.uiawrapper.UIAWrapper.set_focus",
                                        side_effect=AssertionError("UIA focus attempted")))

    def tearDown(self):
        self.patches.close()
        self.assertEqual(win32gui.GetForegroundWindow(), self.desktop[1])
        if last_input_tick() == self.last_input:
            self.assertEqual(win32api.GetCursorPos(), self.desktop[0])
        self.assertFalse(win32gui.GetWindowLong(self.window.hwnd, win32con.GWL_EXSTYLE)
                         & win32con.WS_EX_NOACTIVATE, "mutation leaked the activation guard")
        # Physical input can occur while this test runs. Never pin or restore
        # a person's cursor to force an assertion; forbidden input/focus paths
        # above are trapped even if a physical move occurs.

    def text(self):
        return win32gui.GetWindowText(self.window.controls[101])

    def test_background_guard_restores_owned_bit_after_failure(self):
        hwnd = self.window.hwnd
        original = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        try:
            with self.assertRaisesRegex(RuntimeError, "provider failed"):
                with window_manager.background_action(hwnd):
                    current = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                    self.assertTrue(current & win32con.WS_EX_NOACTIVATE)
                    window_manager._set_ex_style(hwnd, current | win32con.WS_EX_ACCEPTFILES)
                    raise RuntimeError("provider failed")
            restored = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            self.assertFalse(restored & win32con.WS_EX_NOACTIVATE)
            self.assertTrue(restored & win32con.WS_EX_ACCEPTFILES)
            window_manager._set_ex_style(hwnd, original | win32con.WS_EX_NOACTIVATE)
            with window_manager.background_action(hwnd):
                pass
            self.assertTrue(win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                            & win32con.WS_EX_NOACTIVATE)
        finally:
            window_manager._set_ex_style(hwnd, original)

    def test_foreground_target_is_not_restyled(self):
        with patch.object(win32gui, "GetForegroundWindow", return_value=self.window.hwnd), \
                patch.object(window_manager, "_set_ex_style",
                             side_effect=AssertionError("foreground target must not be restyled")):
            with window_manager.background_action(self.window.hwnd):
                pass

    def test_cosmetic_failure_does_not_fail_completed_invocation(self):
        count = self.window.invocations
        with patch.object(input_sim, "_interaction_hook", side_effect=RuntimeError("overlay unavailable")):
            result = json.loads(server.click_element("Run"))
        self.assertEqual(result["action"], "invoked")
        deadline = time.monotonic() + 2
        while self.window.invocations == count and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.window.invocations, count + 1)

    def test_inspection_scoped_to_covered_target(self):
        elements = json.loads(server.get_elements())
        run = next(e for e in elements if e["automation_id"] == "201")
        self.assertEqual(run["name"], "Run")
        self.assertIn("invoke", run["actions"])
        self.assertEqual(run["control_type"], "ButtonControl")
        self.assertTrue(win32gui.IsWindowVisible(self.cover))
        self.assertNotIn("winauto test cover", [e["name"] for e in elements])

    def test_control_step_capabilities_match_real_controls(self):
        elements = {e["automation_id"]: e for e in json.loads(server.get_elements())}
        self.assertEqual(elements["101"]["control_steps"], ["click", "type", "capture"])
        self.assertEqual(elements["102"]["control_steps"], ["click", "capture"])
        self.assertEqual(elements["201"]["control_steps"], ["click", "capture"])
        self.assertEqual(elements["205"]["control_steps"], ["capture"])

    def test_control_set_text_and_control_only_crop_under_cover(self):
        from PIL import Image, ImageChops
        server._state["seen"] = []
        text = "ทดสอบ textbox 😀 {+^%}"
        result = server.control_set(automation_id="101", expected_control_type="EditControl", steps=[
            {"do": "click"}, {"do": "type", "text": text, "replace": True}, {"do": "capture"},
        ], delay_ms=0)
        report = json.loads(result[0])
        self.assertTrue(report["ok"])
        self.assertEqual(report["completed_steps"], 3)
        self.assertEqual(self.text(), text)
        self.assertTrue(report["steps"][1]["result"]["verified"])
        self.assertEqual(len(result), 2, "return one control image only")
        image = Image.open(io.BytesIO(result[1].data)).convert("RGB")
        rect = tuple(report["capture"]["region"])
        expected = screenshot.grab_window(self.window.hwnd).crop(rect)
        self.assertEqual(image.size, (rect[2] - rect[0], rect[3] - rect[1]))
        proof = os.environ.get("WINAUTO_CONTROL_PROOF")
        if proof:
            image.save(proof)
            expected.save(str(pathlib.Path(proof).with_stem(pathlib.Path(proof).stem + '-expected')))
            ImageChops.difference(image, expected).save(str(pathlib.Path(proof).with_stem(pathlib.Path(proof).stem + '-diff')))
        self.assertIsNone(ImageChops.difference(image, expected).getbbox())
        self.assertEqual(report["capture"]["coordinate_offset"], list(rect[:2]))
        self.assertEqual(server._state["seen"][-1]["rect"], rect)
        self.assertEqual(len(server._state["seen"]), 1)
        self.assertTrue(win32gui.IsWindowVisible(self.cover))
        append = server.control_set(automation_id="101", steps=[
            {"do": "type", "text": " ต่อท้าย"}, {"do": "capture"}], delay_ms=0)
        self.assertTrue(json.loads(append[0])["ok"])
        self.assertEqual(self.text(), text + " ต่อท้าย")
        records = json.loads(server.history())["records"]
        self.assertTrue({"control:click", "control:type", "control:capture"}
                        <= {r["tool"] for r in records})

    def test_control_set_prevalidates_all_fields_before_click_or_write(self):
        bad_steps = [{"do": "type"}, {"do": "type", "text": None},
                     {"do": "type", "text": "bad", "replace": "yes"},
                     {"do": "type", "text": "bad", "automation_id": "102"},
                     {"do": "capture", "region": [0, 0, 1, 1]},
                     {"do": "click", "x": 10}, {"do": "click", "button": "right"},
                     {"do": "capture"}, {"do": "unknown"}, {"do": []}, "type"]
        with patch.object(input_sim, "_activate", side_effect=AssertionError("unexpected click")), \
                patch.object(input_sim, "_type_into", side_effect=AssertionError("unexpected write")):
            for bad in bad_steps:
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    server.control_set(automation_id="101", steps=[
                        {"do": "click"}, {"do": "type", "text": "bad"}, bad, {"do": "capture"}])
            for plan in ([], [{"do": "click"}], [{"do": "capture"}] * 21):
                with self.subTest(plan=plan), self.assertRaises(ValueError):
                    server.control_set(automation_id="101", steps=plan)
            for delay in (-1, 3001, True, 0.5):
                with self.subTest(delay=delay), self.assertRaises(ValueError):
                    server.control_set(automation_id="101", steps=[{"do": "capture"}], delay_ms=delay)
        self.assertEqual(self.text(), "")

    def test_control_set_refuses_wrong_type_readonly_and_ambiguous_targets(self):
        count = self.window.invocations
        for selector in ({"automation_id": "201"}, {"automation_id": "102"},
                         {"automation_id": "205"}, {"name": "Duplicate"}, {}):
            with self.subTest(selector=selector), self.assertRaises(ValueError):
                server.control_set(**selector, steps=[
                    {"do": "click"}, {"do": "type", "text": "bad"}, {"do": "capture"}])
        with self.assertRaisesRegex(ValueError, "expected EditControl, found ButtonControl"):
            server.control_set(automation_id="201", expected_control_type="EditControl", steps=[
                {"do": "click"}, {"do": "capture"}])
        self.assertEqual(self.window.invocations, count)
        self.assertEqual(self.text(), "")
        readonly = server.control_set(automation_id="102", steps=[{"do": "capture"}], delay_ms=0)
        self.assertTrue(json.loads(readonly[0])["ok"])

    def test_control_set_does_not_write_to_same_selector_replacement(self):
        from types import SimpleNamespace
        replaced = False

        def replace_field(_seconds):
            nonlocal replaced
            if replaced:
                return
            replaced = True

            def replace_on_ui():
                win32gui.DestroyWindow(self.window.controls[101])
                self.window.controls[101] = win32gui.CreateWindow(
                    "EDIT", "replacement untouched", win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_BORDER,
                    10, 20, 280, 30, self.window.hwnd, 101, 0, None)
            self.window.call_ui(replace_on_ui)

        with patch.object(control_sets, "time", SimpleNamespace(monotonic=time.monotonic, sleep=replace_field)):
            result = server.control_set(automation_id="101", steps=[
                {"do": "click"}, {"do": "type", "text": "must not write", "replace": True},
                {"do": "capture"}], delay_ms=1)
        report = json.loads(result[0])
        self.assertFalse(report["ok"])
        self.assertEqual(report["completed_steps"], 1)
        self.assertEqual(report["stopped_at_step"], 2)
        self.assertIn("bound control disappeared", report["steps"][-1]["result"])
        self.assertEqual(self.text(), "replacement untouched")
        self.assertEqual(len(result), 1)

    def test_control_set_failed_capture_keeps_completed_write_and_returns_no_image(self):
        server._state["seen"] = []
        with patch.object(screenshot, "_print_window", return_value=None):
            result = server.control_set(automation_id="101", steps=[
                {"do": "click"}, {"do": "type", "text": "write completed", "replace": True},
                {"do": "capture"}], delay_ms=0)
        report = json.loads(result[0])
        self.assertFalse(report["ok"])
        self.assertEqual(report["completed_steps"], 2)
        self.assertEqual(report["stopped_at_step"], 3)
        self.assertIn("covers it", report["steps"][-1]["result"])
        self.assertEqual(self.text(), "write completed")
        self.assertEqual(len(result), 1)
        self.assertIsNone(report["capture"])
        self.assertEqual(server._state["seen"], [])

    def test_control_capture_uses_current_bounds_and_refuses_racing_layout(self):
        from types import SimpleNamespace
        calls = 0

        def move_edit(x, y, width, height):
            self.window.call_ui(lambda: win32gui.SetWindowPos(
                self.window.controls[101], 0, x, y, width, height,
                win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER))

        def after_step(_seconds):
            nonlocal calls
            calls += 1
            if calls == 2:
                move_edit(20, 80, 320, 40)

        plan = [{"do": "click"}, {"do": "type", "text": "moved", "replace": True}, {"do": "capture"}]
        try:
            with patch.object(control_sets, "time", SimpleNamespace(monotonic=time.monotonic, sleep=after_step)):
                result = server.control_set(automation_id="101", steps=plan, delay_ms=1)
            report = json.loads(result[0])
            self.assertTrue(report["ok"])
            self.assertEqual(report["capture"]["region"], [20, 80, 340, 120])
            self.assertEqual(report["capture"]["crop_size"], [320, 40])
            original_capture = screenshot.grab_window

            def racing_capture(hwnd):
                frame = original_capture(hwnd)
                move_edit(30, 80, 320, 40)
                return frame

            with patch.object(screenshot, "grab_window", side_effect=racing_capture):
                result = server.control_set(automation_id="101", steps=plan, delay_ms=0)
            report = json.loads(result[0])
            self.assertFalse(report["ok"])
            self.assertEqual(report["completed_steps"], 2)
            self.assertIn("geometry changed", report["steps"][-1]["result"])
            self.assertEqual(len(result), 1)
        finally:
            move_edit(10, 20, 280, 30)

    def test_unicode_replace_append_readback(self):
        result = json.loads(server.type_text("สวัสดี 😀 {+^%}", automation_id="101", replace=True))
        self.assertTrue(result["verified"])
        self.assertEqual(self.text(), "สวัสดี 😀 {+^%}")
        server.type_text(" ต่อท้าย")
        self.assertEqual(self.text(), "สวัสดี 😀 {+^%} ต่อท้าย")
        self.assertEqual(json.loads(server.read_element(automation_id="101"))["value"], self.text())

    def test_logical_edit_target_without_focus(self):
        result = json.loads(server.click_element(automation_id="101"))
        self.assertIn("selected text target", result["action"])
        server.type_text("logical")
        self.assertEqual(self.text(), "logical")

    def test_command_preserves_logical_text_target(self):
        server.click_element(automation_id="101")
        server.type_text("before")
        server.click_element("Run")
        server.type_text(" after")
        self.assertEqual(self.text(), "before after")

    def test_named_button_invokes_real_handler(self):
        count = self.window.invocations
        result = json.loads(server.click_element("Run"))
        self.assertEqual(result["action"], "invoked")
        deadline = time.monotonic() + 2
        while self.window.invocations == count and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.window.invocations, count + 1)

    def test_coordinate_button_uses_target_tree_under_cover(self):
        count = self.window.invocations
        x, y = self.window.center(201)
        self.assertIn("invoked", server.click(x, y, force=True, keep_cursor=True))
        deadline = time.monotonic() + 2
        while self.window.invocations == count and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.window.invocations, count + 1)

    def test_checkbox_toggles_and_reads_state(self):
        before = json.loads(server.read_element(automation_id="202"))["toggle_state"]
        self.assertEqual(json.loads(server.click_element(automation_id="202"))["action"], "toggled")
        after = json.loads(server.read_element(automation_id="202"))["toggle_state"]
        self.assertNotEqual(before, after)

    def test_numeric_slider_set_readback(self):
        result = json.loads(server.set_value(37, automation_id="301"))
        self.assertTrue(result["verified"])
        self.assertEqual(json.loads(server.read_element(automation_id="301"))["range_value"], 37)
        with self.assertRaises(ValueError):
            server.set_value(1000, automation_id="301")

    def test_scroll_pattern_changes_list_position(self):
        box = self.window.controls[401]
        before = win32gui.SendMessage(box, win32con.LB_GETTOPINDEX, 0, 0)
        x, y = self.window.center(401)
        server.scroll(x, y, -4)
        after = win32gui.SendMessage(box, win32con.LB_GETTOPINDEX, 0, 0)
        self.assertGreater(after, before)

    def test_ambiguous_and_empty_selectors_refused(self):
        for name in ("Duplicate", "", "  "):
            with self.subTest(name=name), self.assertRaises(ValueError):
                server.click_element(name)
        with self.assertRaises(ValueError):
            server.type_text("must not choose between edits")
        self.assertEqual(self.text(), "")

    def test_disabled_readonly_and_canvas_refused(self):
        with self.assertRaisesRegex(ValueError, "disabled"):
            server.click_element(automation_id="205")
        with self.assertRaisesRegex(ValueError, "read-only"):
            server.type_text("bad", automation_id="102", replace=True)
        with self.assertRaises(input_sim.UnsupportedAction):
            server.click(*self.window.center(501), force=True)
        self.assertEqual(self.text(), "")

    def test_unsupported_gestures_never_take_desktop(self):
        calls = [lambda: server.hover(20, 20, force=True),
                 lambda: server.drag(20, 20, 80, 80, force=True),
                 lambda: server.press_key("enter"), lambda: server.hotkey(["ctrl", "s"]),
                 lambda: server.click_element("Run", button="right"),
                 lambda: server.click_element("Run", double=True),
                 lambda: server.click(20, 20, modifiers=["ctrl"], force=True),
                 lambda: server.attach_window(self.window.hwnd, take_control=True),
                 lambda: server.keep_foreground(True)]
        for call in calls:
            with self.subTest(call=call), self.assertRaises(input_sim.UnsupportedAction):
                call()

    def test_batch_validation_before_any_mutation(self):
        for bad in [{"do": "key", "key": "enter"}, {"do": "drag", "x1": 1},
                    {"do": "click_element", "name": "Run", "double": True},
                    {"do": "type"}, {"do": "click", "x": 10}, {"do": "unknown"},
                    {"do": "click", "x": "bad", "y": 10},
                    {"do": "type", "text": "bad", "name": " "},
                    {"do": "set_value", "value": float("nan"), "automation_id": "301"},
                    {"do": "wait_stable"},
                    {"do": "check", "region": [0, 1]}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                server.run_steps([{"do": "type", "text": "bad", "automation_id": "101"}, bad])
            self.assertEqual(self.text(), "")

    def test_batch_semantic_results_and_runtime_stop(self):
        result = server.run_steps([
            {"do": "type", "text": "batch", "automation_id": "101", "replace": True},
            {"do": "type", "text": " ✓"},
            {"do": "click_element", "name": "Run"},
        ], delay_ms=0)
        summary = json.loads(result[0])
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["performed"], 3)
        self.assertEqual(self.text(), "batch ✓")
        result = server.run_steps([
            {"do": "click_element", "name": "does not exist"},
            {"do": "type", "text": "bad", "automation_id": "101"},
        ], delay_ms=0)
        self.assertEqual(json.loads(result[0])["stopped_at_step"], 1)
        self.assertEqual(self.text(), "batch ✓")

    def test_uipi_refusal_before_any_action(self):
        with patch.object(integrity, "window_level", return_value=integrity.HIGH), \
                patch.object(integrity, "_OWN_LEVEL", integrity.MEDIUM), \
                patch.object(integrity, "_OWN_UIACCESS", False):
            calls = [lambda: server.type_text("bad", automation_id="101"),
                     lambda: server.control_set(automation_id="101", steps=[
                         {"do": "type", "text": "bad"}, {"do": "capture"}]),
                     lambda: server.click_element("Run"),
                     lambda: server.scroll(20, 20, -1),
                     lambda: server.set_value(12, automation_id="301")]
            for call in calls:
                with self.assertRaises(integrity.InputBlocked):
                    call()
        self.assertEqual(self.text(), "")

    def test_keyboard_tools_stay_passive(self):
        server.release_keyboard(True)
        server.release_control()
        state = json.loads(server.keyboard_status())
        self.assertFalse(state["blocking"])
        self.assertFalse(state["watching"])
        self.assertFalse(state["blocking_enabled"])
        self.assertNotIn("input_guard", sys.modules)

    def test_stale_guard_and_snapshot_journal(self):
        from PIL import Image, ImageDraw
        base = Image.new("RGB", (200, 200), "white")
        altered = base.copy()
        ImageDraw.Draw(altered).rectangle((90, 90, 110, 110), fill="black")
        server._state["seen"] = []
        server._mark_seen(base)
        server._state["pre_frame"] = altered
        self.assertTrue(json.loads(server._stale_block(100, 100, "test")[0])["blocked"])
        server._state["pre_frame"] = None
        server._state["seen"] = []
        server.snapshot()
        server.type_text("visible change", automation_id="101", replace=True)
        self.assertIn("changed_bbox", json.loads(server.diff_since_snapshot(region=[0, 0, 300, 80])))
        records = json.loads(server.history())["records"]
        typed = next(r for r in records if r["tool"] == "type_text")
        self.assertTrue(typed["ok"])
        self.assertIn("before", typed)
        self.assertIn("after", typed)
        self.assertEqual(len(server.replay_frame(typed["seq"])), 2)

    def test_capture_refuses_wrong_cover_pixels(self):
        with patch.object(screenshot, "_print_window", return_value=None):
            with self.assertRaisesRegex(ValueError, "covers it"):
                screenshot.grab_window(self.window.hwnd)

    def test_mcp_worker_calls_initialize_com(self):
        async def scenario():
            tools = await server.mcp.list_tools()
            self.assertIn("read_element", [t.name for t in tools])
            result = await server.mcp.call_tool("type_text", {
                "text": "worker สวัสดี", "automation_id": "101", "replace": True,
            })
            self.assertFalse(result.is_error)
            result = await server.mcp.call_tool("read_element", {"automation_id": "101"})
            self.assertFalse(result.is_error)
            data = json.loads(next(b.text for b in result.content if b.type == "text"))
            self.assertEqual(data["value"], "worker สวัสดี")
            result = await server.mcp.call_tool("control_set", {
                "automation_id": "101", "expected_control_type": "EditControl", "delay_ms": 0,
                "steps": [{"do": "click"}, {"do": "type", "text": "worker crop", "replace": True},
                          {"do": "capture"}],
            })
            self.assertFalse(result.is_error)
            report = json.loads(next(b.text for b in result.content if b.type == "text"))
            self.assertTrue(report["ok"])
            self.assertEqual(len([b for b in result.content if b.type == "image"]), 1)
            self.assertEqual(self.text(), "worker crop")
        asyncio.run(scenario())

    def test_uia_calls_in_host_sta_thread(self):
        def scenario():
            import pythoncom
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            try:
                return uia_inspect.get_elements(self.window.hwnd)
            finally:
                pythoncom.CoUninitialize()
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            elements = pool.submit(scenario).result(timeout=10)
        self.assertIn("201", [e["automation_id"] for e in elements])

    def test_coordinate_label_resolves_enclosing_button(self):
        from types import SimpleNamespace
        def wrapper(kind, rect):
            return SimpleNamespace(element_info=SimpleNamespace(control_type=kind),
                                   rectangle=lambda: SimpleNamespace(
                                       left=rect[0], top=rect[1], right=rect[2], bottom=rect[3]))
        button = wrapper("Button", (110, 110, 210, 150))
        text = wrapper("Text", (130, 120, 180, 140))
        with patch.object(uia_inspect, "root_wrapper"), \
                patch.object(uia_inspect, "walk", return_value=iter([button, text])), \
                patch.object(input_sim, "get_client_size", return_value=(300, 300)), \
                patch.object(window_manager, "get_client_rect_screen", return_value=(100, 100, 400, 400)):
            self.assertIs(input_sim._at_point(self.window.hwnd, 50, 30, interactive=True), button)

    def test_action_selector_refuses_incomplete_tree(self):
        from types import SimpleNamespace
        from functools import partial
        info = SimpleNamespace(name="Duplicate", automation_id="1", control_type="Button")
        leaf = SimpleNamespace(element_info=info, children=lambda: [])
        root = SimpleNamespace(element_info=info, children=lambda: [leaf])
        bounded_walk = partial(uia_inspect.walk, max_nodes=1)
        with patch.object(uia_inspect, "root_wrapper", return_value=root), \
                patch.object(uia_inspect, "walk", side_effect=bounded_walk):
            with self.assertRaisesRegex(ValueError, "uniqueness cannot be verified"):
                input_sim._find(self.window.hwnd, name="Duplicate")


if __name__ == "__main__":
    unittest.main(verbosity=2)
