# Deployment

Triton Control can run as a local Compose stack or as a Kubernetes deployment.

For installation, start with [Kubernetes Ingress and TLS setup](ingress-tls.md)
or the [Minikube development environment](local-kubernetes.md).

## Optional Email Delivery

Email is not required. The safe default is `EMAIL_DELIVERY_MODE=disabled`, so
closed environments need no SMTP service. Use `manual-link` to let an
administrator generate a one-time invitation or reset URL and transfer it over
an already trusted channel. Use `smtp` with an external relay to enable
automatic invitations and the public forgot-password flow.

Set `EMAIL_CONFIG_SOURCE=env` when Compose, Helm, or GitOps configuration is the
source of truth; the Email settings tab is then read-only. Set it to `db` only
when administrators should manage and persist email settings through the UI.

Both enabled modes require `EMAIL_PUBLIC_APP_URL`, for example
`https://triton-control.example.com`. It is the trusted link origin and must
match the URL users can open. `EMAIL_SMTP_PASSWORD` belongs in Compose secrets,
Kubernetes Secrets, an external secret operator, or Helm `app.secretEnv`.
`EMAIL_SECRET_ENCRYPTION_KEY` is additionally required when
`EMAIL_CONFIG_SOURCE=db` stores an SMTP password. Never commit production
values. The chart exposes safe defaults under `app.env` and secret placeholders
under `app.secretEnv` and does not deploy a mail server.

Save database-managed SMTP settings before sending a test email. The test
button is intentionally disabled while settings differ from the persisted
configuration. SMTP test, invitation, and reset requests show progress because
the bounded SMTP operation is synchronous and can take several seconds.

Before switching an installation to `disabled`, cancel pending invitations that
must no longer be usable. Disabling delivery prevents new lifecycle operations
but does not invalidate links that were already issued.

Common relay patterns (replace every placeholder with deployment-owned values):

```text
# Submission relay
EMAIL_SMTP_HOST=smtp.example.com
EMAIL_SMTP_PORT=587
EMAIL_SMTP_TLS_MODE=starttls

# Implicit TLS relay
EMAIL_SMTP_HOST=smtp.example.com
EMAIL_SMTP_PORT=465
EMAIL_SMTP_TLS_MODE=tls
```

## Docker Compose

Prerequisite: Docker Desktop or another Docker engine. Host Node.js, npm, and
Java are not required; the Dockerfile installs the frontend build tools inside
the image build and regenerates the Swagger/OpenAPI client before building
Angular.

```bash
docker compose up --build
```

Exposed endpoints:

- Frontend: `http://localhost:8080`
- Backend API: `http://localhost:8000`
- PostgreSQL: `127.0.0.1:5433`

The Compose app database URL is:

```text
postgresql://triton:tritonpw@postgres:5432/triton_backend
```

Backend logging is quiet by default. Set `BACKEND_VERBOSE=true` for info-level
backend logs and Uvicorn access logs, or `LOG_LEVEL=DEBUG` for deeper debugging.
Set `DATABASE_ECHO=1` only when SQL statement logging is needed.

The default Docker network is explicitly named:

```text
triton-control
```

## Podman Compose

```bash
podman-compose -f podman-compose.yaml up --build
```

The Podman Compose file uses fully qualified image names and the same network
name:

```text
triton-control
```

## Kubernetes With Helm

Triton Control has been tested on Kubernetes with Argo CD managing the Helm
release in a GitOps workflow. OIDC provider compatibility is independent of
the Kubernetes deployment target; see [Configuration](configuration.md) for
tested providers.

### Prerequisites

The Helm chart is intended for standard Kubernetes clusters and uses only common
workload, service, secret, PVC, and Ingress resources.

Required:

- Kubernetes `v1.19` or newer when Ingress is enabled. The chart renders
  `networking.k8s.io/v1` Ingress resources.
- Helm `v3`.
- `kubectl` access to the target namespace.
- Permission to create Deployments, Services, Secrets, PersistentVolumeClaims,
  and Ingress resources in that namespace.
- A container registry that the cluster can pull from.
- Real values for `SESSION_SECRET`, `JWT_SECRET`, and
  `S3_SECRET_ENCRYPTION_KEY`.

