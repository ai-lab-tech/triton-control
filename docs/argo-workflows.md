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

### Link an S3 Profile

Members and administrators can open **Workflows → Configure S3 Secrets**, select
one of their saved **S3 profiles**, and click **Link profile**. No manual credential
entry or `kubectl` access is needed. Create or edit the source profile through
**S3 Profiles** in the account menu.

The source profile remains in the application database. Linking stores its profile
ID and synchronization metadata, and creates two resources in the configured
workflow namespace (normally `triton-control`):

| Resource | Contents |
| --- | --- |
| ConfigMap | Endpoint host and port, bucket, region, HTTPS settings, and references to the Secret, under the `repository` key. |
| Secret | `access-key-id`, `secret-access-key`, and optional public CA bundle `ca.pem`. |

Both resources use the same generated name, such as `workflow-s3-dev-1-a1b2c3`.
The dialog shows the name, namespace, workflow reference, and synchronization status.
The workflow credential API returns metadata, not the secret key or CA contents.
Linking makes these credentials available to workflows in the shared workflow
namespace; the source profile itself remains owner-scoped.

### Upload Scripts and Inspect Results

Use either AWS CLI or the bundled **Triton Control Deploy** code-server plugin.
For the plugin, connect through **Explorer → S3 Operations → Choose Profile…**
on a workspace item and select the profile you link to Argo. Drag scripts from
the workspace onto an S3 destination; cross-storage drags copy by default.
After a run, refresh the S3 tree and open or copy the output files from Explorer.
See [Development Workspaces](development-workspaces.md#s3-profiles-and-s3-browser).

AWS CLI requires its own credentials, endpoint, addressing mode, and optional
CA configuration; it does not inherit the code-server connection. Use the same
bucket and full object keys with either client. A profile's Explorer prefix is
not automatically prepended to Argo artifact keys.

### Use the Profile in a Workflow

Copy the dialog's reference under workflow `spec`:

```yaml
spec:
  artifactRepositoryRef:
    configMap: workflow-s3-dev-1-a1b2c3 # Use the name shown in your dialog.
    key: repository
```

Submit the workflow in the namespace shown in the dialog. Input and output
artifacts need only their S3 object key, for example:

```yaml
s3:
  key: workflows/sklearn-iris-training/train_iris.py
```

Do not repeat endpoint, bucket, region, credentials, or `caSecret` in these artifact
blocks. Object keys are full bucket paths; the profile's browsing prefix is not
added automatically.

For HTTPS with a custom CA, save the public PEM certificate bundle in the S3
profile. Triton Control adds `caSecret` to the repository configuration automatically.
Publicly trusted HTTPS endpoints need no custom CA. The profile's endpoint scheme
controls HTTPS versus plain HTTP. These settings configure Argo artifact transfers;
code-server extensions and AWS CLI have their own certificate trust configuration.

### Updates and Synchronization

Saving the source profile synchronizes its endpoint, bucket, region, credentials,
and CA settings to all linked workflow resources without changing their names.
New runs use the updated configuration; running workflows may retain values
already loaded. The dialog shows the last successful sync and any sync error.
The profile owner can click **Sync now** to retry. Existing links created before
repository support need **Sync now** once after upgrading to create their ConfigMap.

Removing a link deletes its workflow ConfigMap and Secret, so workflows using that
reference can no longer access them. Remove all links before deleting the source
profile.

See the [Iris training example](https://github.com/ai-lab-tech/triton-control/tree/main/examples/workflows/sklearn-iris-training)
for a complete workflow and instructions for uploading scripts and checking results.

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
