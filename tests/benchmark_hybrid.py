r"""Repeatable local benchmark; disposable windows only, no physical input.

Run from repo: .venv\Scripts\python.exe tests\benchmark_hybrid.py
Retained for measuring future changes to batch capture and comparison costs.
"""
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import guarded_batch
import server
import visual_guard
from test_pywinauto import TestWindow


def median_ms(action, rounds):
    samples = []
    for _ in range(rounds):
        started = time.perf_counter()
        action()
        samples.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(samples), 3)


def main():
    pixels = []
    for size in ((80, 40), (400, 300), (1280, 720)):
        before = Image.new("RGB", size, "white")
        after = before.copy()
        ImageDraw.Draw(after).rectangle((5, 5, 20, 20), fill="black")
        guard = {"region": [0, 0, *size]}
        row = {"size": list(size)}
        for backend in ("pillow", "opencv"):
            if backend == "opencv" and visual_guard.cv2 is None:
                continue
            row[backend + "_median_ms"] = median_ms(lambda: visual_guard.compare(before, after, guard, backend), 40)
        pixels.append(row)
    plan = [
        {"do": "type", "automation_id": "101", "text": "benchmark", "replace": True},
        {"do": "set_value", "automation_id": "301", "value": 30},
        {"do": "click_element", "automation_id": "201"},
    ]
    batches = {}
    with tempfile.TemporaryDirectory(prefix="winauto-benchmark-") as folder, \
            patch.object(server.journal, "ROOT", folder):
        window = TestWindow()
        try:
            server.attach_window(window.hwnd)
            # Warm provider paths before measuring either implementation.
            server.get_elements()
            for name in ("run_steps", "guarded_steps"):
                captures = []
                original = server.grab_window if name == "run_steps" else guarded_batch.screenshot.grab_window
                module = server if name == "run_steps" else guarded_batch.screenshot
                def execute():
                    with patch.object(module, "grab_window", wraps=original) as grab:
                        result = (server.run_steps(plan, delay_ms=0) if name == "run_steps"
                                  else server.guarded_steps(plan))
                        report = json.loads(result[0])
                        if not report["ok"]:
                            raise RuntimeError(report)
                        captures.append(grab.call_count)
                batches[name] = {"median_ms": median_ms(execute, 7), "captures_per_batch": captures}
        finally:
            server.detach_window()
            window.close()
    print(json.dumps({"pixel_comparison": pixels, "owned_window_three_actions": batches,
                      "scope": "local medians; excludes AI/network latency; no universal speed claim"}, indent=2))


if __name__ == "__main__":
    main()