Usually required:

- Traefik with its Kubernetes Ingress provider enabled if `ingress.enabled=true`.
- DNS for the configured Ingress host.
- A TLS certificate Secret when exposing the application over HTTPS.
- A default StorageClass, or `postgresql.persistence.storageClass`, when using
  the bundled PostgreSQL database with persistence enabled.
- Network access from the backend Pod to the Triton servers, metrics endpoints,
  OIDC provider, S3 endpoint, and PostgreSQL database.

Recommended for production:

- Use an external managed PostgreSQL database or enable persistent storage for
  the bundled PostgreSQL deployment.
- Store secrets in a pre-created Kubernetes Secret and reference it with
  `app.existingSecret` or `app.envFrom`.
- Set CPU and memory requests/limits in `app.resources` and
  `postgresql.resources`.
- Keep OIDC values in Helm values and secrets with `OIDC_CONFIG_SOURCE=env`.
- Set explicit allowed origins with `CORS_ORIGINS`.

Preflight checks:

```bash
kubectl version
helm version
kubectl get storageclass
kubectl get ingressclass
kubectl auth can-i create deployments.apps
kubectl auth can-i create services
kubectl auth can-i create secrets
kubectl auth can-i create persistentvolumeclaims
kubectl auth can-i create ingresses.networking.k8s.io
```

Build and push the image:

```bash
docker build -t registry.example.com/triton-control:0.1.0 .
docker push registry.example.com/triton-control:0.1.0
```

The Docker image build requires Docker on the build host. It does not require
host npm or Java. The build needs network access to install npm packages and,
if the Swagger generator jar is not already cached in the build context, to
download `swagger-codegen-cli.jar`.

Install:

```bash
helm upgrade --install triton-control ./charts/triton-control \
  --namespace triton-control \
  --create-namespace \
  -f values-prod.yaml
```

Minimal values:

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
```

If `postgresql.enabled=true`, the chart generates and injects `DATABASE_URL`.
For an external database, set `postgresql.enabled=false` and provide
`DATABASE_URL` with `app.existingSecret`, `app.env`, or `app.envFrom`.

Check the rollout:

```bash
kubectl -n triton-control get pods
kubectl -n triton-control rollout status deployment/triton-control
kubectl -n triton-control get svc,ingress
```

If Ingress is disabled, use a port-forward for a smoke test:

```bash
kubectl -n triton-control port-forward svc/triton-control 8080:80
```

For GitOps-managed OIDC configuration, set `OIDC_CONFIG_SOURCE=env` and provide
the OIDC values through Helm `app.env` plus Kubernetes Secrets. See
[Configuration](configuration.md) for the full variable list and an example.

### Optional Argo Workflows Dependency

The Triton Control chart pins the official Argo Workflows chart as an optional
dependency. It is disabled by default.

Enable the global Argo Server and workflow controller with:

```yaml
argoWorkflows:
  enabled: true
