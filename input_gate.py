"""Short-lived keyboard/mouse suppression on a dedicated hook message thread.

Only this worker's tagged injected events pass. Physical Escape cancels. The
gate exists only inside the disposable foreground worker, never background UIA.
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time


class _KeyEvent(ctypes.Structure):
    _fields_ = [('vkCode', wt.DWORD), ('scanCode', wt.DWORD), ('flags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class _MouseEvent(ctypes.Structure):
    _fields_ = [('pt', wt.POINT), ('mouseData', wt.DWORD), ('flags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class InputGate:
    def __init__(self, tag):
        self.tag = tag
        self.ready, self.stop, self.cancelled = threading.Event(), threading.Event(), threading.Event()
        self.handles = []
        self.error = None
        self.last_pump = 0
        self.active = False
        self.own_events = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready.wait(1) or self.error or not self.active:
            self.close()
            raise RuntimeError(f'input gate could not start: {self.error or "timeout"}')

    def check(self):
        if self.cancelled.is_set():
            raise RuntimeError('human pressed Escape; temporary control cancelled')
        if not self.active or not self.thread.is_alive() or time.monotonic() - self.last_pump > 0.2:
            raise RuntimeError('input gate message pump is unavailable; stopped')

    def decision(self, code, message, event, keyboard):
        """Return None to chain, 1 to suppress; never record human input."""
        if code < 0 or self.stop.is_set() or self.cancelled.is_set():
            return None
        injected = event.flags & (0x10 if keyboard else 0x01)
        if injected and event.dwExtraInfo == self.tag:
            self.own_events += 1
            return None
        if keyboard and not injected and event.vkCode == 0x1B and message in (0x100, 0x104):
            self.cancelled.set()
        return 1

    def _run(self):
        import win32gui
        user = ctypes.WinDLL('user32', use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        user.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, wt.HINSTANCE, wt.DWORD]
        user.SetWindowsHookExW.restype = wt.HANDLE
        user.CallNextHookEx.argtypes = [wt.HANDLE, ctypes.c_int, wt.WPARAM, wt.LPARAM]
        user.CallNextHookEx.restype = ctypes.c_ssize_t
        user.UnhookWindowsHookEx.argtypes = [wt.HANDLE]
        user.UnhookWindowsHookEx.restype = wt.BOOL
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        kernel.GetModuleHandleW.restype = wt.HINSTANCE

        def callback(keyboard):
            def handle(code, message, pointer):
                try:
                    if code >= 0:
                        event = ctypes.cast(pointer, ctypes.POINTER(_KeyEvent if keyboard else _MouseEvent)).contents
                        result = self.decision(code, message, event, keyboard)
                        if result is not None:
                            return result
                except Exception:
                    self.cancelled.set()  # fail open for human input, stop AI immediately
                return user.CallNextHookEx(None, code, message, pointer)
            return callback_type(handle)

        self.callbacks = [callback(True), callback(False)]  # retain Python callback lifetimes
        try:
            for kind, proc in zip((13, 14), self.callbacks):
                handle = user.SetWindowsHookExW(kind, proc, kernel.GetModuleHandleW(None), 0)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handles.append(handle)
            self.active = True
            self.last_pump = time.monotonic()
            self.ready.set()
            while not self.stop.is_set() and not self.cancelled.is_set():
                win32gui.PumpWaitingMessages()
                self.last_pump = time.monotonic()
                self.stop.wait(0.005)
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.active = False
            for handle in reversed(self.handles):
                user.UnhookWindowsHookEx(handle)
            self.handles.clear()
            self.ready.set()

    def close(self):
        self.stop.set()
        if self.thread.ident and self.thread is not threading.current_thread():
            self.thread.join(0.5)
        if self.thread.is_alive():
            raise RuntimeError('input gate failed to stop; worker exit will remove its hooks')
