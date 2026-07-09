# embedded-app-new-tab

## Purpose

Allow users to open an embedded, backend-proxied tool (MLflow, Argo Workflows,
or the code-server development workspace) in a dedicated browser tab from its
in-app toolbar, reusing the existing authenticated session.

## Requirements

### Requirement: Open embedded tool in a new browser tab

The system SHALL provide an "Open in new tab" action in the workspace toolbar of
each embedded, backend-proxied tool view (MLflow, Argo Workflows, and the
Development code-server workspace) that opens the tool's same-origin proxy URL in
a new browser tab.

The action MUST open the same backend proxy URL that the in-app iframe uses, so
that the existing authenticated session is reused without additional sign-in.
The new tab MUST be opened with `noopener` so the opened document cannot access
the originating window via `window.opener`.

#### Scenario: Open MLflow in a new tab

- **WHEN** MLflow is installed and ready and the user clicks "Open in new tab"
- **THEN** the MLflow proxy URL opens in a new browser tab using the current
  session, and the existing in-app iframe view remains unchanged.

#### Scenario: Open Argo Workflows in a new tab

- **WHEN** the Argo Workflows server is available and embedded and the user
  clicks "Open in new tab"
- **THEN** the Workflows proxy URL opens in a new browser tab using the current
  session.

#### Scenario: Open Development workspace in a new tab

- **WHEN** a Development (code-server) workspace is ready and embedded and the
  user clicks "Open in new tab"
- **THEN** the selected workspace's proxy URL opens in a new browser tab using
  the current session.

#### Scenario: Opened tab cannot reference the opener

- **WHEN** any "Open in new tab" action is invoked
- **THEN** the new tab is opened with `noopener` and the opened document's
  `window.opener` is `null`.

### Requirement: Gate the action on embedded readiness

The "Open in new tab" action SHALL only be available when the corresponding
embedded tool is ready to load, matching the same readiness condition that gates
the in-app iframe for that view. The action MUST be positioned before the
Refresh and the destructive Uninstall/Delete actions in the toolbar.

#### Scenario: Action hidden or disabled until ready

- **WHEN** an embedded tool is not yet ready (not installed, not running, or no
  workspace selected)
- **THEN** the "Open in new tab" action is not available to trigger.

#### Scenario: Action ordered before destructive actions

- **WHEN** the workspace toolbar is rendered for a ready embedded tool
- **THEN** "Open in new tab" appears before Refresh and before the
  Uninstall/Delete action.

### Requirement: Display in-cluster service URL beside the new-tab action

The system SHALL display the in-cluster service URL of MLflow and of Argo
Workflows in the workspace toolbar, positioned beside the "Open in new tab"
action for that tool. The displayed value MUST be the cluster service endpoint
in the form `http://<service>.<namespace>.svc.cluster.local:<port>` (MLflow port
`5000`, Argo port `2746`), and MUST NOT be the browser proxy path. The value
MUST be provided by the backend status response so that ports are not hardcoded
in the frontend. The control MUST be read-only and MUST allow copying the value
to the clipboard.

#### Scenario: MLflow tracking URI shown

- **WHEN** MLflow is installed and ready
- **THEN** the MLflow page toolbar shows the MLflow in-cluster service URL (its
  tracking URI) beside the "Open in new tab" action.

#### Scenario: Argo server URL shown

- **WHEN** the Argo Workflows server is enabled and reachable
- **THEN** the Workflows page toolbar shows the Argo in-cluster server URL beside
  the "Open in new tab" action.

#### Scenario: Copy the service URL

- **WHEN** the user activates the copy control next to a displayed service URL
- **THEN** the cluster service URL is copied to the clipboard.

#### Scenario: Proxy path is not shown as the service URL

- **WHEN** a service URL is displayed for MLflow or Argo Workflows
- **THEN** the displayed value is the `…svc.cluster.local:<port>` endpoint and not
  the browser proxy path (for example `/api/mlflow/proxy/`).

#### Scenario: No service URL when the tool is unavailable

- **WHEN** the tool is not installed, disabled, or its service URL is not
  configured
- **THEN** no service URL control is shown for that tool.
