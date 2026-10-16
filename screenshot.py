"""Capture a screenshot of a window's client area, and pixel-level analysis
helpers (content-bbox-by-contrast, before/after diff) that back the
locate_in_region / snapshot / diff_since_snapshot MCP tools. These exist so
the LLM client never has to eyeball coordinates from a displayed crop --
crop thumbnails can be rescaled for display in a way that doesn't map back
to real source-image pixels, which was a repeated source of 50-150+ px
click misses before these tools existed."""

import ctypes
import io
from collections import Counter

import mss
import win32gui
import win32ui
from PIL import Image, ImageChops

from window_manager import get_client_rect_screen
from overlay import hidden_for_capture

# Tells PrintWindow to include content drawn outside the classic GDI paint
# path -- DirectComposition, and the GPU-backed surfaces that engine editors
# render into. Without it, exactly the apps this server targets come back
# blank. Windows 10 1903 and later.
PW_RENDERFULLCONTENT = 0x00000002


def _native_window_rect(hwnd):
    """Measure in the target's render context; restore our physical-pixel context.

    A bitmap-virtualized legacy window can paint logical pixels into a physical
    sized PrintWindow DC, returning success with scaled content and black padding.
    Reject that path before allocating a DC; guarded screen capture stays aligned
    with UIA without guessing content bounds or stretching app pixels.
    """
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetWindowDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
    user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    context = user.GetWindowDpiAwarenessContext(hwnd)
    if not context:
        return None
    previous = user.SetThreadDpiAwarenessContext(context)
    if not previous:
        return None
    try:
        return win32gui.GetWindowRect(hwnd)
    finally:
        if not user.SetThreadDpiAwarenessContext(previous):
            raise RuntimeError('could not restore capture thread DPI context')


def _print_window(hwnd) -> Image.Image | None:
    """Ask the window to render itself into a bitmap, and crop out its client
    area. Returns None if it declines or hands back nothing usable.

    This is what lets a run happen while the machine stays usable: it reads
    the window's own pixels rather than scraping the screen, so the window can
    sit behind a browser, and nothing has to be raised or focused to look at
    it. Measured working against Blender 5.2 (OpenGL), the Godot 4.6 editor
    and Windows 11 Notepad, all while covered by another window.
    """
    win_left, win_top, win_right, win_bottom = win32gui.GetWindowRect(hwnd)
    win_w, win_h = win_right - win_left, win_bottom - win_top
    if win_w <= 0 or win_h <= 0:
        return None
    native_rect = _native_window_rect(hwnd)
    if native_rect is None or (native_rect[2] - native_rect[0], native_rect[3] - native_rect[1]) != (win_w, win_h):
        return None  # DPI-virtualized pixels cannot safely represent UIA coordinates.

    window_dc = win32gui.GetWindowDC(hwnd)
    if not window_dc:
        return None
    src = mem = bmp = previous = None
    try:
        src = win32ui.CreateDCFromHandle(window_dc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, win_w, win_h)
        previous = mem.SelectObject(bmp)
        if not ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(), PW_RENDERFULLCONTENT):
            return None
        info = bmp.GetInfo()
        img = Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1
        )
    except (win32ui.error, win32gui.error, ValueError):
        return None
    finally:
        # GetWindowDC is borrowed: ReleaseDC, never DeleteDC. Attempt every
        # cleanup even when the target closes during PrintWindow. Cosmetic
        # capture cleanup must not mask the original result with DeleteDC failed.
        cleanup = []
        if mem is not None and previous is not None:
            cleanup.append(lambda: mem.SelectObject(previous))
        if bmp is not None:
            cleanup.append(lambda: win32gui.DeleteObject(bmp.GetHandle()))
        if mem is not None:
            cleanup.append(mem.DeleteDC)
        cleanup.append(lambda: win32gui.ReleaseDC(hwnd, window_dc))
        for release in cleanup:
            try:
                release()
            except Exception:
                pass

    # PrintWindow paints the whole window; every coordinate in this server is
    # client-relative, so trim the border and title bar off the same way the
    # screen-grab path does.
    if not win32gui.IsWindow(hwnd):
        return None
    if win32gui.GetWindowRect(hwnd) != (win_left, win_top, win_right, win_bottom):
        return None
    cl, ct, cr, cb = get_client_rect_screen(hwnd)
    crop = (cl - win_left, ct - win_top, cr - win_left, cb - win_top)
    if crop[2] > img.width or crop[3] > img.height or crop[2] <= crop[0] or crop[3] <= crop[1]:
        return None
    img = img.crop(crop)

    # A window that renders by a route PrintWindow can't reach returns a
    # bitmap that succeeded and is entirely black. That is indistinguishable
    # from success by return code, and handing it back would be worse than
    # scraping the screen -- an all-black frame reads as "the app went blank".
    sample = list(img.resize((64, 64)).getdata())
    if sum(1 for r, g, b in sample if r + g + b > 24) < len(sample) * 0.005:
        return None
    return img


