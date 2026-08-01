# Configuration

Triton Control reads backend configuration from environment variables. For
local development, copy `triton-backend/.env.example` to `triton-backend/.env`.
For Docker, Kubernetes, Flux, Argo CD, or another GitOps flow, provide the same
values through Compose environment entries, Helm `app.env`, Helm
`app.secretEnv`, or a Kubernetes Secret referenced by `app.existingSecret` or
`app.envFrom`.

## OIDC Configuration Source

`OIDC_CONFIG_SOURCE` controls where OIDC settings are managed.

OIDC login is independent of the deployment target and has been tested with
Microsoft Entra ID, Keycloak, and Dex. For Kubernetes GitOps deployments,
Triton Control has been tested with Argo CD managing the Helm chart and
environment values.

| Value | Behavior | Typical Use |
| --- | --- | --- |
| `db` | OIDC settings are stored in the database and managed through the application settings UI. Runtime `OIDC_REDIRECT_URI` and `APP_BASE_URL` can still override stored redirect values. | Local setup or UI-managed deployments. |
| `env` | OIDC settings come from environment variables and are read-only through the API/UI. | GitOps deployments with Flux, Argo CD, Helm values, sealed secrets, or external secret operators. |

Use `OIDC_CONFIG_SOURCE=env` when deployment manifests are the source of truth.
This prevents UI changes from drifting away from the Git-managed configuration.

In `db` mode, the Settings page does not display the stored OIDC client secret
after it has been saved. If a secret already exists, the field shows that a
stored secret is configured. Leave the field empty to keep the stored secret, or
enter a new value to replace it.

## Backend Environment Variables

