"""Rolling record of every tool call this server makes, with before/after
screen thumbnails, written to a throwaway session folder under %TEMP%.

Exists because an automation run is a chain of decisions made from images the
model saw at different times, and when the chain goes wrong ("why did that
click land on nothing?") the only honest answer comes from what the screen
actually looked like at that step -- not from the model's recollection of it.
The journal makes those earlier frames retrievable after the fact.

Frames are downscaled JPEGs, deliberately: they are evidence for reading, not
a coordinate source. Every record carries the `scale` they were shrunk by so a
caller can tell that coordinates read off a thumbnail do not map to the client
area 1:1 -- reading coordinates off a rescaled image is exactly the mistake
locate_in_region exists to prevent.
"""

import io
import json
import os
import shutil
import tempfile
import time
from collections import deque
from datetime import datetime

from PIL import Image as PILImage

ROOT = os.path.join(tempfile.gettempdir(), "winauto-mcp")

KEEP_SESSIONS = 5  # older session folders are deleted when a new one starts
RING_SIZE = 300  # records kept in memory for history(); the .jsonl keeps all
THUMB_MAX_W = 800
THUMB_QUALITY = 70
ARGS_MAX_CHARS = 200
RESULT_MAX_CHARS = 400

_state = {
    "dir": None,
    "id": None,
    "label": "",
    "started": None,
    "seq": 0,
    "n": 0,  # sessions started by this process; keeps folder names unique
    "ring": deque(maxlen=RING_SIZE),
}


def _prune_old_sessions(keep: int) -> None:
    try:
        entries = sorted(
            d for d in os.listdir(ROOT) if os.path.isdir(os.path.join(ROOT, d))
        )
    except OSError:
        return
    for stale in entries[: max(0, len(entries) - keep)]:
        shutil.rmtree(os.path.join(ROOT, stale), ignore_errors=True)


def start_session(label: str = "") -> str:
    """Begin a new session folder and make it current. Old session folders
    beyond KEEP_SESSIONS are deleted -- this is scratch evidence, not an
    archive."""
    os.makedirs(ROOT, exist_ok=True)
    _prune_old_sessions(KEEP_SESSIONS - 1)
    # The trailing counter is what makes the name unique, not the timestamp:
    # two sessions started inside the same second would otherwise share a
    # folder and append to each other's journal while overwriting each other's
    # frames (seq restarts at 1 per session). A second-resolution stamp alone
    # isn't enough even with pruning in play -- pruning frees old names for
    # reuse. The timestamp still leads, so folders sort oldest-first.
    _state["n"] += 1
    session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{_state['n']:03d}"
    path = os.path.join(ROOT, session_id)
    os.makedirs(path, exist_ok=True)
    _state.update(
        {"dir": path, "id": session_id, "label": label, "started": time.time(), "seq": 0}
    )
    _state["ring"].clear()
    return session_id


def _ensure_session() -> str:
    if _state["dir"] is None:
        start_session()
    return _state["dir"]


def session_dir() -> str | None:
    return _state["dir"]


def session_info() -> dict:
    return {
        "session_id": _state["id"],
        "dir": _state["dir"],
        "label": _state["label"],
        "started": (
            datetime.fromtimestamp(_state["started"]).isoformat(timespec="seconds")
            if _state["started"]
            else None
        ),
        "records": _state["seq"],
        "in_memory": len(_state["ring"]),
    }


def _write_thumb(frame, path: str) -> float:
    """Save a downscaled JPEG of `frame` (a PIL image, or PNG bytes). Returns
    the scale factor applied (1.0 = full size), which callers must surface so
    nobody reads coordinates off the result."""
    img = frame if isinstance(frame, PILImage.Image) else PILImage.open(io.BytesIO(frame))
    if img.mode != "RGB":
        img = img.convert("RGB")
    scale = 1.0
    if img.width > THUMB_MAX_W:
        scale = THUMB_MAX_W / img.width
        img = img.resize((THUMB_MAX_W, max(1, round(img.height * scale))), PILImage.LANCZOS)
    img.save(path, format="JPEG", quality=THUMB_QUALITY)
    return scale


def _clip(value, limit: int):
    """Shorten a value for the log, but leave scalars as their real type so a
    coordinate reads back as 100, not "100"."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"...[+{len(text) - limit} chars]"


def record(
    tool: str,
    args: dict | None = None,
    ok: bool = True,
    result=None,
    ms: int | None = None,
    window: str | None = None,
    before=None,
    after=None,
    note: str | None = None,
) -> int:
    """Append one tool call to the journal. Returns its sequence number.

    Never raises: a journal that can crash the automation it is documenting is
    worse than no journal.
    """
    try:
        path = _ensure_session()
        _state["seq"] += 1
        seq = _state["seq"]
        entry = {
            "seq": seq,
            "t": datetime.now().isoformat(timespec="milliseconds"),
            "tool": tool,
            "args": {k: _clip(v, ARGS_MAX_CHARS) for k, v in (args or {}).items()},
            "ok": ok,
            "ms": ms,
        }
        if window:
            entry["window"] = window
        if result is not None:
            entry["result"] = _clip(result, RESULT_MAX_CHARS)
        if note:
            entry["note"] = note
        for which, frame in (("before", before), ("after", after)):
            if frame is None:
                continue
            name = f"{seq:04d}_{which}.jpg"
            try:
                entry["scale"] = round(_write_thumb(frame, os.path.join(path, name)), 4)
                entry[which] = name
            except (OSError, ValueError):
                pass
        _state["ring"].append(entry)
        with open(os.path.join(path, "journal.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return seq
    except Exception:  # noqa: BLE001 - journaling must never break automation
        return -1


def recent(n: int = 20, tool: str | None = None, failures_only: bool = False) -> list[dict]:
    """Most recent records, oldest-first, optionally filtered."""
    items = list(_state["ring"])
    if tool:
        needle = tool.strip().lower()
        items = [e for e in items if needle in e["tool"].lower()]
    if failures_only:
        items = [e for e in items if not e["ok"]]
    return items[-n:]


def get(seq: int) -> dict | None:
    for entry in _state["ring"]:
        if entry["seq"] == seq:
            return entry
    return None


def frame_path(seq: int, which: str = "after") -> str | None:
    """Absolute path to a stored thumbnail, or None if that step has no frame
    of that kind (or has aged out of memory)."""
    entry = get(seq)
    if entry is None or _state["dir"] is None:
        return None
    name = entry.get(which)
    if not name:
        return None
    path = os.path.join(_state["dir"], name)
    return path if os.path.exists(path) else None
