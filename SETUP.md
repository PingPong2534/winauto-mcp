# Setup

## Requirements

Windows, Python 3.10+ and a desktop session containing the target app.
This migration was tested with Python 3.12.10, pywinauto 0.6.9 and MCP 2.0.0.
Install the complete bundle for the selected AI client:

```powershell
cd F:\tools\winauto-mcp
.\install.ps1 -Client codex
# Or: .\install.ps1 -Client claude-code / -Client both
```

`uiautomation` is no longer required. An existing venv may still contain it;
production code does not import it. The installed `pywinauto` uses its UIA
backend for ordinary controls. The explicit foreground_steps mode uses native
Win32 low-level input hooks/SendInput in a disposable worker, without another plugin.

The installer prepares the venv/dependencies, probes the real server, installs
declared required plugins, and registers a native plugin containing the complete
skill and enabled stdio MCP connection. See
[installation and repair](docs/skill-installation.md). Use `-IncludeHeap` when
the requested feature set needs .NET heap tooling.

## MCP launch configuration reference

Use stdio and absolute paths, so the client working directory is irrelevant.
The templates below explain the launch contract for custom clients and diagnosis.
For a supported-client installation, use install.ps1 so skill and connection
are installed together; config snippets alone are not a complete install.

### Codex

```toml
[mcp_servers.winauto]
command = 'F:\tools\winauto-mcp\.venv\Scripts\python.exe'
args = ['F:\tools\winauto-mcp\server.py']
enabled = true
startup_timeout_sec = 30
```

### VS Code

Example `.vscode/mcp.json`:

```json
{
  "servers": {
    "winauto": {
      "type": "stdio",
      "command": "F:\\tools\\winauto-mcp\\.venv\\Scripts\\python.exe",
      "args": ["F:\\tools\\winauto-mcp\\server.py"]
    }
  }
}
```

### Claude Desktop (MCP-only reference)

This illustrates `%APPDATA%\Claude\claude_desktop_config.json`. Claude Desktop
skill provisioning differs from Claude Code; this is not a supported
complete-bundle installation path:

```json
{
  "mcpServers": {
    "winauto": {
      "command": "F:\\tools\\winauto-mcp\\.venv\\Scripts\\python.exe",
      "args": ["F:\\tools\\winauto-mcp\\server.py"]
    }
  }
}
```

Restart/reconnect the client's MCP session after changing server source or
configuration, then confirm it exposes 35 tools, including `read_element`,
`set_value`, `control_set` and `foreground_steps`. Check tool names as well as the count: an older
process may expose a different tool set after files have changed. The installer
registers selected clients through their native plugin CLI and backs up settings;
the examples above do not imply configuration has been applied.

## Bundled agent skill

The [WinAuto MCP skill](docs/skill-installation.md) is part of every supported
installation, alongside server and connection. Its references cover all tools.

## Verify locally

```powershell
.\.venv\Scripts\python.exe tests\smoke.py
.\.venv\Scripts\python.exe -m compileall -q server.py control_sets.py input_sim.py uia_inspect.py window_manager.py screenshot.py integrity.py journal.py overlay.py heap.py location_cache.py tests
```

The smoke entry point runs the UIA and real stdio transport tests. It creates
only owned windows shown without activation and cleans them up. The UIA target
is normally activatable so provider activation can be detected. The suite does
not launch a user app or replay historical probes from `tests/legacy`.

Attach to your chosen application using `list_windows`, inspect `get_elements`,
and verify a supported command with `read_element` before relying on it.
Supporting UIA is app-dependent. Higher-integrity writes are refused; start
both app and server at a matching level when that is necessary. Do not use
`take_control=True` or physical-input fallbacks.

## Optional heap diagnostics

`heap_snapshot`/`heap_diff` need the .NET SDK and `dotnet-gcdump`:

```powershell
dotnet tool install -g dotnet-gcdump
```

These are separate diagnostics and force a blocking GC in the target process.
No .NET installation is needed for the UIA backend or its current tests.
