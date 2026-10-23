# Security review - 2026-10-23

## Reviewed scope and disposition

Reviewed the complete staged source, tests, instructions, package manifests,
lockfile and sanitized reports for the persistent-browser, Microsoft Extension,
app-guide learning and installer updates. The fetched base had no unpublished
commits on this branch. The exact reviewed index tree is checked again before
commit; every resulting outgoing commit is checked before push.

Resolved findings:

- Real-account captures and installer/regression logs contain private page data
  or local paths. They remain local, are excluded from Git, and are replaced in
  the release by sanitized conclusions. Profiles, cookies, session state and
  learned per-user guides are not included.
- Displayed URLs now strip userinfo as well as query strings and fragments.
  Regression cases cover embedded user/password and IPv6 authority handling.
- Password type matching is case-insensitive. Password and autocomplete-marked
  OTP values are redacted and refused as action targets; real Edge fixture tests
  verify redaction and zero completed writes for uppercase password/OTP inputs.
- Guide records, lock files and target directories refuse symbolic/reparse links
  redirecting them outside the owned store. Atomic replacement, revision conflict
  checks and an OS writer lock protect normal concurrent guide updates.
- Real-site probes no longer hardcode a real account or story, persist raw page
  snapshots/account links, or log upstream error contents. Defaults write bounded
  sanitized metadata outside the checkout and use a generic search term.
- Node.js 20+ matches the locked transitive Playwright engine requirement.
- Known-vulnerable installed Python packages were upgraded and dependency floors
  raised. The installer also prepares a patched pip version.
- Public GitHub noreply identity is used for the outgoing commit metadata.

## Automated and manual checks

- detect-secrets 1.5.0 scanned all 108 tracked/staged files, including this report.
  Three findings are synthetic basic-auth URLs in tests/test_browser.py and
  tests/test_app_guides.py, reviewed as intentional rejection/redaction fixtures.
  No real credential was identified. Offline verification prevents sending any
  candidate secret to a third-party validation endpoint.
- Additional staged-content review checked private account markers, email-like
  strings, private-key headers, binary content and generated artifacts. Only
  synthetic example.com credential fixtures matched the email-like pattern.
- Bandit 1.9.4 found no medium/high findings. Three low findings were reviewed:
  best-effort stale-handle disposal, subprocess import and argument-list execution
  in the installer. Commands use trusted client/runtime executables and argument
  arrays without shell=True; no web content is executed as shell code.
- pip-audit 2.10.1: initial installed environment reported 70 advisories in seven
  packages; the updated environment reported no known vulnerabilities.
- npm audit: zero reported vulnerabilities in the locked Microsoft connector.
- Full regression: 159 tests run, 158 passed and one skipped. The skip is the
  real file-symlink test because this Windows user cannot create symlinks.
  Browser, UIA, transport and isolated Codex/Claude install/update checks passed.
- Skill validation, Python compilation, pip check and Git whitespace checks passed.

Audited runtime versions: MCP 2.3.0, Pillow 13.0.0, httpx2/httpcore2 2.16.0,
OpenTelemetry API 1.45.1, PyJWT 2.15.1 and pip 26.2.2. Microsoft MCP remains pinned
at 0.0.83 with its lockfile integrity metadata reviewed.

## Limits

The review found no unresolved security defect or private artifact in the outgoing
scope. Scanners do not establish absence of every possible defect or future
advisory. Local user-owned storage is not a hostile-filesystem security boundary.
The symlink integration case remains unexecuted on this host. Microsoft Extension
retains upstream capabilities and normal tab approval, with no token bypass;
WinAuto's owned-profile guards do not wrap that separate server. No live account
publication or schedule operation was performed during this review.
