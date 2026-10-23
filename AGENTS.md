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
- For app/browser control and interop probes, follow the skill's
  `references/app-guides.md`: reuse only live-checked guidance and maintain
  minimal current per-target guides outside Git; never save private test data.
- For installer changes, test native Codex/Claude plugin registration with isolated
  client homes, verify copied skills and MCP descriptors, and perform a real stdio
  handshake. Do not attach or mutate a user app during installation tests.

# Security review before commit or push

- Before every commit, review the exact staged diff and complete staged file
  contents for security defects and sensitive data. Before every push, review
  all outgoing commits and their contents, including intermediate revisions;
  a clean final tree does not prove the outgoing history is safe.
- Check credentials, API/security keys, tokens, passwords, cookies, browser
  profiles/session state, private keys, connection strings, personal/private
  data, and sensitive URLs or local configuration. Include generated files,
  logs, test evidence, screenshots, binary files, metadata and lockfiles.
- Review changed code for unsafe input/code execution, authentication and
  authorization failures, data exposure, and regressions in browser ownership
  or input-safety controls. Use available secret/security scanners in addition
  to manual review; passing tests or .gitignore alone is insufficient.
- Block commit/push while findings remain unresolved or any included content
  cannot be inspected. Remove or sanitize sensitive artifacts and verify the
  exact scope again. A fix in a later commit does not remove an earlier secret
  from outgoing history; resolve it before pushing. Never print secret values.
- Report the reviewed scope, checks and result before proceeding. Repeat the
  review after any change to staged content or outgoing commits; never claim
  safety from an earlier review or a scanner result alone.