def _screen_grab(hwnd) -> Image.Image:
    """Read the pixels currently on screen where the window is. Only correct
    while nothing covers it -- anything on top is captured instead."""
    left, top, right, bottom = get_client_rect_screen(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise ValueError("window has zero-area client rect (minimized or offscreen?)")
    with mss.mss() as sct:
        raw = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def grab_window(hwnd, allow_occluded: bool = True) -> Image.Image:
    """Return the window's client area as a PIL image, with no PNG encoding.

    Tries PrintWindow first so a covered window still reads correctly, and
    falls back to scraping the screen for windows PrintWindow cannot render.
    Pass allow_occluded=False to force a guarded screen grab for GPU content
    PrintWindow partially omits. Both screen paths refuse foreign occlusion.

    No PNG encoding: polling loops (wait_stable) compare dozens of frames and
    never show them to anyone; making each one round-trip through PNG encode
    and decode would dominate the poll interval and blur the very timing they
    measure.
    """
    if win32gui.IsIconic(hwnd):
        raise ValueError("cannot capture a minimized window without restoring it")
    if not win32gui.IsWindowVisible(hwnd):
        raise ValueError("cannot capture a hidden window")
    if allow_occluded:
        img = _print_window(hwnd)
        if img is not None:
            return img
        # Background actions must not make decisions from the covering app's
        # pixels when PrintWindow cannot render the target. Do not raise it.
        if win32gui.IsIconic(hwnd):
            raise ValueError("cannot capture a minimized window without restoring it")
        with hidden_for_capture():
            return _uncovered_screen_grab(hwnd)
    with hidden_for_capture():
        return _uncovered_screen_grab(hwnd)


def _uncovered_screen_grab(hwnd):
    """Check occlusion while our own outline is temporarily hidden."""
    target = get_client_rect_screen(hwnd)
    ensure_uncovered(hwnd, target)
    image = _screen_grab(hwnd)
    ensure_uncovered(hwnd, target)
    if get_client_rect_screen(hwnd) != target:
        raise ValueError('target geometry changed during screen capture')
    return image


def _cloaked(hwnd):
    value = ctypes.c_ulong()
    result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        ctypes.c_void_p(hwnd), 14, ctypes.byref(value), ctypes.sizeof(value))
    return result == 0 and bool(value.value)


