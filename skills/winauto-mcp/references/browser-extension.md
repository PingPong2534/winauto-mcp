# Microsoft Extension mode

Use for explicitly authorized existing Edge tabs/session reuse. Prefer provider
native browser control unless WinAuto is explicitly requested or the provider
route has an observed limitation. Never use this mode to bypass a site refusal.

## Installation and connection

- Run the complete bundle installer. It installs pinned `@playwright/mcp`
  0.0.83 using npm ci and registers `playwright-extension` alongside `winauto`.
- Install Microsoft's Playwright Extension from the official link in
  https://github.com/microsoft/playwright/blob/main/packages/extension/README.md.
  The store ID is `mmlmfjhmonkocbjadbfplnigmagldckm`.
- The first browser tool opens native approval/tab selection in Edge. Keep
  interactive approval; ask the human to select the intended tab when needed.
  Do not configure `PLAYWRIGHT_MCP_EXTENSION_TOKEN`, scrape tokens, copy cookies,
  or enable stealth/security-bypass options. Never log connect URL queries.
- Multiple profiles require explicit selection of the profile containing the
  user's intended session. Do not silently select a different account.

## Tool and ownership contract

- Resolve the actual `playwright-extension` namespace and live schemas. This
  official upstream connector exposes 25 tools in the pinned version. The
  original `winauto` connector separately exposes 45 tools, including its nine
  owned-profile tools. Never mix their refs/session IDs or assume same schemas.
- List tabs, inspect `browser_snapshot`, verify current URL/account and use
  current snapshot refs for `browser_fill_form`, `browser_click`, and uploads.
  Upstream offers broad operations, including JavaScript; use ordinary DOM tools
  where sufficient. These are upstream capabilities, not WinAuto's UIA guards.
- Limit control to the human-authorized tab. Preserve unrelated tabs, forms,
  accepted upload IDs, drafts and schedules. Serialize all controllers.
- Existing authentication does not authorize publication. For a no-post test,
  allow only tab discovery, snapshots and reversible Posts search fill/restore.
  Never invoke upload, Post, Schedule, delete or submission during that test.
- Do not call upstream `browser_close` to disconnect: it can close a user page.
  End the MCP connection and verify the existing browser/tab remains available.
- On failure inspect current state before retrying only unfinished work. A
  timeout may leave the native connection page pending; avoid opening duplicate
  approval pages. Site security/CAPTCHA refusals need supported resolution.

`tests/probe_extension_session.py` is a maintained real-transport no-post probe.
It accepts connect/inspect/search/stop; search fills and restores only the
observed empty TikTok Posts search textbox. It records no authentication tokens.
