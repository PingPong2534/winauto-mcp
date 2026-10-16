"""Explicit, bounded real-input batches. Ordinary UIA tools never call this module.

The disposable worker owns SendInput and a dedicated low-level hook input gate.
A separate watchdog exits the worker on deadline or parent death; Windows then
removes the hooks even if the main thread is stuck in a native API.
"""
import ctypes
import ctypes.wintypes as wt
from contextlib import contextmanager
import gc
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
CLEANUP_TIMEOUT_S = 0.25
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
    schemas = {'click': ({'x', 'y'}, {'button', 'modifiers'}), 'hover': ({'x', 'y'}, {'dwell_ms'}),
               'drag': ({'x1', 'y1', 'x2', 'y2'}, {'button', 'duration_ms', 'modifiers'}),
               'scroll': ({'x', 'y', 'clicks'}, set()), 'type': ({'text'}, {'replace'}),
               'key': ({'keys'}, {'hold_ms'}), 'wait': ({'ms'}, set()), 'capture': (set(), {'region'})}
    requested_ms = 400  # visible announcement before any input is blocked
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or step.get('do') not in schemas:
            raise ValueError(f'step {i}: unsupported step')
        required, optional = schemas[step['do']]
        if not required <= step.keys() or set(step) - required - optional - {'do'}:
            raise ValueError(f'step {i}: missing or unknown fields')
        requested_ms += 40  # dispatch/repaint after each step
        for field in ('x', 'y', 'x1', 'y1', 'x2', 'y2', 'clicks', 'ms', 'dwell_ms', 'duration_ms', 'hold_ms'):
            if field in step and type(step[field]) is not int:
                raise ValueError(f'step {i}: {field} must be an integer')
        if step.get('button', 'left') not in ('left', 'right'):
            raise ValueError(f'step {i}: button must be left or right')
        if 'modifiers' in step:
            modifiers = step['modifiers']
            if (not isinstance(modifiers, list) or len(modifiers) > 3
                    or any(not isinstance(k, str) or k not in ('ctrl', 'shift', 'alt') for k in modifiers)
                    or len(set(modifiers)) != len(modifiers)):
                raise ValueError(f'step {i}: modifiers must be distinct ctrl/shift/alt names')
        if step['do'] == 'drag':
            duration = step.get('duration_ms', 500)
            if not 100 <= duration <= 1500:
                raise ValueError(f'step {i}: duration_ms must be 100..1500')
            if (step['x1'], step['y1']) == (step['x2'], step['y2']):
                raise ValueError(f'step {i}: drag endpoints must differ')
            requested_ms += duration
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
            hold_ms = step.get('hold_ms', 80)
            if not 20 <= hold_ms <= 500:
                raise ValueError(f'step {i}: hold_ms must be 20..500')
            requested_ms += hold_ms
        if step['do'] == 'type' and step.get('replace', False):
            requested_ms += 80
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
        parent_timed_out = False
        with hidden_for_capture():  # attachment decoration must not obstruct pointer targeting
            proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker'],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                _, stderr = proc.communicate(json.dumps(request).encode('utf-8'),
                                             timeout=timeout_ms / 1000 + 3)
            except subprocess.TimeoutExpired:
                parent_timed_out = True
                proc.kill()
                _, stderr = proc.communicate(timeout=2)
            except BaseException:
                proc.kill()
                proc.communicate(timeout=2)
                raise
        report_path = Path(folder) / 'result.json'
        report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {
            'ok': False, 'completed_steps': 0, 'steps': []}
        if parent_timed_out or proc.returncode or report.get('phase') != 'released':
            report = worker_exit_report(report, proc.returncode)
            if parent_timed_out:
                report.update(timed_out=True, parent_timed_out=True)
            if stderr:
                report['diagnostic'] = stderr.decode('utf-8', errors='replace')[:1000]
        # A successful capture can precede a failed release; never mislabel it as batch success.
        image = Path(folder) / 'capture.png'
        return report, image.read_bytes() if image.exists() and report.get('ok') else None


