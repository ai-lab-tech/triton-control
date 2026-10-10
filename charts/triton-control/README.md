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
`EMAIL_SECRET_ENCRYPTION_KEY`, and `POSTGRES_PASSWORD` values before using
compose outside local development. Store `EMAIL_SMTP_PASSWORD` as a secret when
authenticated SMTP is enabled.

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

tritonDeployments:
  # Used only by vLLM init/sidecar repository modes. Standard Triton S3
  # deployments do not create this container.
  s3SyncImage: registry.example.com/amazon/aws-cli:2.22.35
  # Optional emptyDir size limits for vLLM local sync.
  modelRepositoryEmptyDirSize: 20Gi
  s3SyncStagingEmptyDirSize: 20Gi

mlflow:
  version: "3.14.0"
  image:
    repository: registry.example.com/mlflow/mlflow

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

## Development Workspace HTTPS Certificates

The built-in **Explorer → S3 Browser** loads saved S3 profiles dynamically.
Each request uses the selected profile's endpoint, credentials, and public CA
certificate (`ca_certificate`), alongside Node's trusted public roots. Save a
private CA in the S3 profile when its endpoint requires one. Switching profiles
or updating their certificates does not require restarting the workspace.

Workspaces do not mount a global S3 CA ConfigMap. The legacy `extraCaBundle`,
`extraCaConfigMap`, and `extraCaKey` Helm settings are no longer used. TLS
verification remains enabled.

Existing workspace StatefulSets are not updated by a Helm upgrade. Remove their
legacy `code-server-extra-ca` volume and volume mount, remove `NODE_EXTRA_CA_CERTS`,
and set `NODE_TLS_REJECT_UNAUTHORIZED` to `1` in the pod template to migrate them.

Standalone tools such as AWS CLI and third-party extensions have their own
configuration; they do not automatically receive Triton Control S3 profiles.
For AWS CLI, save the required public CA bundle to a workspace file and set
`AWS_CA_BUNDLE` to that file's path.

## Optional Email Lifecycle

The chart defaults to `EMAIL_CONFIG_SOURCE=env` and
`EMAIL_DELIVERY_MODE=disabled`. No mail server is deployed. Use `manual-link`
for administrator-mediated invitation and reset links, or `smtp` with an
external relay for automatic delivery and public forgot-password.

Both enabled delivery modes require an externally reachable
`EMAIL_PUBLIC_APP_URL`. SMTP transport, sender, expiry, timeout, rate-limit, and
template settings are available in `app.env`. Put `EMAIL_SMTP_PASSWORD` in
`app.secretEnv` or `app.existingSecret`. For `EMAIL_CONFIG_SOURCE=db`, also set
`EMAIL_SECRET_ENCRYPTION_KEY` so stored SMTP passwords can be encrypted.

Example:

```yaml
app:
  env:
    - name: EMAIL_CONFIG_SOURCE
      value: "env"
    - name: EMAIL_DELIVERY_MODE
      value: "smtp"
    - name: EMAIL_PUBLIC_APP_URL
      value: "https://triton-control.example.com"
    - name: EMAIL_SMTP_HOST
      value: "smtp.example.com"
    - name: EMAIL_SMTP_PORT
      value: "587"
    - name: EMAIL_SMTP_TLS_MODE
      value: "starttls"
    - name: EMAIL_SMTP_USERNAME
      value: "triton-control@example.com"
    - name: EMAIL_SENDER_EMAIL
      value: "triton-control@example.com"
  secretEnv:
    EMAIL_SMTP_PASSWORD: "replace-with-smtp-secret"
```

Do not commit real SMTP credentials. See the main
[configuration documentation](../../docs/configuration.md) for all variables
and delivery-mode behavior.

## MLflow version

The embedded MLflow server uses an operator-configured version. Users cannot
override its image through the installation UI or API:

```yaml
mlflow:
  version: "3.14.0"
  image:
    repository: ghcr.io/mlflow/mlflow
  gatewayImage: nginx:1.28-alpine
```

The server uses `<repository>:v<version>`. If you use the separately installed
[`mlflow-triton-control`](../../plugins/mlflow-triton-control/README.md) deployment
plugin, test it before changing your client
MLflow version:

```bash
bash plugins/mlflow-triton-control/smoke-latest.sh 3.14.0
```

MLflow listens on pod loopback behind a gateway that accepts only a credential
held by the Triton Control backend. Argo containers submitted through Triton
Control and new code-server workspaces receive `MLFLOW_TRACKING_URI` and
`MLFLOW_TRACKING_TOKEN` automatically. The tracking proxy sets `mlflow.user` to
the authenticated workflow submitter or workspace owner's email and prevents
that tag from being changed or deleted. Binary artifacts and model registry
operations use the same proxy. This attributes new runs; it does not hide
other users' runs or change the creator of existing runs.

After deploying this backend to an existing installation, the backend
automatically detects and upgrades legacy MLflow deployments in the background.
Failed or interrupted migrations are retried every 60 seconds without delaying
backend startup. This adds the gateway and updates existing workspace
environments in place, retaining MLflow and workspace PVCs and existing S3
credentials. Workspace pods roll to inherit the new environment. Previously
submitted workflows keep their old configuration; submit a new workflow after
the upgrade. Installations with the gateway already configured are left unchanged.
Administrators can still trigger **POST `/api/mlflow/upgrade`** manually.

For example, from the internal code-server terminal with an administrator's
local-login token already exported:

```bash
curl --fail-with-body -X POST \
  -H "Authorization: Bearer $TRITON_CONTROL_TOKEN" \
  http://triton-control.triton-control.svc.cluster.local:8000/api/mlflow/upgrade
```

