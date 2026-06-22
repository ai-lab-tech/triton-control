# Architecture Overview

## Scope

Triton Control is a management platform for NVIDIA Triton Inference Server
instances. It includes a browser-based UI, a FastAPI backend, persistence,
optional OIDC identity integration, and S3-compatible model storage.

## System Context (C4 Level 1)

```mermaid
graph LR
  admin[Triton Control Admin]
  platform[Triton Control]
  triton[Triton Inference Server]
  oidcProvider[OIDC Provider]

  admin -->|Manages instances, users, models| platform
  platform -->|Monitors health, proxies inference, model lifecycle| triton
  platform -->|OIDC authentication optional| oidcProvider
```

## Containers (C4 Level 2)

```mermaid
graph LR
  admin[Triton Control Admin]
  spa[Triton Control UI\nAngular + NgRx]
  api[Triton Backend API\nFastAPI]
  appdb[(PostgreSQL Backend)]
  s3store[(S3-Compatible Object Store)]
  oidcProvider[OIDC Provider]
  triton[Triton Inference Server]
  vllmSync[vLLM Repository Sync\nInit container or sidecar]

  admin -->|HTTPS| spa
  spa -->|REST JSON| api

  api -->|SQL TLS| appdb
  api -->|S3 API HTTPS| s3store
  api -->|OIDC exchange| oidcProvider
  api -->|HTTP REST /v2| triton

  triton -->|Native model repository, non-vLLM| s3store
  s3store -->|Differential download, vLLM only| vllmSync
  vllmSync -->|Stable local /models volume| triton
```

## Runtime Responsibilities

- Triton Control UI: user workflows for instances, users, models, and S3 browsing.
- Backend API: auth (local + OIDC BFF), Triton proxy APIs, health polling,
  and storage operations.
- PostgreSQL: users, roles, instance config, OIDC settings, and health state.
- S3-compatible object store: model repository files consumed by Triton and
  managed via backend.
- Repository access: non-vLLM deployments retain Triton's native S3 repository.
  vLLM deployments use an explicitly selected init container or sidecar because
  local paths in `model.json` must resolve against a stable absolute filesystem
  path.
- OIDC provider: optional external identity provider.
