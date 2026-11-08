# Security

This page summarizes the security-sensitive configuration areas.

## Secrets

Replace local defaults before using the app outside local development:

- `SESSION_SECRET`
- `JWT_SECRET`
- `EMAIL_SECRET_ENCRYPTION_KEY`
- SMTP credentials
- `REGISTRY_SECRET_ENCRYPTION_KEY`
- `POSTGRES_PASSWORD`
- OIDC client secrets
- S3 credentials

## OIDC

OIDC can be configured through environment variables or through the application
settings flow, depending on `OIDC_CONFIG_SOURCE`.

Use `OIDC_CONFIG_SOURCE=env` for GitOps-managed deployments where Flux,
Argo CD, Helm values, or Kubernetes Secrets should be the source of truth.
Use `OIDC_CONFIG_SOURCE=db` when administrators should manage OIDC settings
through the application UI.

Important settings include:

- issuer URL
- client ID
- client secret
- redirect URI
- scopes
- TLS verification and CA bundle

See [Configuration](configuration.md) for the full environment variable
reference.

See [User Management](user-management.md) for role behavior, local
email/password users, OIDC users, approvals, and instance assignment.

For local email/password mode, enforce strong passwords:

- 12-128 characters
- at least one uppercase letter
- at least one lowercase letter
- at least one digit
- at least one special character
- no whitespace

## Invitations and Password Recovery

Email lifecycle features apply only to local accounts. OIDC account
invitations and recovery remain the identity provider's responsibility.
Invitation and reset links are bearer credentials: they are random,
purpose-specific, expiring, single-use, revocable, and stored only as hashes.
A completed password reset also invalidates existing local tokens through the
account credential version.

Links are built only from `EMAIL_PUBLIC_APP_URL`, never from a request host.
Administrators must verify a person's identity before transferring a manual
link or initiating recovery. Copy a manual link once and send it through an
approved confidential channel; do not place it in tickets, logs, or chat
archives. Public forgot-password responses are deliberately identical for
known, unknown, inactive, and OIDC addresses and are rate limited.

Disabling email delivery prevents new lifecycle operations in User Management,
but it does not invalidate bearer links that were already issued. Before
switching to `disabled`, cancel pending invitations that must no longer be
usable. After delivery is disabled, the User Management reissue and cancel
controls are unavailable; protected API revocation remains available for
emergency invalidation.

SMTP defaults to certificate-validated STARTTLS. Implicit TLS is supported.
Plain SMTP requires an explicit insecure opt-in and should be limited to a
trusted isolated network. Stored SMTP passwords are encrypted and API responses
only expose whether a credential is configured.

## TLS

Backend TLS setup is documented in:

```text
triton-backend/tls/READEME.md
```

Frontend HTTPS dev-server TLS paths are configured in:

```text
triton-frontend/angular.json
```

Triton connections have SSL verification settings in the instance detail page.
S3 connections use the selected profile's endpoint and CA certificate, configured
by an administrator in **S3 Profiles**. Profile-based instance S3 connections
enable certificate verification. The endpoint scheme controls whether the backend uses
HTTP or HTTPS. The SSL flag controls whether the backend verifies the remote
certificate. When SSL verification is enabled, use the system trust store or
paste a PEM CA certificate for private or self-signed certificates.

See [User Guide](user-guide.md#https-triton-connections) for Triton and S3
connection examples.

## S3 Credentials

Only administrators can create, edit, or delete S3 profiles. All profiles are
global and available to administrators and members, including legacy profiles.
The stored creator does not restrict profile visibility. Browser profile responses omit secret keys.
Deployment and instance connection requests select profiles instead of supplying
credentials. The authenticated workspace profile endpoint returns credentials
needed for direct S3 operations.

The current S3 `encrypt_secret`/`decrypt_secret` helpers are identity functions:
S3 secrets are not encrypted at rest by those helpers. Protect database access
and backups accordingly. This differs from central registry credentials.

## Central Registry Credentials

Only administrators can read configuration status or replace/remove the central
image pull secret through `/api/settings/registry`. Responses never include its
contents. New managed workloads receive the secret server-side; request-level
registry credential overrides are rejected.

The registry secret is encrypted with Fernet using a key derived from
`REGISTRY_SECRET_ENCRYPTION_KEY`, falling back to `SESSION_SECRET`. A stable key
is required before saving credentials. Preserve it across restarts and re-save
credentials after rotating it. Removing the central secret does not remove
Secrets already created for existing workloads.

## Reverse Proxy Headers

The combined Docker image uses Nginx in front of FastAPI. Proxy headers are
defined in:

```text
docker/proxy_headers.conf
```

The `Host` header forwarded to the backend is fixed instead of passing through a
client-controlled host value.

## Managed Workspace S3 Access

The native code-server S3 browser authenticates to its workspace-scoped profile
endpoint using `TRITON_CONTROL_PROFILE_TOKEN`, injected from a Kubernetes Secret.
The backend verifies the workspace owner and account status before returning
profiles. This token is not accepted as general application API authentication.
Processes in an active member/admin workspace can retrieve all global S3 profiles
and use their credentials for direct storage access.

The extension uses S3 credentials in memory and does not persist them to editor
settings or `.aws/credentials`. HTTPS verification uses the selected profile's
CA certificate dynamically. Disconnecting removes the Explorer connection;
it does not revoke the workspace token or the underlying S3 credentials.