def worker_exit_report(report, returncode):
    """Preserve durable cleanup acknowledgements if only post-release work died."""
    confirmed = (report.get('phase') == 'released' and report.get('input_blocked') is False
                 and report.get('keys_released') is True and report.get('mouse_buttons_released') is True)
    report.update(ok=False, timed_out=returncode == 124, worker_returncode=returncode,
                  worker_exit_phase=report.get('phase', 'initializing'),
                  release_confirmed=confirmed, input_released_by_worker_exit=not confirmed,
                  error=report.get('error', 'worker exited after confirmed release' if confirmed
                                   else 'worker exited before cleanup completed'),
                  interrupted_step_outcome=('none; worker exited after cleanup was reported' if confirmed
                                            else 'unknown; inspect state before retrying'))
    report.update(input_blocked=False, phase='worker_exited')
    if not confirmed:
        report['mouse_buttons_released'] = None
        report['keys_released'] = None
    return report


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


def held_virtual_keys(api=None):
    """One passive async-state sample, without hooks, character text or history."""
    if api is None:
        import win32api
        api = win32api
    return [key for key in range(1, 256) if api.GetAsyncKeyState(key) & 0x8000]


class HeldInputError(RuntimeError):
    def __init__(self, keys):
        self.keys = keys
        super().__init__(f'a key/button is held; held_virtual_keys={keys}; '
                         'origin is unknown (physical or injected); '
                         'release it before requesting temporary control')


