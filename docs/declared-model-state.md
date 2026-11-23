# Declared startup model state

A Kubernetes-managed development deployment running Triton in explicit mode
allows interactive control when it has one stable pod. Load and Unload go
directly to that owned pod on port 8000,
which the backend must be able to reach.

## Scale a running model

Under **Serving → Instances**, open the instance, enter **2** in **Desired replicas**
and choose **Apply replica count**. On the first scale-up, Triton Control reads the
ready model names and versions directly from the current pod. It captures those
versions from the instance's versioned S3 repository and pins the runtime image.
If S3 objects changed since the original pod loaded them, the replacement pods
use the bytes captured at scale time.

The original pod keeps serving while Kubernetes schedules a replacement with
the snapshot. The replacement must pass local readiness checks for every selected
model version before Kubernetes can retire the original. The backend then applies
the requested replica count. You can close the page during the rollout. If the
replacement is unschedulable or fails readiness, the original stays available.
Choose **Restore interactive control** to cancel a pending scale-up. Later
changes to replica count reuse the frozen template.

### Installation setup

For automatic scaling, set the backend environment variable
`TRITON_MODEL_SCALE_POLICY` to a JSON object. Its `policy` field holds the
[qualified materializer policy](immutable-materialization.md) for a helper image
built with support for `run --selected-objects`. Its `limits` field contains
the numeric capture limits `max_bytes`, `max_file_bytes`, and `deadline_seconds`.
Allow temporary disk space on the backend for the selected model files and
retain the captured object versions while replicas use them.

The helper uses the instance's namespace-local S3 credential Secret and needs
permission to read specific object versions. For a private CA, set
`ca_secret_name` to a Secret in the workload namespace containing `ca.crt`.
When Docker reports only an image config ID, pin the deployment image by digest
or supply a `runtime_manifests` mapping in the policy configuration. Each key
is the observed `docker://sha256:…` ID and its value is the pullable image
reference whose platform manifest you verified references that config ID.

Automatic capture supports the standard managed explicit-mode launcher with
dependencies installed in the runtime image. Repositories requiring custom
configuration rewrites can use an operator-prepared release.

## Use an operator-prepared startup release

A trusted cluster operator prepares a development startup release and a
[versioned materialization plan](immutable-materialization.md). Use
`apiVersion: serving.triton-control/v1alpha1` and `kind: DevelopmentStartupRelease`.
Its `spec` contains `image` pinned by digest, `repository`, `models`, and
`resourcesPerReplica` in the same format as a [ServingRelease](serving-metadata-contracts.md).
An existing ServingRelease can also be used. Select the desired models and versions explicitly. The prepared inventory must contain
exactly those model directories and version directories. Configure a
namespace-local read-only S3 credential Secret, a qualified materializer, and any
required pull credentials before rolling out.

Create an **immutable** ConfigMap in the workload namespace named
`<deployment-name>.startup.<first 32 hex characters of the release digest>`.
Set its `data["startup.json"]` to a JSON object with these fields:

| Field | Value |
| --- | --- |
| `release` | DevelopmentStartupRelease or ServingRelease. Its canonical SHA-256 digest is the startup release digest. |
| `plan` | MaterializationPlan with the versioned source and prepared inventories. |
| `policy` | MaterializerPolicy with the pinned helper and qualification. |
| `storage` | StorageBinding for the named repository and its read-only Secret. |
| `limits` | Explicit materialization budget, including `max_bytes`, `max_file_bytes` and `deadline_seconds`. |

Restrict writes to this namespace's deployments, autoscalers, Secrets and
ConfigMaps to trusted provisioning identities. The ConfigMap is the development
operator's approved input. It is not production evidence admission or a
production release. Keep the owned materialization ConfigMaps and the saved
interactive template available for the deployment's lifetime.

## Roll out and change replicas

Under **Use an operator-prepared startup release**, enter the prepared startup release digest and choose
**Roll out startup state**. The transition replaces the current single pod, so
expect a serving interruption. The Deployment uses a recreate strategy so the
transition works without a spare GPU. The replacement uses pod-local readiness
checks for the frozen materialization receipt and every declared model version.
Wait for the rollout to become stable before applying a replica count above one.
The replica change only updates desired capacity and keeps the frozen template.

To resume Load and Unload, scale back to one, wait for surplus pods to terminate,
and choose **Restore interactive control**. The original pod template is
restored, and interactive actions become available once the restored workload
is stable. An explicitly failed single-replica rollout can also be restored
after surplus pods are gone. The restored template starts normally from its
repository and does not reconstruct ephemeral per-model actions from the old pod.

The instance-scoped API exposes status at `GET /api/instances/{id}/model-state`.
Use `POST .../declare` with `release_digest`, `PUT .../replicas` with
`replicas`, and `POST .../interactive`. A mutation response acknowledges
acceptance; use the status endpoint to observe convergence. Concurrent model
or capacity mutations may return HTTP 409 and can be retried after refreshing.
Deployments with an HPA are read-only for these controls. Production installation
policy continues to block these mutations.

## Upgrade behavior

The additive database migration adds a nullable model-state record. The first
observation of a legacy managed instance records its existing Deployment UID,
desired replicas, capacity owner, and mode without patching the workload or
resetting its replica count. Later observations reconcile that record from the
current Deployment. The development Helm Role adds read-only access to list
HorizontalPodAutoscalers. Refresh equivalent custom RBAC before upgrading.
