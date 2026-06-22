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
- [Helm chart README](../charts/triton-control/README.md#optional-argo-workflows)
