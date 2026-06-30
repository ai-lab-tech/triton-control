## Why

Triton Control now spans frontend workflows, backend APIs, persistence, S3 storage, Triton integration, Kubernetes-managed workloads, and code-server tooling. A repo-wide baseline gives future changes a stable shared map before feature-specific design decisions are made.

## What Changes

- Document the current Triton Control system baseline across frontend, backend, storage, deployment, authentication, Kubernetes add-ons, tests, and docs.
- Trace major runtime flows that future changes depend on: instance/model lifecycle, S3 model repository operations, self-deployment, Development workspaces, and code-server deployment.
- Identify ownership boundaries that new features should not cross without an explicit design decision.
- Add a baseline task list that can be completed before implementing dependent feature changes.

## Capabilities

### New Capabilities

- `repo-baseline`: Defines the expected repo-wide baseline documentation and traceability for Triton Control architecture, workflows, boundaries, and verification surfaces.

### Modified Capabilities

- None.

## Impact

- Affected docs/planning: OpenSpec baseline artifacts and likely follow-up documentation under `docs/`.
- Affected code: none expected for the baseline itself.
- Affected verification: documentation review plus existing backend/frontend checks when baseline claims are validated against code.