| Variable | Required | Default / Example | Description |
| --- | --- | --- | --- |
| `BACKEND_VERBOSE` | No | `false` | Enables info-level backend logs and Uvicorn access logs when true. |
| `LOG_LEVEL` | No | `WARNING` | Explicit Python logging level, for example `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `LOG_FORMAT` | No | backend default format | Optional Python logging format string. |
| `DATABASE_URL` | Yes | `postgresql://triton:tritonpw@localhost:5433/triton_backend` | SQLAlchemy/PostgreSQL connection URL. Add `sslmode=verify-full&sslrootcert=/path/to/rootCA.pem` when using verified PostgreSQL TLS. |
| `DATABASE_ECHO` | No | `false` | Enables SQLAlchemy SQL statement logging when true. Use only for debugging. |
| `TRITON_SERVER_URL` | No | `http://localhost:8888` | Default Triton server URL used by local backend workflows. |
| `TRITON_CONNECTION_VALIDATION_TIMEOUT_SECONDS` | No | `5` | Timeout in seconds for validating a Triton URL when creating or editing an instance. Keep low enough that unreachable URLs fail quickly in the UI. |
| `TRITON_HEALTH_REFRESH_INTERVAL_SECONDS` | No | `10` | Interval in seconds for the background poller that refreshes instance health, metrics, and model repository snapshots in the database. |
| `TRITON_HTTP_TRUST_ENV` | No | `false` | Allows direct Triton HTTP clients to honor `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` when true. Keep false for local Minikube/hosts-file ingress names unless a proxy is required. |
| `KUBERNETES_ENABLED` | No | auto-detected | Optional override for whether Kubernetes-backed features should be considered available. If unset, the backend detects in-cluster ServiceAccount credentials. |
| `KUBERNETES_KUBECONFIG_PATH` | No | unset | Development/testing kubeconfig path for backend runs outside Kubernetes. Leave unset for in-cluster deployments. |
| `TRITON_DEPLOY_CODE_SERVER_EXTENSION_DIR` | No | auto-detected | Optional source directory for the bundled Triton Deploy extension used by Development workspaces. Set this for local backend runs if the repository extension directory is not found automatically. Use forward slashes in Windows `.env` files. |
| `ARGO_WORKFLOWS_ENABLED` | No | `false` | Enables the global Argo Workflows status and authenticated proxy. Helm sets this from `argoWorkflows.enabled`. |
| `ARGO_WORKFLOWS_SERVER_URL` | When enabled | Helm-derived Service URL | Internal Argo Server base URL, normally port `2746`. |
| `ARGO_WORKFLOWS_NAMESPACE` | No | Helm release namespace | Namespace containing Triton Control, Argo components, and user Workflows. |
| `ARGO_WORKFLOWS_SERVICE_NAME` | No | Helm-derived | Argo Server Service name shown in status responses. |
| `ARGO_WORKFLOWS_BASE_PATH` | No | `/api/workflows/proxy/` | Browser-facing proxy path; must match Argo Server `baseHref`. |
| `TRITON_DEPLOY_S3_SYNC_IMAGE` | No | `amazon/aws-cli:2.22.35` | Default image for vLLM S3 repository init containers and sidecars created by Add Deployment. For a host-run backend, set it in `triton-backend/.env` or the backend process environment. Helm sets it from `tritonDeployments.s3SyncImage`. Non-vLLM deployments do not use it. |
| `TRITON_DEPLOY_MODEL_REPOSITORY_EMPTYDIR_SIZE` | No | unset | Optional Kubernetes `emptyDir.sizeLimit` for the local `/models` volume used by vLLM init/sidecar repository sync. Helm sets it from `tritonDeployments.modelRepositoryEmptyDirSize`. |
| `TRITON_DEPLOY_S3_SYNC_STAGING_EMPTYDIR_SIZE` | No | unset | Optional Kubernetes `emptyDir.sizeLimit` for the `/staging` volume used by vLLM init/sidecar repository sync. Helm sets it from `tritonDeployments.s3SyncStagingEmptyDirSize`. |
| `OIDC_CONFIG_SOURCE` | No | `db` | Selects OIDC source: `db` for application-managed settings, `env` for environment-managed settings. |
| `OIDC_ENABLED` | No | `true` in `.env.example`, `false` in Compose/Helm defaults | Enables OIDC login when OIDC settings are valid. |
| `OIDC_ISSUER` | In `env` mode | `https://identity.example.com/realms/triton` | OIDC issuer URL published by your identity provider. |
| `OIDC_CLIENT_ID` | In `env` mode when OIDC is enabled | `triton-fastapi` | OIDC client ID. |
| `OIDC_CLIENT_SECRET` | In `env` mode when OIDC is enabled | `change-me` | OIDC confidential client secret. Store as a secret in shared environments. |
| `APP_BASE_URL` | Recommended | `https://127.0.0.1:8000` | Public backend base URL used for OIDC redirect-sensitive behavior. |
| `OIDC_REDIRECT_URI` | Recommended | `https://127.0.0.1:8000/auth/callback` | Runtime OIDC callback URL. This value takes precedence over DB-stored redirect settings. |
| `OIDC_SCOPE` | No | `openid profile email` | Space-separated OIDC scopes requested during login. |
| `OIDC_SSL_VERIFY` | No | `false` in `.env.example` | Enables TLS verification for OIDC discovery and token validation when true. In DB mode, the UI SSL setting controls this instead. |
| `OIDC_CA_BUNDLE` | No | unset | File path to a CA bundle used by env-managed OIDC TLS validation. DB mode uses the certificate stored through the UI instead. |
| `FRONTEND_REDIRECT_URL` | Recommended | `https://127.0.0.1:4200` | Browser destination after successful login. |
| `OIDC_ADMIN_EMAILS` | No | empty | Comma-separated allowlist of emails that may become the first auto-bootstrapped OIDC admin when `OIDC_CONFIG_SOURCE=env`. |
| `SESSION_SECRET` | Yes | `change-me-please` | Signing key for the session cookie. Use a strong random value in production. |
| `SESSION_COOKIE` | No | `session` | Session cookie name. |
| `SESSION_SAMESITE` | No | `lax` | Session cookie SameSite policy. |
| `SESSION_HTTPS_ONLY` | Recommended | `true` in `.env.example` | Marks the session cookie HTTPS-only. Set true when browsers access the backend through HTTPS, including HTTPS ingress. |
| `SESSION_MAX_AGE_SECONDS` | No | `1209600` | Session cookie lifetime in seconds. Default is 14 days. |
| `SERVER_HTTPS_ENABLED` | No | `true` in `.env.example` | Enables HTTPS directly in Uvicorn. Keep false when TLS terminates at an ingress or reverse proxy. |
| `TLS_KEY_FILE` | If direct backend HTTPS is enabled | `./tls/key.pem` | Uvicorn TLS private key path. |
| `TLS_CERT_FILE` | If direct backend HTTPS is enabled | `./tls/cert.pem` | Uvicorn TLS certificate path. |
| `JWT_SECRET` | Yes | `change-me-jwt` | Signing secret for local auth JWTs. Use a strong random value in production. |
| `JWT_ACCESS_TOKEN_EXPIRES_MINUTES` | No | `60` | Lifetime of local email/password JWT access tokens in minutes. |
| `EMAIL_CONFIG_SOURCE` | No | `env` | `env` makes email settings read-only and GitOps-managed; `db` enables admin management in Settings. |
| `EMAIL_DELIVERY_MODE` | No | `disabled` | Optional account delivery: `disabled`, `manual-link`, or `smtp`. |
| `EMAIL_PUBLIC_APP_URL` | For manual/SMTP | `https://triton-control.example.com` | Explicit browser origin used to build lifecycle links. Request host headers are never used. |
| `EMAIL_SMTP_HOST` / `EMAIL_SMTP_PORT` | For SMTP | unset / `587` | External SMTP relay address. Triton Control does not include a mail server. |
| `EMAIL_SMTP_TLS_MODE` | For SMTP | `starttls` | `starttls`, implicit `tls`, or `none`. |
| `EMAIL_SMTP_ALLOW_INSECURE` | For plain SMTP | `false` | Must be explicitly true when TLS mode is `none`. Do not use over untrusted networks. |
| `EMAIL_SMTP_USERNAME` / `EMAIL_SMTP_PASSWORD` | No | unset | Optional relay authentication. Store the password in a secret. |
| `EMAIL_SENDER_EMAIL` / `EMAIL_SENDER_NAME` | For SMTP | unset / `Triton Control` | Message sender identity. |
| `EMAIL_SMTP_CA_CERTIFICATE` | No | unset | PEM CA certificate appended to system trust for a private relay. |
| `EMAIL_CONNECT_TIMEOUT_SECONDS` / `EMAIL_OPERATION_TIMEOUT_SECONDS` | No | `10` / `15` | Bounded SMTP connection and operation timeouts. |
| `EMAIL_INVITE_EXPIRY_MINUTES` / `EMAIL_RESET_EXPIRY_MINUTES` | No | `1440` / `30` | Single-use token lifetimes. |
| `EMAIL_RECOVERY_RATE_LIMIT` | No | `5` | Hourly public recovery limit per normalized email digest; client limits are also applied. |
| `EMAIL_SECRET_ENCRYPTION_KEY` | In DB mode with SMTP password | unset | Secret used for authenticated encryption of the database-managed SMTP password. |
| `EMAIL_INVITE_SUBJECT`, `EMAIL_INVITE_TEXT_TEMPLATE`, `EMAIL_INVITE_HTML_TEMPLATE` | No | built in | Invitation templates. Text and HTML templates must contain `{link}`. |
| `EMAIL_RESET_SUBJECT`, `EMAIL_RESET_TEXT_TEMPLATE`, `EMAIL_RESET_HTML_TEMPLATE` | No | built in | Reset templates. Supported placeholders are `{display_name}`, `{product_name}`, `{link}`, and `{expiry_minutes}`. |
| `S3_SECRET_ENCRYPTION_KEY` | Yes | `change-me-s3` | Fernet key used to encrypt stored S3 secret keys for instance S3 settings and reusable S3 deployment profiles. |
| `CORS_ORIGINS` | No | local Angular dev origins | Comma-separated list of allowed frontend origins. |

