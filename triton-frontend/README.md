# Triton Control (Angular Material)

Modern Angular Material admin UI for managing Triton instances, user access, and OIDC settings.

For real code-server extension compatibility tests and upgrade checks, see
[Code-server plugin smoke tests](../docs/development.md#code-server-plugin-smoke-tests).

## Setup

```bash
npm run -s generate:api
npm install
npm start
```

### Generate certificate

For local HTTPS certificate setup details, see [tls/READEME.md](tls/READEME.md).

### Start frontend

HTTP mode:

```bash
npm run start:http
```

HTTPS mode:

```bash
npm run start:https
```

The HTTPS dev-server certificate is configured in `angular.json` under
`serve.configurations.https`:

```json
"sslCert": "tls/cert.pem",
"sslKey": "tls/key.pem"
```

These paths are relative to the `triton-admin-angular-material` project root.

## Generate API client

```bash
npm run -s generate:api
```

## Perf S3 browser smoke test

Use a development installation with a writable S3 test repository. Set
`SMOKE_FRONTEND_URL` and `SMOKE_BACKEND_URL` to your test endpoints and
`SMOKE_ADMIN_EMAIL` and `SMOKE_ADMIN_PASSWORD` to the test account credentials.
For a port-forward to the combined Kubernetes service, both URLs use the same port.

```bash
SMOKE_S3_PERF_PATH=/instances/1/models/iris/versions/1/profile \
  npm run test:smoke -- perf-s3-browser
```

The test uploads a uniquely named Python file, edits it in Perf, checks the stored
contents and batch size, and deletes the test file.

## Routes

- `/dashboard`
- `/instances`
- `/workspaces`
- `/deployments/new`
- `/instances/:id/models/:modelName/versions/:version/profile` — model Perf controls, results, and S3 Browser
- `/s3-profiles`
- `/workflows` - embedded global Argo Workflows UI through Triton Control
- `/mlflow` - embedded managed MLflow tracking UI through Triton Control
- `/users`
- `/settings`
- `/error-logs`

## Local Email/Password Policy

Local password registration and first-admin bootstrap require passwords with
12-128 characters and no whitespace. Admin-created local users may omit a
password to require later self-registration; if a password is entered, the same
policy applies.

Password regex:

```regex
^(?=.{12,128}$)(?!.*\s).+$
```

Email validation uses a simple practical format check:

```regex
^[^\s@]+@[^\s@]+\.[^\s@]+$
```

## Architecture choices (State management)

Current decision:

- We use Angular Signals for local/component UI state.
- We use interceptors for technical cross-cutting concerns (auth, credentials, 401 handling).
- We use a shared API error mapper to avoid duplicate error parsing in components.

Why not full NgRx yet:

- For the current scope, full NgRx would add unnecessary boilerplate.
- Most state is still feature-local and can be handled cleanly with Signals.

Planned evolution:

- We will introduce NgRx for complex, long-running, cross-page workflows.
- Primary candidates are Helm/Kubernetes deployment flows, status orchestration, and shared caching.
- Signals will remain for local UI-only state even after NgRx is introduced.
