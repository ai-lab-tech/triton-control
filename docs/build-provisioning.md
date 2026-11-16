# Builder qualification and provisioning

This package implements S06-A: qualification of one exact container-image builder
and its installation policy. Builds remain disabled by default. S06-B supplies
the [build-context staging API](build-contexts.md). Staging alone does not enable
execution; the S06-C executor must pass the qualification gate before execution.
No image-build execution API, UI, production pipeline or automatic image deployment is introduced.

## Candidate and provenance

The measured candidate is `osscontainertools/kaniko` release `v1.29.1`, published
2026-11-08. Its OCI index resolved to:

```text
ghcr.io/osscontainertools/kaniko@sha256:e7cba1f7e70c01c8ee1cbe622d07990c04dea550430c4c81319d26679226cd37
```

The linux/amd64 child manifest is
`sha256:16e1243b68f685f849e3ebd14e8c4d544ec6f9a62829dde9b09817209fa669eb`.
An index digest binds the platform-specific manifests; qualification also binds
`platform`, and records the image ID actually run by Kubernetes.

Verify against the exact release workflow identity:

```bash
cosign verify \
  --certificate-identity https://github.com/osscontainertools/kaniko/.github/workflows/images.yaml@refs/tags/v1.29.1 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/osscontainertools/kaniko@sha256:e7cba1f7e70c01c8ee1cbe622d07990c04dea550430c4c81319d26679226cd37
```

The fixture executes this verification itself; a caller cannot supply a boolean
claim instead. Cosign verifies certificate trust and transparency-log inclusion.
The digest is a candidate until every qualification check passes. Neither a
floating tag nor a fallback executor is shipped in Helm defaults.

