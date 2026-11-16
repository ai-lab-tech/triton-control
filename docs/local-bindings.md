# Local credential bindings and capacity admission

S08-B adds installation-local authorization and credential resolution for the
S08-A serving contracts. It does not deploy workloads or approve changes.
The initial public delivery is the development-side resolver and admin API;
production reconciliation and independent approval remain separate work.

## Four independent purposes

| Policy section | Provider | Scope |
| --- | --- | --- |
| `source_read` | Existing S3 profile or admin-managed Git reader | Exact configured metadata source location; Git repository or S3 bucket/prefix |
| `model_storage` | Existing S3 profile | Model repository bucket/prefix |
| `registry_pull` | Admin-managed registry credential with `permission: pull` | Registry host and repository prefix |
| `build_push` | Separate admin-managed registry credential with `permission: push` | Registry host and repository prefix, development only |

Logical names follow the serving contract: lowercase letters, digits and hyphens,
maximum 63 characters. They are not Kubernetes Secret names, arbitrary profile
paths or Vault paths. There is no Vault integration or automatic fallback to
another credential. Missing configuration denies access. Source credentials
cannot be used for model storage by changing a purpose argument; each policy
section explicitly grants that use. Operators can deliberately map separate S3
bindings to the same existing profile if its server-side permissions permit it.

Git credentials declare `read_only: true` and are scoped to one HTTPS repository.
Only the source-read resolver can return them. Administrators must provision an
actually read-only token at the Git host; the declaration cannot downgrade an
upstream write-capable token. Registry pull and push permissions similarly need
matching upstream ACLs. This feature does not probe Git, S3 or registry servers.
Future adapters must preserve TLS verification, reject credential forwarding
across redirects and use only their permitted operations and scoped paths.

## Administration API

All configuration endpoints require a currently active administrator, looked up
again in the database. A stale token claiming `admin` does not grant admin access.
Delegated workspace/workflow tokens cannot use these endpoints or resolvers.

1. Configure S3 profiles through the existing settings API.
2. Create purpose-specific Git/registry credentials with
   `PUT /api/local-bindings/credentials/{logical-name}`.
3. Read `GET /api/local-bindings/policy` and submit its `revision` as
   `expected_revision` to `PUT /api/local-bindings/policy`.
4. Validate a proposed source/target/release with
   `POST /api/local-bindings/admissions`.

First policy creation uses revision 0; concurrent/stale writes return 409. Each
successful update increments the local revision. The configuration DTO contains
only non-secret bindings, source/target grants and capacity/hardware policy.
See [the complete example](examples/local-binding-policy.json). Replace its
profile IDs, endpoint/bucket scopes, user IDs and hardware with local values;
create the referenced registry credentials before saving it.

A Git credential request has this shape (password is supplied only on write):

```json
{
  "provider": "git",
  "repository": "https://git.example.invalid/team/releases",
  "username": "reader",
  "password": "<read-only token supplied by administrator>",
  "read_only": true
}
```

A registry credential request has this shape:

```json
{
  "provider": "registry",
  "registry": "registry.example.invalid",
  "repository_prefix": "synthetic",
  "username": "reader",
  "password": "<registry token supplied by administrator>",
  "permission": "pull"
}
```

Use a different name/token with `permission: push` for build publishing. New Git
and registry passwords use the existing registry Fernet encryption mechanism:
configure a stable `REGISTRY_SECRET_ENCRYPTION_KEY` or `SESSION_SECRET`, consistently
on all backend replicas, before creating them. Keep the key across upgrades and
backups. S3 credentials keep their existing profile storage implementation; this
change does not migrate or improve that existing storage encryption.

Credential GET/PUT responses return only `name`, `provider`, `configured`.
DELETE revokes the named credential; dangling bindings subsequently deny access.
Rotating a password under the same scope is supported. Changing provider,
username, repository scope or permission requires explicit deletion/recreation.
There is no secret export endpoint. Input validation errors use the existing
sanitized handler and do not echo credential values.

## Admission and authorization

The admission request contains the S08-A `source`, `target` and `release`
documents, plus a local `hardware` name. The selected release digest must match
the canonical, default-expanded release content exactly. Admission uses the
shared Pydantic serving contracts and their semantic checks. A file/CI consumer
must still use the bounded shared JSON/YAML loader before calling this service.

Local source grants pin the exact Git repository/ref/path or S3 bucket/key and
its read binding. A target grant specifies the sources allowed to manage it.
Every declared `allowedTargets` entry must be locally authorized, not just the
one currently being admitted. Members require explicit user grants on both the
source and target; viewers and inactive users are rejected. Admins may diagnose
any locally configured association but cannot bypass binding scope or capacity.
Team membership alone does not grant access to secrets or targets.

