## ADDED Requirements

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
