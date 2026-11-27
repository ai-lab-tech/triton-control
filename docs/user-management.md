# User Management

User management is available only to administrators. Open the account menu at the bottom of the sidebar and select **User Management** to create accounts, approve pending users, assign Triton instances, and
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

- **Dev:** With OIDC disabled and an empty user database, create the first
  local admin in the browser.
- **Prod:** A cluster operator must provision the first local admin before login.
  The sign-in page explains this and blocks sign-up until an admin exists.

After signing in, manage additional accounts in **User Management**.

## Add A Local Email/Password User

With OIDC disabled, choose the flow for your delivery mode:

- **Disabled:** The user selects **Register with password** on the sign-in page.
  Verify their identity, then select **Approve** in **User Management**.
- **SMTP:** In **User Management → Add user**, select **Invite user**. The user
  sets their password through the one-time link sent by email.
- **Manual link:** Use **Invite user** and share the one-time link directly
  with the intended user.

For an existing inactive account without a password, use **Send invitation**
with SMTP or manual links enabled.

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
2. Open **User Management**.
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

1. Open **User Management** as an admin.
2. Select **Approve** on the pending user’s row to activate the account with its current role.
3. Select the user’s name to change the role or assign Triton instances.

Approval sets the user active. Instance assignment still controls what a
non-admin user can see and operate on.

## Assign Instances

In **User Management**, select the user’s name to open account details. Use
**Assigned instances** to add or remove an instance.
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

At least one active administrator must remain. To hand over administration,
promote a second active account before demoting or deleting the current admin.
Pending invitations do not count toward this requirement.

Admins can delete users from the Users page. Deleting a user removes that local
Triton Control account record. For OIDC users, it does not delete the identity
from the external OIDC provider.

## ML Engineering Responsibility

Each installation serves one use case and has one engineering group. In **User
Management** (`/users`), the overview shows **ML engineer** beside the names of
engineering group members. Select a user’s name and toggle **ML engineer** in
account details to assign or revoke that responsibility. There is no team creation, naming or selection screen. Existing
`/teams` bookmarks redirect to user management.

Roles remain `admin`, `member` and `viewer`. Engineering membership never grants
instance access, artifact access, workspace ownership or admin permissions. Only
active member/admin accounts are eligible for future engineering task routing;
a viewer can hold responsibility but is not eligible until their access role permits it.
Members and admins submit models through **Development → Engineering queue → Submit model**.
Eligible engineers can claim a submitted task from its receipt.

On first use, the installation group is initialized once. Active memberships from
active legacy teams are copied into it, including inactive accounts whose eligibility
remains suspended. Revoked memberships and memberships in inactive teams are not
reactivated. Original teams, memberships and audit history are preserved. Later
initialization calls do not import again, so revocations stay revoked. Migration
and membership changes retain audit events; roles and resource assignments are unchanged.

Inactive users cannot be newly assigned. An admin can still revoke their existing
responsibility. Changes are idempotent and retained in the audit history.

### Local access groups

In the shared Prod Hub, Platform/Ops can use **User Management → Group Management**
to assign viewers and instances to a named group. A viewer's **View access sources** dialog
shows its direct assignments and the groups that grant it access. Changes to
group membership or instance assignments apply on subsequent requests. A user
keeps access while any group or direct assignment still grants it.

Group grants apply only to viewers in production mode. Dev spokes continue to
use direct instance assignments. Both local and OIDC viewers can join a group.
Engineering responsibility remains independently managed.

### Team API

All endpoints require a currently active administrator, including reads.
The UI uses `PUT /api/teams/engineering` to initialize/resolve the installation
group, then its membership endpoints. Generic team endpoints remain for legacy
compatibility; they are not exposed in the user interface.

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

Administrators can also configure model size and capture budgets in
**Settings → Model preparation**. See [preparation settings](model-preparation-settings.md).
