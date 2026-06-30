## ADDED Requirements

### Requirement: Repo-wide subsystem baseline
The system SHALL maintain a baseline description of Triton Control's major subsystems and their ownership boundaries.

#### Scenario: Baseline covers major subsystems
- **WHEN** a reader uses the baseline to understand the repo
- **THEN** it identifies frontend, backend API, services, repositories, persistence, S3 storage, Triton integration, Kubernetes deployments, Development workspaces, operational add-ons, auth/OIDC, docs, and tests

### Requirement: Runtime flow traceability
The baseline SHALL trace core runtime flows across subsystem boundaries.

#### Scenario: Baseline traces model lifecycle
- **WHEN** a reader follows the model lifecycle baseline
- **THEN** it explains how model repository files, S3 storage, Triton instance visibility, model config inspection, load/unload, inference, and profiling relate

#### Scenario: Baseline traces Development workflow
- **WHEN** a reader follows the Development workspace baseline
- **THEN** it explains how the UI, backend API, Kubernetes resources, code-server proxying, bundled extension installation, and deployment-created navigation relate

#### Scenario: Baseline traces deployment workflow
- **WHEN** a reader follows the self-deployment baseline
- **THEN** it explains how deployment requests, S3 settings, Kubernetes resources, Triton instance records, and repository sync behavior relate

### Requirement: Boundary preservation guidance
The baseline SHALL identify boundaries that feature changes MUST preserve unless explicitly changed by that feature's design.

#### Scenario: Feature references baseline boundaries
- **WHEN** a later feature proposes changes near existing APIs, storage, auth, deployment, or validation paths
- **THEN** the feature design can reference the baseline and state which boundaries are preserved or intentionally changed

### Requirement: Verification surface inventory
The baseline SHALL identify the docs and tests that define expected behavior for the mapped subsystems.

#### Scenario: Baseline identifies verification surfaces
- **WHEN** a future implementer plans a related change
- **THEN** the baseline points to relevant backend tests, frontend tests, e2e tests, generated API workflows, and documentation areas to check
