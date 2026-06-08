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

  admin -->|HTTPS| spa
  spa -->|REST JSON| api

  api -->|SQL TLS| appdb
  api -->|S3 API HTTPS| s3store
  api -->|OIDC exchange| oidcProvider
  api -->|HTTP REST /v2| triton

  triton -->|Model repository| s3store
```

## Runtime Responsibilities

- Triton Control UI: user workflows for instances, users, models, and S3 browsing.
- Backend API: auth (local + OIDC BFF), Triton proxy APIs, health polling,
  and storage operations.
- PostgreSQL: users, roles, instance config, OIDC settings, and health state.
- S3-compatible object store: model repository files consumed by Triton and
  managed via backend.
- OIDC provider: optional external identity provider.
