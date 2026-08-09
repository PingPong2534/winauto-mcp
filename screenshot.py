"""Capture a screenshot of a window's client area, and pixel-level analysis
helpers (content-bbox-by-contrast, before/after diff) that back the
locate_in_region / snapshot / diff_since_snapshot MCP tools. These exist so
the LLM client never has to eyeball coordinates from a displayed crop --
crop thumbnails can be rescaled for display in a way that doesn't map back
to real source-image pixels, which was a repeated source of 50-150+ px
click misses before these tools existed."""

import io
from collections import Counter

import mss
from PIL import Image, ImageChops

from window_manager import get_client_rect_screen


def capture_window_png(hwnd) -> bytes:
    """Return PNG bytes of the window's client area (screen coords)."""
    left, top, right, bottom = get_client_rect_screen(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise ValueError("window has zero-area client rect (minimized or offscreen?)")

    with mss.mss() as sct:
        raw = sct.grab({"left": left, "top": top, "width": width, "height": height})
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def find_content_bbox(png_bytes: bytes, region: tuple[int, int, int, int], threshold: int = 30):
    """Within `region` (x1,y1,x2,y2) of the image, find the tight bounding box
    of pixels that differ from the region's dominant (most common) color by
    more than `threshold` on any channel. Returns a (x1,y1,x2,y2) box in the
    same coordinate space as `region`, or None if nothing differs enough.
    Assumes the region is mostly flat background with one piece of
    text/icon/content in it -- pick a small, single-purpose region."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
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


def diff_bbox(png_before: bytes, png_after: bytes, threshold: int = 10):
    """Return the bounding box of pixels that changed by more than `threshold`
    (0-255, per grayscale-diff intensity) between two same-size screenshots,
    or None if nothing changed. threshold=0 means any pixel difference at
    all counts."""
    a = Image.open(io.BytesIO(png_before)).convert("RGB")
    b = Image.open(io.BytesIO(png_after)).convert("RGB")
    if a.size != b.size:
        return (0, 0, *b.size)
    diff = ImageChops.difference(a, b)
    if threshold <= 0:
        return diff.getbbox()
    gray = diff.convert("L")
    binarized = gray.point(lambda p: 255 if p > threshold else 0)
    return binarized.getbbox()