The workflow executor ServiceAccount must have only its normal task-result
permissions, not permission to read arbitrary Secrets or create privileged
pods. Managed workflow submissions allow inline container/script templates,
the submitter's linked S3 Secrets, and workflow-owned `volumeClaimTemplates`.
Foreign Secret/PVC mounts, external/resource templates, elevated
ServiceAccounts, and host/privileged access are rejected to prevent tracking
credential impersonation. Kubernetes administrators remain trusted.
Alternate Argo creation APIs (template submission, resubmission, retries, and
CronWorkflow/template writes) are rejected by this proxy; submit a new inline
Workflow instead. Reading, deleting, suspending, resuming, stopping, and
terminating existing workflows remain available.

## RBAC Scope

By default, the chart creates namespace-scoped RBAC (least privilege):

- `serviceAccount.create=true`
- `rbac.create=true`
- `rbac.clusterWide=false`
- `rbac.manageNamespaces=false`

The namespace-scoped Role includes PVC permissions because the embedded MLflow
installer applies and removes its `mlflow-data` PersistentVolumeClaim.

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

### Argo API authentication

The embedded Argo Server uses **client authentication**. Triton Control strips
browser Authorization headers and forwards HTTP and WebSocket requests using
its own pod-bound Kubernetes ServiceAccount token. The backend reads the
projected token on each connection so Kubernetes rotation is picked up without
a restart. This token is never injected into workflow pods or stored in a
mountable application Secret. The status check verifies an authenticated
workflow-list API request rather than only checking the public UI assets.

The namespace-scoped `*-argo-proxy` RoleBinding gives the backend ServiceAccount
workflow management permissions. The executor ServiceAccount retains only
`create`/`patch` on `workflowtaskresults`; it cannot create workflows or pods, or
read Secrets. Calling Argo directly with no token is rejected; using a workflow
executor token cannot create a second workflow to bypass Secret validation.
People with independent Kubernetes workflow/pod creation permissions remain
trusted unless Kubernetes admission policies restrict their submissions.

`argoIntegration.networkPolicy.enabled=true` also restricts ingress to Argo
Server port 2746 to this release's Triton Control app pods in the same namespace.
This requires a NetworkPolicy-capable CNI. Existing policies are additive: a
broad allow policy can weaken this network restriction, but client authentication
and RBAC still apply. Workflow pods do not need Argo Server access to report
results; they use the Kubernetes API and their executor permissions.

Deploy the updated backend image and Helm chart together. Remove an old
`argoWorkflows.server.authModes: [server]` override and set `[client]`; the chart
rejects insecure auth-mode overrides. Prefer an explicit values file over
`--reuse-values` so the old server mode is not retained. Existing workflow pods
keep running. Users continue using the embedded UI without another login.
With `rbac.create=false`, supply equivalent backend RBAC yourself.

For a backend running outside Kubernetes, set `ARGO_WORKFLOWS_TOKEN_PATH` to a
protected file containing a Kubernetes bearer token with the same permissions;
a kubeconfig with only a client certificate does not supply this Argo credential.
For an independently managed Argo Server, configure client mode and equivalent
RBAC/network restrictions in that installation as well.

### Argo REST from code-server

New workspaces automatically receive `TRITON_CONTROL_ARGO_URL` and
`TRITON_CONTROL_ARGO_TOKEN`. The URL points to a dedicated Triton Control
endpoint, not directly to Argo Server. The random credential is separate from
MLflow tracking, S3 profile access, and the privileged backend ServiceAccount
credential. Users do not need to export a personal login token.

From the code-server terminal, list your workflows:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $TRITON_CONTROL_ARGO_TOKEN" \
  "$TRITON_CONTROL_ARGO_URL"
```

Submit an inline workflow using the same JSON wrapper as the Argo REST API:

```bash
curl --fail-with-body -X POST \
  -H "Authorization: Bearer $TRITON_CONTROL_ARGO_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"workflow":{"metadata":{"generateName":"workspace-hello-"},"spec":{"entrypoint":"main","templates":[{"name":"main","container":{"image":"python:3.12-slim","command":["python","-c"],"args":["print(42)"]}}]}}}' \
  "$TRITON_CONTROL_ARGO_URL"
```

The backend resolves the workspace owner, checks that they are active and have
member/admin access, and applies the embedded UI's submission validation.
Created workflows receive that user's managed tracking identity and owner
label. Workspace credentials can list the owner's labelled workflows, read or
delete an owned workflow at `/$WORKFLOW_NAME`, and `PUT` an owned workflow's
`/suspend`, `/resume`, `/terminate`, or `/stop` endpoint. Other creation APIs,
retries, templates, and administration endpoints are outside this credential's
scope. Changing or omitting list selectors cannot remove the owner filter.
Older workflows without the new owner label are omitted from listings; they can
still be addressed by name if they have the matching managed owner annotation.
Deleting the workspace or its Secret revokes access; disabling its owner or
removing their member/admin role also blocks subsequent requests.

Existing workspaces require a one-time **admin** call to
`POST /api/workflows/upgrade-workspaces` after deploying the updated backend:

```bash
curl --fail-with-body -X POST \
  -H "Authorization: Bearer $TRITON_CONTROL_TOKEN" \
  http://triton-control.triton-control.svc.cluster.local:8000/api/workflows/upgrade-workspaces
```

This adds the separate credential and environment in place, preserving PVCs,
S3 and MLflow tokens. Workspace pods roll to inherit the new environment;
repeating the migration preserves existing Argo tokens. MLflow does not need
to be installed to enable workspace Argo access.

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
