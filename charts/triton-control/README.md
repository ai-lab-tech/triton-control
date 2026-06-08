# Triton Control Helm Chart

This chart runs:

- one combined `app` image containing frontend and backend
- one optional PostgreSQL Deployment as the only extra image
- one Service for the app ports
- one Ingress resource for external routing

The combined app image must expose:

- frontend HTTP on `app.ports.frontend`, default `8080`
- backend API on `app.ports.backend`, default `8000`

Ingress itself is a Kubernetes resource. The ingress controller Pod, for example nginx-ingress, must already exist in the cluster.

## Install

Build prerequisite: Docker or another compatible image builder. Host Node.js,
npm, and Java are not required for the chart image build; the Dockerfile
installs the frontend build tools inside the Node build stage and regenerates
the Swagger/OpenAPI client before building Angular.

```bash
helm upgrade --install triton-control ./charts/triton-control \
  --namespace triton-control \
  --create-namespace \
  -f values-prod.yaml
```

## Local Compose

The repository also includes compose files that mirror the chart defaults:

- combined `triton-control` app image built from the root `Dockerfile`
- `postgres:16-alpine`
- frontend exposed on `http://localhost:8080`
- backend exposed on `http://localhost:8000`
- Postgres exposed on `127.0.0.1:5433`

Docker Compose:

```bash
docker compose up --build
```

Podman Compose:

```bash
podman-compose -f podman-compose.yaml up --build
```

Both compose files use the same app database URL as the chart pattern, with the
database host set to the compose service name:

```text
postgresql://triton:tritonpw@postgres:5432/triton_backend
```

Replace the default `SESSION_SECRET`, `JWT_SECRET`, `S3_SECRET_ENCRYPTION_KEY`,
and `POSTGRES_PASSWORD` values before using compose outside local development.

Backend logging is quiet by default. Set `BACKEND_VERBOSE=true` in `app.env`
for info-level backend logs and Uvicorn access logs. Set `DATABASE_ECHO=1`
only when SQL statement logging is needed.

## Minimal Values Override

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
  className: nginx
  proxyBodySize: 256m
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

If `postgresql.enabled` is `true`, the chart injects `DATABASE_URL` into the app from the generated PostgreSQL Secret. If you use an external database, set `postgresql.enabled=false` and provide `DATABASE_URL` through `app.existingSecret` or `app.env`.

For larger file uploads through nginx ingress, set `ingress.proxyBodySize` (for example `256m` or `1g`).

## RBAC Scope

By default, the chart creates namespace-scoped RBAC (least privilege):

- `serviceAccount.create=true`
- `rbac.create=true`
- `rbac.clusterWide=false`
- `rbac.manageNamespaces=false`

Enable cluster-wide RBAC only when needed:

```yaml
rbac:
  create: true
  clusterWide: true
  manageNamespaces: true
```

Use `manageNamespaces=true` only if Triton Control must create/delete namespaces.
Recommended security posture: keep `rbac.clusterWide=false` and
`rbac.manageNamespaces=false` unless a reviewed operational requirement
explicitly needs broader permissions.