```

The default integration:

- installs Argo Workflows `v4.0.6` through chart version `1.0.16`
- pulls controller, executor, and server images directly from `quay.io`
- runs Argo Server, controller, workflow RBAC, and Workflow pods in the Triton
  Control Helm release namespace
- creates `argo-service-account` in that namespace
- enables Argo single-namespace mode
- exposes Argo Server internally through a `ClusterIP` Service on port `2746`
- configures `/api/workflows/proxy/` as the Argo UI base path

The Argo Server runs plain HTTP inside the cluster. TLS remains the
responsibility of the Triton Control ingress. Browser access passes through the
authenticated Triton Control backend proxy.

The configured public Argo system images do not cover private images referenced
by Workflow YAML. Such images still require an image pull Secret in the Triton
Control release namespace. Keep credentials outside Workflow YAML and inject a
server-managed `spec.imagePullSecrets` reference.

See the [Helm chart README](https://github.com/ai-lab-tech/triton-control/blob/main/charts/triton-control/README.md#optional-argo-workflows)
for image sources and existing-installation behavior, and
[Argo Workflows](argo-workflows.md) for runtime, security, and credential
details.

### Self-Deployed Triton And Perf Analyzer Namespace Behavior

Triton Control supports creating self-managed Triton deployments and per-model
Perf Analyzer Jobs from the UI. See [Model performance Jobs](model-performance-jobs.md)
for upgrade steps and non-root Job settings.

Namespace selection depends on where Triton Control backend is running:

- Running **inside Kubernetes** (in-cluster ServiceAccount detected):
  - Self-deployed Triton and Perf Analyzer are created in the **same namespace**
    as the running Triton Control pod.
- Running **outside Kubernetes** (for example local dev with
  `KUBERNETES_KUBECONFIG_PATH`):
  - Triton deployment namespace defaults to the deployment name.
  - Perf Analyzer defaults to the shared `triton-control` namespace. Override
    it with `TRITON_CONTROL_NAMESPACE`, `KUBERNETES_NAMESPACE`, or
    `POD_NAMESPACE`.

Runtime detection is automatic and based on in-cluster Kubernetes environment
signals (service host/port and ServiceAccount files), not only on UI settings.

`KUBERNETES_KUBECONFIG_PATH` is a development/testing override for external
backend runs. For in-cluster production deployments, keep it unset and rely on
ServiceAccount-based in-cluster Kubernetes client configuration.

When the backend runs on a host with Kubernetes integration enabled, configure
the vLLM sync worker image in `triton-backend/.env` (or the environment of the
backend service):

```dotenv
TRITON_DEPLOY_S3_SYNC_IMAGE=registry.example.com/amazon/aws-cli:2.22.35
```

The host does not run this image. It places the configured image reference in
the vLLM init-container or sidecar pod specification created through the
Kubernetes API. Ensure cluster nodes can pull it and provide an image pull
secret in Add Deployment when the registry is private.

## Ingress

The chart can create Ingress resources, but the Ingress controller itself must
already exist in the cluster.

Backend routes expected behind ingress include:

- `/api`
- `/auth`
- `/login`
- `/logout`
- `/whoami`

## Triton Server Path Access

Triton Control does not need every Triton HTTP endpoint, but the backend must
be able to reach the paths used by the enabled features. This matters when a
reverse proxy, ingress controller, API gateway, service mesh, or other network
policy in front of Triton uses path or method allowlists. If only inference is
allowed, inference requests can work, but health checks, instance save
validation, model lists, model config, load/unload actions, and metrics will be
limited or unavailable.

Minimum required for registering and health-monitoring an instance:

| Triton path        | Method | Requirement             | Used for                                            | If blocked                                                              |
| ------------------ | ------ | ----------------------- | --------------------------------------------------- | ----------------------------------------------------------------------- |
| `/v2/health/ready` | `GET`  | Must have               | Add/edit instance validation and readiness checks.  | Saving a new or edited instance can fail, and readiness is unavailable. |
| `/v2/health/live`  | `GET`  | Must have for health UI | Live health state on instance detail and dashboard. | Live status is unavailable or shown as unhealthy/unknown.               |

Feature-dependent paths:

| Triton path                                    | Method                      | Requirement               | Used for                                                                                 | If blocked                                                           |
| ---------------------------------------------- | --------------------------- | ------------------------- | ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `/v2`                                          | `GET`                       | Recommended               | Triton server metadata, version, and extension summary.                                  | Metadata and version details are unavailable.                        |
| `/v2/repository/index`                         | `POST`, with `GET` fallback | Recommended               | Model list, model state, and unavailable-model dashboard checks.                         | The models tab and model-state alerts are unavailable or incomplete. |
| `/v2/models/<model>/versions/<version>/config` | `GET`                       | Optional                  | Show API/model config.                                                                   | Config display is unavailable.                                       |
| `/v2/models/<model>/versions/<version>/infer`  | `POST`                      | Required for inference    | Inference requests from the UI/API.                                                      | Inference fails.                                                     |
| `/v2/models/stats`                             | `GET`                       | Optional                  | Fallback inference timing metrics when Prometheus metrics are unavailable.               | Inference metrics may show no timing source.                         |
| `/v2/repository/models/<model>/load`           | `POST`                      | Optional write action     | Explicit model load.                                                                     | Load action fails or should be hidden by policy.                     |
| `/v2/repository/models/<model>/unload`         | `POST`                      | Optional write action     | Explicit model unload.                                                                   | Unload action fails or should be hidden by policy.                   |
| `/metrics`                                     | `GET`                       | Optional metrics endpoint | CPU, RAM, GPU, and Prometheus inference metrics. This is often on Triton's metrics port. | Metrics show `N/A` or fall back to `/v2/models/stats` when possible. |

For a strict inference-only public Triton ingress, allow only:

```text
POST /v2/models/<model>/versions/<version>/infer
```

Do not use that restricted URL as the Triton Control instance URL if you expect
the full management UI to work. Prefer a separate internal control-plane URL
from the Triton Control backend to Triton that allows the required health paths
and any optional feature paths you want to use.

Perf Analyzer target access:

- Self-deployed Triton instance: Perf Analyzer must reach the internal Service
  endpoint used by the instance URL.
- Existing/manual Triton instance: Perf Analyzer must reach the external
  endpoint configured in the instance URL.
- In both cases, connectivity must work from the Perf Analyzer pod to the
  configured Triton HTTP endpoint (REST), including host and port.

Perf Analyzer endpoint notes:

- Minimum for REST profiling runs: `POST /v2/models/<model>/infer`
- For server-side timing analysis (for example ensemble-focused analysis in
  Triton Control), `GET /v2/models/stats` must be reachable.
- If Prometheus-based counters are required, expose Triton metrics endpoint
  `GET /metrics` (typically on port `8002`).

Image pull secrets:

- Add Deployment and Perf Analyzer both accept Docker registry authentication
  JSON for private images.
- Paste the value as `.dockerconfigjson` in the UI image pull secret field.
- The JSON below is only an example Docker config shape, not a fixed template.
- The `auth` value is the base64 encoding of `username:token` or
  `username:password`.

vLLM S3 repository handling:

- Deployments without the vLLM backend use Triton's native S3 behavior directly.
  They create no repository init container or sidecar.
- Deployments with the vLLM backend use the sync worker path so Triton reads a
  stable local `/models` repository. Polling mode keeps the repository refreshed
  while Triton runs; explicit mode uses the configured startup model behavior.
- The sync path keeps model versions under `/models/<model-name>/<version>` and
  rewrites relative vLLM `model.json` `model`/`tokenizer` values to absolute
  paths.
- vLLM requires a CUDA device in normal Triton deployments. Set GPU count to at
  least `1`; the code-server deploy extension defaults this for detected vLLM
  repositories.
- The sync worker copies downloaded files without preserving source ownership
  or timestamps, so it can run as Triton Control's non-root workload user.
- Configure the worker image with Helm value
  `tritonDeployments.s3SyncImage`. Non-vLLM deployments do not use this image.
- Large vLLM repositories may need larger local sync volumes. Configure
  `/models` with `tritonDeployments.modelRepositoryEmptyDirSize` and `/staging`
  with `tritonDeployments.s3SyncStagingEmptyDirSize`. Each volume should fit the
  unpacked model repository plus a little headroom.

Example:

```yaml
tritonDeployments:
  modelRepositoryEmptyDirSize: 12Gi
  s3SyncStagingEmptyDirSize: 12Gi
