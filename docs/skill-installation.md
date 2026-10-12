# Install the complete WinAuto MCP bundle

An installation always includes the server runtime dependencies, the
[winauto-mcp skill](../skills/winauto-mcp/SKILL.md), an enabled MCP connection,
and every declared required plugin. Do not install only the skill or only
an MCP config entry and report the bundle as complete.

## Supported clients

| Client | Installation |
| --- | --- |
| Codex CLI; local desktop/IDE on the same Codex home | Native local plugin containing skills and stdio MCP configuration |
| Claude Code on Windows | Native user-scoped plugin containing skills and stdio MCP configuration |
| Claude Desktop / Cowork / cloud clients | Not a supported complete-bundle target in this installer; skill/connector provisioning differs |

Install the chosen AI client first and sign in through its normal flow. The
installer requires its plugin-capable CLI; Codex desktop's bundled CLI is
detected when it is not on PATH. The server needs a local Windows desktop
session. Installing a Windows stdio connection does not provision a cloud host.

The package uses the supported [Codex plugin layout](https://developers.openai.com/plugins/build/plugins)
and [Claude Code marketplace/plugin installation](https://code.claude.com/docs/en/plugin-marketplaces).
Plugin registration adds the skill and MCP connection together; no separate
manual skill copy or MCP-config edit is required.

## One-command installation

Run from a checkout containing `install.ps1`, `installation.json` and the skill:

```powershell
# Codex
.\install.ps1 -Client codex

# Claude Code
.\install.ps1 -Client claude-code

# Both clients
.\install.ps1 -Client both

# Detect supported clients already present on this machine
.\install.ps1
```

The installer:

1. Creates `.venv` when missing, installs `requirements.txt`, and runs `pip check`.
   If no Python is available, it installs Python 3.12 through WinGet. Python
   must be 3.10+. Missing WinGet or a failed prerequisite exits with an error.
2. Starts the real stdio server from a different working directory, initializes
   MCP, discovers its tools and performs a passive status query. It does not
   attach or change a user application.
3. Builds a local marketplace under `%LOCALAPPDATA%\winauto-mcp` and packages
   the complete skill with absolute stdio executable/server paths.
4. Installs/enables selected clients' declared plugin dependencies, then
   installs/enables `winauto-mcp@winauto-local` through each client's native CLI.
5. Verifies the installed skill files and MCP descriptor, then writes
   `installation-result.json`. `ok=true` requires all selected clients to pass.

Restart the affected AI client/session to load the installed plugin and MCP
tools. Preserve the checkout and `.venv`: the connection launches that server.
Rerun the installer after moving the checkout.

If PowerShell blocks a downloaded script, review it and use your normal local
script execution policy. The installer does not change system execution policy.

## Required plugins and feature dependencies

`installation.json` declares `required_plugins` separately for Codex and Claude
Code. There are currently **no external plugin dependencies** for the UIA
backend. Python libraries are runtime dependencies and are always installed.
Required marketplace sources are resolved and plugins installed/updated and
verified enabled before the main plugin. Unrelated suggestions are not installed.

To declare a future dependency, add an entry under the appropriate client:

```json
{
  "id": "required-plugin@its-marketplace",
  "marketplace_source": "owner/repository"
}
```

The source must be a real, reviewed local/Git marketplace accepted by that
client. Missing/unavailable dependencies fail installation; they are not skipped.
Keep the selected client's list accurate. Claude's generated manifest also
declares these dependencies.

For the .NET heap features, install missing tooling in the same operation:

```powershell
.\install.ps1 -Client both -IncludeHeap
```

This ensures an SDK (installs .NET SDK 10 through WinGet if none is found) and
installs `dotnet-gcdump` if absent. WinGet may need the OS's normal elevation
flow. See [Microsoft's Windows .NET installation](https://learn.microsoft.com/en-us/dotnet/core/install/windows).
Heap collection needs a suitable .NET target and forces blocking GC; installation
does not collect a user's heap. No heap tools are needed for UIA.

## Use the installed skill

- Codex: invoke `$winauto-mcp`, or select **WinAuto MCP** in the skill picker.
- Claude Code: invoke `/winauto-mcp:winauto-mcp` (plugin-prefixed skill).

Example request:

> Use the WinAuto MCP skill to attach the customer app, label the feature
> "Customer name", inspect its text controls, replace the name with "Alice",
> and return a crop of the textbox. Inspect the report before retrying a write.

The skill covers all 36 tools with [the tool reference](../skills/winauto-mcp/references/tools.md)
and [batch recovery](../skills/winauto-mcp/references/batches-and-diagnostics.md).

## Update, repair and diagnosis

Update the checkout and rerun the same command with the same state directory.
The plugin cache version incorporates skill/server content, so changed files
receive a new native plugin revision. Repeated installation updates/re-enables
the existing plugin ID. Hash checks detect incomplete/stale skill copies.

Client settings are edited by the native CLI. Existing configuration files are
backed up under the state directory's `backups` before registration; unrelated
settings remain in place. Existing standalone winauto entries are not migrated;
use the installed plugin's namespace and retire obsolete standalone entries
through the client's normal controls.

Failures exit nonzero. Installed components are not rolled back; inspect
`installation-result.json` and the error, resolve the blocker and rerun. A
restart is still required after the installer validates the bundle.

```powershell
codex plugin list --marketplace winauto-local --json
claude plugin list --json
Get-Content "$env:LOCALAPPDATA\winauto-mcp\installation-result.json"
```

For isolated validation or a nondefault client home:

```powershell
.\install.ps1 -Client both -StateDir C:\Temp\winauto-test\state `
    -CodexHome C:\Temp\winauto-test\codex -ClaudeHome C:\Temp\winauto-test\claude
```

Parameters apply to subprocesses; they do not change your shell's home variables.
Keep test state outside real client settings. To uninstall, remove the native
plugin and its local marketplace through each client's plugin manager; this
does not delete the checkout or venv. Claude Desktop requires its own supported
skill-import/connector workflow; copying a skill to Claude Code's directory
does not provision Claude Desktop.
