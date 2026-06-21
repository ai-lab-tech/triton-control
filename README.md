# Triton Control

Triton Control is a web application for managing and operating NVIDIA Triton
Inference Server environments. The primary deployment target is Kubernetes
through the Helm chart in `charts/triton-control`.
The same application can also run with Docker Compose or Podman Compose for
local evaluation (with reduced Kubernetes-specific functionality, for example
no self-deployed Triton deployment workflows), and with separate Python/npm
processes for development.

Documentation: https://ai-lab-tech.github.io/triton-control/

Core capabilities include:

- existing Triton instance registration and management
- self-deployed Triton serving workflows when Triton Control runs in Kubernetes
- per-user browser-based development workspaces backed by code-server
- user management and instance access control
- model inference workflows with model configuration inspection
- S3-backed model repository integration with an integrated S3 Browser
- Perf Analyzer workflows when Triton Control runs in Kubernetes
- embedded Argo Workflows UI and API through an authenticated backend proxy

## Repository Layout

- `triton-frontend/` - Angular Material frontend.
- `triton-backend/` - Python FastAPI backend.
- `charts/triton-control/` - Helm chart for Kubernetes deployment.
- `compose.yaml` - Docker Compose stack for Triton Control and PostgreSQL.
- `podman-compose.yaml` - Podman Compose equivalent of the Docker Compose stack.
- `Dockerfile` - Builds a combined runtime image with frontend, backend, and Nginx.

## Run With Docker Compose

Prerequisite: Docker Desktop or another Docker engine. Host Node.js, npm, and
Java are not required for the Docker image build; the Dockerfile installs the
frontend build tools inside the `node:22-alpine` build stage and regenerates the
Swagger/OpenAPI client before building Angular.

```bash
docker compose up --build
```

The Compose stack exposes:

- Frontend: `http://localhost:8080`
- Backend API: `http://localhost:8000`
- PostgreSQL: `127.0.0.1:5433`

The app container uses:

```text
postgresql://triton:tritonpw@postgres:5432/triton_backend
```

Backend logging is quiet by default. Set `BACKEND_VERBOSE=true` to enable
info-level backend logs and Uvicorn access logs. Set `DATABASE_ECHO=1` only
when SQL statement logging is needed.
Set `CLIENT_MAX_BODY_SIZE` (for example `256m` or `1g`) to allow larger file
uploads through the container nginx reverse proxy.

For local development this is ready to run. Before using Compose outside local
development, replace `SESSION_SECRET`, `JWT_SECRET`, `S3_SECRET_ENCRYPTION_KEY`,
and `POSTGRES_PASSWORD`.

If a Triton server is running in another Docker Compose project, `127.0.0.1`
inside Triton Control points to the Triton Control container itself. Use one of
these instead:

```text
http://host.docker.internal:<published-triton-http-port>
```

or attach the Triton container to the `triton-control` network and
use the Triton container name:

```bash
docker network connect triton-control tritonserver-explicit
```

```text
http://tritonserver-explicit:8000
```

For metrics, the backend automatically appends `/metrics` when the metrics URL
has no path.

TLS certificate note:

- With SSL verification enabled, Triton and S3 HTTPS endpoints are validated
  against the system trust store by default.
- A pasted CA certificate is optional and only needed for private,
  self-signed, or internal CA chains.

## Run With Podman Compose

Prerequisite: Podman and `podman-compose`.

```bash
podman-compose -f podman-compose.yaml up --build
```

The exposed URLs are the same as Docker Compose:

- Frontend: `http://localhost:8080`
- Backend API: `http://localhost:8000`
- PostgreSQL: `127.0.0.1:5433`

The Podman file uses fully qualified image names such as
`docker.io/library/postgres:16-alpine` and `localhost/triton-control:compose`.

## Run On Kubernetes

The Helm chart deploys:

- one combined app image with Nginx, Angular, and FastAPI
- one optional PostgreSQL Deployment
- one optional Argo Workflows installation
- one Service for frontend and backend ports
- optional Ingress routes

OIDC login has been tested with Microsoft Entra ID, Keycloak, and Dex. The
Kubernetes deployment has also been tested with Argo CD managing the Helm
release in a GitOps workflow.

Build and push the image to a registry first:

```bash
docker build -t registry.example.com/triton-control:0.1.0 .
docker push registry.example.com/triton-control:0.1.0
```

Only Docker is required on the build host for this image build. The build needs
network access to install npm packages and, if the Swagger generator jar is not
already cached in the build context, to download `swagger-codegen-cli.jar`.

Create a values file, for example `values-prod.yaml`:

```yaml
app:
  image:
    repository: registry.example.com/triton-control
    tag: "0.1.0"
  secretEnv:
    SESSION_SECRET: "replace-me"
    JWT_SECRET: "replace-me"
    S3_SECRET_ENCRYPTION_KEY: "replace-me"

postgresql:
  enabled: true
  auth:
    database: triton_backend
    username: triton
    password: "replace-me"
  persistence:
    enabled: true
    size: 20Gi

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: triton-control.example.com
      paths:
        frontend:
          - path: /
            pathType: Prefix
        backend:
          - path: /api
            pathType: Prefix
          - path: /auth
            pathType: Prefix
          - path: /login
            pathType: Prefix
          - path: /logout
            pathType: Prefix
          - path: /whoami
            pathType: Prefix
```

