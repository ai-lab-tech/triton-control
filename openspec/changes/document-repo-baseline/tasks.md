## 1. Source Review

- [x] 1.1 Review architecture, user, deployment, development-workspace, security, and troubleshooting docs for current system claims.
- [x] 1.2 Inventory frontend routes, pages, generated API client usage, NgRx feature state, and e2e coverage.
- [x] 1.3 Inventory backend routers, schemas, services, repositories, persistence entities, and tests.
- [x] 1.4 Inventory Kubernetes-managed features including self-deployed Triton, Development workspaces, Perf Analyzer, MLflow, and Workflows.

## 2. Runtime Flow Traces

- [x] 2.1 Trace auth, role, instance assignment, and proxy access boundaries.
- [x] 2.2 Trace Triton instance and model lifecycle from registration/deployment through repository index, config inspection, load/unload, inference, and profiling.
- [x] 2.3 Trace S3 model repository browse/read/write/delete behavior and `config.pbtxt` validation.
- [x] 2.4 Trace self-deployment from API request through Kubernetes resources, repository sync behavior, and instance record creation.
- [x] 2.5 Trace Development workspace lifecycle from frontend request through Kubernetes resources, code-server proxying, bundled extension installation, S3 upload, deployment creation, and navigation handoff.

## 3. Baseline Documentation

- [x] 3.1 Create or update baseline documentation with subsystem ownership boundaries.
- [x] 3.2 Add runtime flow summaries with file path references for each traced flow.
- [x] 3.3 Document boundaries future changes should preserve unless explicitly changed.
- [x] 3.4 Document verification surfaces, including backend tests, frontend tests, e2e tests, OpenAPI generation, and relevant docs.

## 4. Verification

- [x] 4.1 Cross-check baseline statements against current source files and docs.
- [x] 4.2 Resolve or record discrepancies between docs and code.
- [x] 4.3 Run OpenSpec validation for this baseline change.
