# Installation

- An install request means the complete bundle: Python runtime dependencies,
  the winauto-mcp skill, enabled MCP connection, and declared required plugins.
  Use `install.ps1`; never report completion after installing only one component.
- Support local Windows Codex and Claude Code through native plugin registration.
  Preserve unrelated client settings. Do not treat Claude Desktop MCP-only setup
  as a complete supported installation.
- Declare required plugin IDs and marketplace sources in `installation.json`.
  The installer installs/enables them before the main plugin. Do not add unrelated
  plugins. Include heap tooling when the requested feature set needs it.
- Load `docs/skill-installation.md` when installing, updating or repairing a client.

# Validation

- Skip Unity smoke/integration probes when Unity Editor is unavailable; record
  the skip and continue other checks. Do not install Unity for smoke tests.
  Keep failures from an available Editor as failures.
- Keep `skills/winauto-mcp/references/tools.md` aligned with registered server tools.
- For installer changes, test native Codex/Claude plugin registration with isolated
  client homes, verify copied skills and MCP descriptors, and perform a real stdio
  handshake. Do not attach or mutate a user app during installation tests.
