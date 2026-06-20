# Triton Control Helm Chart

This chart runs:

- one combined `app` image containing frontend and backend
- one optional PostgreSQL Deployment as the only extra image
- one optional, globally shared Argo Workflows installation
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

The chart pins its optional `argo-workflows` dependency in `Chart.lock`. When
changing dependency versions, refresh the lock and packaged dependency with:

```bash
helm dependency update charts/triton-control
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

## Optional Argo Workflows

The chart includes the official Argo Workflows chart as an optional dependency:

- chart version: `1.0.16`
- Argo Workflows version: `v4.0.6`
- disabled by default
- one global controller and Argo Server per Triton Control Helm release
- Argo components and Workflow pods restricted to the Triton Control release namespace

Enable it with:

```yaml
argoWorkflows:
  enabled: true
```

The chart sets `argoWorkflows.fullnameOverride=argo-workflows` so every
generated Kubernetes resource name is lowercase and RFC-1123 compatible. Keep
this override when supplying environment-specific values.

The default integration pulls Argo system images directly from public
registries:

```text
quay.io/argoproj/workflow-controller
quay.io/argoproj/argocli
quay.io/argoproj/argoexec
registry.k8s.io/kubectl
```

No image pull Secret is configured for these system images. The Argo Server is
an internal `ClusterIP` service on port `2746`, uses HTTP internally, and is
configured with this base path for the authenticated Triton Control proxy and
embedded **Workflows** page:

```text
/api/workflows/proxy/
```

The integration uses Argo's single-namespace mode:

```yaml
argoWorkflows:
  enabled: true
  singleNamespace: true
```

Argo Server, controller, `argo-service-account`, workflow RBAC, and Workflow
pods are created in the namespace selected by:

```bash
helm upgrade --install ... --namespace <namespace>
```

Controller workflow defaults enforce the ServiceAccount and configured
non-root/container security contexts.

Workflow pods explicitly mount the `argo-service-account` token. The Argo
executor requires this token to create and patch `workflowtaskresults`; setting
`automountServiceAccountToken: false` on these pods would prevent workflows
from reporting completion. The workflow ServiceAccount is therefore limited by
the namespace-scoped workflow Role instead of disabling its token.

Optional aggregate ClusterRoles and ClusterWorkflowTemplates are disabled.
Argo CRDs remain cluster-scoped because Kubernetes custom resource definitions
cannot be namespace-scoped.

### User Workflow Images

The public Argo system image configuration does not grant access to private
images referenced by user-submitted Workflow YAML.

For private Workflow images, a `kubernetes.io/dockerconfigjson` Secret must
exist in the Triton Control release namespace, and the Workflow must reference
it through `spec.imagePullSecrets`. A future controlled Triton Control upload
flow can create a temporary per-workflow Secret and inject its server-generated
name. Do not place registry credentials directly in Workflow YAML.

### Existing Argo Installation

Keep `argoWorkflows.enabled=false` when Argo Workflows is managed by a separate
Helm or GitOps release. The future Triton Control proxy/configuration layer
should support connecting to that existing internal Argo Server as a separate
operating mode.
