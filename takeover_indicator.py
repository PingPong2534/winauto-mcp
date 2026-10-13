"""Visible desktop-edge warning, owned by the temporary input worker."""
import ctypes
import queue
import threading
import time
import win32api
import win32con
import win32gui


class ScreenIndicator:
    def __init__(self, hwnd, reason, timeout_ms):
        self.target, self.reason, self.timeout_ms = hwnd, reason, timeout_ms
        self.windows = []
        self.deadline = time.monotonic() + timeout_ms / 1000
        self.name = f'winauto_takeover_{id(self)}'
        self.brush = win32gui.CreateSolidBrush(0x0088FF)  # orange COLORREF
        spec = win32gui.LOGFONT()
        spec.lfFaceName = 'Segoe UI'
        dpi = ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(hwnd)) or 96
        spec.lfHeight = -round(16 * dpi / 96)
        self.font = win32gui.CreateFontIndirect(spec)
        wc = win32gui.WNDCLASS()
        wc.lpszClassName, wc.lpfnWndProc, wc.hbrBackground = self.name, self._proc, self.brush
        win32gui.RegisterClass(wc)
        self.label = None
        self.phase = ''

    def _proc(self, hwnd, msg, wp, lp):
        if msg == win32con.WM_MOUSEACTIVATE:
            return win32con.MA_NOACTIVATE
        if msg == win32con.WM_NCHITTEST:
            return win32con.HTTRANSPARENT
        if msg == win32con.WM_PAINT and hwnd == self.label:
            dc, paint = win32gui.BeginPaint(hwnd)
            previous_font = win32gui.SelectObject(dc, self.font)
            try:
                rect = win32gui.GetClientRect(hwnd)
                win32gui.FillRect(dc, rect, self.brush)
                win32gui.SetBkMode(dc, win32con.TRANSPARENT)
                win32gui.DrawText(dc, self.phase, -1, (8, 0, rect[2] - 8, rect[3]),
                                  win32con.DT_SINGLELINE | win32con.DT_VCENTER | win32con.DT_END_ELLIPSIS | win32con.DT_NOPREFIX)
            finally:
                win32gui.SelectObject(dc, previous_font)
                win32gui.EndPaint(hwnd, paint)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wp, lp)

    def _window(self, rect):
        l, t, r, b = rect
        styles = win32con.WS_EX_TOPMOST | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_LAYERED
        hwnd = win32gui.CreateWindowEx(styles, self.name, '', win32con.WS_POPUP,
                                       l, t, r - l, b - t, 0, 0, 0, None)
        self.windows.append(hwnd)
        win32gui.SetLayeredWindowAttributes(hwnd, 0, 235, win32con.LWA_ALPHA)
        return hwnd

    def show(self, phase):
        if not self.windows:
            for _, _, (l, t, r, b) in win32api.EnumDisplayMonitors():
                for rect in ((l, t, r, t + 6), (l, b - 6, r, b), (l, t, l + 6, b), (r - 6, t, r, b)):
                    self._window(rect)
            monitor = win32api.GetMonitorInfo(win32api.MonitorFromWindow(self.target, win32con.MONITOR_DEFAULTTONEAREST))['Monitor']
            l, t, r, b = monitor
            width = min(850, r - l - 24)
            self.label = self._window(((l + r - width) // 2, t + 6, (l + r + width) // 2, t + 42))
        self.phase = f'WinAuto: {phase} | Esc: ยกเลิก | สูงสุด {self.timeout_ms / 1000:g}s | {self.reason}'
        for hwnd in self.windows:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            win32gui.InvalidateRect(hwnd, None, True)
            win32gui.UpdateWindow(hwnd)
        win32gui.PumpWaitingMessages()
        if not all(win32gui.IsWindowVisible(hwnd) for hwnd in self.windows):
            raise RuntimeError('screen-edge indicator is not visible; takeover refused')

    def wait(self, ms, guard=None):
        deadline = time.monotonic() + ms / 1000
        while time.monotonic() < deadline:
            win32gui.PumpWaitingMessages()
            if guard:
                guard()
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))

    def prepare_capture(self):
        # Import before the announcement/input gate, not inside its lease.
        import mss
        from PIL import Image
        from screenshot import ensure_uncovered

    def capture(self, native, region, folder, progress=lambda phase: None):
        # Screen pixels preserve transient UI (tooltips/popups); PrintWindow may omit it.
        from pathlib import Path
        import mss
        from PIL import Image
        from screenshot import ensure_uncovered
        native.guard()
        l, t, r, b = native.windows.get_client_rect_screen(self.target)
        width, height = r - l, b - t
        rect = region if region is not None else [0, 0, width, height]
        x1, y1, x2, y2 = rect
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValueError('capture region must be entirely inside the attached client area')
        for hwnd in self.windows:
            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
        try:
            area = (l + x1, t + y1, l + x2, t + y2)
            progress('occlusion_check')
            ensure_uncovered(self.target, area, native.belongs)
            # Driver/GDI calls may hang. A daemon does only capture/encoding;
            # it never injects input or writes a late image. Bound this optional
            # step and reserve 300 ms for release within the original watchdog.
            deadline = min(time.monotonic() + 1.0, self.deadline - 0.3)
            if deadline <= time.monotonic():
                raise TimeoutError('insufficient capture budget; reserved remaining lease for cleanup')
            messages = queue.Queue()
            def read_pixels():
                try:
                    import io
                    messages.put(('phase', 'dwm_flush'))
                    ctypes.windll.dwmapi.DwmFlush()
                    messages.put(('phase', 'screen_grab'))
                    with mss.mss() as capture:
                        raw = capture.grab({'left': area[0], 'top': area[1],
                                            'width': x2 - x1, 'height': y2 - y1})
                    messages.put(('phase', 'png_encode'))
                    output = io.BytesIO()
                    Image.frombytes('RGB', raw.size, raw.bgra, 'raw', 'BGRX').save(output, format='PNG')
                    messages.put(('result', output.getvalue()))
                except Exception as exc:
                    messages.put(('error', exc))
            threading.Thread(target=read_pixels, daemon=True).start()
            phase = 'capture_start'
            while True:
                native.guard()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f'optional capture timed out during {phase}; input cleanup will run')
                try:
                    kind, value = messages.get(timeout=min(0.01, remaining))
                except queue.Empty:
                    win32gui.PumpWaitingMessages()
                    continue
                if kind == 'phase':
                    phase = value
                    progress(phase)
                elif kind == 'error':
                    raise value
                else:
                    png = value
                    break
            progress('verify_and_save')
            native.guard()
            ensure_uncovered(self.target, area, native.belongs)
            if native.windows.get_client_rect_screen(self.target) != (l, t, r, b):
                raise RuntimeError('target geometry changed during capture')
            Path(folder, 'capture.png').write_bytes(png)
            progress('complete')
        finally:
            self.show('กำลังควบคุมชั่วคราว')
        return {'region': rect, 'crop_size': [x2 - x1, y2 - y1], 'coordinate_offset': [x1, y1],
                'source': 'fresh screen pixels before focus release'}

    def close(self):
        for hwnd in reversed(self.windows):
            if win32gui.IsWindow(hwnd):
                win32gui.DestroyWindow(hwnd)
        self.windows.clear()
        win32gui.UnregisterClass(self.name, None)
        # Windows owns/deletes the registered class's background brush.
        win32gui.DeleteObject(self.font)