```

S3 deployment profiles:

- Members and admins can create reusable S3 profiles from the application
  account menu.
- Profiles are user-owned and store endpoint, bucket, prefix, region,
  path-style mode, optional CA certificate, access key, and encrypted secret
  key.
- The deployment form reads profiles through `/api/s3-profiles` for repository
  upload and deployment creation.
- The native code-server S3 browser loads the workspace owner's profiles through
  `/api/development/workspace-s3-profiles/<namespace>/<statefulset-name>` using
  its injected workspace token. It uses credentials and CA certificates in
  memory. See [Development Workspaces](development-workspaces.md#credentials-and-certificates).

```json
{
  "auths": {
    "<REGISTRY_HOST>": {
      "auth": "<BASE64_USERNAME_COLON_TOKEN>",
      "email": "<EMAIL>"
    }
  }
}
```

## Recommended Kubernetes Deployment Model

For environments where teams require isolated Triton Control installations,
deploy one dedicated Triton Control instance per Kubernetes namespace. A team is
the organizational owner of that namespace, not a Kubernetes resource type.

Each namespace gets its own:

- Kubernetes namespace
- Triton Control deployment
- Ingress endpoint
- Configuration
- Self-deployed Triton instances

This keeps environments isolated and makes ownership clear between teams.

```mermaid
flowchart TB
    GitOps[GitOps Repository<br/>Helm/Kustomize/Manifests]

    GitOps --> DeptA
    GitOps --> DeptB
    GitOps --> DeptC

    subgraph DeptA["Namespace: ai-dept-a"]
        TC_A[Triton Control<br/>own deployment]
        ING_A[Ingress<br/>triton-a.company.com]
        TR_A1[Triton Server A1]
        TR_A2[Triton Server A2]

        ING_A --> TC_A
        TC_A --> TR_A1
        TC_A --> TR_A2
    end

    subgraph DeptB["Namespace: ai-dept-b"]
        TC_B[Triton Control<br/>own deployment]
        ING_B[Ingress<br/>triton-b.company.com]
        TR_B1[Triton Server B1]

        ING_B --> TC_B
        TC_B --> TR_B1
    end

    subgraph DeptC["Namespace: ai-dept-c"]
        TC_C[Triton Control<br/>own deployment]
        ING_C[Ingress<br/>triton-c.company.com]
        TR_C1[Triton Server C1]
        TR_C2[Triton Server C2]

        ING_C --> TC_C
        TC_C --> TR_C1
        TC_C --> TR_C2
    end