Set `OIDC_ISSUER` to the exact issuer URL published by your OIDC provider. Do
not configure provider-specific base or realm variables; Triton Control uses the
issuer URL directly.

`CORS_ORIGINS` must list each browser origin exactly, including scheme, host,
and port. HTTP and HTTPS are different origins, so include both when both are
used in development:

```text
http://localhost:4200,https://localhost:4200
```

For production, set `CORS_ORIGINS` to the real external URL users open in the
browser, for example:

```text
https://triton-control.example.com
```

## GitOps Example

For GitOps, keep non-secret values in Helm values and secret values in a
Kubernetes Secret, sealed secret, SOPS-encrypted manifest, or external secret.

```yaml
app:
  env:
    - name: OIDC_CONFIG_SOURCE
      value: "env"
    - name: OIDC_ENABLED
      value: "true"
    - name: OIDC_ISSUER
      value: "https://identity.example.com/realms/triton"
    - name: OIDC_CLIENT_ID
      value: "triton-control"
    - name: APP_BASE_URL
      value: "https://triton-control.example.com"
    - name: OIDC_REDIRECT_URI
      value: "https://triton-control.example.com/auth/callback"
    - name: FRONTEND_REDIRECT_URL
      value: "https://triton-control.example.com"
    - name: OIDC_SCOPE
      value: "openid profile email"
    - name: OIDC_SSL_VERIFY
      value: "true"
    - name: SESSION_HTTPS_ONLY
      value: "true"
    - name: SERVER_HTTPS_ENABLED
      value: "false"
    - name: CORS_ORIGINS
      value: "https://triton-control.example.com"
  secretEnv:
    SESSION_SECRET: "replace-with-generated-secret"
    JWT_SECRET: "replace-with-generated-secret"
    S3_SECRET_ENCRYPTION_KEY: "replace-with-fernet-key"
    OIDC_CLIENT_SECRET: "replace-with-oidc-client-secret"
```

If the secret values already exist in a Kubernetes Secret, reference that Secret
instead of putting them in `secretEnv`:

```yaml
app:
  existingSecret: triton-control-backend-secrets
```

## TLS And Ingress Notes

For Kubernetes ingress, normally set:

```text
SESSION_HTTPS_ONLY=true
SERVER_HTTPS_ENABLED=false
```

This means the browser only sends cookies over HTTPS, while Uvicorn still runs
plain HTTP inside the cluster behind the ingress controller.

Set `SERVER_HTTPS_ENABLED=true` only when Uvicorn itself terminates TLS and can
read `TLS_KEY_FILE` and `TLS_CERT_FILE`.
