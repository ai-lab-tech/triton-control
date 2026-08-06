# Triton Control – Web UI for NVIDIA Triton Inference Server

[![Release](https://img.shields.io/github/v/release/ai-lab-tech/triton-control)](https://github.com/ai-lab-tech/triton-control/releases/latest)
[![Backend CI](https://github.com/ai-lab-tech/triton-control/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/ai-lab-tech/triton-control/actions/workflows/backend-ci.yml)
[![Frontend CI](https://github.com/ai-lab-tech/triton-control/actions/workflows/frontend-ci.yml/badge.svg)](https://github.com/ai-lab-tech/triton-control/actions/workflows/frontend-ci.yml)
[![Docs](https://github.com/ai-lab-tech/triton-control/actions/workflows/docs-pages.yml/badge.svg)](https://ai-lab-tech.github.io/triton-control/)
[![License](https://img.shields.io/github/license/ai-lab-tech/triton-control)](LICENSE)

**Operate NVIDIA Triton on Kubernetes from one open-source control plane.**

Triton Control brings deployment, model repositories, inference testing,
performance analysis, development workspaces, MLflow, and Argo Workflows into
one open-source UI for NVIDIA Triton on Kubernetes.

[Documentation](https://ai-lab-tech.github.io/triton-control/) ·
[Quickstart](https://ai-lab-tech.github.io/triton-control/getting-started/) ·
[Examples](examples) ·
[Roadmap](https://ai-lab-tech.github.io/triton-control/roadmap/)

<p>
  <img src="assets/readme/triton-control-demo.gif" alt="Triton Control Add Deployment demo showing form entry, S3 repository settings, Deploy click, pod startup, ready model, and inference metrics" />
</p>

The primary deployment target is Kubernetes through the Helm chart in
`charts/triton-control`. The same application can also run with Docker Compose
or Podman Compose for local evaluation, with reduced Kubernetes-specific
functionality such as no self-deployed Triton deployment workflows.

Published container image:
[`ailabtechtriton/triton-control:v1.2.3`](https://hub.docker.com/r/ailabtechtriton/triton-control)

## Why Triton Control?

NVIDIA Triton is a powerful inference server, but the workflow around it is
often fragmented. Teams coordinate model repositories, S3 credentials,
Kubernetes resources, model configuration, endpoint tests, performance runs,
development environments, and access control across separate tools. Triton
Control connects those steps without replacing Triton or hiding the underlying
model engineering.

Core capabilities include:

- existing Triton instance registration and management
- self-deployed Triton serving workflows when Triton Control runs in Kubernetes
- per-user browser-based development workspaces backed by code-server
- user management and instance access control
- model inference workflows with model configuration inspection
- S3-backed model repository integration with reusable S3 profiles and an
  integrated S3 Browser
- Perf Analyzer workflows when Triton Control runs in Kubernetes
- Kubernetes-managed MLflow tracking with persistent storage and an embedded,
  authenticated MLflow UI
- embedded Argo Workflows UI and API through an authenticated backend proxy

## Run With Docker Compose

With Docker installed, start the published image:

```bash
docker pull ailabtechtriton/triton-control:v1.2.3
docker tag ailabtechtriton/triton-control:v1.2.3 triton-control:compose
docker compose up --no-build
```

After the stack starts, open `http://localhost:8080` in your browser.

To build from source instead:

```bash
docker compose up --build
```

The backend API is available at `http://localhost:8000` and PostgreSQL at
`127.0.0.1:5433`.

Docker Compose does not provide Kubernetes. Deployments, Development
workspaces, managed MLflow, and Argo Workflows are therefore disabled.

For Triton running on the host, use
`http://host.docker.internal:<published-http-port>` instead of `127.0.0.1`.
Before a non-development deployment, replace the secrets and database password
defined in `compose.yaml`.

## Run With Podman Compose

With Podman and `podman-compose` installed, start the published image:

```bash
podman pull docker.io/ailabtechtriton/triton-control:v1.2.3
podman tag docker.io/ailabtechtriton/triton-control:v1.2.3 \
  localhost/triton-control:compose
podman-compose -f podman-compose.yaml up --no-build
```

After the stack starts, open `http://localhost:8080` in your browser.

To build from source instead:

```bash
podman-compose -f podman-compose.yaml up --build
```

The backend API is available at `http://localhost:8000` and PostgreSQL at
`127.0.0.1:5433`.

Podman Compose does not provide Kubernetes. Deployments, Development
workspaces, managed MLflow, and Argo Workflows are therefore disabled.

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

The Helm deployment can use the published image directly; users do not need to
build, pull, or push it on the machine running Helm. Kubernetes pulls the image
from Docker Hub when it creates the application pod.

Create a values file, for example `values-prod.yaml`:

```yaml
app:
  image:
    repository: ailabtechtriton/triton-control
    tag: "v1.2.3"
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

## Screenshots

<p>
  <img src="assets/readme/triton-control-dashboard-hero.png" alt="Triton Control dashboard showing healthy Triton instances" />
</p>

<p>
  <img src="assets/readme/triton-control-instances-list.png" alt="Triton Control instances page showing three healthy Triton deployments" />
</p>

<p>
  <img src="assets/readme/triton-control-instance-qwen3-models.png" alt="Triton Control Qwen3 instance page showing the deployed qwen3_4b_instruct model as ready" />
</p>

## Repository Layout

- `triton-frontend/` - Angular Material frontend.
- `triton-backend/` - Python FastAPI backend.
- `charts/triton-control/` - Helm chart for Kubernetes deployment.
- `compose.yaml` - Docker Compose stack for Triton Control and PostgreSQL.
- `podman-compose.yaml` - Podman Compose equivalent of the Docker Compose stack.
- `Dockerfile` - Builds a combined runtime image with frontend, backend, and Nginx.

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