```

Use GitOps to manage all Triton Control installations and their configuration.
The GitOps repository should contain the desired state for each namespace:

```text
environments/
  dept-a/
    triton-control-values.yaml
    ingress.yaml
  dept-b/
    triton-control-values.yaml
    ingress.yaml
  dept-c/
    triton-control-values.yaml
    ingress.yaml
```

Benefits:

- Clear separation between namespaces
- Independent lifecycle per team
- Easier access control
- Safer configuration changes
- Reproducible deployments
- Better auditability through Git history
- Simple rollback using GitOps tooling

Use this model when:

- Teams require separate Kubernetes namespaces.
- Access must be isolated between teams.
- Each team manages its own Triton instances.
- Configuration should be maintained declaratively.

Ingress recommendation:

- Use a dedicated hostname per namespace or team, for example
  `triton-a.company.com`, `triton-b.company.com`, and
  `triton-c.company.com`.
- Keep Ingress resources in the same namespace as the related Triton Control
  deployment.
- Use TLS for every externally reachable Triton Control endpoint.
- Manage Ingress hosts, TLS secrets, annotations, and controller-specific
  settings through GitOps.

## Managed MLflow storage

New MLflow installations require a saved S3 profile selected in the installation
form. The server writes artifacts to `s3://<bucket>/<profile-prefix>/mlflow` using
a server-only Kubernetes Secret. Profile endpoint, region, addressing style and
custom CA settings are included. Clients use the authenticated MLflow proxy and
do not receive the S3 credentials.

Tracking and registry metadata use the application's PostgreSQL connection in the
isolated `mlflow_tracking` schema. PostgreSQL must be reachable from the MLflow
pod and the database user must be allowed to create this schema. No new MLflow
PVC is created. The default server image installs the S3/PostgreSQL drivers at
startup when missing; for restricted networks, preinstall boto3 and psycopg2.

The tracking upgrade endpoint requires and preserves existing S3 storage
configuration. SQLite/PVC installations must be migrated before upgrading; old
runs are not migrated automatically. S3 installations retain
artifacts and PostgreSQL metadata when uninstalled; reinstall against the same
artifact endpoint and root to retain valid proxy references.


## Changing deployment access

For a Kubernetes-managed Triton instance, open **Endpoints → Edit endpoint**.
Members assigned to the instance and administrators can switch between
**Cluster only** and **External access (Ingress)**. The switch changes the Ingress
and authentication Secret without restarting Triton or changing its models. The
ClusterIP Service remains in both modes. Internal mode removes the managed Ingress
and its authentication Secret; requests already in flight may finish while the
controller reconciles the change. Other manually created routes are outside this
setting's scope.

