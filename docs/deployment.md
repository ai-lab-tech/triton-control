# Deployment

Triton Control can run as a local Compose stack or as a Kubernetes deployment.

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

- An Ingress controller, such as nginx-ingress, if `ingress.enabled=true`.
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

See the [Helm chart README](../charts/triton-control/README.md#optional-argo-workflows)
for image sources and existing-installation behavior, and
[Argo Workflows](argo-workflows.md) for runtime, security, and credential
details.

### Self-Deployed Triton And Perf Analyzer Namespace Behavior

Triton Control supports creating self-managed Triton deployments and a singleton
Perf Analyzer workload from the UI.

Namespace selection depends on where Triton Control backend is running:

- Running **inside Kubernetes** (in-cluster ServiceAccount detected):
  - Self-deployed Triton and Perf Analyzer are created in the **same namespace**
    as the running Triton Control pod.
- Running **outside Kubernetes** (for example local dev with
  `KUBERNETES_KUBECONFIG_PATH`):
  - Existing behavior stays unchanged:
    - Triton deployment namespace defaults to the deployment name.
    - Perf Analyzer namespace defaults to the installation name.

Runtime detection is automatic and based on in-cluster Kubernetes environment
signals (service host/port and ServiceAccount files), not only on UI settings.

`KUBERNETES_KUBECONFIG_PATH` is a development/testing override for external
backend runs. For in-cluster production deployments, keep it unset and rely on
ServiceAccount-based in-cluster Kubernetes client configuration.

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

| Triton path | Method | Requirement | Used for | If blocked |
| --- | --- | --- | --- | --- |
| `/v2/health/ready` | `GET` | Must have | Add/edit instance validation and readiness checks. | Saving a new or edited instance can fail, and readiness is unavailable. |
| `/v2/health/live` | `GET` | Must have for health UI | Live health state on instance detail and dashboard. | Live status is unavailable or shown as unhealthy/unknown. |

Feature-dependent paths:

| Triton path | Method | Requirement | Used for | If blocked |
| --- | --- | --- | --- | --- |
| `/v2` | `GET` | Recommended | Triton server metadata, version, and extension summary. | Metadata and version details are unavailable. |
| `/v2/repository/index` | `POST`, with `GET` fallback | Recommended | Model list, model state, and unavailable-model dashboard checks. | The models tab and model-state alerts are unavailable or incomplete. |
| `/v2/models/<model>/versions/<version>/config` | `GET` | Optional | Show API/model config. | Config display is unavailable. |
| `/v2/models/<model>/versions/<version>/infer` | `POST` | Required for inference | Inference requests from the UI/API. | Inference fails. |
| `/v2/models/stats` | `GET` | Optional | Fallback inference timing metrics when Prometheus metrics are unavailable. | Inference metrics may show no timing source. |
| `/v2/repository/models/<model>/load` | `POST` | Optional write action | Explicit model load. | Load action fails or should be hidden by policy. |
| `/v2/repository/models/<model>/unload` | `POST` | Optional write action | Explicit model unload. | Unload action fails or should be hidden by policy. |
| `/metrics` | `GET` | Optional metrics endpoint | CPU, RAM, GPU, and Prometheus inference metrics. This is often on Triton's metrics port. | Metrics show `N/A` or fall back to `/v2/models/stats` when possible. |

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
