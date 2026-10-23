# Browser and guide verification - 2026-10-23

This report contains sanitized conclusions only. Real-account captures, installer
output, browser profiles and learned per-user guides remain local and are
excluded from Git. No account handles, post IDs, private content or schedules
are included here.

## Implemented routes

- The original WinAuto server exposes 45 tools: 36 desktop tools and nine owned
  persistent-browser tools. Microsoft Extension is a separate 25-tool connector.
- Owned browser profiles persist outside the checkout. Existing Edge tabs use
  the official Microsoft extension with normal per-connection tab approval.
  No approval-bypass token, cookie copying or stealth configuration is installed.
- The skill maintains one compact current guide per app/site outside Git and
  plugin caches, with live checks, invalidation and evidence-based replacement.

## Verified behavior

- Real Edge plus an isolated local HTTP fixture verified authentication and a
  draft surviving browser reopen and full MCP restart, then continued editing.
- The fixture verified upload/check/select and partial-failure reporting. Its
  publication endpoint received zero requests.
- A real authenticated TikTok Studio tab was inspected through the Microsoft
  extension. Reversible Posts search fill/read/clear/read succeeded; disconnect
  retained the user's original browser and tabs. No external publish, delete,
  upload or schedule operation was performed in these tests.
- Live guide reuse passed. The existing search anchor still matched the live
  page. A deliberately stale guide copy in a disposable store was invalidated
  and repaired against live observations; only its latest record remained.
  The real guide was refreshed with verified facts. Visible post identity/time
  fingerprints matched before and after. This was a controlled stale-guide test,
  not a claim that the site changed.
- Native Codex/Claude install/update tests use isolated client homes and passive
  real stdio handshakes; they preserve unrelated settings and never attach to
  user applications.

## Limits

- A separate new-profile TikTok login was not completed. Local fixture persistence
  does not establish real-site login acceptance or prove absence of site limits.
- Initial foreground-sensitive runs overlapped manual authentication and failed;
  a later serialized full run passed. Final security-review regression results
  are recorded separately in security-review.md.
- Microsoft Extension tools are upstream capabilities, including JavaScript;
  WinAuto desktop/owned-profile guards do not wrap that separate connector.
- Stored guides remain advisory, untrusted data. They do not grant publication
  authority, bypass native approval, or establish untaught workflows as tested.

## Reproduce safely

Run tests/test_browser.py, tests/test_browser_integration.py, tests/test_app_guides.py,
tests/test_installation.py and tests/test_transport.py for isolated verification.
The maintained real-site probes require explicit authorization and native login/tab
approval. Their default evidence destination is LOCALAPPDATA/winauto-mcp/verification,
outside the repository, and they persist only bounded sanitized result metadata.