New deployments offer the same choice under **Access**. Existing API clients can
continue supplying `ingress_host` without `access_mode`. An omitted host means
internal access. The new `GET /api/deployments/{id}/access` and `PUT` endpoint return
current Kubernetes exposure settings. `PUT` accepts `access_mode`, `ingress_host`,
`ingress_scheme` and the optional authentication fields. Users cannot supply
`ingress_class_name`; the administrator controls the class for every deployment.

Triton Control manages new deployments and deployments whose access was changed
through their internal Service DNS address, including health, metrics and model
operations. The backend must therefore have cluster DNS/network access. The
**Endpoints** tab shows the client-facing endpoint separately. Ingress
exposes Triton's HTTP API and metrics when enabled; gRPC remains on the internal
Service. Existing deployments retain their stored management URL until access is
saved.

### Administrator-managed Traefik and HTTPS

Model Ingress can be disabled independently of Triton Control's UI/API Ingress:

```yaml
ingress:
  enabled: false
tritonDeployments:
  ingressEnabled: false
```

`tritonDeployments.ingressEnabled` defaults to `true` for compatibility. Setting
it to `false` hides new external-access options and rejects model Ingress
creation and edits in the backend, including queued creation. Existing model
Ingresses are not automatically removed; users can still switch them to
cluster-only access. **Settings → Endpoints** shows this deployment-managed
switch. The HTTP permission override cannot enable Ingress when this switch is
off. No database migration is needed for this switch.

New model endpoints require HTTPS by default. To permit HTTP through Helm:

```yaml
tritonDeployments:
  allowHttp: true
```

Administrators can override this default in **Settings → Endpoints**, or select
**Use deployment default** to remove the override. The page shows the effective
policy and its source. The override is persisted in the `deployment_policy`
table, created automatically at startup. This policy controls model endpoints,
not the Triton Control UI's own Ingress.

Disabling HTTP blocks new HTTP endpoints, transitions from HTTPS to HTTP, and
hostname changes on HTTP endpoints. Existing HTTP endpoints remain active and
can retain their current hostname; they can also switch to HTTPS or cluster-only
access. Basic authentication always requires HTTPS. Enforcement runs in the
backend for creation and editing, including a recheck before queued creation.


Deployment creation and endpoint edits reject hostnames that overlap an existing
Ingress rule in any namespace for the configured ingress class or another class
using the Traefik controller. Unclassified routes and missing IngressClass resources
are checked conservatively; only verified different controllers are excluded.
This may also reject overlaps between separately operated Traefik controllers.
Matching includes single-label wildcards (for example, `*.example.com` conflicts with `model.example.com`).
An edit may retain its own hostname. The Helm chart grants cluster-wide
`list` permission for Ingresses even with namespaced workload permissions;
custom RBAC must grant this permission too.

Creation checks before accepting the request and again before applying resources.
These are preflight checks, not an atomic cluster-wide hostname reservation:
simultaneous writers or resources managed outside Triton Control can still race.
Use cluster admission policy if strict hostname exclusivity is required.

Traefik is the supported controller. Install it separately with the Kubernetes
Ingress provider enabled. Basic authentication additionally requires the
Kubernetes CRD provider and the `middlewares.traefik.io` CRD. Triton Control does
not install or configure the controller itself.

Administrators configure the class and entrypoints through Helm:

```yaml
tritonDeployments:
  ingressClassName: traefik
  ingressHttpEntrypoint: web
  ingressHttpsEntrypoint: websecure
  ingressTlsSecretName: triton-wildcard-tls
  certManagerClusterIssuer: ""
```

The backend verifies that the configured IngressClass uses
`traefik.io/ingress-controller` for every exposed deployment. Custom Traefik class
names are supported; user overrides and other controllers are rejected. Configure
both Traefik providers to observe the deployment namespace and the chosen class.
The chart grants the backend `get` permission on IngressClasses and `get`/`create`
on Traefik Middlewares in its managed namespace (or cluster-wide when configured).

Choose one certificate strategy:

- **Existing certificate:** set `ingressTlsSecretName` to a `kubernetes.io/tls`
  Secret containing `tls.crt` and `tls.key` in each deployment namespace. Your
  certificate tooling manages renewal. The certificate must cover the hostname.
