## 1. MLflow view

- [x] 1.1 Add a raw proxy URL string signal (e.g. `frameRawUrl`) populated wherever the iframe `SafeResourceUrl` is built in `mlflow-page.component.ts`, and clear it when the frame is cleared.
- [x] 1.2 Add an `openInNewTab()` method that calls `window.open(rawUrl, "_blank", "noopener")` when the URL is present.
- [x] 1.3 Add an "Open in new tab" button to `workspace-toolbar-actions` in `mlflow-page.component.html`, placed before Refresh and Uninstall, gated on `current.ready && frameRawUrl()`.

## 2. Workflows (Argo) view

- [x] 2.1 Add a raw proxy URL string signal alongside the existing iframe `SafeResourceUrl` in `workflows-page.component.ts`, kept in sync with the frame URL.
- [x] 2.2 Add an `openInNewTab()` method using `window.open(rawUrl, "_blank", "noopener")`.
- [x] 2.3 Add an "Open in new tab" button to the toolbar in `workflows-page.component.html`, gated on the same readiness condition as the iframe (`frameUrl()`), placed before Refresh.

## 3. Development (code-server) view

- [x] 3.1 Add an `openInNewTab(workspace)` method in `development-page.component.ts` that builds the URL via the existing `proxyUrl(workspace.url)` and calls `window.open(url, "_blank", "noopener")`.
- [x] 3.2 Add an "Open in new tab" button to `workspace-toolbar-actions` in `development-page.component.html`, placed before Refresh and Delete, gated on the workspace being ready/embedded.

## 4. Verification

- [x] 4.1 Update/extend the relevant component spec files to cover the new action (button present when ready, `window.open` called with `_blank`/`noopener`).
- [x] 4.2 Run `npm run lint` and `npm test` in `triton-frontend` and ensure they pass.
- [x] 4.3 Manually verify each view opens the proxied tool in a new tab with the session preserved.
