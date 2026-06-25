# Argo Workflows

Triton Control can embed one global Argo Workflows installation. The Helm chart
includes the official Argo Workflows chart as an optional dependency. It is
disabled by default.

## Enable Argo Workflows

Enable the dependency in the Triton Control Helm values:

```yaml
argoWorkflows:
  enabled: true
```

The bundled configuration installs Argo Workflows `v4.0.6` through Helm chart
version `1.0.16`.

After deployment, members and administrators can open **Workflows** in the
Triton Control sidebar. The page embeds the Argo UI through:

```text
/api/workflows/proxy/
```

The Argo Server remains an internal `ClusterIP` Service. Triton Control
authenticates HTTP and WebSocket requests before proxying them to Argo.

## Kubernetes Layout

Argo runs in the Triton Control Helm release namespace:

- Argo Server provides the UI and API.
- Workflow Controller creates and monitors Workflow pods.
- `argo-service-account` is used by Workflow pods.
- Argo RBAC uses namespace-scoped Roles and RoleBindings.
- ClusterWorkflowTemplates and optional aggregate ClusterRoles are disabled.

Argo custom resource definitions remain cluster-scoped because Kubernetes CRDs
cannot be namespace-scoped.

## Pod Security

The default configuration applies these controls to Argo containers and
Workflow pods:

- non-root execution
- `RuntimeDefault` seccomp profile
- privilege escalation disabled
- all Linux capabilities dropped
- Workflow pods run as UID `1000`
- the Workflow Controller uses a read-only root filesystem

Workflow pods intentionally mount their ServiceAccount token. The Argo executor
needs it to create and patch `workflowtaskresults`. The associated
`argo-service-account` Role limits this token to the required namespace-scoped
operations.

## Workflow Images

Argo system images are pulled from their public registries. This does not grant
access to private images referenced by user-submitted Workflow YAML.

For a private Workflow image:

1. Create a `kubernetes.io/dockerconfigjson` Secret in the Triton Control
   namespace.
2. Reference that Secret through `spec.imagePullSecrets`.
3. Keep registry credentials out of Workflow YAML.

## On-Premise / Artifactory Image Mirrors

On-premise or air-gapped clusters can retarget every Argo **system** image at an
internal artifactory instead of the public internet. Argo pulls four images,
each configured under the `argoWorkflows` block in `values.yaml`:

| Image | Values path | Default |
| --- | --- | --- |
| Workflow Controller | `controller.image.registry` / `.repository` | `quay.io` / `argoproj/workflow-controller` |
| Workflow Executor (init + wait in every Workflow pod) | `executor.image.registry` / `.repository` | `quay.io` / `argoproj/argoexec` |
| Argo Server (UI/API) | `server.image.registry` / `.repository` | `quay.io` / `argoproj/argocli` |
| CRD install Job (`kubectl`) | `crds.upgradeJob.image.repository` | `registry.k8s.io/kubectl` |

The resolved pull string is `<registry>/<repository>:<tag>`. For the three
`argoproj` images, override only the `registry` host; the upstream repository
paths stay the same. The CRD `kubectl` image bakes the host into its repository
string, so override the whole `repository`.

```yaml
argoWorkflows:
  enabled: true
  images:
    pullPolicy: IfNotPresent
    pullSecrets:
      - name: artifactory-pull-secret
  controller:
    image:
      registry: artifactory.corp.example.com
  executor:
    image:
      registry: artifactory.corp.example.com
  server:
    image:
      registry: artifactory.corp.example.com
  crds:
    upgradeJob:
      image:
        repository: artifactory.corp.example.com/k8s-remote/kubectl
        tag: v1.36.2
```

For an authenticated artifactory, pre-create a `kubernetes.io/dockerconfigjson`
Secret in the release namespace and list it under `images.pullSecrets`; it
applies to all four system images.

The CRD `kubectl` image is only pulled when `crds.full` is `true` (the default),
where a one-shot Job server-side applies the full Workflow CRD. To avoid that
fourth image pull entirely, set `crds.full: false` to install minified CRDs
through Helm directly — at the cost of the full OpenAPI validation schema.

These settings cover Argo **system** images only. Images referenced by
user-submitted Workflow YAML are handled separately (see above).

## S3 Credentials

Members and administrators can manage Workflow S3 credentials through Triton
Control. The Secret Access Key is stored only in an opaque Kubernetes Secret in
the Workflow namespace. The application database stores only management
metadata: display name, namespace, Kubernetes Secret name, Access Key ID,
creator, and timestamps.

Workflow templates must reference the generated Secret rather than embedding
the Access Key ID or Secret Access Key directly. Triton Control displays the
stored Access Key ID so users can identify each configured credential; the
Secret Access Key is never returned by the API or displayed again.

## Internal Transport

Argo Server uses plain HTTP inside the cluster by default. External TLS
terminates at the Triton Control ingress. Do not expose the Argo Server Service
directly unless separate authentication and TLS controls are configured.

## Related Configuration

Backend variables are documented in [Configuration](configuration.md):

- `ARGO_WORKFLOWS_ENABLED`
- `ARGO_WORKFLOWS_SERVER_URL`
- `ARGO_WORKFLOWS_NAMESPACE`
- `ARGO_WORKFLOWS_SERVICE_NAME`
- `ARGO_WORKFLOWS_BASE_PATH`

Deployment details and Helm values are available in:

- [Deployment](deployment.md#optional-argo-workflows-dependency)
- [Helm chart README](https://github.com/ai-lab-tech/triton-control/blob/main/charts/triton-control/README.md#optional-argo-workflows)
