# Security

This page summarizes the security-sensitive configuration areas.

## Secrets

Replace local defaults before using the app outside local development:

- `SESSION_SECRET`
- `JWT_SECRET`
- `S3_SECRET_ENCRYPTION_KEY`
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

## TLS

Backend TLS setup is documented in:

```text
triton-backend/tls/READEME.md
```

Frontend HTTPS dev-server TLS paths are configured in:

```text
triton-frontend/angular.json
```

Triton and S3 instance connections have their own SSL verification settings in
the instance detail page. The endpoint scheme controls whether the backend uses
HTTP or HTTPS. The SSL flag controls whether the backend verifies the remote
certificate. When SSL verification is enabled, use the system trust store or
paste a PEM CA certificate for private or self-signed certificates.

See [User Guide](user-guide.md#https-triton-connections) for Triton and S3
connection examples.

## S3 Credentials

S3 secret keys are encrypted by the backend before storage. This includes
instance S3 settings and reusable S3 deployment profiles. Configure a strong:

```text
S3_SECRET_ENCRYPTION_KEY
```

Do not reuse local development secrets in shared or production environments.
Profiles are user-owned and visible only to the owning signed-in user through
the S3 profile API.

## Reverse Proxy Headers

The combined Docker image uses Nginx in front of FastAPI. Proxy headers are
defined in:

```text
docker/proxy_headers.conf
```

The `Host` header forwarded to the backend is fixed instead of passing through a
client-controlled host value.
