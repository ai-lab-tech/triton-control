# Development Workspaces

The **Development** sidebar entry provides each user with a private,
browser-based development workspace for Triton model repositories. The
workspace UI is powered by code-server and is embedded directly in Triton
Control.

## Prerequisites

Development workspaces are available only when Kubernetes support is enabled.
The sidebar entry is disabled when Triton Control cannot perform Kubernetes
actions.

Each authenticated user can own one workspace. Workspaces are isolated by
ownership: the backend permits users to list, open, proxy, refresh, and delete
only their own workspace.

The Kubernetes cluster must provide:

- a default or otherwise matching StorageClass for the workspace PVC
- permission for Triton Control to create StatefulSets, Services, ConfigMaps,
  Secrets, PVCs, and optional image pull Secrets
- internet access from the workspace pod when code-server or marketplace
  extensions must be downloaded
- NVIDIA GPU scheduling support when a GPU count is requested

## Create a Workspace

Open **Development** in the sidebar and configure the workspace:

| Field | Required | Default | Purpose |
| --- | --- | --- | --- |
| Workspace name | Yes | `workspace` | Name used to derive the user's Kubernetes resource names. |
| Triton development image | Yes | `nvcr.io/nvidia/tritonserver:25.02-py3` | Container image used for the workspace pod. |
| Image already has Development installed | No | disabled | Uses the `code-server` binary from the image instead of installing a standalone runtime during startup. Triton Control starts it on `0.0.0.0:8080`. |
| Workspace storage | Yes | `20Gi` | Persistent volume claim size mounted at `/workspace`. |
| VS Code theme | Yes | `Default Dark+` | Initial code-server color theme. |
| CPU request | No | unset | CPU request and limit for the workspace container. |
| Memory request | No | unset | Memory request and limit for the workspace container. |
| GPU count | No | unset | Adds an `nvidia.com/gpu` resource limit when greater than zero. |
| Registry credentials | No | unset | Docker `.dockerconfigjson` used to pull a private workspace image. |

In this field, **Development** refers to the code-server runtime. Enable
**Image already has Development installed** only if `code-server` is available
on the image's `PATH`. Triton Control overrides the image startup command and
starts that binary with `--bind-addr 0.0.0.0:8080`; the image does not need to
start code-server itself. The resulting workspace must listen on container port
`8080`, which is also used by the Kubernetes Service and health probes.
Otherwise workspace startup fails. When the option is disabled, the pod
installs a standalone code-server runtime under
`/tmp/triton-control-code-server` and starts it on the same port.

Creating a workspace provisions:

- one StatefulSet with a single workspace pod
- one internal Service on port `8080`
- one persistent volume claim mounted at `/workspace`
- Secrets for workspace authentication mode and optional registry credentials
- a ConfigMap containing the bundled **Triton Control Deploy** extension

The workspace is created in the Triton Control namespace when the backend runs
inside Kubernetes. For an external backend, the namespace is selected from
`TRITON_CONTROL_NAMESPACE`, `KUBERNETES_NAMESPACE`, or `POD_NAMESPACE`, with
`triton-control` as the fallback.

## Workspace Lifecycle

After creation, Triton Control polls the StatefulSet and pod until the workspace
status becomes `ready`. The page then embeds code-server through the
authenticated backend proxy:

```text
/api/development/<workspace-id>/proxy/?folder=/workspace
```

Both HTTP and WebSocket traffic pass through this proxy. The code-server
Service is therefore not exposed directly to the browser.

Use **Refresh** to read the latest pod status. Use **Delete** to remove the
managed StatefulSet, Service, Secrets, ConfigMap, and any legacy ingress.

!!! warning "Persistent volume retention"

    Deleting a Development workspace does not delete its Kubernetes PVC.
    Remove retained workspace claims separately when their data is no longer
    needed.

## Persistent Files and Extensions

The `/workspace` directory is persistent. Triton Control also keeps code-server
settings and user-installed extensions on that volume:

```text
/workspace/.triton-control/code-server-settings.json
/workspace/.triton-control/code-server-extensions/
```

These files survive pod restarts while the PVC remains available. New
workspaces receive the Python extension when its marketplace installation
succeeds and the bundled **Triton Control Deploy** extension.

## S3 Profiles and Optional S3/R2 Explorer

The **Triton Control Deploy** extension works best with an S3 profile. Members
and admins can create profiles from the Triton Control account menu under
**S3 Profiles**. The extension loads those profiles and shows them in an
`S3 profile` dropdown.

Use profiles for shared or repeated deployment settings. Each profile stores
the endpoint, bucket, optional prefix, region, access key, encrypted secret
key, path-style mode, and optional CA certificate.

Manual S3 settings are still available inside the extension in a collapsed
section. Use them for one-off deployments or to save a new profile from inside
code-server.

### Optional: Install S3/R2 Explorer

S3/R2 Explorer is optional and is not installed automatically when a
Development workspace is created. The **Triton Control Deploy** extension works
without it because S3 profiles and manual settings are handled by Triton
Control.

To add S3/R2 Explorer:

1. Open the **Extensions** view in the Development workspace.
2. Search the code-server extension marketplace for **S3/R2 Explorer**.
3. Select **Install**.
4. Open the extension settings and configure the S3-compatible endpoint,
   region, access key, secret key, and path-style behavior.

The installed extension is stored under
`/workspace/.triton-control/code-server-extensions` and therefore survives
workspace pod restarts while the PVC is retained.

When S3/R2 Explorer is installed and configured, its settings can still be used
as defaults for manual extension fields:

| Setting | Used for |
| --- | --- |
| `s3x.endpointUrl` | S3-compatible endpoint |
| `s3x.region` | S3 region |
| `s3x.accessKeyId` | Access key |
| `s3x.secretAccessKey` | Secret key |
| `s3x.forcePathStyle` | Path-style request mode |

Use path-style access for compatible object stores that address objects as
`https://s3.example.com/bucket/key`. Disable it only when the object store uses
virtual-host addressing such as `https://bucket.s3.example.com/key`.

For a private or self-signed HTTPS object-store certificate, provide the PEM CA
certificate requested by the extension. This certificate is passed to the
Triton deployment so its model repository client can trust the endpoint.

## Deploy a Model Repository

The **Triton Control Deploy** extension uploads a model repository to
S3-compatible storage and creates a self-deployed Triton instance.

1. Create or edit a Triton model repository under `/workspace`.
2. Right-click the repository root or a single model folder.
3. Run **Triton Control: Deploy Model Repository**.
4. Select an S3 profile, or expand manual S3 settings for a one-off deploy.
5. Confirm the Triton image, model control mode, repository access mode, and
   optional resources.
6. After deployment, Triton Control opens the new instance and its deployment
   logs.

A repository should follow Triton's model layout:

```text
repository-root/
  model-name/
    config.pbtxt
    1/
      model.py
```

The extension reads the model name from `config.pbtxt` and prompts when the
name is missing. Selecting a single model folder uploads it below the chosen
deployment prefix while keeping the deployment's `s3_url` at the parent
repository prefix.

The resulting deployment behaves like one created through **Add Deployment**.
Its in-pod S3 repository connection is fixed at deployment time. Changing that
connection requires deleting and recreating the deployment.
