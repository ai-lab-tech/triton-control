# User Management

User management is available only to administrators. Open **Users** in Triton
Control to create accounts, approve pending users, assign Triton instances, and
change roles.

## Roles

| Role     | Scope              | Current Behavior                                                                                                                                                    |
| -------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `admin`  | Global             | Can see all Triton instances, add/delete instances, manage users, manage OIDC settings, and perform instance/model/S3 actions.                                       |
| `member` | Assigned instances | Can add instances and use assigned instances, including metrics, model inspection, inference, model load/unload, instance connection updates, and S3 write workflows. |
| `viewer` | Assigned instances | Read-only access to assigned instances. Can inspect dashboard, health, metrics, model state/config, inference, and S3 files, but cannot add or change configuration. |

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
7. Save the user.

If a password is provided, the user can sign in with email/password. Passwords
must have 12-128 characters, at least one uppercase letter, one lowercase
letter, one digit, one special character, and must not contain whitespace.

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

## Pending And Approved Users

The Users page shows each account as `active` or `pending`.

Pending users cannot use assigned workflows until an admin approves them. To
approve a user:

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
