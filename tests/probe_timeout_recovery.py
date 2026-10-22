"""Explicit bounded foreground proof for #21/#23/#27, owned native windows only.

Not included in smoke discovery. Never replay a partial text mutation. Every
following batch has a fresh synthetic replacement or a read-only purpose.
"""
import ctypes
import json
from pathlib import Path
import platform
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import foreground_control
import win32con
import win32gui
from test_pywinauto import TestWindow


def main():
    with tempfile.TemporaryDirectory(prefix='winauto-recovery-proof-') as journal_dir:
        server.journal.ROOT = journal_dir
        window = TestWindow(auto_scroll=True)
        results = []
        try:
            window.call_ui(lambda: win32gui.SetWindowPos(window.hwnd, 0, 0, 100, 740, 560,
                win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER))
            win32gui.ShowWindow(window.cover, win32con.SW_HIDE)
            def allow_long_text():
                edit = window.controls[101]
                style = win32gui.GetWindowLong(edit, win32con.GWL_STYLE)
                win32gui.SetWindowLong(edit, win32con.GWL_STYLE, style | win32con.ES_AUTOHSCROLL)
                win32gui.SendMessage(edit, win32con.EM_SETLIMITTEXT, 4000, 0)
            window.call_ui(allow_long_text)
            server.attach_window(window.hwnd, label='Owned timeout recovery test')
            dpi = ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(window.hwnd))
            cases = [('ascii101', 'A' * 101, 4000),
                     ('unicode180', ('ภาษาไทยabc' * 18), 6000),
                     ('short_lease', 'ท' * 2000, 1500)]
            for name, text, timeout in cases:
                # A fresh replacement is deliberate test input, never an append retry.
                result = server.foreground_steps(reason='ทดสอบข้อความและ cleanup ในหน้าต่างทดสอบ',
                    timeout_ms=timeout, allow_takeover=True, steps=[
                        {'do': 'click', 'x': 80, 'y': 35},
                        {'do': 'type', 'text': text, 'replace': True}])
                report = json.loads(result[0])
                actual = json.loads(server.read_element(automation_id='101'))['value']
                held = foreground_control.held_virtual_keys()
                proof = {'case': name, 'report': report, 'expected_characters': len(text),
                         'actual_characters': len(actual), 'readback_matches': actual == text,
                         'readback_is_prefix': text.startswith(actual), 'held_virtual_keys': held}
                results.append(proof)
                print(json.dumps(proof, ensure_ascii=True), flush=True)
                if not report.get('ok') and not report.get('keys_released'):
                    raise AssertionError('owned key release unconfirmed; stopped without further input')
                if not report.get('mouse_buttons_released') or held:
                    raise AssertionError('input release not established; stopped without further input')
                if name != 'short_lease':
                    assert report['ok'] and actual == text, proof
                else:
                    assert report['phase'] == 'released', proof
                    assert report['ok'] or 'cleanup reserve' in report.get('error', ''), proof
                    assert text.startswith(actual), proof
            # No replay: a click-only batch checks that the next acquisition is usable.
            following = json.loads(server.foreground_steps(reason='ตรวจรับ control หลัง cleanup',
                timeout_ms=3000, allow_takeover=True,
                steps=[{'do': 'click', 'x': 80, 'y': 35}])[0])
            assert following['ok'], following
            hung = TestWindow()
            ready, release = threading.Event(), threading.Event()
            errors = []
            def busy_ui():
                try:
                    hung.call_ui(lambda: (ready.set(), release.wait(8)))
                except Exception as exc:
                    errors.append(str(exc))
            busy = threading.Thread(target=busy_ui)
            try:
                win32gui.ShowWindow(hung.cover, win32con.SW_HIDE)
                busy.start()
                assert ready.wait(1), 'owned unresponsive fixture did not start'
                refused, _ = foreground_control.run(hung.hwnd,
                    [{'do': 'click', 'x': 80, 'y': 35}], 'Owned unresponsive activation test',
                    timeout_ms=2000, allow_takeover=True)
                assert not refused['ok'] and refused['completed_steps'] == 0, refused
                assert refused['failure_phase'] == 'activating', refused
                assert refused['failure_native_phase'] == 'activation_probe', refused
                assert refused['phase'] == 'released' and refused['keys_released'], refused
                print(json.dumps({'case': 'unresponsive_activation', 'report': refused}, ensure_ascii=True))
            finally:
                release.set()
                if busy.ident:
                    busy.join(2)
                hung.close()
            assert not errors and not busy.is_alive(), errors
            print(json.dumps({'ok': True, 'platform': platform.platform(), 'dpi': dpi,
                              'cases': len(results), 'following_batch': following}, ensure_ascii=True))
        finally:
            server.detach_window()
            window.close()


if __name__ == '__main__':
    main()