S3 bindings pin profile ID, endpoint, bucket and prefix. Profile deletion or scope
changes revoke resolution until an admin updates the binding. Key/prefix checks
use path segment boundaries, so `models-other` cannot match `models`. S3 model
repositories are supported initially; HTTPS model repositories fail closed until
a scoped provider is implemented. A target's pull credential must also cover an
optional materializer image. Registry host and repository prefix are enforced
for both runtime pulls and development build destinations.

Capacity is a static **per-target** envelope, not a shared cluster reservation:

- CPU, memory and GPU limits are checked per replica.
- Total limits cover `replicas + rollout.maxSurge`, using the release's resource
  **limits**, conservatively including maximum burst capacity.
- Maximum desired replica count is checked separately.
- CPU uses exact decimal arithmetic and memory uses the shared Kubernetes
  quantity parser; no floating-point rounding.
- The selected hardware profile must be allowed on the target, with an explicit
  architecture and GPU ceiling/accelerator where applicable.

Hardware profiles are operator declarations, not discoveries or assertions about
image architecture, validation evidence, currently free GPUs or schedulability.
Downstream executors must enforce node placement and verify image/evidence
compatibility. During a mixed old/new release rollout, the executor must also
account for old Pods with different resource limits. Cluster-wide quotas and
atomic reservations are outside this static per-target check.

`TRITON_CONTROL_MODE` defaults to `development`. Unknown values deny resolution;
production mode rejects build-push resolution. Admission diagnostics do not
activate production capabilities or bypass independent approval.

## Consumer interfaces and safe audit records

`app.services.local_bindings` exposes:

- `resolve_source_read(session, claims, source)`: validate current source access
  and resolve a scoped credential before an adapter fetches metadata.
- `admit(session, claims, request)`: return a credential-free diagnostic result.
- `resolve_for_execution(session, claims, request)`: repeat authorization,
  resource and scope checks, returning in-process credential objects.
- `resolve_build_push(session, claims, binding_name, image_repository)`: authorize
  a development build push to an allowlisted registry/repository, without tags,
  digests, URIs, percent-encoded paths or traversal.

Credential objects hide username/password in their representation and use
`SecretStr`. Consumers explicitly unwrap only at the provider boundary. They must
not serialize these objects into metadata, logs, build contexts, Jobs or audit
snapshots. Workloads should receive separately provisioned scoped Secrets, not
credential values embedded in manifests or command arguments.

An admission response records the policy revision/hash, selected release hash,
source/target IDs, hardware profile, peak replica count and logical binding names.
It can be retained by a downstream operation as safe diagnostic evidence, but
is **not** a reusable execution or approval token. It intentionally contains no
secret-derived fingerprint. Call the resolver with a fresh session immediately
before every provider operation/retry; a previous successful diagnostic never
allows a deleted credential, revoked user or changed policy to be reused.
The caller must additionally enforce source enablement, authoritative current
metadata, exact fetched-byte hashes, approval and operation fencing as appropriate.

The existing direct deployment, S3 profile and central registry settings flows
keep their current behavior. Wiring source adapters, release UI, reconciliation
and build execution to these interfaces belongs to their downstream tickets.
There is no new UI in S08-B; S08-C covers configuration experience.

## Upgrade and verification

Startup adds `local_binding_policy` and `local_credentials` tables through the
existing serialized `create_tables` path. No existing profile or credential is
rewritten, no binding is inferred, and unconfigured admission denies access.
Older binaries can continue to run with the additional tables; their existing
flows do not use this policy. Do not treat a rollback to an older binary as
preserving enforcement in new consumers. Back up the database and encryption key.

Run:

```sh
python -m unittest tests.test_local_bindings -v
LOCAL_BINDINGS_TEST_DATABASE_URL=postgresql://... python -m unittest tests.test_local_bindings_postgres -v
```

The PostgreSQL suite uses isolated disposable schemas, repeats the behavior/API
suite, verifies competing first writes and updates, and concurrent startup with
existing profiles/credentials. It runs in Backend CI. This story makes no
Kubernetes/provider calls; workload/provider verification belongs to consumers
that actually fetch or apply resources. Required backend lint, typing and
architecture checks also apply.

Local verification on 2026-11-16: 53 focused SQLite/PostgreSQL tests passed; full
backend discovery ran 1,211 tests successfully (170 environment-dependent skips),
with 83.6% coverage. Mypy (297 files), Ruff, all four architecture contracts and
Bandit passed. Frontend API generation, Angular build, ESLint and Prettier passed
with the updated OpenAPI copies. No live installation was changed.
