# Historical SendInput probes

These scripts and `input_guard.py` are retained as evidence for the former
SendInput backend on `master` before the pywinauto migration. They are not the
current regression suite and are not discovered by `tests/smoke.py`.

Several launch or modify real apps (including Notepad and its saved session),
use global input hooks, or call removed input/focus APIs. They require the old
revision and must not be used to validate the UIA backend. They were archived
without executing them during this migration.

Run the current suite from the repo root instead:

```powershell
.\.venv\Scripts\python.exe tests\smoke.py
```

`test_pywinauto.py` owns and cleans up its test windows. It traps real input
and focus calls, verifies values and actual control handlers, and tests a
covered target. `test_transport.py` launches the real MCP stdio process from
an unrelated temporary working directory.
