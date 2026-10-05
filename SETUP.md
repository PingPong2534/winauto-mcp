# Setup

## Requirements

Windows, Python 3.10+ and a desktop session containing the target app.
This migration was tested with Python 3.12.10, pywinauto 0.6.9 and MCP 2.0.0.
Install dependencies into the repository's venv:

```powershell
cd F:\tools\winauto-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

`uiautomation` is no longer required. An existing venv may still contain it;
production code does not import it. The installed `pywinauto` uses its UIA
backend, never its physical input helpers.

## MCP client launch configuration

Use stdio and absolute paths, so the client working directory is irrelevant.
The examples below preserve the repository's existing client configuration
formats. They are templates, not a statement that any client is configured.

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

### Claude Desktop

Merge the server entry into `%APPDATA%\Claude\claude_desktop_config.json`:

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
configuration, then confirm its tool list includes `read_element` and
`set_value`. This migration does not modify any installed client configuration.

## Verify locally

```powershell
.\.venv\Scripts\python.exe tests\smoke.py
.\.venv\Scripts\python.exe -m compileall -q server.py input_sim.py uia_inspect.py window_manager.py screenshot.py integrity.py journal.py overlay.py heap.py location_cache.py tests
```

The smoke entry point runs the UIA and real stdio transport tests. It creates
only owned, non-activating windows and cleans them up. It does not launch a
user app or replay historical probes from `tests/legacy`.

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
