# SETUP — installing winauto-mcp

An MCP server that lets an LLM attach to an application window on Windows and
both **see** it (screenshots + UI Automation) and **drive** it (click, type,
hotkeys).

What each tool does is in [HOWTOUSE.md](HOWTOUSE.md) · the guaranteed behaviour
is in [docs/SPEC.md](docs/SPEC.md).

## Contents

- [🔴 Read this first: Windows only](#-read-this-first-windows-only)
- [Requirements](#requirements)
- [Step 1 — Get Python ready](#step-1--get-python-ready)
- [Step 2 — Install winauto-mcp](#step-2--install-winauto-mcp)
- [Step 3 — Verify the install with no client at all](#step-3--verify-the-install-with-no-client-at-all)
- [Step 4 — Connect it to a harness](#step-4--connect-it-to-a-harness)
- [What this does to your machine](#what-this-does-to-your-machine)
- [Troubleshooting](#troubleshooting)
- [Uninstalling](#uninstalling)

---

## 🔴 Read this first: Windows only

**This cannot be installed or run on macOS or Linux.** Not "untested" —
*impossible*. The server is bound to the Win32 API from its first lines. Three
layers of evidence:

| Layer | What breaks | Where |
|---|---|---|
| **At `pip install`** | `pywin32` and `uiautomation` only publish Windows wheels — pip finds nothing and fails before anything runs | `requirements.txt` |
| **At import** | `ctypes.windll` exists only on Windows, and it is called in the first few lines, before anything else | `server.py:10-35` (`_set_dpi_awareness()`) |
| **At run time** | Capture uses `PrintWindow`, element reading uses UI Automation, input guarding uses `WH_KEYBOARD_LL`/`WH_MOUSE_LL` hooks — all Windows-only, with no macOS equivalent | `screenshot.py`, `uia_inspect.py`, `input_guard.py` |

**So what do you do on a Mac?** This server speaks **stdio**, which means the
server and the harness must be on the **same machine** — there is no
cross-machine bridge. That leaves:

1. Run Windows in a VM (Parallels / VMware / UTM) and install **both the server
   and the harness inside that VM** — drive Windows apps in the VM using the
   Claude Code CLI running in the VM.
2. Use a separate Windows box over RDP — but beware: closing the RDP window
   locks the session and apps stop painting, so `screenshot` comes back blank.

> Options 1 and 2 are **untested** — there was no macOS machine available when
> this document was written. They follow from the stdio transport constraint
> that is verifiable in the code, not from an actual trial.

---

## Requirements

The right-hand column is what was **actually measured** on the machine this
document was written on (2026-08-26).

| Item | Minimum | Test machine |
|---|---|---|
| OS | Windows 10 1809 or later / Windows 11 | Windows 11 Home Single Language 10.0.26200.0 |
| Python | **3.10 or later** (the code uses `str \| None`, which needs 3.10+) | 3.12.10 |
| tkinter | **Required** — it draws the green outline around the attached window | 8.6 (bundled with the python.org installer) |
| Disk | ~120 MB for the venv | measured **97.9 MB** |
| Display scaling | Any value — the server declares itself per-monitor DPI aware | 100% |
| Privileges | Ordinary user, no Administrator needed | Ordinary user |

**Packages used** — `requirements.txt` lists six, and every one of them is
actually imported. The right-hand column is what was installed at the time of
writing:

| Package | What it is for | Version tested |
|---|---|---|
| `mcp[cli]>=1.2.0` | The MCP server transport | 2.0.0 |
| `pywin32` | Win32 calls — enumerate windows, raise a window, `SendInput` | 312 |
| `uiautomation` | Read the element tree (button names, fields, tooltip text) | 2.0.29 |
| `mss` | Screen grabs (the only path `hover` can use, since `PrintWindow` cannot see tooltips) | 10.2.0 |
| `Pillow` | Image diffing, cropping, downscaling, PNG encoding | 12.3.0 |
| `psutil` | Read the owning process name of a window | 7.2.2 |

> **`mcp 2.0.0` works.** This project imports `mcp.server.mcpserver`, which is
> the 2.x API. There is no need to pin `mcp<2` the way some older MCP servers
> do.

**Not needed**: Node.js, uv/uvx, Docker, Visual Studio Build Tools — `pywin32`
ships prebuilt wheels, so nothing is compiled.

---

## Step 1 — Get Python ready

Check what you have:

```powershell
python --version
```

You need **3.10.x or later**. Real output on the test machine:

```
Python 3.12.10
```

If it is missing, install from
[python.org](https://www.python.org/downloads/windows/) or:

```powershell
winget install Python.Python.3.12
```

Then **close and reopen the terminal** so PATH updates.

> ⚠️ **Do not install Python from the Microsoft Store.** That build ships
> without `tkinter`, so the green outline cannot be drawn and `import overlay`
> fails.

Check tkinter separately — it is the single most common thing missing:

```powershell
python -c "import tkinter; print('tkinter', tkinter.TkVersion)"
```

You should get `tkinter 8.6`.

---

## Step 2 — Install winauto-mcp

```powershell
git clone https://github.com/PingPong2534/winauto-mcp.git
cd winauto-mcp
python -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Clone it wherever you like. **Every command from here on is relative and assumes
you are in the repo root** — the folder holding `server.py`. Step 4 is the one
place that needs the absolute path, and it shows you how to print it.

**Why a venv** — `pywin32` installs system-level DLLs, and `mcp` changed its API
between major versions. Keeping it separate means it cannot collide with other
MCP tools on the same machine, and uninstalling later is one folder deletion.

---

## Step 3 — Verify the install with no client at all

**Do not skip this.** If you jump straight to configuring a harness and the
tools do not show up, you will have no way to tell whether the install or the
config is at fault.

### 3.1 Does it import?

```powershell
.venv\Scripts\python.exe -c "import server; print('ok', len([t for t in dir(server)]))"
```

A `ModuleNotFoundError` here means `pip install` did not complete — go back to
step 2.

### 3.2 Speak the MCP handshake directly

This is you acting as the client, with no dependence on Claude, Codex or VS
Code. Save this as `check_mcp.py` **in the repo root** and run it from there:

```python
import json, subprocess

PY  = r".venv\Scripts\python.exe"
SRV = "server.py"

p = subprocess.Popen([PY, SRV], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     text=True, encoding="utf-8", bufsize=1)
send = lambda o: (p.stdin.write(json.dumps(o) + "\n"), p.stdin.flush())

send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2024-11-05", "capabilities": {},
    "clientInfo": {"name": "check", "version": "0"}}})
init = json.loads(p.stdout.readline())

send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
tools = json.loads(p.stdout.readline())
p.terminate()

print("protocolVersion:", init["result"]["protocolVersion"])
print("serverInfo:", init["result"]["serverInfo"]["name"])
print("tools:", len(tools["result"]["tools"]))
```

Real output on the test machine:

```
protocolVersion: 2024-11-05
serverInfo: winauto-mcp
tools: 30
```

**The line that matters is `tools: 30`.** If you get that number, the server
runs, declares its full tool set, and is ready for a harness — anything that
still does not work after this is purely a harness config problem.

### 3.3 Try it for real (optional)

```powershell
.venv\Scripts\python.exe -c "import window_manager, json; print(json.dumps(window_manager.list_windows()[:3], ensure_ascii=False, indent=1))"
```

You should get a list of windows actually open on your screen, each with
`hwnd`, `title` and `process`.

---

## Step 4 — Connect it to a harness

Every harness connects the same way: **stdio**, running
`python.exe server.py`. Only the config file format differs.

**This is the one step that needs an absolute path.** A harness launches the
server itself, from a working directory you do not control, so a relative path
in a config file will not resolve. Print yours from the repo root:

```powershell
(Resolve-Path .).Path
```

Everything below writes that value as `<install-dir>` — substitute whatever
`Resolve-Path` printed, so that `<install-dir>\server.py` becomes the real full
path to `server.py` on your machine.

> In JSON you must write `\\` (two backslashes), because `\` is a JSON escape
> character. In TOML, use single quotes `'...'` and a single `\` is fine.

### Claude Code (CLI)

Run this **from the repo root** — PowerShell expands `$(Resolve-Path ...)`
before `claude` sees it, so what gets stored is the absolute path:

```powershell
claude mcp add winauto --scope user -- "$((Resolve-Path .\.venv\Scripts\python.exe).Path)" "$((Resolve-Path .\server.py).Path)"
```

Check that it connected:

```powershell
claude mcp list
```

You should see a line like `winauto: ... - ✓ Connected`.

> `--scope user` = available in every project on this machine (written to
> `~/.claude.json`). For a single project use `--scope project`, which writes
> `.mcp.json` in that folder.
>
> ⚠️ **Not actually registered on this machine** — `claude mcp list` at the
> time of writing does not contain `winauto`. The command shape is confirmed by
> `kinocut`, which is registered the same way and does report `✓ Connected`,
> and the server itself passed the handshake in step 3.2.

### VS Code (GitHub Copilot / Agent mode)

Create `.vscode/mcp.json` in the project folder:

```json
{
  "servers": {
    "winauto": {
      "type": "stdio",
      "command": "<install-dir>\\.venv\\Scripts\\python.exe",
      "args": ["<install-dir>\\server.py"]
    }
  }
}
```

For every project instead of one, press `Ctrl+Shift+P` and pick **MCP: Open
User Configuration**, which gives you a user-level file using the same schema.

Then open Copilot Chat in **Agent** mode and click the tools icon to check that
`winauto` appears.

> ⚠️ **Untested** — VS Code is installed on this machine (`code.cmd` is on
> PATH) but there is no `mcp.json` at either user or project level. What is
> written above is VS Code's `servers` schema, which is **different from Claude
> Desktop's `mcpServers`**. If your VS Code version rejects it, check the MCP
> page in the VS Code docs for the current schema.

> **Note**: if you use Claude Code through its VS Code extension, it reads
> Claude Code's own config (above), not `.vscode/mcp.json` — configure only one
> of the two.

### Codex

Edit `%USERPROFILE%\.codex\config.toml` and add:

```toml
[mcp_servers.winauto]
command = '<install-dir>\.venv\Scripts\python.exe'
args = ['<install-dir>\server.py']
enabled = true
startup_timeout_sec = 30
```

Then restart Codex Desktop or start a new task. To check, ask it to call
`list_windows` — if it is connected you get back a list of windows with
`hwnd`, `title` and `process`.

> ⚠️ **Untested** — this machine does have `~/.codex/config.toml` (it already
> holds an `[mcp_servers.node_repl]` block) but no `winauto` block.
>
> 🔴 **If you copied this block from the README before 2026-08-26**, it said
> `K:\winauto-mcp`, and there is no `K:` drive — the path was the reason
> nothing loaded, not the config format. The README is corrected now.

### Claude Desktop

Edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "winauto": {
      "command": "<install-dir>\\.venv\\Scripts\\python.exe",
      "args": ["<install-dir>\\server.py"]
    }
  }
}
```

Then **fully quit Claude Desktop** (right-click the system tray icon → Quit,
not just the window's X) and start it again.

> ⚠️ **Untested** — this machine has no `claude_desktop_config.json`, meaning
> Claude Desktop is not installed here.

### Confirming the connection in any harness

Ask the chat to **"call list_windows"**. If it is connected you get the windows
that are genuinely open right now, with `hwnd`, `title` and `process`. If it
says there is no such tool, the harness has not loaded the server — see
[Troubleshooting](#troubleshooting).

---

## What this does to your machine

This is not a read-only tool. It drives your real mouse and keyboard, so know
this before you use it (full detail in
[docs/SPEC.md](docs/SPEC.md#costs-and-irreversibility)):

| What happens | When | Notes |
|---|---|---|
| Installs a **machine-wide keyboard hook** | On the first input call, not at startup | It sees every key on the machine but **stores none of them** — only a count and a timestamp, never which key |
| Installs a **machine-wide mouse hook** | Only on the first `hover` call | It never reads cursor coordinates. Never call `hover` and it is never installed |
| **Steals the foreground** | On every input call | Handed back automatically when the action ends |
| **Blocks your keyboard** | While an action runs | Expires on its own after 20 s · press **Esc three times within 1.5 s** to take it back immediately |
| **Pins your mouse** | Only during `hover` | Expires on its own after 3 s · refuses to pin at all if you are mid-drag |
| Writes screen images to `%TEMP%` | On every capturing tool call | **Unencrypted.** If your screen shows something private, so does the image. Cleared automatically every 5 sessions |
| Writes `.location_cache.json` | When `remember_location` is called | Sits next to `server.py` |

**Emergency exit** if you feel you have lost control of the machine: mash
**Esc three times**. The keyboard comes back immediately and latches so it
cannot be taken again until `release_keyboard()` is called. If that is not
enough, kill the harness process — Windows tears down every hook for you.

---

## Troubleshooting

### The harness says there is no `list_windows` tool

1. Run step [3.2](#32-speak-the-mcp-handshake-directly) first. If you get
   `tools: 30`, the server is fine and the problem is the config.
2. Check that the config path points at `.venv\Scripts\python.exe` and **not**
   plain `python` — the `python` on PATH does not have the venv's packages.
3. Restart the whole harness application. Reloading a window usually does not
   reload MCP servers.

### `ModuleNotFoundError: No module named 'win32api'`

`pip install` did not complete, or you ran the wrong Python. Rerun with the
full path:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### `ModuleNotFoundError: No module named 'tkinter'`

Your Python has no tkinter — almost always the Microsoft Store build. Install
from [python.org](https://www.python.org/downloads/windows/) and recreate the
venv.

### Clicks land 50–150 px off target

DPI scaling is the usual suspect, but the server already declares itself
per-monitor DPI aware in its first lines (`server.py:13-35`). The far more
common cause is **reading coordinates off a cropped image by eye** — a crop
shown in chat has been rescaled in a way that cannot be mapped back to real
coordinates. Use `locate_in_region()` instead (see
[HOWTOUSE.md](HOWTOUSE.md#getting-coordinates-right)).

### `screenshot` comes back blank or pure black

Happens with some GPU-rendered apps, and always in an RDP session whose window
has been closed (Windows stops painting once the session locks) — you need a
session that is genuinely unlocked.

### The green outline covers another window or gets stuck

Call `release_control()` or `detach_window()` and it disappears; it comes back
on the next input call.

---

## Uninstalling

```powershell
# Remove the Claude Code registration (run this first, while the folder exists)
claude mcp remove winauto --scope user

# Remove leftover journal images
Remove-Item -Recurse -Force "$env:TEMP\winauto_*" -ErrorAction SilentlyContinue

# Remove the program itself, from the PARENT of the clone (the venv is inside it)
cd ..
Remove-Item -Recurse -Force .\winauto-mcp
```

For VS Code / Codex / Claude Desktop, delete the `winauto` block from the
config file by hand.

**Nothing persists in the system.** Every hook is owned by the process; when
the process dies, Windows removes them. No registry key, no service, no startup
entry.

---

Last updated: 2026-08-26 · Tested on Windows 11 Home Single Language 10.0.26200.0 · Python 3.12.10 · mcp 2.0.0 · 30 tools
