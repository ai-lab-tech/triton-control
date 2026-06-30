# Repo Baseline

This baseline describes the current Triton Control repository before feature
changes are layered on top. Use it to orient new OpenSpec changes and to decide
which existing boundaries a change must preserve or explicitly modify.

## System Scope

Triton Control manages NVIDIA Triton Inference Server environments. The repo
contains:

- `triton-frontend/`: Angular UI, routed pages, generated OpenAPI clients, NgRx
  state, guards, shared auth, and frontend tests.
- `triton-backend/`: FastAPI backend, SQLModel persistence, service and
  repository layers, Triton/S3/Kubernetes integrations, OpenAPI export, and
  backend tests.
- `code-server-extensions/triton-deploy/`: bundled code-server extension used
  from managed Development workspaces to upload model repositories and create
  Triton deployments.
- `charts/triton-control/`: Helm chart for Kubernetes deployment.
- `docs/`: product, architecture, security, deployment, operations, and project
  development documentation.

Existing high-level architecture is documented in
[Architecture Overview](architecture-overview.md) and
[Backend Components](architecture-backend-components.md).

## Subsystem Ownership

### Frontend

Routing is defined in `triton-frontend/src/app/app.routes.ts`. Authenticated
routes are wrapped by `authGuard`; admin-only routes use `adminGuard`.

Main page surfaces:

- Dashboard: `pages/dashboard/`
- Instances list/detail: `pages/instances/`
- Model inference/profile: `pages/instances/infer/`,
  `pages/instances/profile/`
- S3 browser/editor: `pages/instances/s3/`
- Development workspace: `pages/development/`
- Add Deployment: `pages/deployments/`
- Perf Analyzer: `pages/perf-analyzers/`
- MLflow: `pages/mlflow/`
- Workflows: `pages/workflows/`
- S3 Profiles: `pages/s3-profiles/`
- Users, Settings, Error Logs: admin pages under `pages/users/`,
  `pages/settings/`, and `pages/error-logs/`

Generated API clients live under `triton-frontend/src/app/api/generated/`.
API shape changes require backend OpenAPI export and frontend client
regeneration, as described in [API](api.md).

Feature state is under `triton-frontend/src/app/state/`. Adjacent feature
state for Triton model workflows includes `instances-list`, `instances-detail`,
`instances-s3`, `instances-infer`, `instances-profile`, `dashboard`, `users`,
`settings`, and `login`.

### Backend

Backend routers live under `triton-backend/app/api/`:

- `auth_api.py`, `oidc_api.py`, `user_api.py`: local auth, OIDC BFF, user and
  assignment management.
- `instance_api.py`, `model_api.py`: Triton instance registration, repository
  index, model config, load/unload, and inference proxy paths.
- `s3_api.py`, `s3_profile_api.py`: S3 model repository file operations and
  reusable S3 profiles.
- `deployment_api.py`: self-deployed Triton workload creation/deletion.
- `development_api.py`: managed code-server workspace CRUD, proxying, and
  deployment navigation handoff.
- `perf_analyzer_api.py`, `mlflow_api.py`, `workflows_api.py`: Kubernetes-backed
  operational add-ons and authenticated proxies.
- `dashboard_api.py`, `error_log_api.py`: fleet health views and error events.

Business logic lives under `triton-backend/app/services/`. Database access
lives under `triton-backend/app/repositories/`. Request/response contracts live
under `triton-backend/app/schemas/`.

### Persistence

SQLModel entities are defined in `triton-backend/app/db/entities.py`. Current
tables include:

- Triton instances and deployment metadata: `TritonInstanceEntity`
- Users, roles, local/OIDC auth metadata, instance assignments: `UserEntity`
- OIDC settings: `OidcConfigEntity`
- Development workspaces: `CodeServerEntity`
- S3 profiles: `S3ProfileEntity`
- Workflow S3 credentials: `WorkflowS3CredentialEntity`
- Perf Analyzer and latest run results: `PerfAnalyzerEntity`,
  `PerfAnalyzerRunEntity`
- MLflow installation: `MlflowEntity`
- Dashboard alerts and error events: `DashboardAlertEntity`,
  `ErrorEventEntity`

### External Systems

- Triton REST `/v2` APIs are accessed through `app/services/triton/`.
- S3-compatible object stores are accessed through `app/services/storage/`.
- Kubernetes resources are created through deployment, development,
  perf-analyzer, MLflow, and workflow service modules.
- Optional OIDC providers are accessed through `app/services/oidc/`.

## Runtime Flows

### Auth and Access

Local auth, bootstrap, self-registration, and OIDC settings are handled by
`app/api/auth_api.py` and `app/services/auth/`. Browser-facing OIDC login and
callback behavior is handled by `app/api/oidc_api.py` and
`app/services/oidc/`.

Access control is role and assignment based. Frontend guards protect routes;
backend dependencies and service guards protect APIs and proxies. Shared
instance-access logic is in `app/services/access.py`.

### Triton Instance and Model Lifecycle

Users create or update Triton instance connections through the instances UI and
`app/api/instance_api.py`. Backend services in `app/services/triton/` read live
health, server metadata, repository index, model configs, and metrics.