- **cert-manager:** install cert-manager and configure a ClusterIssuer, then set
  `certManagerClusterIssuer` and leave `ingressTlsSecretName` empty. Each HTTPS
  Ingress receives the issuer annotation and its own `<deployment>-ingress-tls`
  Secret name. cert-manager creates and renews the Certificate and Secret. Configure
  the issuer's validation method for your domains and Traefik; issuance is
  asynchronous, so saving access settings does not mean the certificate is ready.
- **Traefik default certificate:** leave both values empty and configure Traefik's
  default TLS certificate yourself. Empty values do not provision a trusted certificate.

Setting both a TLS Secret and a ClusterIssuer is rejected. DNS must resolve to
Traefik. HTTPS routes use only the configured HTTPS entrypoint; configure HTTP to
HTTPS redirection centrally in Traefik if desired. HTTP routes use the HTTP
entrypoint and do not request certificates.

For non-Helm installations, use `TRITON_DEPLOY_INGRESS_CLASS`,
`TRITON_DEPLOY_INGRESS_HTTP_ENTRYPOINT`, `TRITON_DEPLOY_INGRESS_HTTPS_ENTRYPOINT`,
`TRITON_DEPLOY_INGRESS_TLS_SECRET`, and `TRITON_DEPLOY_CERT_MANAGER_CLUSTER_ISSUER`.
Users cannot select TLS Secrets, issuers, entrypoints, or controllers.

The Triton Control application's own Ingress is configured separately through
`ingress.className`, `ingress.annotations`, and `ingress.tls`. To request its
certificate, add `cert-manager.io/cluster-issuer` to those annotations and set a
TLS host and Secret name.

See [Traefik Ingress configuration](https://doc.traefik.io/traefik/reference/routing-configuration/kubernetes/ingress/)
and [cert-manager Ingress certificates](https://cert-manager.io/docs/usage/ingress/).

### Optional basic authentication

Enable **Require basic authentication** and supply a username/password. This
requires HTTPS and protects both HTTP inference and metrics routes. Triton Control
creates a salted bcrypt htpasswd entry in a Secret's `users` key and a Traefik
`Middleware` referencing that Secret. The Ingress references the Middleware;
the middleware removes the Authorization header before forwarding requests.
No plaintext password is stored in the database or returned by the access API.
Passwords may contain 1–72 UTF-8 bytes.

When editing, leave both credential fields blank to keep existing credentials.
Enter both to rotate them, or disable authentication to remove the managed Secret.
Each Middleware is owned by its Secret, so Kubernetes garbage collection removes
it when that Secret is deleted. Rotation creates a new Secret and Middleware before
switching the Ingress. A failed Ingress update keeps the previous credentials.
A missing or modified retained Middleware requires entering credentials again.
When moving an existing nginx-authenticated route to Traefik, enter credentials
again to create the Traefik Middleware; the old credential Secret is removed
only after the Ingress update succeeds.

Authentication protects the Ingress route; cluster-internal access remains subject
to your network policies. See [Traefik BasicAuth](https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/basicauth/).

### Plugin endpoint configuration

Code-server (**External access**) and MLflow use the same deployment API and
admin policy. Supply only a short hostname, without a domain, protocol, port or path:

```json
{"access_mode":"ingress","ingress_host":"smollm","ingress_scheme":"https"}
```

Optional `basic_auth_*` settings require HTTPS. MLflow's `external_url`
is the public endpoint; `url` remains internal. Update installed plugins
to use these options.

### Model base domain

```yaml
tritonDeployments:
  ingressEnabled: true
  baseDomain: models.example.com
```

Enter `smollm`; the API creates `smollm.models.example.com`.
Users always enter a short hostname, never a full domain or URL.
Administrators configure wildcard DNS and TLS. If `baseDomain` is empty,
the short hostname is used unchanged. Invalid domain configuration rejects new external
hostnames but still permits internal deployments and removing existing exposure.
Existing endpoints keep their domain when credentials or protocol are edited.
Changing the short hostname uses the current base domain.
Triton Control's own hostname is configured under `ingress.hosts`.
