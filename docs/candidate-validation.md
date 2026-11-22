# Execute an exact-candidate validation

An assigned ML engineer or administrator can run a single-replica validation for a preparation task in a development installation. The operation starts the digest-pinned candidate and the supplied evaluator in one Kubernetes Job. The evaluator connects to the candidate over pod loopback. A passing report is available for subsequent evidence review. Production approval and authenticated evidence admission belong to separate workflows.

## Installation and inputs

Set the Helm value `candidateValidation.namespace` to an existing, dedicated development namespace (or configure `TRITON_VALIDATION_NAMESPACE` on the backend). Use Kubernetes native Job sidecars and a NetworkPolicy-capable CNI. Restrict direct workload and ConfigMap writes in the namespace to the controller and trusted provisioning identities. Helm installs the controller Role and RoleBinding when RBAC is enabled and an ingress-deny policy for candidate Jobs. Configure storage egress, quotas, registry access and GPU scheduling for the installation. Other users must have no pod exec, attach, or workload mutation access there.

Provision the following inputs in that namespace:

1. A versioned S3 repository and its read-only materializer credential Secret. Reuse the qualified materializer policy and materialization plan. The plan binds exact source object versions and prepared bytes. An identity-layout S3 import can supply a plan through `app.services.materialization.imports.from_import`.
2. A task-scoped, read-only input PVC containing regular files named after the 64 hexadecimal characters of their SHA-256 digests. Include the dataset, expected outputs, evaluator Python file, startup configuration, and source code when the manifest declares it. The startup artifact is JSON with the same `models` and `resourcesPerReplica` as the manifest. The helper copies and hashes the selected files into a private volume before starting Triton. Total staged inputs are limited to 10 GiB.
3. An immutable ConfigMap named `validation-inputs-<task UUID without hyphens>`. Its `data["inputs.json"]` contains the following operator-provisioned binding. Use actual inventories, digests and qualification evidence. The binding is reusable across attempts with different criteria, datasets or pinned candidate runtimes. A replacement during Job setup fails the attempt.

| Field | Value |
| --- | --- |
| `evaluator` | Trusted evaluator identity (name, version, pinned image and code reference) |
| `claim` | Name of the validation input PVC |
| `plan` | Serialized `MaterializationPlan` |
| `policy` | Serialized `MaterializerPolicy` with the qualified image and platform |
| `storage` | Serialized `StorageBinding` with bucket, prefix, region and Secret name |

The submitted manifest uses the existing [validation contracts](validation-contracts.md). A suitable existing runtime image pinned by registry digest is sufficient. The evaluator image must contain Python, the validation contract package and its own dependencies. Its verified code must implement `evaluate(manifest) -> ValidationReport`. It reads `/verified/dataset` and `/verified/expected`, measures the actual target, runs inference at `TRITON_VALIDATION_ENDPOINT` and returns the metrics or explicit unsupported/error outcome. After the evaluator returns, the launcher emits the single-line JSON report. The controller retrieves only the final bounded log line; preceding evaluator diagnostics stay out of the operation record.

## Submit and retrieve an attempt

POST the complete ValidationManifest to `/api/preparation-tasks/{task_id}/validations` with a new `Idempotency-Key`. GET the same task URL to retrieve its validation attempts, newest first. Both endpoints use the durable `candidate.validate.v1` operation. Poll the returned status URL. Replaying the request with the same key retrieves the original attempt. A new key creates a separate attempt and retains previous failures and reports.

The worker persists the intended workload identity before creating a suspended Job, records its Kubernetes UID, creates its immutable owned configuration, and then starts the Job. Triton uses the production materializer, model layout and pod-local model-version/receipt readiness probe. Both the candidate and evaluator run without a service-account token. Only the materializer receives the repository credentials.

The controller reads a bounded report from the evaluator container of the completed Job's UID-owned pod, checks the pod identity again after reading and verifies the report against the submitted manifest and the evaluator container’s execution window. A missing metric, wrong subject, replaced pod, container restart or invalid report cannot produce success. A completed evaluator with failing measurements yields a failed operation with the original report. Unsupported targets and evaluator errors also retain their report. A Job deadline or failed input verification yields a failed attempt without a fabricated report.

The result includes `manifestDigest`, the Job identity and, when available, `report` and `reportDigest`. The controller records the outcome before deleting the Job with a UID precondition. Cancellation uses the existing operation cancellation endpoint. A controller restart can resume using the operation record. New installations use the existing operation table; this feature adds no database migration. Pending validation operations block production cutover. Clear completed operations according to the installation's normal database retention policy.
