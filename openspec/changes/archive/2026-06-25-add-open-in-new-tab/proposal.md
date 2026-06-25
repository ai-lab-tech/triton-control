## Why

MLflow, Argo Workflows, and Development (code-server) are embedded inside Triton
Control via `<iframe>`, so they live inside a single browser tab and lose the
benefits of a standalone window (full screen real estate, browser history,
bookmarking, multi-monitor use). Users need a way to pop each embedded tool out
into its own dedicated browser tab.

## What Changes

- Add an **"Open in new tab"** action to the workspace toolbar of each embedded
  tool view: MLflow, Workflows (Argo), and Development (code-server).
- The action opens the existing same-origin backend **proxy URL** in a new
  browser tab using `window.open(url, "_blank", "noopener")`, so the current
  session cookie carries over and no new authentication or ingress is required.
- The button is placed first in the toolbar action group (before Refresh and the
  destructive Uninstall/Delete actions) and is only shown/enabled when the
  embedded frame is ready to load (same readiness gate each page already uses for
  its iframe).
- No backend or API changes.

## Capabilities

### New Capabilities
- `embedded-app-new-tab`: Ability to open an embedded, backend-proxied tool
  (MLflow, Argo Workflows, code-server development workspace) in a dedicated
  browser tab from its in-app toolbar.

### Modified Capabilities
<!-- None: no existing spec requirements change. -->

## Impact

- Frontend only (`triton-frontend`):
  - `src/app/pages/mlflow/mlflow-page.component.{ts,html}`
  - `src/app/pages/workflows/workflows-page.component.{ts,html}`
  - `src/app/pages/development/development-page.component.{ts,html}`
- Each component already computes a same-origin proxy URL string for its iframe;
  MLflow and Workflows additionally need to expose the raw (unsanitized) URL
  string alongside their existing `SafeResourceUrl` signal.
- No backend, API contract, database, or Helm changes.
- Security: `noopener` prevents the opened tab from accessing `window.opener`.
