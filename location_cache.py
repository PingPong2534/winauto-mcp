"""Persistent per-app location cache: remembers where a semantically-labeled
click target was last found, scoped to (process name, client size, label).
Never trusted blindly -- server.recall_location() re-verifies a cached entry
against the live screen (via find_content_bbox) before handing back a
coordinate, and drops the entry if the re-scan doesn't match closely enough."""

import json
import os

_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".location_cache.json")


def _load() -> dict:
    if not os.path.exists(_CACHE_PATH):
        return {}
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _key(process_name: str, client_w: int, client_h: int, label: str) -> str:
    return f"{process_name.lower()}|{client_w}x{client_h}|{label.strip().lower()}"


def get(process_name: str, client_w: int, client_h: int, label: str):
    return _load().get(_key(process_name, client_w, client_h, label))


def put(process_name: str, client_w: int, client_h: int, label: str, bbox) -> None:
    data = _load()
    data[_key(process_name, client_w, client_h, label)] = {"bbox": list(bbox)}
    _save(data)


def forget(process_name: str, client_w: int, client_h: int, label: str) -> None:
    data = _load()
    data.pop(_key(process_name, client_w, client_h, label), None)
    _save(data)
