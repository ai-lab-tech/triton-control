---
hide:
  - toc
---

# Triton Control Documentation

Triton Control is a web application for managing and operating NVIDIA Triton
Inference Server environments.
The primary deployment target is Kubernetes through the Helm chart in
`charts/triton-control`.

Core capabilities:

- existing Triton instance registration and management
- self-deployed Triton serving workflows when Triton Control runs in Kubernetes
- per-user browser-based Development workspaces backed by code-server
- user management and instance access control
- model inference workflows with model configuration inspection
- S3-backed model repository integration with an integrated S3 Browser
- Perf Analyzer workflows when Triton Control runs in Kubernetes
- Kubernetes-managed MLflow tracking with persistent storage and an embedded,
  authenticated MLflow UI
- embedded Argo Workflows UI and API through an authenticated backend proxy

The same application can also run with Docker Compose or Podman Compose for
local evaluation, and with separate Python/npm processes for development.

## What This Documentation Covers

- [Getting Started](getting-started.md): Kubernetes-first setup plus Docker, Podman, and local development paths.

Product:

- [User Guide](user-guide.md): user workflows for dashboard, instances, inference, profile, S3, Add Deployment, Perf Analyzer, MLflow, and Add Instance.
- [User Management](user-management.md): roles, local users, OIDC users, approvals, and instance assignment.
- [Development Workspaces](development-workspaces.md): the Kubernetes-backed, browser-based code-server workspace and its Triton deployment extension.
- [Argo Workflows](argo-workflows.md): installation, authenticated proxy, Kubernetes layout, pod security, RBAC, and Workflow credentials.

Architecture:

- [Architecture Overview](architecture-overview.md): high-level architecture, runtime boundaries, and core system interactions.
- [Architecture Backend Components](architecture-backend-components.md): backend service components and responsibilities.
- [Model Config Validation](model-config-validation.md): model repository validation behavior and configuration rules.

Operations:

- [Configuration](configuration.md): backend environment variables, OIDC source modes, and GitOps examples.
- [Deployment](deployment.md): Kubernetes prerequisites, Helm deployment, Docker Compose, and Podman Compose.
- [Security](security.md): secrets, OIDC, TLS, S3 credentials, and proxy headers.
- [Troubleshooting](troubleshooting.md): common Docker, database, and Triton issues.

Reference:

- [API](api.md): OpenAPI, Swagger, ReDoc, and generated frontend client notes.

Project:

- [Roadmap](roadmap.md): planned product changes for upcoming versions.
- [Local Project Development](development.md): local Python/npm workflow, tests, and CI checks for Triton Control itself.

## Main Components

- `triton-frontend/`: Angular Material frontend.
- `triton-backend/`: Python FastAPI backend.
- `charts/triton-control/`: Helm chart.
- `Dockerfile`: combined frontend/backend/Nginx image.
- `compose.yaml`: Docker Compose stack.
- `podman-compose.yaml`: Podman Compose stack.
