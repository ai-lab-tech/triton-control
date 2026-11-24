# Installation capabilities

`TRITON_CONTROL_MODE` is a process-start configuration: `development` (default) or
`production`. Any other value fails startup. Restart to change it; there is no
browser, database, role, or administrator override. Use separate installations
and namespaces for development and production.

The authenticated `GET /api/capabilities` returns the installation ceiling
intersected with the caller's role. Existing resource ownership checks still apply.
Production permits serving inventory, health, model configuration, logs and artifact
reads, identity administration, and admin rotation/revocation of existing credentials
without scope changes. Triton's own serving endpoints remain independent.

Production disables Control's interactive inference, code-server, MLflow, Argo,
preparation/build/benchmark jobs, model/artifact writes, direct deployment changes,
new credential scopes and trust/source-policy changes. This includes administrators,
delegated tokens, HTTP aliases, WebSockets, direct service callers and background
workers. New API endpoints are denied until explicitly reviewed in
`app/api/installation_policy.py`. Independent approval/execution is a later contract;
`production_changes` remains false. Local admission diagnostics remain available.

The frontend loads capabilities from the backend, displays the installation mode,
hides unavailable actions and guards deep links. A failed policy request clears
previous permissions and shows an explanation. UI controls are not the enforcement
boundary.

## Helm

Apply `charts/triton-control/values-production.yaml` together with your normal
installation values. The preset sets `app.installationMode: production`, which
survives overrides of `app.env`. If a legacy `TRITON_CONTROL_MODE` entry remains
in `app.env`, it must agree with `app.installationMode`. Duplicate mode entries,
`valueFrom`, invalid modes, enabled Argo, build qualification policies and
development tool environment overrides are rejected. The production template omits development tool configuration;
MLflow cannot be installed through the production API.

Production startup reconciles the operator-owned release-source file or
environment configuration. The production API can read registered sources.
Changing the source configuration does not enable workload execution.

## Cutover

Before switching an existing installation, stop development admission and workers,
finish or cancel jobs, and remove development workspaces, MLflow, workflow credentials,
build push credentials and build policy bindings using the development installation.
Remove development executors and their ingress, tokens, network access and external
storage permissions. Provision identity/OIDC through controlled configuration;
unauthenticated first-admin registration is disabled in production.

Startup checks persistent development resources, unfinished jobs, uncleaned attempts
and build credentials. In Kubernetes it also inventories the configured installation
namespaces for development workloads, services, service accounts, secrets and Argo
workflows. Inventory failure blocks startup. Set an explicit `POD_NAMESPACE` (the chart
sets it automatically), `TRITON_CONTROL_NAMESPACE` or `KUBERNETES_NAMESPACE` when using
an external kubeconfig. RBAC must permit this read-only inventory. The production chart grants only read
permissions and omits pod execution and resource mutation privileges.

These checks never delete resources. Resolve reported blockers before retrying;
existing serving resources remain intact. The inventory covers Control's known
resources in configured namespaces, not arbitrary external executors or credentials.
Operators must separately verify external access revocation and namespace/network
isolation. No database migration is required.

## Verification

Policy tests use Arrange–Act–Assert and cover role ceilings, every denied route and
alias, WebSockets, delegated tokens, service calls, startup and credential scope.
`tests.test_installation_policy_postgres` uses `INSTALLATION_TEST_DATABASE_URL` and
isolated schemas, including concurrent revocation/rotation.
`tests.test_installation_policy_kubernetes` uses `INSTALLATION_TEST_KUBERNETES=1` and
`KUBERNETES_KUBECONFIG_PATH`, creating disposable namespaces to verify cutover leaves
serving services intact and refuses orphan development access.

## Rotate instance artifact-read credentials

After rotating an existing S3 profile, an administrator can refresh the credentials
copied into an existing instance with `PUT /api/instances/{id}/s3` and JSON
`{"enabled": true, "s3_profile_id": 1}`. In production, send only those two fields.
The profile must match the existing endpoint, bucket, region, TLS trust and address
style, and its prefix must contain the instance's existing repository prefix.
Only the copied access key and secret are updated; storage settings remain unchanged.
This refreshes Control's artifact browsing and health checks, not credentials mounted
in external serving workloads, which require a separately controlled rotation.
