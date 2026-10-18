# Local Project Development

This page covers development of Triton Control itself. For the user-facing
**Development** feature backed by code-server on Kubernetes, see
[Development Workspaces](development-workspaces.md).

Use local Python for the backend and npm for the frontend.

## Prerequisites

- Python `3.12`.
- Node.js and npm.
- Java, Bash, curl, and Python for `npm run generate:api`; the script downloads
  and runs `swagger-codegen-cli.jar`.

## Backend

```bash
cd triton-backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python main.py
```

Windows PowerShell:

```powershell
cd triton-backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
python main.py
```

Backend environment is loaded from:

```text
triton-backend/.env
```

Create it from:

```text
triton-backend/.env.example
```

## Frontend

```bash
cd triton-frontend
npm ci
npm run generate:api
npm run start:http
```

HTTPS mode:

```bash
npm run start:https
```

## VS Code

The repo includes `.vscode/launch.json` with a generic Python current-file
debug configuration. Open `triton-backend/main.py` and start the Python debugger
for backend debugging.

Run the Angular dev server from a terminal:

```bash
cd triton-frontend
npm run start:http
```

## Backend Checks

```bash
docker run --rm -v "$PWD:/repo" -w /repo ghcr.io/gitleaks/gitleaks:latest detect --no-git --source . --redact --verbose
cd triton-backend
pip install -e ".[dev]"
pip install pip-audit
pip-audit
coverage run -m unittest discover -s tests -p "test_*.py" -v
coverage report --fail-under=75
mypy app/ tests/ scripts/ main.py
ruff check app/ main.py
lint-imports
bandit -r app/ main.py
```

## Frontend Checks

```bash
cd triton-frontend
npm ci
npm audit --audit-level=moderate
npm run generate:api
npm run lint
npm run format:check
npm test -- --watch=false --browsers=ChromeHeadless --code-coverage
```

## Smoke Tests

```bash
cd triton-frontend
npm run test:smoke
```

### User Authentication and Provisioning

This suite tests additional users after administrator setup. Playwright opens
the Users page and provisions a member and a viewer through the Add user dialog.
Each recipient uses a separate browser session. The backend and frontend run
from the current checkout, with disposable PostgreSQL, Mailpit and Keycloak
containers. Only the Triton inference server is simulated using the existing
mock server; authentication, email delivery and user storage are real.

#### Configurations

| Mode | Additional-user path |
| --- | --- |
| `smtp` | Admin sends invitations; Mailpit captures actual SMTP messages; recipients activate through the emailed links and sign in. Password recovery captures and consumes a real reset email. |
| `manual-link` | Admin invites users and obtains the one-time link in the UI; recipients activate without sending email. An admin-generated reset link changes an invited user's password. |
| `disabled` | Admin creates passwordless inactive accounts. Matching public registration activates them while preserving role and instance assignments. Invitations and admin resets are unavailable. |
| `oidc` | Admin provisions OIDC identities in Triton Control; recipients sign in through Keycloak's browser authorization-code flow and are matched by email. Local invitations and password login are blocked. |

OIDC identities are seeded in a disposable Keycloak realm; Triton Control does
not send local activation invitations for them. The first administrator is
setup, not the main assertion. Each mode verifies both newly added users.

All modes check assigned-instance visibility, rejection of unassigned access,
admin-only user management, prevention of self-promotion, member writes versus
viewer read-only access, removal of instance access, application logout and
deletion of an additional user. OIDC and disabled-delivery modes also verify
that an unknown identity needs administrator approval.

Invitation modes additionally check that activation links are single-use,
public registration cannot claim an invited account, reissue invalidates the old
link, cancellation invalidates the replacement, and a password reset invalidates
old passwords, session cookies and bearer tokens. No-delivery modes assert that
the test mailbox remains empty.

#### Run locally

Requirements: Linux, Docker with host networking, Node.js 22, Python 3, curl,
and internet access for container images and package downloads. Ports 18080,
18000, 15432, 18081, 19081, 18025, 11025 and 19000 must be free.

From the repository root:

```bash
npm ci --prefix triton-frontend
cd triton-frontend
npx playwright install --with-deps chromium
cd ..
bash triton-frontend/e2e/user-auth/run.sh
```

By default, all four modes run sequentially with a fresh application database
for each. To run one configuration:

```bash
AUTH_SMOKE_MODE=smtp bash triton-frontend/e2e/user-auth/run.sh
```

`AUTH_SMOKE_SKIP_BUILD=true` reuses `triton-control:user-auth-smoke`; leave it
unset to build the current checkout. The runner generates disposable
credentials, binds test services to loopback, and cleans up its named containers,
database volumes, mock server and temporary credentials on exit. Mailpit only
captures mail; no external recipients are contacted.

The Keycloak realm uses the documented
[startup realm import](https://www.keycloak.org/server/importExport), and SMTP
messages are read using the [Mailpit API](https://mailpit.axllent.org/docs/api-v1/).

#### Reports and CI

```bash
cd triton-frontend
npx playwright show-report playwright-report/user-auth/smtp
```

Replace `smtp` with `manual-link`, `disabled` or `oidc` for other reports.
Failure screenshots/traces are in `test-results/user-auth/<mode>/`; service logs
are in `test-results/user-auth-runtime/`. Traces can contain test credentials and
activation links, so keep artifacts confined to this disposable test environment.

`.github/workflows/user-auth-smoke.yml` runs a four-job matrix for relevant pull
requests and supports manual dispatch. Every job uploads its report and logs;
there are no automatic test retries. These tests cover plain SMTP capture and a
local Keycloak provider, not production mail TLS or every external OIDC provider.

### Workspace Connections to MLflow and Argo

The **Backend CI → workspace-connections-smoke** job runs automatically on
backend, extension, and chart changes. It creates an isolated Kind cluster,
installs Triton Control and Argo, creates MLflow and a code-server workspace
through the API, and tests both connections with the injected credentials.
Missing or incorrect credentials must be rejected. The cluster is deleted after
the test; failures fail the CI job.
The real Argo CLI also lists, submits, reads, and deletes a test workflow through
the workspace proxy, and verifies rejection of an incorrect credential.

To reproduce the complete CI test locally, run from the repository root with
Docker, Kind, Helm, kubectl, and Python 3 installed:

```bash
bash triton-backend/scripts/ci_workspace_connections.sh
```

To check an existing workspace instead:

```bash
kubectl exec -i -n triton-control <workspace-pod> -c code-server -- python3 - \
  < triton-backend/scripts/smoke_workspace_connections.py
```

Replace `<workspace-pod>` with your code-server pod name. The test runs inside
the workspace using its injected credentials. It lists experiments and workflows,
checks that missing or incorrect credentials are rejected, and exits nonzero on
failure. It creates no resources and prints no tokens.
