## Why

To run a job from a code-server workspace against MLflow or Argo Workflows
(e.g. `mlflow.set_tracking_uri(...)` or `argo submit --argo-server ...`), the
user needs the **in-cluster service URL** of that tool. Today that URL is not
shown anywhere in the UI, so users must guess the service name, namespace, and
port. The browser "Open in new tab" proxy URL does not work for in-cluster job
submission, which makes the gap easy to get wrong.

## What Changes

- Display the **in-cluster service URL** for MLflow and Argo Workflows in the
  workspace toolbar, beside the existing "Open in new tab" action:
  - MLflow page shows the MLflow tracking URI, e.g.
    `http://<service>.<namespace>.svc.cluster.local:5000`.
  - Workflows page shows the Argo server URL, e.g.
    `http://<service>.<namespace>.svc.cluster.local:2746`.
- The displayed value is the cluster service endpoint (NOT the browser proxy
  path), suitable for use from inside a code-server pod.
- The URL is presented as a read-only, copy-to-clipboard control so it can be
  pasted into `MLFLOW_TRACKING_URI` / `--tracking-uri` / `--argo-server`.
- Backend exposes the canonical service URL on the existing status responses so
  the frontend does not hardcode ports.

## Capabilities

### New Capabilities
<!-- None: extends the existing embedded-app-new-tab capability. -->

### Modified Capabilities
- `embedded-app-new-tab`: add a requirement to display the in-cluster service
  URL beside the "Open in new tab" action for MLflow and Argo Workflows.

## Impact

- Backend (`triton-backend`):
  - `app/schemas/workflows.py` — add `service_url` to `ArgoWorkflowsStatusResponse`.
  - `app/services/workflows/status.py` — populate `service_url` from `config.server_url`.
  - `app/schemas/mlflow.py` — add `service_url` to `MlflowStatusResponse`.
  - `app/services/mlflow/installer.py` — populate `service_url` via the existing
    `kubernetes.service_url(namespace, service_name)` helper when installed.
- Frontend (`triton-frontend`):
  - Regenerate the OpenAPI client (`npm run generate:api`).
  - `pages/mlflow/mlflow-page.component.{ts,html}` and
    `pages/workflows/workflows-page.component.{ts,html}` — show the service URL +
    copy button in the toolbar.
- No database, Helm, or auth changes. The service URL is a non-secret DNS name.
