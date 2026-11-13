# User Management

User management is available only to administrators. Open **Users** in Triton
Control to create accounts, approve pending users, assign Triton instances, and
change roles.

## Roles

| Role     | Scope              | Current Behavior                                                                                                                                                      |
| -------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `admin`  | Global             | Can see all Triton instances, add/delete instances, manage users, manage OIDC settings, and perform instance/model/S3 actions.                                        |
| `member` | Assigned instances | Can add instances and use assigned instances, including metrics, model inspection, inference, model load/unload, instance connection updates, and S3 write workflows. |
| `viewer` | Assigned instances | Read-only access to assigned instances. Can inspect dashboard, health, metrics, model state/config, inference, and S3 files, but cannot add or change configuration.  |

Instance visibility is controlled by the user's assigned instances unless the
user is an admin. If a non-admin user has no assigned instances, they can sign
in but will not see any Triton instances.

Changing a user's role from the Users page also activates a pending account.

## First Admin

On a fresh local-auth deployment, the first local admin can be created through
the initial setup flow. This flow is available only when OIDC is disabled and no
users exist.

The first admin needs:

- email
- password with 12-128 characters, at least one uppercase letter, one lowercase
  letter, one digit, one special character, and no whitespace
- optional display name

After the first admin exists, additional users are managed from the **Users**
page.

## Add A Local Email/Password User

Local users are supported only when OIDC is disabled. When OIDC is enabled,
local email/password creation is blocked and users must authenticate with OIDC.

To add a local user:

1. Sign in as an admin.
2. Open **Users**.
3. Select **Add user**.
4. Enter full name and email.
5. Select `admin`, `member`, or `viewer`.
6. Assign one or more Triton instances for non-admin users.
7. Select the available account-setup option and submit the form.

The available account setup depends on email delivery:

- With `manual-link` or `smtp`, **Invite user** creates an inactive account and
  issues a single-use activation link. SMTP sends it automatically;
  `manual-link` shows it once for secure out-of-band transfer.
- With `manual-link` or `smtp`, **Create inactive account** creates an account
  that cannot sign in and cannot be claimed through public self-registration.
- With delivery `disabled`, the form uses the legacy **Add user** behavior. It
  creates a passwordless inactive local account. Matching public
  self-registration sets its initial password, activates it, and preserves the
  role and instances selected by the administrator.

Administrators never assign or transmit a user's initial password. Passwords
chosen during registration, invitation activation, or reset must have 12-128
characters, at least one uppercase letter, one lowercase letter, one digit, one
special character, and must not contain whitespace.

While delivery is `manual-link` or `smtp`, admins can reissue or cancel a
pending invitation and issue a reset for an active local account. These
lifecycle actions are disabled when delivery is `disabled`. Disable delivery
only after canceling any pending invitations that should no longer be usable;
an already-issued bearer link otherwise remains valid until it expires, is
consumed, is superseded, or is revoked through the protected API. OIDC
lifecycle operations remain with the configured identity provider.

SMTP invitation and reset operations can take several seconds. The affected
button shows progress and duplicate submissions remain disabled until the SMTP
operation completes.

Password validation uses this practical baseline:

```regex
^(?=.{12,128}$)(?!.*\s)(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).+$
```

The email format check is intentionally simple:

```regex
^[^\s@]+@[^\s@]+\.[^\s@]+$
```

Local users can also self-register from the public auth endpoint. A brand-new
self-registered local user is created as `viewer`, has no assigned instances,
and is pending admin approval. An admin must approve the user and assign
instances before the account is useful.

## Add An OIDC User

OIDC users are supported only when OIDC is enabled. The available OIDC settings
depend on `OIDC_CONFIG_SOURCE`:

- `OIDC_CONFIG_SOURCE=db`: admins manage OIDC settings from the Settings page.
- `OIDC_CONFIG_SOURCE=env`: OIDC settings are managed by environment variables,
  Helm values, or GitOps manifests and are read-only in the UI.

To add an OIDC user:

1. Sign in as an admin.
2. Open **Users**.
3. Select **Add user**.
4. Enter the user's full name and email.
5. Select a role.
6. Assign one or more Triton instances for non-admin users.
7. Save the user.

Triton Control matches the account by email during the user's first OIDC login
and then stores the OIDC subject after the user signs in. Admins do not need to
enter the OIDC `sub` claim manually.

When `OIDC_CONFIG_SOURCE=env`, you can optionally set `OIDC_ADMIN_EMAILS` to a
comma-separated allowlist. If no admin exists yet, a matching OIDC user can be
bootstrapped as the first active admin. Without that allowlist, OIDC users are
created as non-admin users and need normal admin management.

