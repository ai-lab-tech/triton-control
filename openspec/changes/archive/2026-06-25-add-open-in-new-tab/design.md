## Context

MLflow, Argo Workflows, and the Development code-server workspace are surfaced in
Triton Control through `<iframe>` elements that point at same-origin backend
**proxy** paths (e.g. `/api/mlflow/proxy/`, the Argo `base_path`, and
`proxyUrl(workspace.url)`). The backend acts as an authenticated reverse proxy
(BFF) and the browser session cookie authorizes those requests. Each page builds
a URL string and wraps it with `DomSanitizer.bypassSecurityTrustResourceUrl(...)`
to bind it to the iframe `[src]`.

Today there is no way to open these tools in a standalone browser tab; they are
constrained to the embedded frame within the single Triton Control tab.

## Goals / Non-Goals

**Goals:**
- Add an "Open in new tab" toolbar action to the MLflow, Workflows, and
  Development views.
- Reuse the existing same-origin proxy URL so the current session authorizes the
  request with no extra sign-in or ingress.
- Keep the change frontend-only and minimal.

**Non-Goals:**
- Exposing or linking to the tools' real (non-proxied) ingress URLs.
- Any backend, API contract, Helm, or auth changes.
- Changing how the embedded iframe itself is loaded or refreshed.

## Decisions

- **Open the backend proxy URL, not the real tool ingress.** The proxy URL is
  same-origin and cookie-authenticated, so `window.open` inherits the session
  with zero backend work. Linking the real ingress would require separate auth,
  TLS, and ingress exposure. _Alternative considered:_ deep-link to the tool's
  own origin — rejected for added auth/ingress complexity and inconsistent
  availability.

- **Use `window.open(url, "_blank", "noopener")`.** `noopener` severs the
  `window.opener` reference, preventing reverse-tabnabbing from the opened
  document. _Alternative considered:_ an `<a target="_blank" rel="noopener">`
  link — viable, but a button-triggered `window.open` keeps parity with the
  existing imperative toolbar actions and the ready-state gating logic.

- **Expose a raw URL string alongside the existing `SafeResourceUrl`.** The
  iframe `[src]` signals hold sanitized `SafeResourceUrl` objects that cannot be
  passed to `window.open`. MLflow and Workflows build their URL inline inside the
  frame-open path; they will additionally store the plain string in a sibling
  signal (e.g. `frameRawUrl`). The Development page already has a `proxyUrl()`
  helper returning a string, so it can derive the raw URL directly from the
  selected workspace. _Alternative considered:_ "unwrapping" the SafeResourceUrl
  — not supported by Angular's API and fragile.

- **Gate the action on the same readiness signal as the iframe.** Each view only
  shows/enables the button when its frame is ready (`current.ready && frameUrl()`
  for MLflow, `frameUrl()` for Workflows, `embeddedWorkspaceUrl()` /
  `workspace.status === "ready"` for Development), so the action never points at a
  missing target.

- **Place the action first in the toolbar group.** Ordering it before Refresh and
  the destructive Uninstall/Delete keeps the destructive control at the far edge,
  reducing misclick risk.

## Risks / Trade-offs

- **Popup blockers** → The action is a direct, user-initiated click handler, so
  browsers treat it as a user gesture and allow the popup.
- **Reverse tabnabbing** → Mitigated by passing `noopener` to `window.open`.
- **Proxy URL drift between iframe and button** → Mitigated by deriving the raw
  URL from the same source the iframe uses (shared signal / `proxyUrl()` helper),
  rather than recomputing it independently.
- **Reload-nonce query param** (`_tc_reload=`) is appended for the iframe to force
  reloads; the new-tab URL can omit it since a fresh tab loads fresh. Keeping or
  dropping it is cosmetic and does not affect auth.
