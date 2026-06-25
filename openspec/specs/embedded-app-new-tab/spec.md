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