def ensure_uncovered(hwnd, target, belongs=None):
    """Refuse intersecting visible windows above target; omit window titles.

    DWM-cloaked windows do not contribute screen pixels. Unknown DWM state is
    conservatively treated as visible. Owned popups are allowed only when the
    foreground caller supplies its checked window-family predicate.
    """
    import win32process
    above = win32gui.GetTopWindow(0)
    visited = set()
    while above and above != hwnd and above not in visited:
        visited.add(above)
        if (win32gui.IsWindowVisible(above) and not win32gui.IsIconic(above)
                and not (belongs and belongs(above)) and not _cloaked(above)):
            left, top, right, bottom = win32gui.GetWindowRect(above)
            if left < target[2] and right > target[0] and top < target[3] and bottom > target[1]:
                _, pid = win32process.GetWindowThreadProcessId(above)
                raise ValueError(
                    'capture region is covered by another window (another window covers it); '
                    f'covering_hwnd={above}, pid={pid}, class={win32gui.GetClassName(above)!r}; '
                    'screen capture would include its pixels. Uncover the target or use '
                    'get_elements/read_element. No foreground change was made by capture.')
        above = win32gui.GetWindow(above, 2)  # GW_HWNDNEXT
    if above != hwnd:
        raise ValueError('attached window disappeared during capture')


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def changed_bbox(before: Image.Image, after: Image.Image, threshold: int = 10, region=None):
    """Bounding box of pixels differing by more than `threshold`, or None.

    It is one box around *every* changed pixel, so two small changes far apart
    produce a box covering everything between them -- typing one word into
    Notepad moves the text, the tab's modified marker and the status bar's
    Ln/Col readout, and the box around those three is nearly the whole window
    even though almost none of it changed. A large box therefore means "the
    change is spread out", never "everything changed", and its centre is not a
    changed pixel. To find out where the change really is, re-run over smaller
    regions.

    `region` (x1,y1,x2,y2) limits the comparison without moving the result's
    origin: the returned box is still in full-image coordinates. Restricting
    it is how a caller ignores a part of the window that never stops moving
    (a spinner, a live viewport) while still watching the rest -- and how a
    caller narrows a sprawling box down to the cluster it cares about.

    `threshold` is applied to the *luminance* of the per-pixel difference, so
    a shift confined to one channel counts for less than its raw size -- blue
    especially (weighted 0.114, i.e. a blue-only jump of 8 reads as 1). That
    suits the intended use, ignoring rendering noise while catching content
    appearing or disappearing; to catch a subtle colour-only change, pass a
    low threshold. find_content_bbox uses max-per-channel instead.
    """
    if before.size != after.size:
        return (0, 0, *after.size)
    if region is not None:
        x1, y1, x2, y2 = region
        before, after = before.crop(region), after.crop(region)
    else:
        x1, y1 = 0, 0
    diff = ImageChops.difference(before, after)
    if threshold > 0:
        diff = diff.convert("L").point(lambda p: 255 if p > threshold else 0)
    box = diff.getbbox()
    if box is None:
        return None
    return (box[0] + x1, box[1] + y1, box[2] + x1, box[3] + y1)


def as_image(source) -> Image.Image:
    """Accept either a PIL image or PNG bytes. Frames travel as PIL images
    wherever they are only measured, and are encoded to PNG only when one is
    actually handed to a caller -- re-encoding a frame to compare it against
    another costs more than the comparison."""
    if isinstance(source, Image.Image):
        return source if source.mode == "RGB" else source.convert("RGB")
    return Image.open(io.BytesIO(source)).convert("RGB")


def find_content_bbox(source, region: tuple[int, int, int, int], threshold: int = 30):
    """Within `region` (x1,y1,x2,y2) of the image, find the tight bounding box
    of pixels that differ from the region's dominant (most common) color by
    more than `threshold` on any channel. Returns a (x1,y1,x2,y2) box in the
    same coordinate space as `region`, or None if nothing differs enough.
    Assumes the region is mostly flat background with one piece of
    text/icon/content in it -- pick a small, single-purpose region."""
    img = as_image(source)
    x1, y1, x2, y2 = region
    crop = img.crop((x1, y1, x2, y2))
    w, h = crop.size
    pixels = list(crop.getdata())
    if not pixels:
        return None
    bg = Counter(pixels).most_common(1)[0][0]
    br, bgc, bb = bg
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for idx, (r, g, b) in enumerate(pixels):
        if max(abs(r - br), abs(g - bgc), abs(b - bb)) > threshold:
            xx, yy = idx % w, idx // w
            if xx < min_x:
                min_x = xx
            if xx > max_x:
                max_x = xx
            if yy < min_y:
                min_y = yy
            if yy > max_y:
                max_y = yy
    if max_x < 0:
        return None
    return (x1 + min_x, y1 + min_y, x1 + max_x + 1, y1 + max_y + 1)


