# ZiiDMS Departments testing

Load only for ZiiDMS NextGen launched through DmsEnv. These are app/environment
notes, not MCP tool guarantees. The current winauto test suite uses owned Win32
controls and does not verify ZiiDMS, its launcher or its business rules.

## Session and target

- Confirm the active client exposes the winauto MCP tools, including control_set.
  A local clone alone does not register the server; reconnect after configuration
  or source changes. See [SETUP.md](../SETUP.md).
- Start with list_windows. It omits hidden, minimized and untitled windows.
  A Get-Process.MainWindowHandle of zero alone does not prove there is no target.
- The MCP server has no launch/restart tool. If the app is missing, verify the
  machine's launcher path, arguments and expected app state before launching it.
- For the DmsEnv hidden-wrapper case, verify that it applies to the current
  launcher before stopping/relaunching the prior client PID. Restore any temporary
  visible-wrapper launcher edit immediately after use.

## Launch example

This path was recorded for a different local profile. Check its existence and
the endpoint/organization/dealership/screen values on the current machine first.

```powershell
& 'C:\Users\Ping\.dmsenv\repos\eziidms-nextgen-dmsenv-a59a578e\run-client.ps1' -BypassLogin -ApiServer 'https://localhost:7160' -OrganizationId 7000 -DealershipId 7001 -Screen DepartmentsView
```

## Control and evidence

- Attach to the HWND returned for ZiiDMSNextGen Local, with a feature label.
  Capture the full client area and UIA summary as the initial baseline.
- Obtain unique selectors from get_elements. Prefer automation IDs; exact names
  such as Save, Delete Selected, Restore Selected, New Department and Include
  Deleted are usable only when unambiguous in the current screen.
- For textbox click/type/crop, use control_set with the returned control_type
  and control_steps. Read-only/disabled writes are refused by the server.
- For grid rows use an exposed UIA selection action. Custom-drawn cells without
  supported patterns cannot be controlled through this backend. Coordinate
  clicks still require an interactive UIA control; no physical-input fallback.
- Inspect read_element for exposed selection/value states and get_elements for
  enabled states. Capture the full screen for row styling or screen-wide state;
  a textbox crop cannot establish whether other buttons or rows are missing.
- Record unexpected discard/confirmation prompts before dismissing them.
  The expectation that protected fields leave the detail clean is an app-specific
  acceptance criterion to test, not behavior guaranteed by winauto.
