"""Managed-heap object counts for the attached process, via dotnet-gcdump.

Only meaningful for .NET processes (WinForms, WPF, WinUI/Uno, any CoreCLR app).

Why gcdump rather than reading the process's memory size: a .NET process does
not return heap segments to the OS when objects die, so working-set can stay
flat while objects leak and can stay high with nothing leaking. gcdump induces
a **gen2 blocking GC** and then counts only what survived it, so a type still
present afterwards is genuinely still referenced by something -- which is the
question a leak hunt is actually asking.

Everything below about the report format was measured against a real .NET 9
process holding a known number of objects (20,000 System.Uri), not assumed:

- The report's first column is the size of ONE object of that type, not the
  row's total. Summing (per-object x count) over every row came to 7,441,674
  bytes against the 9,816,966 the report's own header claimed -- 75.8%. So
  **byte figures here are approximate and counts are not**: the measured count
  for System.Uri was 20,008 against the 20,000 deliberately allocated, the
  extra 8 being the runtime's own.
- A type can appear on SEVERAL rows, split by object-size bucket
  ("(Bytes > 1K)", "(Bytes > 10K)", ...). System.String came back as three
  rows of 4, 25 and 57,406. 30 of 1,882 types were split this way. Rows must
  therefore be aggregated by type name before anything is compared, or a type
  will be undercounted and a leak that shifts an object between size buckets
  will read as growth in one row and shrinkage in another.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

# Rows look like:
#     262,168         1  System.Uri[] (Bytes > 100K)  [System.Private.Uri.dll]
#          56    20,008  System.Uri  [System.Private.Uri.dll]
_ROW = re.compile(r"^\s*([\d,]+)\s+([\d,]+)\s+(.+?)\s*$")
_HEADER_BYTES = re.compile(r"^\s*([\d,]+)\s+GC Heap bytes")
_HEADER_OBJECTS = re.compile(r"^\s*([\d,]+)\s+GC Heap objects")
_BUCKET = re.compile(r"\s*\(Bytes > [^)]+\)")
_MODULE = re.compile(r"\s*\[([^\]]+)\]\s*$")

# Measured: collect against a 100 MB pwsh took 1.58 s wall-clock, of which the
# target was actually stopped for 24 ms. Both scale with heap size, so this is
# generous rather than tight -- the cost of guessing low is a failed snapshot
# in the middle of someone's leak hunt.
COLLECT_TIMEOUT_S = 180


def find_gcdump():
    """Locate dotnet-gcdump, or None.

    PATH is checked first, then the default global-tool directory -- a shell
    started before `dotnet tool install -g` ran has a stale PATH and will not
    see it, which is the normal case right after someone installs it.
    """
    exe = shutil.which("dotnet-gcdump")
    if exe:
        return exe
    candidate = Path(os.environ.get("USERPROFILE", "")) / ".dotnet" / "tools" / "dotnet-gcdump.exe"
    return str(candidate) if candidate.exists() else None


_NOT_INSTALLED = (
    "dotnet-gcdump is not installed. It needs the .NET SDK:\n"
    "  dotnet tool install -g dotnet-gcdump\n"
    "If that reports 'No .NET SDKs were found', only the runtime is present "
    "and the SDK has to be installed first (winget install Microsoft.DotNet.SDK.9)."
)


def collect(pid: int, out_path: str) -> None:
    """Write a gcdump of `pid` to `out_path`. Raises with the tool's own
    message on failure -- most often because the process is not .NET at all,
    which no amount of retrying will change."""
    exe = find_gcdump()
    if exe is None:
        raise RuntimeError(_NOT_INSTALLED)

    try:
        proc = subprocess.run(
            [exe, "collect", "-p", str(pid), "-o", out_path],
            capture_output=True,
            text=True,
            timeout=COLLECT_TIMEOUT_S,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"dotnet-gcdump did not finish within {COLLECT_TIMEOUT_S}s against pid {pid}"
        ) from None

    if not os.path.exists(out_path):
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"dotnet-gcdump produced no dump for pid {pid} (exit {proc.returncode}). "
            "The usual cause is that the process is not a .NET process -- gcdump "
            f"can only read a managed heap. Tool output:\n{detail[:800]}"
        )


def report(dump_path: str) -> str:
    exe = find_gcdump()
    if exe is None:
        raise RuntimeError(_NOT_INSTALLED)
    proc = subprocess.run(
        [exe, "report", dump_path],
        capture_output=True,
        text=True,
        timeout=COLLECT_TIMEOUT_S,
        encoding="utf-8",
        errors="replace",
    )
    if not proc.stdout.strip():
        raise RuntimeError(
            f"dotnet-gcdump report returned nothing for {dump_path}: "
            f"{(proc.stderr or '').strip()[:400]}"
        )
    return proc.stdout


def parse(text: str) -> dict:
    """Turn a gcdump report into {"types": {name: {count, bytes_per_obj, module}},
    "total_objects": n, "total_bytes": n}.

    Rows sharing a type name are summed, because the report splits a type
    across size buckets (see the module docstring).

    bytes_per_obj is taken from the row holding the MOST instances, not the
    largest row and not a total. Taking the largest was tried and rejected on
    the measurement: System.String's three rows were 4 objects at 28,130 bytes,
    25 at 9,706 and 57,406 at 22, so "the largest" described 0.007% of the
    strings and reported every one of them as costing 28 KB. The dominant row
    describes what an instance of this type typically costs, which is the only
    reading that helps weigh a count.
    """
    types: dict[str, dict] = {}
    total_objects = 0
    total_bytes = 0

    for line in text.splitlines():
        m = _HEADER_BYTES.match(line)
        if m:
            total_bytes = int(m.group(1).replace(",", ""))
            continue
        m = _HEADER_OBJECTS.match(line)
        if m:
            total_objects = int(m.group(1).replace(",", ""))
            continue

        m = _ROW.match(line)
        if not m:
            continue
        per_obj = int(m.group(1).replace(",", ""))
        count = int(m.group(2).replace(",", ""))
        rest = m.group(3)

        module = ""
        mod = _MODULE.search(rest)
        if mod:
            module = mod.group(1)
            rest = _MODULE.sub("", rest)
        name = _BUCKET.sub("", rest).strip()
        if not name:
            continue

        entry = types.setdefault(
            name, {"count": 0, "bytes_per_obj": 0, "module": module, "_dominant": -1}
        )
        entry["count"] += count
        if count > entry["_dominant"]:
            entry["_dominant"] = count
            entry["bytes_per_obj"] = per_obj

    for entry in types.values():
        del entry["_dominant"]

    return {"types": types, "total_objects": total_objects, "total_bytes": total_bytes}


def diff(before: dict, after: dict, top: int = 25, min_delta: int = 1) -> dict:
    """Types whose instance count rose between two snapshots, biggest first.

    Counts only. The report's byte column is per-object and averaged per size
    bucket, so a byte delta computed from it would be a plausible-looking
    number that does not add up to the heap it claims to describe; the
    approximate bytes each surviving instance costs is reported alongside so a
    count can be weighed, but nothing is summed into a total.
    """
    b_types = before["types"]
    a_types = after["types"]

    grew = []
    for name, a in a_types.items():
        delta = a["count"] - b_types.get(name, {}).get("count", 0)
        if delta >= min_delta:
            grew.append(
                {
                    "type": name,
                    "before": b_types.get(name, {}).get("count", 0),
                    "after": a["count"],
                    "delta": delta,
                    "approx_bytes_each": a["bytes_per_obj"],
                    "module": a.get("module", ""),
                    "new": name not in b_types,
                }
            )
    grew.sort(key=lambda r: r["delta"], reverse=True)

    return {
        "grew": grew[:top],
        "types_that_grew": len(grew),
        "total_objects_before": before["total_objects"],
        "total_objects_after": after["total_objects"],
        "total_objects_delta": after["total_objects"] - before["total_objects"],
    }