Install or upgrade:

```bash
helm upgrade --install triton-control ./charts/triton-control \
  --namespace triton-control \
  --create-namespace \
  -f values-prod.yaml
```

If `postgresql.enabled=true`, the chart generates and injects `DATABASE_URL`.
For an external database, set `postgresql.enabled=false` and provide
`DATABASE_URL` through `app.existingSecret`, `app.env`, or `app.envFrom`.

### Self-Deployed Triton And Perf Analyzer Namespace Behavior

When you use Triton Control to install a self-deployed Triton instance or
Perf Analyzer, namespace behavior depends on backend runtime context:

- Triton Control backend running in Kubernetes (in-cluster detection):
  self-deployed Triton and Perf Analyzer are created in the same namespace as
  the Triton Control pod.
- Triton Control backend running outside Kubernetes (for example local dev with
  `KUBERNETES_KUBECONFIG_PATH`):
  self-deployed Triton remains name-based, while Perf Analyzer defaults to the
  shared `triton-control` namespace.

In-cluster detection is automatic and uses Kubernetes runtime signals
(ServiceAccount files and Kubernetes service environment).

`KUBERNETES_KUBECONFIG_PATH` is intended as a local development/testing
override for Triton Control running outside Kubernetes. In-cluster production
deployments should use ServiceAccount-based in-cluster configuration.

## Run Locally With Python And npm

This mode is useful when working in VS Code or another IDE.

Local development prerequisites:

- Python `3.12`.
- Node.js and npm for the Angular frontend.
- Java, Bash, curl, and Python on the frontend host if you run
  `npm run generate:api`; that command downloads and runs
  `swagger-codegen-cli.jar`.

### 1. Start Backend PostgreSQL

The backend has a local PostgreSQL Compose file with TLS support:

```bash
cd triton-backend/postgresql
docker compose up -d
```

It exposes PostgreSQL on:

```text
127.0.0.1:5433
```

### 2. Configure Backend Environment

```bash
cd triton-backend
cp .env.example .env
```

On Windows PowerShell:

```powershell
cd triton-backend
Copy-Item .env.example .env
```

The default local database URL is:

```text
DATABASE_URL=postgresql://triton:tritonpw@localhost:5433/triton_backend
```

### 3. Install And Run Backend

macOS/Linux:

```bash
cd triton-backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python main.py
```

Windows PowerShell:

```powershell
cd triton-backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
python main.py
```

Backend API:

```text
https://localhost:8000
https://localhost:8000/docs
```

If `SERVER_HTTPS_ENABLED=false` in `.env`, use `http://127.0.0.1:8000`
instead.

### 4. Install And Run Frontend

Open a second terminal:

```bash
cd triton-frontend
npm ci
npm run generate:api
npm run start:http
```

Frontend:

```text
http://localhost:4200
```

The default frontend environment points API calls to:

```text
http://127.0.0.1:8000
```

HTTPS frontend mode is also available:

```bash
npm run start:https
```

Certificate paths are configured in `triton-frontend/angular.json`.

## VS Code Notes

The repository includes `.vscode/launch.json` with a generic Python current-file
debug configuration. For backend debugging, open `triton-backend/main.py` and
start the Python debugger from VS Code.

For frontend work, use the integrated terminal:

```bash
cd triton-frontend
npm run start:http
```

Run backend and frontend in separate terminals. The backend must be running for
most frontend API workflows.

## CI Checks

Backend:

```bash
docker run --rm -v "$PWD:/repo" -w /repo ghcr.io/gitleaks/gitleaks:latest detect --no-git --source . --redact --verbose
cd triton-backend
pip install -e ".[dev]"
pip install pip-audit
pip-audit
coverage run -m unittest discover -s tests -p "test_*.py" -v
coverage report --fail-under=75
mypy app/ tests/ scripts/ main.py
ruff check app/ main.py
lint-imports
bandit -r app/ main.py
```

Frontend:

```bash
cd triton-frontend
npm ci
npm audit --audit-level=moderate
npm run generate:api
npm run lint
npm run format:check
npm test -- --watch=false --browsers=ChromeHeadless --code-coverage
npm run test:smoke
```

## More Documentation

- Documentation site source: `docs/`
- Getting started: `docs/getting-started.md`
- User guide: `docs/user-guide.md`
- User management: `docs/user-management.md`
- API documentation: `docs/api.md`
- Architecture overview: `docs/architecture-overview.md`
- Architecture backend components: `docs/architecture-backend-components.md`
- Deployment: `docs/deployment.md`
- Development: `docs/development.md`
- Security: `docs/security.md`
- Troubleshooting: `docs/troubleshooting.md`
- Backend details: `triton-backend/README.md`
- Frontend details: `triton-frontend/README.md`
- Helm chart details: `charts/triton-control/README.md`
- Backend TLS setup: `triton-backend/tls/README.md`
