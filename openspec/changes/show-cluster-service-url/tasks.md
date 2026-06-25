## 1. Backend: expose service URL

- [ ] 1.1 Add `service_url: str = ""` to `ArgoWorkflowsStatusResponse` in `app/schemas/workflows.py`.
- [ ] 1.2 Populate `service_url=config.server_url` in every `ArgoWorkflowsStatusResponse(...)` return in `app/services/workflows/status.py`.
- [ ] 1.3 Add `service_url: str = ""` to `MlflowStatusResponse` in `app/schemas/mlflow.py`.
- [ ] 1.4 Populate `service_url` in `get_mlflow_status` (`app/services/mlflow/installer.py`) via `kubernetes.service_url(entity.namespace, entity.service_name)` when installed; leave empty otherwise.
- [ ] 1.5 Update/extend backend tests (`tests/test_workflows.py`, `tests/test_mlflow.py`) to assert `service_url` is present and correct.

## 2. Frontend: regenerate client

- [ ] 2.1 Run `npm run generate:api` and confirm `service_url` appears on the generated status models / inline types.

## 3. Frontend: MLflow toolbar

- [ ] 3.1 Surface `service_url` from the status response in `mlflow-page.component.ts` (signal/getter) and a `copyServiceUrl()` handler using the Clipboard API with a safe fallback.
- [ ] 3.2 Add a read-only service-URL chip with a copy button to `workspace-toolbar-actions` in `mlflow-page.component.html`, beside "Open in new tab", shown only when `service_url` is non-empty.

## 4. Frontend: Workflows toolbar

- [ ] 4.1 Surface `service_url` from the status response in `workflows-page.component.ts` and add a `copyServiceUrl()` handler.
- [ ] 4.2 Add the read-only service-URL chip with copy button to the toolbar in `workflows-page.component.html`, beside "Open in new tab", shown only when `service_url` is non-empty.

## 5. Verification

- [ ] 5.1 Update/extend the MLflow and Workflows component spec files to cover the displayed URL and copy action.
- [ ] 5.2 Run backend tests (`pytest`) and frontend `npm run lint` + `npm test`; ensure all pass.
- [ ] 5.3 Manually verify the displayed URL is the `…svc.cluster.local:<port>` endpoint and copies correctly.
