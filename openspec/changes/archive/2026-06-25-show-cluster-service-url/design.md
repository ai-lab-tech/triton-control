## Context

Code-server workspaces run as pods in the same cluster as MLflow and Argo
Workflows. To submit jobs (e.g. set `MLFLOW_TRACKING_URI`, run
`train_tiny_classifier.py --tracking-uri …`, or `argo submit --argo-server …`)
the user needs the tool's **in-cluster service URL**, not the browser proxy path
the iframe / "Open in new tab" action uses.

The backend already knows these URLs:
- Argo: `app/services/workflows/config.py` exposes `server_url`
  (`ARGO_WORKFLOWS_SERVER_URL`), already an in-cluster endpoint on port `2746`.
- MLflow: `app/services/mlflow/kubernetes.py` has
  `service_url(namespace, service_name)` → `http://…svc.cluster.local:5000`.

The status responses (`ArgoWorkflowsStatusResponse`, `MlflowStatusResponse`)
are returned to the frontend but do not currently include a canonical service
URL.

## Goals / Non-Goals

**Goals:**
- Surface the in-cluster service URL for MLflow and Argo in their toolbars,
  beside "Open in new tab", as a copyable read-only value.
- Keep the backend as the source of truth for the URL (including port).

**Non-Goals:**
- Showing a service URL on the Development/code-server page (its own URL is not
  the job-submission target).
- Auto-injecting the URL into the workspace environment (future enhancement).
- Any change to authentication, the proxy, or how iframes load.

## Decisions

- **Backend exposes `service_url` on the status responses.** Add a
  `service_url` field to `ArgoWorkflowsStatusResponse` (populated from
  `config.server_url`) and to `MlflowStatusResponse` (populated from
  `kubernetes.service_url(namespace, service_name)` when installed). This keeps
  the port authoritative in the backend. _Alternative considered:_ construct the
  URL in the frontend from `service_name` + `namespace` — rejected because it
  would hardcode `5000`/`2746` in Angular and duplicate backend knowledge.

- **Reuse existing helpers.** Argo's `server_url` is already the in-cluster URL;
  MLflow already has a `service_url()` helper. No new URL-construction logic is
  introduced.

- **Scope to MLflow and Workflows pages.** These are the job-submission targets.
  The Development page is intentionally excluded.

- **Read-only + copy-to-clipboard control.** The value is meant to be pasted into
  scripts; a copy affordance avoids manual selection errors. _Alternative
  considered:_ plain static text — rejected as less ergonomic.

- **Empty `service_url` ⇒ no control.** When the tool is not installed/enabled or
  the URL is unset, the frontend renders nothing, matching existing readiness
  gating.

## Risks / Trade-offs

- **Frontend API client must be regenerated** after the schema change → run
  `npm run generate:api`; the new optional field is backward compatible.
- **Clipboard API availability** (requires secure context / HTTPS) → guard the
  copy handler and fail silently if the Clipboard API is unavailable.
- **Argo `server_url` could be a non-cluster URL if mis-set** in the Helm values
  → this is operator configuration; the field simply reflects the configured
  value, which is already what the backend uses to reach Argo.
