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
