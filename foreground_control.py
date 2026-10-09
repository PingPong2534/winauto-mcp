"""Explicit, bounded real-input batches. Ordinary UIA tools never call this module.

The disposable worker owns SendInput and a dedicated low-level hook input gate.
A separate watchdog exits the worker on deadline or parent death; Windows then
removes the hooks even if the main thread is stuck in a native API.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import secrets
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

MAX_TIMEOUT_MS = 8000
KEYS = {'enter': 0x0D, 'tab': 0x09, 'escape': 0x1B, 'up': 0x26,
        'down': 0x28, 'left': 0x25, 'right': 0x27, 'home': 0x24,
        'end': 0x23, 'backspace': 0x08, 'delete': 0x2E,
        'ctrl': 0x11, 'shift': 0x10, 'alt': 0x12}
KEYS.update({chr(n): n - 32 for n in range(ord('a'), ord('z') + 1)})
KEYS.update({str(n): ord(str(n)) for n in range(10)})


def validate(steps, reason, timeout_ms, allow_takeover):
    if allow_takeover is not True:
        raise ValueError('foreground_steps requires allow_takeover=True; it moves focus/cursor and briefly blocks human input')
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 120 or any(ord(c) < 32 for c in reason):
        raise ValueError('reason must be a nonempty single-line string, at most 120 characters')
    if type(timeout_ms) is not int or not 1000 <= timeout_ms <= MAX_TIMEOUT_MS:
        raise ValueError('timeout_ms must be 1000..8000; the lease cannot be renewed')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise ValueError('steps must contain 1..20 steps')
    schemas = {'click': ({'x', 'y'}, {'button'}), 'hover': ({'x', 'y'}, {'dwell_ms'}),
               'scroll': ({'x', 'y', 'clicks'}, set()), 'type': ({'text'}, {'replace'}),
               'key': ({'keys'}, set()), 'wait': ({'ms'}, set()), 'capture': (set(), {'region'})}
    requested_ms = 400  # visible announcement before any input is blocked
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or step.get('do') not in schemas:
            raise ValueError(f'step {i}: unsupported step')
        required, optional = schemas[step['do']]
        if not required <= step.keys() or set(step) - required - optional - {'do'}:
            raise ValueError(f'step {i}: missing or unknown fields')
        for field in ('x', 'y', 'clicks', 'ms', 'dwell_ms'):
            if field in step and type(step[field]) is not int:
                raise ValueError(f'step {i}: {field} must be an integer')
        if step.get('button', 'left') not in ('left', 'right'):
            raise ValueError(f'step {i}: button must be left or right')
        if step['do'] == 'scroll' and not -20 <= step['clicks'] <= 20:
            raise ValueError(f'step {i}: clicks must be -20..20')
        if step['do'] == 'type':
            if not isinstance(step['text'], str) or len(step['text']) > 2000:
                raise ValueError(f'step {i}: text must be a string of at most 2000 characters')
            if type(step.get('replace', False)) is not bool:
                raise ValueError(f'step {i}: replace must be boolean')
        if step['do'] == 'key':
            keys = step['keys']
            if not isinstance(keys, list) or not 1 <= len(keys) <= 4 or any(not isinstance(k, str) or k not in KEYS for k in keys):
                raise ValueError(f'step {i}: keys must contain 1..4 supported lowercase key names')
            if len(set(keys)) != len(keys):
                raise ValueError(f'step {i}: duplicate keys')
        if step['do'] in ('wait', 'hover'):
            ms = step.get('ms', step.get('dwell_ms', 500))
            if not 0 <= ms <= 1500:
                raise ValueError(f'step {i}: wait/dwell must be 0..1500 ms')
            requested_ms += ms
        if step['do'] == 'capture':
            if i != len(steps) - 1:
                raise ValueError('capture is allowed only as the final step')
            region = step.get('region')
            if region is not None and (not isinstance(region, list) or len(region) != 4
                    or any(type(n) is not int for n in region) or region[2] <= region[0] or region[3] <= region[1]):
                raise ValueError(f'step {i}: region must be [x1,y1,x2,y2]')
    if requested_ms >= timeout_ms:
        raise ValueError('requested waits leave no time for actions inside the lease')


def _write_report(path, report):
    staged = path.with_suffix('.tmp')
    staged.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    staged.replace(path)


def run(hwnd, steps, reason, timeout_ms=3000, allow_takeover=False):
    validate(steps, reason, timeout_ms, allow_takeover)
    import window_manager
    from overlay import hidden_for_capture
    window_manager.ensure_input_allowed(hwnd)
    request = {'hwnd': hwnd, 'pid': window_manager.get_pid(hwnd), 'steps': steps,
               'reason': reason, 'timeout_ms': timeout_ms, 'parent_pid': os.getpid()}
    with tempfile.TemporaryDirectory(prefix='winauto-foreground-') as folder:
        request['folder'] = folder
        with hidden_for_capture():  # attachment decoration must not obstruct pointer targeting
            proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker'],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                _, stderr = proc.communicate(json.dumps(request).encode('utf-8'),
                                             timeout=timeout_ms / 1000 + 3)
            except BaseException:
                proc.kill()
                proc.communicate(timeout=2)
                raise
        report_path = Path(folder) / 'result.json'
        report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {
            'ok': False, 'completed_steps': 0, 'steps': []}
        if proc.returncode or report.get('phase') != 'released':
            report.update(ok=False, timed_out=proc.returncode == 124,
                          error=report.get('error', 'worker exited before cleanup completed'),
                          input_released_by_worker_exit=True,
                          interrupted_step_outcome='unknown; inspect state before retrying')
            report.update(input_blocked=False, phase='worker_exited')
            if stderr:
                report['diagnostic'] = stderr.decode('utf-8', errors='replace')[:1000]
        # A successful capture can precede a failed release; never mislabel it as batch success.
        image = Path(folder) / 'capture.png'
        return report, image.read_bytes() if image.exists() and report.get('ok') else None


class _Mouse(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD),
                ('dwFlags', wt.DWORD), ('time', wt.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class _Keyboard(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class _Hardware(ctypes.Structure):
    _fields_ = [('uMsg', wt.DWORD), ('wParamL', wt.WORD), ('wParamH', wt.WORD)]


class _Payload(ctypes.Union):
    _fields_ = [('mi', _Mouse), ('ki', _Keyboard), ('hi', _Hardware)]


class _Input(ctypes.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', wt.DWORD), ('data', _Payload)]


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('flags', wt.DWORD),
                ('hwndActive', wt.HWND), ('hwndFocus', wt.HWND),
                ('hwndCapture', wt.HWND), ('hwndMenuOwner', wt.HWND),
                ('hwndMoveSize', wt.HWND), ('hwndCaret', wt.HWND), ('rcCaret', wt.RECT)]


class NativeInput:
    def __init__(self, hwnd, pid):
        import win32gui
        import win32process
        import window_manager
        self.gui, self.process, self.windows = win32gui, win32process, window_manager
        self.hwnd, self.pid = hwnd, pid
        self.gate = None
        # Windows' mouse input path can truncate dwExtraInfo to 32 bits even
        # in a 64-bit process. Keep the per-worker tag within that width.
        self.tag = secrets.randbits(31) or 1
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.SendInput.argtypes = [wt.UINT, ctypes.POINTER(_Input), ctypes.c_int]
        self.user.SendInput.restype = wt.UINT
        self.user.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.POINTER(_GuiThreadInfo)]
        self.user.GetGUIThreadInfo.restype = wt.BOOL
        self.previous = self.gui.GetForegroundWindow()
        import win32api
        self.api = win32api
        self.cursor = win32api.GetCursorPos()

    def belongs(self, hwnd):
        if not hwnd or not self.gui.IsWindow(hwnd):
            return False
        if self.process.GetWindowThreadProcessId(hwnd)[1] != self.pid:
            return False
        root = self.gui.GetAncestor(hwnd, 2)  # GA_ROOT
        seen = set()
        while root and root not in seen:
            if root == self.hwnd:
                return True
            seen.add(root)
            root = self.gui.GetWindow(root, 4)  # GW_OWNER
        return False

    def guard(self):
        if self.gate:
            self.gate.check()
        if not self.gui.IsWindow(self.hwnd) or self.process.GetWindowThreadProcessId(self.hwnd)[1] != self.pid:
            raise RuntimeError('attached target closed or was replaced')
        if self.gui.IsIconic(self.hwnd) or not self.gui.IsWindowVisible(self.hwnd):
            raise RuntimeError('target must be visible and not minimized')
        self.windows.ensure_input_allowed(self.hwnd)
        if not self.belongs(self.gui.GetForegroundWindow()):
            raise RuntimeError('foreground left the attached window/owned popup; stopped without reacquiring focus')

    def block(self):
        if any(self.api.GetAsyncKeyState(n) & 0x8000 for n in range(1, 256)):
            raise RuntimeError('a human key/button is held; release it before requesting temporary control')
        from input_gate import InputGate
        self.gate = InputGate(self.tag)
        self.gate.start()

    def unblock(self):
        if self.gate:
            self.gate.close()

    def activate(self):
        if not self.gui.IsWindow(self.hwnd) or self.process.GetWindowThreadProcessId(self.hwnd)[1] != self.pid:
            raise RuntimeError('attached target was replaced')
        if self.gui.IsIconic(self.hwnd) or not self.gui.IsWindowVisible(self.hwnd):
            raise RuntimeError('target must be visible and not minimized')
        self.windows.ensure_input_allowed(self.hwnd)
        if not self.belongs(self.gui.GetForegroundWindow()):
            try:
                self.gui.SetForegroundWindow(self.hwnd)
            except Exception:
                pass
        if not self.belongs(self.gui.GetForegroundWindow()):
            # UIA's native SetFocus is a legitimate accessibility operation;
            # bypass pywinauto's wrapper, which may use cursor/Alt workarounds.
            import uia_inspect
            with uia_inspect.com_session():
                uia_inspect.root_wrapper(self.hwnd).element_info.element.SetFocus()
        self.guard()  # SetForegroundWindow can be denied; never inject into the old app

    def send(self, events):
        self.guard()
        for event in events:
            if event.type == 1:
                event.ki.dwExtraInfo = self.tag
            else:
                event.mi.dwExtraInfo = self.tag
        array = (_Input * len(events))(*events)
        if self.user.SendInput(len(array), array, ctypes.sizeof(_Input)) != len(array):
            raise RuntimeError('SendInput did not accept every event; state may be partial')

    def key(self, keys):
        self.keyboard_guard()
        events = []
        for up, seq in ((False, keys), (True, list(reversed(keys)))):
            for key in seq:
                flags = (2 if up else 0) | (1 if key in ('up', 'down', 'left', 'right', 'home', 'end', 'delete') else 0)
                events.append(_Input(type=1, ki=_Keyboard(wVk=KEYS[key], dwFlags=flags)))
        self.send(events)

    def text(self, text, replace):
        self.keyboard_guard()
        if replace:
            self.key(['ctrl', 'a'])
        encoded = text.encode('utf-16-le')
        # Recheck the foreground between small chunks, never send a long unguarded stream.
        for pos in range(0, len(encoded), 64):
            events = []
            for i in range(pos, min(pos + 64, len(encoded)), 2):
                unit = int.from_bytes(encoded[i:i + 2], 'little')
                events.extend((_Input(type=1, ki=_Keyboard(wScan=unit, dwFlags=4)),
                               _Input(type=1, ki=_Keyboard(wScan=unit, dwFlags=6))))
            self.send(events)

    def keyboard_guard(self):
        self.guard()
        info = _GuiThreadInfo(cbSize=ctypes.sizeof(_GuiThreadInfo))
        if not self.user.GetGUIThreadInfo(0, ctypes.byref(info)) or not self.belongs(info.hwndFocus):
            raise RuntimeError('actual keyboard focus is outside the attached window/owned popup')

    def point(self, x, y):
        self.guard()
        point = self.gui.ClientToScreen(self.hwnd, (x, y))
        if not self.belongs(self.gui.WindowFromPoint(point)):
            raise RuntimeError('pointer point is covered or outside the attached window/owned popup')
        # Tagged SendInput motion is allowed by our gate; human/foreign motion is suppressed.
        left, top = self.api.GetSystemMetrics(76), self.api.GetSystemMetrics(77)
        width, height = self.api.GetSystemMetrics(78), self.api.GetSystemMetrics(79)
        dx, dy = round((point[0] - left) * 65535 / (width - 1)), round((point[1] - top) * 65535 / (height - 1))
        self.send([_Input(type=0, mi=_Mouse(dx=dx, dy=dy, dwFlags=0xC001))])
        deadline = time.monotonic() + 0.15
        while time.monotonic() < deadline:
            actual = self.api.GetCursorPos()
            if abs(actual[0] - point[0]) <= 2 and abs(actual[1] - point[1]) <= 2:
                break
            self.guard()
            time.sleep(0.005)
        else:
            raise RuntimeError(f'injected pointer movement did not reach {point}; actual={self.api.GetCursorPos()}, own_hook_events={self.gate.own_events}')
        self.guard()
        if not self.belongs(self.gui.WindowFromPoint(self.api.GetCursorPos())):
            raise RuntimeError('pointer target changed before input')

    def mouse(self, flags, data=0):
        self.send([_Input(type=0, mi=_Mouse(dwFlags=flag, mouseData=data & 0xFFFFFFFF)) for flag in flags])

    def restore(self):
        # Restore before unblocking, so cleanup cannot override a subsequent human switch.
        if (self.gate and self.gate.cancelled.is_set()) or not self.belongs(self.gui.GetForegroundWindow()):
            return {'focus_restored': False, 'cursor_restored': False}
        self.api.SetCursorPos(self.cursor)
        restored = self.previous == self.gui.GetForegroundWindow()
        if self.previous and self.gui.IsWindow(self.previous) and not restored:
            try:
                self.gui.SetForegroundWindow(self.previous)
                restored = self.gui.GetForegroundWindow() == self.previous
            except Exception:
                restored = False
            if not restored:
                try:
                    import uia_inspect
                    with uia_inspect.com_session():
                        uia_inspect.root_wrapper(self.previous).element_info.element.SetFocus()
                    restored = self.gui.GetForegroundWindow() == self.previous
                except Exception:
                    restored = False
        return {'focus_restored': restored, 'cursor_restored': self.api.GetCursorPos() == self.cursor}


def execute(request, native, indicator, publish):
    """Testable lifecycle; gate acquisition always precedes focus and injection."""
    report = {'ok': False, 'phase': 'announcing', 'reason': request['reason'],
              'timeout_ms': request['timeout_ms'], 'completed_steps': 0, 'steps': [],
              'input_blocked': False, 'input_gate': 'low_level_hooks', 'cancel_key': 'Escape'}
    locked = False
    locked_at = None
    publish(report)
    try:
        indicator.show('ขอควบคุมชั่วคราว')  # failure here must prevent takeover
        indicator.wait(400)
        native.block()
        locked = True
        locked_at = time.monotonic()
        report.update(phase='controlling', input_blocked=True)
        publish(report)
        native.activate()
        indicator.show('กำลังควบคุมชั่วคราว')
        for i, step in enumerate(request['steps']):
            report['stopped_at_step'] = i
            publish(report)  # crash while executing this step has an explicitly unknown outcome
            native.guard()
            kind = step['do']
            if kind in ('click', 'hover', 'scroll'):
                native.point(step['x'], step['y'])
            if kind == 'click':
                native.mouse([2, 4] if step.get('button', 'left') == 'left' else [8, 16])
            elif kind == 'hover':
                indicator.wait(step.get('dwell_ms', 500), native.guard)
            elif kind == 'scroll':
                native.mouse([0x0800], step['clicks'] * 120)
            elif kind == 'type':
                native.text(step['text'], step.get('replace', False))
            elif kind == 'key':
                native.key(step['keys'])
            elif kind == 'wait':
                indicator.wait(step['ms'], native.guard)
            elif kind == 'capture':
                report['capture'] = indicator.capture(native, step.get('region'), request['folder'])
            report['steps'].append({'index': i, 'do': kind, 'ok': True})
            report['completed_steps'] += 1
            publish(report)
            indicator.wait(40, native.guard)  # dispatch/repaint, no stability polling
        report['ok'] = True
        report.pop('stopped_at_step', None)
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        if locked:
            try:
                report.update(native.restore())
            except Exception as exc:
                report['restore_error'] = str(exc)
            try:
                native.unblock()
                report['input_blocked'] = False
                report['input_blocked_ms'] = round((time.monotonic() - locked_at) * 1000)
            except Exception as exc:
                report.update(ok=False, error=str(exc))
        indicator.close()
        report['phase'] = 'released' if not report['input_blocked'] else 'release_failed'
        publish(report)
    return report


def watchdog(parent_pid, timeout_ms, done):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    parent = kernel.OpenProcess(0x100000, False, parent_pid)  # SYNCHRONIZE
    if not parent:
        os._exit(125)
    deadline = time.monotonic() + timeout_ms / 1000
    try:
        while not done.wait(0.02):
            if time.monotonic() >= deadline:
                os._exit(124)
            if kernel.WaitForSingleObject(parent, 0) != 258:  # only WAIT_TIMEOUT means alive
                os._exit(125)
    finally:
        kernel.CloseHandle(parent)


def worker():
    # Match the server's client-physical-pixel convention before any GUI calls.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    request = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    validate(request['steps'], request['reason'], request['timeout_ms'], True)
    done = threading.Event()
    threading.Thread(target=watchdog, args=(request['parent_pid'], request['timeout_ms'], done), daemon=True).start()
    from takeover_indicator import ScreenIndicator
    native = NativeInput(request['hwnd'], request['pid'])
    indicator = ScreenIndicator(request['hwnd'], request['reason'], request['timeout_ms'])
    try:
        report = execute(request, native, indicator,
                         lambda value: _write_report(Path(request['folder']) / 'result.json', value))
        return 0 if report['phase'] == 'released' else 1
    finally:
        if native.gate is None or (not native.gate.active and not native.gate.thread.is_alive()):
            done.set()


if __name__ == '__main__':
    raise SystemExit(worker())
