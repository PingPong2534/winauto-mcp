# Install or repair the bundle

An install request includes the runtime, this skill, an enabled MCP connection
and all declared required plugins. Never report completion from just copying
SKILL.md or registering a server. Native Windows Codex and Claude Code are the
complete-bundle targets; Claude Desktop/Cowork/cloud provisioning differs.

1. Locate a winauto-mcp checkout containing `install.ps1`, `installation.json`
   and `skills/winauto-mcp`. Use the requested client; otherwise detect supported
   clients already installed. Use a trusted source with this version's installer.
2. From that checkout, run `install.ps1 -Client codex`, `-Client claude-code`,
   or `-Client both`. Default `auto` selects supported clients present.
   Include `-IncludeHeap` when installing for managed-heap diagnostics.
3. Let the installer prepare missing Python packages and declared plugin
   dependencies, probe the real server, and install a native plugin containing
   skill and MCP configuration. Dependencies come from installation.json;
   do not invent unrelated plugins.
4. Check exit status and the new `installation-result.json` in the state
   directory (default `%LOCALAPPDATA%\winauto-mcp`). Every selected client must
   have verified skill, MCP connector and enabled plugin. A failed component
   leaves the installation incomplete; inspect partial results before retrying.
5. Restart the client/session and confirm the plugin's MCP tools and skill are
   available. Use the actual namespace. Installation cannot add missing UIA
   patterns to the target app.

For updates/repairs, rerun against the current checkout and same state directory;
it updates the plugin revision and enables required plugins. Preserve unrelated
settings. Codex uses `$winauto-mcp`; Claude Code's plugin skill is
`/winauto-mcp:winauto-mcp`.

If only this portable skill folder is available, locate the matching repository
before server installation. It does not contain a server or standalone installer.
Report missing CLI/prerequisites/source or an unsupported client explicitly;
do not substitute an MCP-only install.
