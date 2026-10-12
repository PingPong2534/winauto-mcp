"""Region comparisons with identical Pillow/OpenCV max-channel semantics.

No capture, encoding, UI input or baseline updates occur here.
"""
import math

from PIL import Image, ImageChops, ImageDraw

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = np = None


def rectangle(value):
    if (not isinstance(value, list) or len(value) != 4
            or any(isinstance(n, bool) or not isinstance(n, int) for n in value)
            or value[0] < 0 or value[1] < 0
            or value[2] <= value[0] or value[3] <= value[1]):
        raise ValueError("region must be [x1,y1,x2,y2], nonnegative with positive area")
    return tuple(value)


def validate(guard):
    if not isinstance(guard, dict) or guard.keys() - {
            "region", "threshold", "max_changed_ratio", "ignore_regions"}:
        raise ValueError("guard accepts region, threshold, max_changed_ratio, ignore_regions")
    region = rectangle(guard.get("region"))
    threshold = guard.get("threshold", 10)
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 255:
        raise ValueError("threshold must be an integer from 0 to 255")
    ratio = guard.get("max_changed_ratio", 0)
    if (isinstance(ratio, bool) or not isinstance(ratio, (int, float))
            or not math.isfinite(ratio) or not 0 <= ratio < 1):
        raise ValueError("max_changed_ratio must be >= 0 and < 1")
    ignored = guard.get("ignore_regions", [])
    if not isinstance(ignored, list) or len(ignored) > 16:
        raise ValueError("ignore_regions must be a list of at most 16 rectangles")
    for item in ignored:
        box = rectangle(item)
        if not covers(region, box):
            raise ValueError("ignore_regions must be fully inside the guard region")
    # Check union coverage without allocating a potentially large preflight image.
    xs = sorted({region[0], region[2], *(box[0] for box in ignored), *(box[2] for box in ignored)})
    covered = 0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((box[1], box[3]) for box in ignored if box[0] <= left and box[2] >= right)
        end, height = region[1], 0
        for top, bottom in intervals:
            height += max(0, bottom - max(end, top))
            end = max(end, bottom)
        covered += (right - left) * height
    if covered == (region[2] - region[0]) * (region[3] - region[1]):
        raise ValueError("ignore_regions exclude the entire guard region")
    return region


def covers(outer, inner):
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[2] >= inner[2] and outer[3] >= inner[3])


def compare(before, after, guard, backend="auto"):
    region = validate(guard)
    if backend not in ("auto", "pillow", "opencv"):
        raise ValueError("vision_backend must be auto, pillow or opencv")
    if before.size != after.size:
        raise ValueError("client size changed; capture_screen and replan")
    if not covers((0, 0, *after.size), region):
        raise ValueError("guard region is outside the current client area")
    area = (region[2] - region[0]) * (region[3] - region[1])
    # Local benchmarks favor the native OpenCV mask path at all measured sizes.
    # Pillow remains the compatible fallback and an explicit diagnostic choice.
    selected = ("opencv" if cv2 is not None else "pillow") if backend == "auto" else backend
    if selected == "opencv" and cv2 is None:
        raise ValueError("OpenCV unavailable; install runtime dependencies or select pillow")
    a, b = before.crop(region), after.crop(region)
    a = a if a.mode == "RGB" else a.convert("RGB")
    b = b if b.mode == "RGB" else b.convert("RGB")
    threshold = guard.get("threshold", 10)
    if selected == "opencv":
        delta = cv2.absdiff(np.asarray(a), np.asarray(b))
        mask = cv2.bitwise_not(cv2.inRange(delta, (0, 0, 0), (threshold, threshold, threshold)))
        ignored = guard.get("ignore_regions", [])
        included = np.full((a.height, a.width), 255, dtype="uint8") if ignored else None
        for box in ignored:
            rows = slice(box[1] - region[1], box[3] - region[1])
            cols = slice(box[0] - region[0], box[2] - region[0])
            mask[rows, cols] = 0
            included[rows, cols] = 0
        count = int(cv2.countNonZero(mask))
        total = int(cv2.countNonZero(included)) if included is not None else area
        x, y, width, height = cv2.boundingRect(mask)
        bbox = (x, y, x + width, y + height) if count else None
    else:
        r, g, blue = ImageChops.difference(a, b).split()
        mask = ImageChops.lighter(ImageChops.lighter(r, g), blue).point(
            [255 if p > threshold else 0 for p in range(256)])
        included = Image.new("L", a.size, 255)
        draw = ImageDraw.Draw(included)
        for box in guard.get("ignore_regions", []):
            draw.rectangle((box[0] - region[0], box[1] - region[1],
                            box[2] - region[0] - 1, box[3] - region[1] - 1), fill=0)
        mask = ImageChops.multiply(mask, included)
        count, total = mask.histogram()[255], included.histogram()[255]
        bbox = mask.getbbox()
    if not total:
        raise ValueError("ignore_regions exclude the entire guard region")
    ratio = count / total
    return {"unchanged": ratio <= guard.get("max_changed_ratio", 0),
            "changed_pixels": count, "compared_pixels": total, "changed_ratio": ratio,
            "changed_bbox": ([bbox[0] + region[0], bbox[1] + region[1],
                              bbox[2] + region[0], bbox[3] + region[1]] if bbox else None),
            "region": list(region), "backend": selected}