Upstream: [releases](https://github.com/osscontainertools/kaniko/releases/tag/v1.29.1)
and [verification instructions](https://github.com/osscontainertools/kaniko#verifying-signed-kaniko-images).

## Installation policy

Supply a complete JSON `BuildPolicy` to the CLI. `buildQualification.policy` in
Helm accepts the same fields and provisions a dedicated namespace, policy
ConfigMap and egress/ingress NetworkPolicy. This namespace is persistent: build
Jobs reuse it, and Job TTL cleanup removes completed Jobs rather than the namespace.
Leave it null to provision nothing.
S06-A Helm templates reject `enabled: true` because the build service is not yet
installed. Review the rendered manifests before installing them.

Required fields:

| Field | Provisioning requirement |
| --- | --- |
| `executor_image`, `release`, `platform` | Exact measured image, signed release and tested architecture |
| `namespace` | Persistent dedicated build namespace; may already be provisioned by Helm |
| `s3_profile_name`, `staging_prefix` | Existing S3 profile with a dedicated context prefix ending in `/` |
| `allowed_repositories` | Exact registry/repository names, without tags, wildcards or prefix matching |
| `base_pull_secret` | Read-only access to the approved base repositories |
| `push_secret` | Write access only to allowed candidate repositories |
| `runtime_pull_secret` | Separate read-only credentials for serving |
| `provisioning_revision` | Operator-controlled revision of credentials, CA trust, registry ACLs and cluster policy |
| `egress_cidrs`, `egress_ports` | Explicit DNS plus registry/S3 destinations and ports; unrestricted CIDRs are rejected |

Limits default to 10 MiB compressed context, 1000 millicores, 1024 MiB and
300 seconds. Bounds are validated by Pydantic; Jobs set CPU/memory requests and
limits, a deadline, zero retries, and a cleanup TTL. HTTP transport is allowed only
inside the disposable registry fixture. Site registries must use trusted TLS.

The tested pod contract uses UID 0, RuntimeDefault seccomp, no privileged mode,
no privilege escalation, no host mounts and no service-account token. All Linux
capabilities are dropped, then CHOWN and FOWNER are added explicitly. Real tests
showed both are needed to unpack the BusyBox base. This is a **baseline** build
pod policy, not a restricted/non-root serving policy. Additional Dockerfiles or
capabilities require a new qualification; this fixture does not establish safety
for arbitrary hostile Dockerfiles or multitenant builds.

NetworkPolicy requires an enforcing CNI. A successfully created policy object is
not evidence of enforcement. Service DNAT behavior varies: allow the required
service/pod destinations explicitly for the chosen CNI and requalify changes.

## Run the disposable fixture

Prerequisites: Python backend dependencies, kubectl, crane, cosign and openssl;
cluster-admin access for a disposable namespace; an existing writable S3 staging
profile; and access to the pinned helper images. No Docker socket is mounted or
used by the builder. Host tools seed the private fixture base via the registry API.
The helpers are digest-pinned in `scripts/builder_fixture.py`.

```bash
cd triton-backend
python scripts/qualify_builder.py \
  --policy /secure/build-policy.json \
  --database-url "$DATABASE_URL" \
  --evidence /retained-evidence/builder-qualification.json
```

The CLI resolves `s3_profile_name` using the existing S3 repository and credential
decryption helper. The staging prefix must stay within the profile's prefix.
For a fixture run outside the installation, `--s3-config /secure/staging.json`
accepts an owner-only (0600) provisioning file with `profile_name`, `endpoint`,
`bucket`, `access_key`, `secret_key`, and optional `region`, `ca_file`,
`pod_endpoint`. Do not commit that file. Use scoped credentials and destroy the
file after the run. The fixture does not create or modify S3 profiles.

For the disposable registry fixture, `allowed_repositories` contains exactly one
unused Kubernetes Service IP with port 5002 and repository `allowed`, for example
`10.96.250.250:5002/allowed`. Reserve an unused address in the cluster's Service
CIDR. Base pull uses the same IP on port 5001; the blocked-network probe targets
port 5000 on the registry pod. Egress must permit 5001/5002 and the configured S3
port, but never 5000. The fixture automatically creates a temporary namespace named
`builder-qualification-<random-id>` for each run. It never adopts or deletes the
configured installation namespace, so Helm can provision that namespace first.
The report records both namespace names; the policy hash still binds the original
installation policy. A namespace collision fails without deleting existing resources.

The fixture uses random temporary credentials, an authenticated private base,
registry-side repository/method restrictions, and a separate runtime reader.
It stages a bounded allowlisted context in S3, checks the downloaded bytes,
builds/pushes, compares the executor's reported digest against the registry,
exports the resulting filesystem to check exclusion, cancels a slow build,
checks a deadline failure, and probes a forbidden network destination.
This is a fixed qualification context, not the general user-context packager
planned for S06-B. Both the application allowlist and registry ACL are required.

Only objects in the automatically created test namespace and this run's random S3 prefix are
removed. Qualification attempts are appended to `build_qualifications`; the JSON
report remains at `--evidence`. Use a retained evidence volume. A nonzero exit or
any false/missing check means the executor is **not qualified** for that policy.

## Evidence and invalidation

The additive table stores the policy hash, fixture source hash, executor, platform,
checks, evidence path, evidence SHA-256 and timestamp. It stores no credentials.
The fixture source hash covers its CLI, runner and Job renderer. Keep reports
and provisioning revisions under operator control; this is not tamper-proof
storage against database/cluster administrators.

The future build service calls `require_qualified` with the **current** fixture
revision, configured policy and development mode. It rejects disabled builds,
production mode, missing/partial/failed reports, policy changes, platform changes,
and fixture changes. A later failed attempt overrides an earlier success for the
same binding. Changing `enabled` alone does not change the execution policy hash.

Credential rotation, registry ACL, CA, cluster/CNI or staging changes must increment
`provisioning_revision` and trigger qualification. A report from an isolated test
registry does not approve different production registry credentials or ACLs.
Provision the target site's real bindings and qualify them before S06-B enables
builds. Generic build execution and approval workflows are outside this package.
The disposable fixture tests the builder contract in isolation; it does not
validate the persistent namespace's installed secrets or effective network policy.
Installation validation against those real bindings remains part of the build
service integration; an isolated fixture report alone must not enable that service.

## Tests

```bash
python -m unittest tests.test_build_qualification -v
ruff check app/ main.py
mypy app/ tests/ scripts/ main.py
lint-imports
helm lint ../charts/triton-control
```

Unit tests use Arrange–Act–Assert and cover invalid policies, exact repository
scope, credential/image/resource invalidation, safe Job manifests, persisted
failure history and the development-only gate. Generated live reports are local
artifacts and are excluded from Git under `docs/evidence/builder-qualification/`.
Attach reports to the related issue or retain them as CI artifacts; commit only
reusable tests, fixtures and provisioning documentation.
