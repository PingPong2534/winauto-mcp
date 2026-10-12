"""Hybrid guards: pixel parity, stop-before-action, identity, real owned UIA.

Only disposable test windows are used; no physical input or focus changes.
"""
import contextlib
import gc
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import guarded_batch
import input_sim
import server
import visual_guard
import win32api
import win32con
import win32gui
from test_pywinauto import TestWindow, last_input_tick


class VisualGuardTests(unittest.TestCase):
    def test_backends_match_channel_threshold_ratio_mask_and_bbox(self):
        a = Image.new("RGB", (90, 80), "black")
        b = a.copy()
        ImageDraw.Draw(b).rectangle((21, 22, 25, 27), fill=(0, 0, 20))
        b.putpixel((29, 29), (0, 0, 10))  # exactly threshold: ignored
        guard = {"region": [20, 20, 40, 40], "threshold": 10,
                 "ignore_regions": [[21, 22, 23, 28], [22, 22, 24, 28]]}
        pillow = visual_guard.compare(a, b, guard, "pillow")
        self.assertEqual(pillow["changed_pixels"], 12)
        self.assertEqual(pillow["compared_pixels"], 382)
        self.assertEqual(pillow["changed_bbox"], [24, 22, 26, 28])
        self.assertFalse(pillow["unchanged"])
        if visual_guard.cv2 is not None:
            cv = visual_guard.compare(a, b, guard, "opencv")
            cv.pop("backend")
            self.assertEqual(cv, {k: v for k, v in pillow.items() if k != "backend"})
        guard["max_changed_ratio"] = 12 / 382
        self.assertTrue(visual_guard.compare(a, b, guard)["unchanged"])

    def test_noise_and_outside_roi_do_not_block(self):
        a = Image.new("RGB", (50, 50), "white")
        b = a.copy()
        b.putpixel((5, 5), (0, 0, 0))
        b.putpixel((21, 21), (250, 250, 250))
        self.assertTrue(visual_guard.compare(a, b, {"region": [20, 20, 30, 30]})["unchanged"])

    def test_changed_size_and_outside_regions_fail_closed(self):
        a = Image.new("RGB", (50, 50))
        with self.assertRaisesRegex(ValueError, "size changed"):
            visual_guard.compare(a, Image.new("RGB", (60, 50)), {"region": [0, 0, 10, 10]})
        with self.assertRaisesRegex(ValueError, "outside"):
            visual_guard.compare(a, a, {"region": [0, 0, 51, 50]})

    def test_ignored_union_cannot_disable_guard(self):
        with self.assertRaisesRegex(ValueError, "entire"):
            visual_guard.validate({"region": [0, 0, 20, 20],
                                   "ignore_regions": [[0, 0, 10, 20], [10, 0, 20, 20]]})

    def test_auto_fallback_and_explicit_missing_opencv(self):
        a = Image.new("RGB", (400, 400))
        with patch.object(visual_guard, "cv2", None):
            self.assertEqual(visual_guard.compare(a, a, {"region": [0, 0, 400, 400]})["backend"], "pillow")
            with self.assertRaisesRegex(ValueError, "OpenCV unavailable"):
                visual_guard.compare(a, a, {"region": [0, 0, 10, 10]}, "opencv")
        self.assertEqual(visual_guard.compare(a, a, {"region": [0, 0, 20, 20]})["backend"],
                         "opencv" if visual_guard.cv2 is not None else "pillow")
        if visual_guard.cv2 is not None:
            self.assertEqual(visual_guard.compare(a, a, {"region": [0, 0, 400, 400]})["backend"], "opencv")

    @unittest.skipIf(visual_guard.cv2 is None, "OpenCV not installed")
    def test_random_rgb_modes_and_thresholds_have_backend_parity(self):
        rng = visual_guard.np.random.default_rng(42)
        a = Image.fromarray(rng.integers(0, 256, (60, 80, 3), dtype="uint8"))
        b = Image.fromarray(rng.integers(0, 256, (60, 80, 3), dtype="uint8"))
        for mode in ("RGB", "RGBA", "L"):
            for threshold in (0, 10, 200, 255):
                guard = {"region": [5, 10, 70, 50], "threshold": threshold,
                         "ignore_regions": [[10, 12, 20, 30], [15, 20, 40, 35]]}
                p = visual_guard.compare(a.convert(mode), b.convert(mode), guard, "pillow")
                c = visual_guard.compare(a.convert(mode), b.convert(mode), guard, "opencv")
                p.pop("backend")
                c.pop("backend")
                self.assertEqual(p, c, (mode, threshold))