Model workflows then branch:

```text
Instance record
  -> live Triton /v2 metadata and repository index
  -> model config inspection
  -> optional S3 repository file editing
  -> load/unload actions
  -> inference page
  -> optional Perf Analyzer profile page
```

Relevant frontend surfaces are `pages/instances/detail/`,
`pages/instances/infer/`, `pages/instances/profile/`, and
`pages/instances/s3/`.

### S3 Model Repository Operations

S3 browser and editor requests are routed through `app/api/s3_api.py` and
`app/services/storage/s3.py`.

Supported operations are:

- read/update instance S3 connection settings
- list repository prefixes
- read text and raw object content
- write object content
- delete object content

Editable `config.pbtxt` writes are validated before upload. Validation is owned
by `app/services/triton/config.py`, which maps Triton server versions to
checked-in `model_config.proto` branches. See
[Model Config Validation](model-config-validation.md).

### Self-Deployed Triton

The Add Deployment page calls `app/api/deployment_api.py`. The deployment
service creates Kubernetes resources using `app/services/deployment/`, records
the resulting Triton instance, and stores S3 repository connection details for
the deployed workload.

Non-vLLM deployments use Triton's native S3 repository support. vLLM
deployments use repository sync behavior so local paths inside `model.json`
resolve against a stable filesystem path.

### Development Workspaces and Code-Server Deploy

The Development page calls `app/api/development_api.py`. Workspace use cases in
`app/services/development/workspaces.py` create per-user Kubernetes resources
through `app/services/development/kubernetes.py`.

The Kubernetes workspace includes:

- a StatefulSet and Service for code-server
- a persistent `/workspace` volume
- Secrets for auth mode and optional image pull credentials
- a ConfigMap containing the bundled `triton-control-deploy` extension

The frontend embeds code-server through:

```text
/api/development/<workspace-id>/proxy/?folder=/workspace
```

The bundled extension in `code-server-extensions/triton-deploy/` uploads a
selected Triton repository to S3 and can create a deployment by calling
`POST /api/deployments` from the current browser session. After successful
deployment, it posts a deployment-created message that the Development page uses
to navigate to the new instance.

See [Development Workspaces](development-workspaces.md).

### Operational Add-Ons

Perf Analyzer is managed by `app/api/perf_analyzer_api.py` and
`app/services/perf_analyzer/`. MLflow is managed by `app/api/mlflow_api.py` and
`app/services/mlflow/`. Argo Workflows status, proxying, and S3 credentials are
managed by `app/api/workflows_api.py` and `app/services/workflows/`.

These add-ons are Kubernetes-backed and have separate frontend pages. They
should not be coupled to Triton repository scaffolding unless a feature design
explicitly calls for it.

## Boundaries To Preserve

Future feature changes should preserve these boundaries unless their design
explicitly changes them:

- Frontend API contracts come from backend OpenAPI schemas and generated
  clients.
- Backend routers should stay thin; business logic belongs in services.
- Repository/database access should remain behind repository modules.
- Auth, role, assignment, and proxy checks must remain enforced server-side.
- S3 model repository writes must continue through backend storage services.
- `config.pbtxt` correctness remains owned by backend protobuf validation.
- Self-deployment creation remains owned by the deployment API/service.
- Development workspaces remain per-user and proxied through the backend.
- The code-server deploy extension should use existing deployment and S3
  profile APIs rather than creating parallel backend contracts.

## Verification Surfaces

Backend tests:

- `triton-backend/tests/test_auth.py`
- `triton-backend/tests/test_user_auth.py`
- `triton-backend/tests/test_user_api.py`
- `triton-backend/tests/test_oidc_bff.py`
- `triton-backend/tests/test_oidc_settings.py`
- `triton-backend/tests/test_triton_service.py`
- `triton-backend/tests/test_triton_service_edges.py`
- `triton-backend/tests/test_api_and_dashboard.py`
- `triton-backend/tests/test_deployments.py`
- `triton-backend/tests/test_development.py`
- `triton-backend/tests/test_perf_analyzers.py`
- `triton-backend/tests/test_mlflow.py`
- `triton-backend/tests/test_workflows.py`
- `triton-backend/tests/test_s3_profiles.py`

Frontend tests:

- Component and state specs colocated under `triton-frontend/src/app/`
- E2E smoke coverage in `triton-frontend/e2e/tests/smoke.spec.ts`
- Mock Triton server in `triton-frontend/e2e/mock-triton-server.mjs`

Generated API checks:

- Backend OpenAPI export: `triton-backend/scripts/export_openapi.py`
- Frontend generated client workflow: `triton-frontend/scripts/generate-api.mjs`
- API docs: [API](api.md)

Documentation surfaces:

- [Architecture Overview](architecture-overview.md)
- [Backend Components](architecture-backend-components.md)
- [User Guide](user-guide.md)
- [Development Workspaces](development-workspaces.md)
- [Deployment](deployment.md)
- [Security](security.md)
- [Troubleshooting](troubleshooting.md)
- [Local Project Development](development.md)