## Account Status

The Users page distinguishes `active`, `inactive`, `invitation pending`, and
`approval pending` accounts.

Self-registered `approval pending` users cannot use assigned workflows until an
admin approves them. To approve a user:

1. Open **Users** as an admin.
2. Choose the intended role.
3. Select **Approve**.
4. Assign Triton instances if the role is `member` or `viewer`.

Approval sets the user active. Instance assignment still controls what a
non-admin user can see and operate on.

## Assign Instances

Admins can add or remove assigned instances for each user from the Users page.
Assignments are stored by Triton instance name.

Assigned users can open that instance and use its available workflows.
`member` and `admin` can perform write actions; `viewer` is read-only.

- dashboard and health visibility
- CPU, RAM, and GPU metrics when a metrics endpoint is configured
- model repository view
- model config view
- inference
- model load/unload when Triton allows explicit model control (`member`/`admin`)
- S3 browser access and file download
- S3 folder creation when S3 is configured (`member`/`admin`)
- S3 file and folder deletion when S3 is configured (`member`/`admin`)
- `.py` / `.pbtxt` editing and file or folder upload when S3 is configured (`member`/`admin`)
- Triton and S3 connection updates (`member`/`admin`)
- instance creation (`member`/`admin`)
- instance deletion (`admin` only)

## Delete Users

Admins can delete users from the Users page. Deleting a user removes that local
Triton Control account record. For OIDC users, it does not delete the identity
from the external OIDC provider.

## Engineering Teams

Admins can open **Engineering Teams** from the account menu (`/teams`) to create,
rename, activate or deactivate teams and add or revoke memberships. Team names
are unique, lowercase identifiers of up to 64 letters, digits, hyphens or
underscores, starting with a letter or digit. Leading/trailing whitespace is
removed and uppercase letters are normalized.

A user may belong to multiple teams. Roles remain `admin`, `member` and `viewer`.
Membership organizes engineering responsibility; it never assigns an instance,
grants artifact access, changes a role or shares another user's workspace.
Only active `member`/`admin` accounts with an active membership in an active team
are eligible for future engineering task routing. Task submission and claiming
are separate features; this page does not create tasks.

Inactive accounts cannot be added. Deactivating a team keeps its memberships
and history but suspends eligibility. Reactivation restores eligibility for
otherwise eligible members; explicitly revoked memberships stay revoked until
an admin adds them again. Membership changes are idempotent, including repeated
revocation requests. Repeated no-op requests do not create duplicate history.

The page shows current and revoked memberships plus the latest 100 changes.
History records the actor, affected user, time and before/after team settings.
There is no team/history delete endpoint. Account deletion removes that user's
current membership rows but retains historical numeric actor/subject identities.
Application history is retained in the database; it is not a claim of tamper-proof
storage against a database administrator.

### Team API

All endpoints require a currently active administrator, including reads.

| Method and path | Behavior |
| --- | --- |
| `GET /api/teams` | List active and inactive teams. |
| `POST /api/teams` | Create `{name, is_active}`; returns 201. |
| `PUT /api/teams/{id}` | Replace the team's name/active state; UUID identity stays stable. |
| `GET /api/teams/{id}/members` | Current/revoked memberships and current eligibility. |
| `PUT /api/teams/{id}/members/{user_id}` | Add or restore membership. |
| `DELETE /api/teams/{id}/members/{user_id}` | Revoke membership while preserving its history. |
| `GET /api/teams/{id}/events?limit=100` | Latest history; limit must be 1–100. |

Invalid input returns 422, non-admin access 403, unknown teams/users 404, and
duplicate names or additions to inactive teams/accounts 409.

### Upgrade and Verification

Startup adds `teams`, `user_teams` and `team_events` without modifying existing
roles or assignments. Table creation uses a transaction-scoped PostgreSQL
advisory lock so concurrent workers cannot race on check-and-create DDL.
Database uniqueness enforces team names and membership pairs. Team changes and
membership changes serialize on the team row and commit their history in the
same transaction. No external services or Kubernetes workloads are added.

Unit/API tests use Arrange–Act–Assert sections. PostgreSQL tests run in isolated,
randomly named schemas and drop only their own schemas afterward:

```bash
cd triton-backend
python -m unittest tests.test_teams -v
TEAMS_TEST_DATABASE_URL=postgresql://teams_test:teams_test@localhost:5432/teams_test \
  python -m unittest tests.test_teams_postgres -v
```

Use a dedicated test database whose user can create schemas. The CI
`teams-postgres` job provisions one and exercises complete startup, uniqueness
races, repeated membership requests and concurrent deactivation.