class ApartmentLifetimeTests(unittest.TestCase):
    def test_cyclic_interfaces_are_collected_on_caller_and_gc_state_is_restored(self):
        owner = threading.get_ident()
        released = []
        class InterfaceCycle:
            def __init__(self):
                self.cycle = self
            def __del__(self):
                released.append(threading.get_ident())
        def execute(*args):
            self.assertFalse(gc.isenabled())
            InterfaceCycle()
            return {}, None, None
        original = gc.isenabled()
        try:
            for enabled in (True, False):
                (gc.enable if enabled else gc.disable)()
                with patch.object(guarded_batch, "_run", side_effect=execute):
                    guarded_batch.run(0, [], [])
                self.assertEqual(gc.isenabled(), enabled)
            self.assertEqual(released, [owner, owner])
        finally:
            (gc.enable if original else gc.disable)()


class HybridBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="winauto-hybrid-test-")
        cls.journal_patch = patch.object(server.journal, "ROOT", cls.folder.name)
        cls.journal_patch.start()
        cls.window = TestWindow()

    @classmethod
    def tearDownClass(cls):
        server.detach_window()
        cls.window.close()
        cls.journal_patch.stop()
        cls.folder.cleanup()

    def setUp(self):
        server.attach_window(self.window.hwnd)
        win32gui.SendMessage(self.window.controls[101], win32con.WM_SETTEXT, 0, "")
        self.focus = win32gui.GetForegroundWindow()
        self.cursor = win32api.GetCursorPos()
        self.input_tick = last_input_tick()
        self.stack = contextlib.ExitStack()
        for module, names in ((win32gui, ("SetForegroundWindow", "BringWindowToTop", "SetFocus")),
                              (win32api, ("SetCursorPos", "mouse_event", "keybd_event"))):
            for name in names:
                self.stack.enter_context(patch.object(module, name, side_effect=AssertionError("physical input attempted")))
        for path in ("pywinauto.base_wrapper.BaseWrapper.click_input",
                     "pywinauto.base_wrapper.BaseWrapper.type_keys",
                     "pywinauto.controls.uiawrapper.UIAWrapper.set_focus"):
            self.stack.enter_context(patch(path, side_effect=AssertionError("physical input attempted")))

    def tearDown(self):
        self.stack.close()
        self.assertEqual(win32gui.GetForegroundWindow(), self.focus)
        if last_input_tick() == self.input_tick:
            self.assertEqual(win32api.GetCursorPos(), self.cursor)
        self.assertFalse(win32gui.GetWindowLong(self.window.hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_NOACTIVATE)

    def run_plan(self, steps, **kwargs):
        result = server.guarded_steps(steps, **kwargs)
        return json.loads(result[0]), result

    def text(self):
        return win32gui.GetWindowText(self.window.controls[101])

    def frames(self):
        size = server.window_manager.get_client_size(self.window.hwnd)
        base = Image.new("RGB", size, "white")
        server._mark_seen(base)
        changed = base.copy()
        ImageDraw.Draw(changed).rectangle((10, 105, 30, 120), fill="black")
        return base, changed

    def test_semantic_success_real_uia_has_zero_capture_cost(self):
        with patch.object(guarded_batch.screenshot, "grab_window", side_effect=AssertionError("unnecessary capture")):
            report, result = self.run_plan([
                {"do": "type", "automation_id": "101", "text": "ไทย🙂", "replace": True,
                 "before": {"value": ""}, "expect": {"value": "ไทย🙂"}},
                {"do": "set_value", "automation_id": "301", "value": 30, "expect": {"range_value": 30}},
                {"do": "read", "automation_id": "101", "expect": {"value": "ไทย🙂"}},
            ])
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["completed_steps"], 3)
        self.assertEqual(report["completed_actions"], 2)
        self.assertEqual(report["capture_count"], 0)
        self.assertEqual(len(result), 1)

    def test_changed_next_area_stops_before_second_action(self):
        _, changed = self.frames()
        with patch.object(guarded_batch.screenshot, "grab_window", return_value=changed):
            report, result = self.run_plan([
                {"do": "type", "automation_id": "101", "text": "done", "replace": True},
                {"do": "click_element", "automation_id": "201", "guard": {"region": [10, 105, 110, 135]}},
            ])
        self.assertEqual(report["status"], "area_changed")
        self.assertEqual(report["completed_actions"], 1)
        self.assertEqual(report["stopped_at_step"], 2)
        self.assertEqual(report["steps"][1]["action_outcome"], "not_attempted")
        self.assertEqual(report["recommended_tool"], "capture_screen")
        self.assertEqual(report["capture"]["coordinate_offset"], [10, 105])
        self.assertEqual(len(result), 2)
        self.assertEqual(self.text(), "done")

    def test_immutable_reference_does_not_accept_progressively_changed_frames(self):
        base, changed = self.frames()
        with patch.object(guarded_batch.screenshot, "grab_window", side_effect=[base, changed]):
            report, _ = self.run_plan([
                {"do": "read", "automation_id": "101", "guard": {"region": [10, 105, 110, 135]}},
                {"do": "click_element", "automation_id": "201", "guard": {"region": [10, 105, 110, 135]}},
            ])
        self.assertEqual(report["status"], "area_changed")
        self.assertEqual(report["completed_actions"], 0)

    def test_real_observed_coordinate_guard_still_uses_uia_under_cover(self):
        server.screenshot()
        x, y = self.window.center(201)
        report, _ = self.run_plan([{"do": "click", "x": x, "y": y,
                                    "guard": {"region": [x - 8, y - 8, x + 8, y + 8]}}])
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["steps"][0]["result"], "invoked")

    def test_partial_seen_region_cannot_authorize_unseen_guard(self):
        base, _ = self.frames()
        server._state["seen"] = []
        server._mark_seen(base, (0, 0, 5, 5))
        report, _ = self.run_plan([{"do": "type", "automation_id": "101", "text": "bad",
                                    "guard": {"region": [0, 0, 10, 10]}}])
        self.assertEqual(report["status"], "baseline_unavailable")
        self.assertEqual(self.text(), "")

    def test_capture_failure_stops_without_fallback_or_action(self):
        self.frames()
        with patch.object(guarded_batch.screenshot, "grab_window", side_effect=ValueError("covered")):
            report, result = self.run_plan([{"do": "type", "automation_id": "101", "text": "bad",
                                            "guard": {"region": [0, 0, 10, 10]}}])
        self.assertEqual(report["status"], "capture_unavailable")
        self.assertEqual(report["completed_actions"], 0)
        self.assertEqual(len(result), 1)
        self.assertIn("capture_error", report)

    def test_runtime_precondition_mismatch_preserves_completed_write(self):
        report, _ = self.run_plan([
            {"do": "type", "automation_id": "101", "text": "done", "replace": True},
            {"do": "type", "automation_id": "101", "text": "bad", "before": {"value": ""}},
        ])
        self.assertEqual(report["status"], "state_mismatch")
        self.assertEqual(report["completed_actions"], 1)
        self.assertEqual(self.text(), "done")

    def test_expectation_wait_does_not_repeat_mutation(self):
        with patch.object(input_sim, "_type_into", wraps=input_sim._type_into) as write:
            report, _ = self.run_plan([{"do": "type", "automation_id": "101", "text": "once",
                                        "expect": {"value": "not ready"}, "wait_ms": 60}])
        self.assertEqual(write.call_count, 1)
        self.assertEqual(report["status"], "expectation_failed")
        self.assertEqual(report["completed_actions"], 1)
        self.assertEqual(report["completed_steps"], 0)
        self.assertEqual(report["steps"][0]["action_outcome"], "performed")
        self.assertEqual(self.text(), "once")

    def test_invalid_later_step_and_unsupported_controls_do_zero_actions(self):
        first = {"do": "type", "automation_id": "101", "text": "bad"}
        bad_steps = [
            {"do": "click", "x": 20, "y": 20},
            {"do": "type", "automation_id": "101", "text": "bad", "force": True},
            {"do": "read", "automation_id": "101", "expect": {"enabled": "yes"}},
            {"do": "read", "automation_id": "101", "wait_ms": 1},
            {"do": "click", "x": 2, "y": 2, "guard": {"region": [0, 0, 5, 5], "ignore_regions": [[1, 1, 3, 3]]}},
        ]
        for bad in bad_steps:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.run_plan([first, bad])
            self.assertEqual(self.text(), "")
        for ident in ("102", "205", "missing"):
            second = {"do": "type", "automation_id": ident, "text": "bad"}
            report, _ = self.run_plan([first, second])
            self.assertEqual(report["status"], "preflight_failed", report)
            self.assertEqual(self.text(), "")

    def test_input_integrity_refusal_happens_before_mutations(self):
        with patch.object(guarded_batch.window_manager, "ensure_input_allowed", side_effect=PermissionError("higher integrity")):
            report, _ = self.run_plan([{"do": "type", "automation_id": "101", "text": "bad"}])
        self.assertEqual(report["status"], "preflight_failed")
        self.assertEqual(report["completed_actions"], 0)
        self.assertEqual(self.text(), "")

    def test_failure_after_provider_write_reports_unknown_and_fresh_evidence(self):
        original = input_sim._type_into
        def fail_after_write(*args):
            original(*args)
            raise RuntimeError("provider lost response after write")
        with patch.object(input_sim, "_type_into", side_effect=fail_after_write):
            report, _ = self.run_plan([{"do": "type", "automation_id": "101", "text": "written"}])
        self.assertEqual(report["steps"][0]["action_outcome"], "unknown")
        self.assertEqual(self.text(), "written")
        self.assertTrue(report["capture"]["fresh"])

    def test_replaced_identity_is_never_reselected_by_same_selector(self):
        with patch.object(guarded_batch, "_bound_state", side_effect=ValueError("the bound control disappeared")):
            report, _ = self.run_plan([{"do": "type", "automation_id": "101", "text": "bad"}])
        self.assertEqual(report["status"], "step_failed")
        self.assertEqual(report["completed_actions"], 0)
        self.assertEqual(self.text(), "")

    def test_real_control_replacement_between_actions_keeps_replacement_untouched(self):
        original = input_sim._activate
        def replace_after_activate(hwnd, wrapper):
            result = original(hwnd, wrapper)
            def replace():
                win32gui.DestroyWindow(self.window.controls[101])
                self.window.controls[101] = win32gui.CreateWindow(
                    "EDIT", "replacement untouched", win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_BORDER,
                    10, 20, 280, 30, self.window.hwnd, 101, 0, None)
            self.window.call_ui(replace)
            return result
        with patch.object(input_sim, "_activate", side_effect=replace_after_activate):
            report, _ = self.run_plan([
                {"do": "click_element", "automation_id": "201"},
                {"do": "type", "automation_id": "101", "text": "bad", "replace": True},
            ])
        self.assertEqual(report["completed_actions"], 1)
        self.assertEqual(report["stopped_at_step"], 2)
        self.assertEqual(self.text(), "replacement untouched")

    def test_changed_geometry_during_guard_capture_stops_before_action(self):
        base, _ = self.frames()
        original = guarded_batch.window_manager.get_client_rect_screen
        def moving_geometry(hwnd):
            rect = original(hwnd)
            return (rect[0] + 1, rect[1], rect[2] + 1, rect[3]) if moved[0] else rect
        moved = [False]
        def grab(hwnd):
            moved[0] = True
            return base
        with patch.object(guarded_batch.screenshot, "grab_window", side_effect=grab), \
                patch.object(guarded_batch.window_manager, "get_client_rect_screen", side_effect=moving_geometry):
            report, _ = self.run_plan([{"do": "type", "automation_id": "101", "text": "bad",
                                        "guard": {"region": [0, 0, 10, 10]}}])
        self.assertEqual(report["status"], "geometry_changed")
        self.assertEqual(report["completed_actions"], 0)

    def test_bound_control_reparented_outside_target_is_refused(self):
        original = input_sim._activate
        def reparent_after_activate(hwnd, wrapper):
            result = original(hwnd, wrapper)
            self.window.call_ui(lambda: win32gui.SetParent(self.window.controls[101], self.window.cover))
            return result
        try:
            with patch.object(input_sim, "_activate", side_effect=reparent_after_activate):
                report, _ = self.run_plan([
                    {"do": "click_element", "automation_id": "201"},
                    {"do": "type", "automation_id": "101", "text": "bad"},
                ])
            self.assertEqual(report["status"], "target_changed", report)
            self.assertEqual(report["completed_actions"], 1)
            self.assertEqual(self.text(), "")
        finally:
            self.window.call_ui(lambda: win32gui.SetParent(self.window.controls[101], self.window.hwnd))

    def test_failed_final_capture_keeps_completed_action(self):
        with patch.object(guarded_batch.screenshot, "grab_window", side_effect=ValueError("no pixels")):
            report, result = self.run_plan([{"do": "type", "automation_id": "101", "text": "done"}], capture_final=True)
        self.assertEqual(report["status"], "capture_unavailable")
        self.assertEqual(report["phase"], "capture")
        self.assertIsNone(report["stopped_at_step"])
        self.assertEqual(report["completed_actions"], 1)
        self.assertEqual(self.text(), "done")
        self.assertEqual(len(result), 1)

    def test_deadline_stops_before_mutation(self):
        ticks = iter([0, 100, 100])
        with patch.object(guarded_batch, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=time.sleep)):
            report, _ = self.run_plan([{"do": "type", "automation_id": "101", "text": "bad"}], timeout_ms=1)
        self.assertEqual(report["status"], "timeout")
        self.assertEqual(report["completed_actions"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