class NativeInput:
    def __init__(self, hwnd, pid):
        import win32gui
        import win32process
        import window_manager
        self.gui, self.process, self.windows = win32gui, win32process, window_manager
        self.hwnd, self.pid = hwnd, pid
        self.gate = None
        self.held_mouse = 0
        self.held_keys = {}
        self.stopping = False
        self.dispatch_lock = threading.Lock()
        # Windows' mouse input path can truncate dwExtraInfo to 32 bits even
        # in a 64-bit process. Keep the per-worker tag within that width.
        self.tag = secrets.randbits(31) or 1
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.user.SendInput.argtypes = [wt.UINT, ctypes.POINTER(_Input), ctypes.c_int]
        self.user.SendInput.restype = wt.UINT
        self.user.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
        self.user.MapVirtualKeyW.restype = wt.UINT
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
        current = self.gui.GetForegroundWindow()
        if not self.belongs(current):
            details = 'none'
            if current and self.gui.IsWindow(current):
                details = (f'hwnd={current}, pid={self.process.GetWindowThreadProcessId(current)[1]}, '
                           f'class={self.gui.GetClassName(current)!r}')
            raise RuntimeError('foreground left the attached window/owned popup; stopped without reacquiring focus; '
                               f'foreground={details}')

    def block(self):
        held = held_virtual_keys(self.api)
        if held:
            raise HeldInputError(held)
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
                self.wait_activation()
            except Exception:
                pass
        if not self.belongs(self.gui.GetForegroundWindow()):
            # UIA's native SetFocus is a legitimate accessibility operation;
            # bypass pywinauto's wrapper, which may use cursor/Alt workarounds.
            import uia_inspect
            with uia_inspect.com_session():
                uia_inspect.root_wrapper(self.hwnd).element_info.element.SetFocus()
            self.wait_activation()
        self.guard()  # SetForegroundWindow can be denied; never inject into the old app

    def wait_activation(self):
        # Cross-thread SetForegroundWindow activation is asynchronous. Wait only
        # for acknowledgement of the single request, never reacquire lost focus.
        # WM_NULL is non-mutating; SMTO_ABORTIFHUNG bounds a nonresponsive target.
        self.gui.SendMessageTimeout(self.hwnd, 0, 0, 0, 2, 200)

    def send(self, events):
        self.guard()
        with self.dispatch_lock:
            self._send_guarded(events)

    def _send_guarded(self, events):
        if self.stopping:
            raise RuntimeError('worker is stopping; no more input permitted')
        # Mark possible downs before dispatch: a short SendInput may have applied
        # a down without its matching up. Cleanup emits only tracked button-ups.
        for event in events:
            if event.type == 0:
                self.held_mouse |= event.mi.dwFlags & (2 | 8)
            elif event.type == 1 and not event.ki.dwFlags & 2:
                key = (event.ki.wVk, event.ki.wScan, event.ki.dwFlags & ~2)
                self.held_keys[key] = None
        self.raw_send(events)
        for event in events:
            if event.type == 0:
                if event.mi.dwFlags & 4:
                    self.held_mouse &= ~2
                if event.mi.dwFlags & 16:
                    self.held_mouse &= ~8
            elif event.type == 1 and event.ki.dwFlags & 2:
                self.held_keys.pop((event.ki.wVk, event.ki.wScan, event.ki.dwFlags & ~2), None)

    def raw_send(self, events):
        for event in events:
            if event.type == 1:
                event.ki.dwExtraInfo = self.tag
            else:
                event.mi.dwExtraInfo = self.tag
        array = (_Input * len(events))(*events)
        if self.user.SendInput(len(array), array, ctypes.sizeof(_Input)) != len(array):
            raise RuntimeError('SendInput did not accept every event; state may be partial')

    def _key_event(self, key, up=False):
        scan = self.user.MapVirtualKeyW(KEYS[key], 4)  # MAPVK_VK_TO_VSC_EX
        if not scan:
            raise ValueError(f'no scan code available for {key}')
        flags = 8 | (2 if up else 0) | (1 if scan & 0xFF00 == 0xE000 else 0)
        return _Input(type=1, ki=_Keyboard(wScan=scan & 0xFF, dwFlags=flags))

    def key(self, keys, hold_ms=80, wait=None):
        self.keyboard_guard()
        events = [self._key_event(key) for key in keys]
        try:
            self.send(events)
            # Polling game engines need a real down interval across frames.
            if wait is not None:
                wait(hold_ms, self.keyboard_guard)
            else:
                deadline = time.monotonic() + hold_ms / 1000
                while time.monotonic() < deadline:
                    self.keyboard_guard()
                    time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        finally:
            self.release_keys()

    @contextmanager
    def modifiers(self, keys):
        events = [self._key_event(key) for key in keys]
        try:
            if events:
                self.send(events)
            yield
        finally:
            self.release_keys()

    def click(self, x, y, button='left', modifiers=()):
        self.point(x, y)
        with self.modifiers(modifiers):
            try:
                self.mouse([2, 4] if button == 'left' else [8, 16])
            finally:
                self.release_mouse()

    def release_keys(self, stop=False):
        if stop:
            self.stopping = True
        acquired = self.dispatch_lock.acquire(timeout=0.1)
        try:
            held = list(self.held_keys)
            if held:
                self.raw_send([_Input(type=1, ki=_Keyboard(wVk=vk, wScan=scan, dwFlags=flags | 2))
                               for vk, scan, flags in reversed(held)])
                for key in held:
                    self.held_keys.pop(key, None)
        finally:
            if acquired:
                self.dispatch_lock.release()

    def release_inputs(self):
        try:
            self.release_mouse(stop=True)
        finally:
            self.release_keys(stop=True)

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
        point = self.check_point(x, y)
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

    def check_point(self, x, y):
        point = self.gui.ClientToScreen(self.hwnd, (x, y))
        if not self.belongs(self.gui.WindowFromPoint(point)):
            raise RuntimeError('pointer point is covered or outside the attached window/owned popup')
        return point

    def release_mouse(self, stop=False):
        # Cleanup must remain possible after Escape, lost focus or a dead gate.
        # Never move, click or send a down in this unguarded cleanup path.
        if stop:
            self.stopping = True
        # Normal dispatch cannot race a terminal release. If a native call is
        # stuck, the watchdog still attempts button-ups before terminating.
        acquired = self.dispatch_lock.acquire(timeout=0.1)
        try:
            held = self.held_mouse
            events = [_Input(type=0, mi=_Mouse(dwFlags=up))
                      for down, up in ((2, 4), (8, 16)) if held & down]
            if events:
                self.raw_send(events)
                self.held_mouse &= ~held
        finally:
            if acquired:
                self.dispatch_lock.release()

    def drag(self, x1, y1, x2, y2, button, duration_ms, wait, modifiers=()):
        count = max(2, (duration_ms + 19) // 20)
        points = [(round(x1 + (x2 - x1) * i / count),
                   round(y1 + (y2 - y1) * i / count)) for i in range(count + 1)]
        self.guard()
        for x, y in points:  # refuse an already covered path before pressing
            self.check_point(x, y)
        self.point(x1, y1)
        with self.modifiers(modifiers):
            try:
                self.mouse([2 if button == 'left' else 8])
                for x, y in points[1:]:
                    wait(duration_ms / count, self.guard)
                    self.point(x, y)  # recheck changing ownership/occlusion and foreground
            finally:
                self.release_mouse()

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
    def cleanup_progress(phase):
        report['phase'] = phase
        try:
            publish(report)
        except Exception:
            pass  # diagnostic I/O must never prevent input release
    publish(report)
    try:
        if any(step['do'] == 'capture' for step in request['steps']):
            report['phase'] = 'preparing_capture'
            publish(report)
            indicator.prepare_capture()  # expensive imports happen before blocking human input
        report['phase'] = 'announcing'
        publish(report)
        indicator.show('ขอควบคุมชั่วคราว')  # failure here must prevent takeover
        indicator.wait(400)
        native.block()
        locked = True
        locked_at = time.monotonic()
        report.update(phase='controlling', input_blocked=True)
        publish(report)
        report['phase'] = 'activating'
        publish(report)
        native.activate()
        report['phase'] = 'controlling'
        publish(report)
        indicator.show('กำลังควบคุมชั่วคราว')
        for i, step in enumerate(request['steps']):
            report['stopped_at_step'] = i
            publish(report)  # crash while executing this step has an explicitly unknown outcome
            native.guard()
            kind = step['do']
            if kind in ('hover', 'scroll'):
                native.point(step['x'], step['y'])
            if kind == 'click':
                native.click(step['x'], step['y'], step.get('button', 'left'), step.get('modifiers', []))
            elif kind == 'drag':
                native.drag(step['x1'], step['y1'], step['x2'], step['y2'],
                            step.get('button', 'left'), step.get('duration_ms', 500), indicator.wait,
                            step.get('modifiers', []))
            elif kind == 'hover':
                indicator.wait(step.get('dwell_ms', 500), native.guard)
            elif kind == 'scroll':
                native.mouse([0x0800], step['clicks'] * 120)
            elif kind == 'type':
                native.text(step['text'], step.get('replace', False))
            elif kind == 'key':
                native.key(step['keys'], step.get('hold_ms', 80), indicator.wait)
            elif kind == 'wait':
                indicator.wait(step['ms'], native.guard)
            elif kind == 'capture':
                def capture_progress(phase):
                    report['capture_phase'] = phase
                    publish(report)
                report['capture'] = indicator.capture(native, step.get('region'), request['folder'], capture_progress)
            report['steps'].append({'index': i, 'do': kind, 'ok': True})
            report['completed_steps'] += 1
            publish(report)
            indicator.wait(40, native.guard)  # dispatch/repaint, no stability polling
        report['ok'] = True
        report.pop('stopped_at_step', None)
    except Exception as exc:
        report['error'] = str(exc)
        if isinstance(exc, HeldInputError):
            report['held_virtual_keys'] = exc.keys
            report['held_input_origin'] = 'unknown; async state does not distinguish physical and injected input'
    finally:
        if locked:
            cleanup_progress('releasing_inputs')
            try:
                native.release_mouse(stop=True)
                report['mouse_buttons_released'] = True
            except Exception as exc:
                report.update(ok=False, mouse_buttons_released=False, mouse_release_error=str(exc))
            try:
                native.release_keys(stop=True)
                report['keys_released'] = True
            except Exception as exc:
                report.update(ok=False, keys_released=False, key_release_error=str(exc))
            if report['mouse_buttons_released'] and report['keys_released']:
                cleanup_progress('restoring')
                try:
                    report.update(native.restore())
                except Exception as exc:
                    report['restore_error'] = str(exc)
            try:
                cleanup_progress('unblocking')
                native.unblock()
                report['input_blocked'] = False
                report['input_blocked_ms'] = round((time.monotonic() - locked_at) * 1000)
            except Exception as exc:
                report.update(ok=False, error=str(exc))
        cleanup_progress('closing_indicator')
        indicator.close()
        report['phase'] = 'released' if not report['input_blocked'] else 'release_failed'
        publish(report)
    return report


def watchdog(parent_pid, timeout_ms, done, cleanup=None):
    def terminate(code):
        try:
            if cleanup:
                # SendInput itself can stall. Cleanup must not disable the deadline
                # or leave the worker's global hooks alive indefinitely.
                def attempt():
                    try:
                        cleanup()
                    except Exception:
                        pass  # release remains unconfirmed in the durable report
                thread = threading.Thread(target=attempt, daemon=True)
                thread.start()
                thread.join(CLEANUP_TIMEOUT_S)
        finally:
            os._exit(code)

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    parent = kernel.OpenProcess(0x100000, False, parent_pid)  # SYNCHRONIZE
    if not parent:
        terminate(125)
    deadline = time.monotonic() + timeout_ms / 1000
    try:
        while not done.wait(0.02):
            if time.monotonic() >= deadline:
                terminate(124)
            if kernel.WaitForSingleObject(parent, 0) != 258:  # only WAIT_TIMEOUT means alive
                terminate(125)
    finally:
        kernel.CloseHandle(parent)


def worker():
    # Match the server's client-physical-pixel convention before any GUI calls.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    request = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    validate(request['steps'], request['reason'], request['timeout_ms'], True)
    done = threading.Event()
    native = None
    def cleanup():
        if native is not None:
            native.release_inputs()
    threading.Thread(target=watchdog, args=(request['parent_pid'], request['timeout_ms'], done, cleanup), daemon=True).start()
    _write_report(Path(request['folder']) / 'result.json', {
        'ok': False, 'phase': 'initializing', 'completed_steps': 0, 'steps': [], 'input_blocked': False})
    from takeover_indicator import ScreenIndicator
    native = NativeInput(request['hwnd'], request['pid'])
    indicator = ScreenIndicator(request['hwnd'], request['reason'], request['timeout_ms'])
    try:
        with worker_apartment():
            report = execute(request, native, indicator,
                             lambda value: _write_report(Path(request['folder']) / 'result.json', value))
        return 0 if report['phase'] == 'released' else 1
    finally:
        if native.gate is None or (not native.gate.active and not native.gate.thread.is_alive()):
            done.set()


@contextmanager
def worker_apartment():
    """Keep activation COM cycles off capture/hook threads until input release."""
    # Initialize without eagerly importing pywinauto: successful native
    # activation never needs that relatively expensive fallback dependency.
    import pythoncom
    initialized = False
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        initialized = True
    except pythoncom.com_error as exc:
        if exc.hresult & 0xFFFFFFFF != 0x80010106:
            raise
    try:
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            yield
        finally:
            try:
                gc.collect()  # execute already released the gate; main apartment is still alive
            finally:
                if was_enabled:
                    gc.enable()
    finally:
        if initialized:
            pythoncom.CoUninitialize()


if __name__ == '__main__':
    raise SystemExit(worker())
