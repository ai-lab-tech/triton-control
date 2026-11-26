# TRITON BACKEND

Backend API for Triton Backend with PostgreSQL storage.

## Setup

### 0. Configure Environment

Create a local `.env` file (or export env vars) based on `.env.example`.

Example:

```bash
cp .env.example .env
```

`DATABASE_URL` is read from the environment (supports `.env`).

Logging is quiet by default: warnings and errors only, no Uvicorn access log,
and no SQL statement echo. Enable verbose logs when debugging:

```bash
BACKEND_VERBOSE=true
```

Use `LOG_LEVEL=DEBUG` for deeper diagnostics and `DATABASE_ECHO=1` only when
you need SQL statement logging.

### 1. Create Virtual Environment

```bash
# Create virtual environment
python -m venv .venv

# Activate on macOS/Linux:
source .venv/bin/activate

# Activate on Windows:
.venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -e .
```

For development tooling:

```bash
pip install -e ".[dev]"
```

### 3. Start PostgreSQL (Docker)

```bash
cd postgresql 

docker compose up -d
```


## Run the Application

For local HTTPS/TLS certificate setup details, see [tls/READEME.md](tls/READEME.md).

```bash
# Using Python
python main.py
```

## Additional Documentation

- [Local TLS setup](tls/READEME.md)
- [Protobuf generation and Triton release mapping](protobuff/README.md)
- [Technology choices](TECHNOLOGY_CHOICES.md)

## Development Workspace Backend

The `/api/development` endpoints manage each user's Kubernetes-backed
Development workspace via code-server.

The backend creates the StatefulSet, Service, persistent workspace volume,
Secrets, and the ConfigMap containing the bundled **Triton Control Deploy**
extension. The extension is installed automatically during workspace startup.
S3/R2 Explorer is optional and is installed manually by the user when needed.

## Perf Analyzer Notes

Each model benchmark runs in its own non-root Kubernetes Job. JSON input is
mounted from a run-owned Secret at `/perf-input/input.json`; temporary volumes
remain writable by UID/GID 10001. See [Model performance Jobs](../docs/model-performance-jobs.md)
for concurrency, cancellation, configuration, and migration details.

The base Job manifest is maintained in
[`perf_analyzer_job.yaml`](app/services/perf_analyzer/perf_analyzer_job.yaml).
`jobs_kubernetes.py` parses the template and fills dynamic fields and optional
Secret references. Shared command preparation lives in `commands.py`;
`jobs.py` handles lifecycle reconciliation and preserves captured output when
final logs are unavailable.

## Auth Session and Token Timeout

You can configure automatic logout behavior with environment variables:

- `JWT_ACCESS_TOKEN_EXPIRES_MINUTES` (default `60`): lifetime of local auth JWT access tokens.
- `SESSION_MAX_AGE_SECONDS` (default `1209600`): session cookie lifetime in seconds (14 days).


## Access the API

- **API**: https://localhost:8000
- **Interactive Docs (Swagger)**: https://localhost:8000/docs
- **Alternative Docs (ReDoc)**: https://localhost:8000/redoc


## Test endpoint with token 

```bash
TOKEN='....'

curl -X POST "https://127.0.0.1:8000/api/instances" \      
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"http://localhost:8888","name":"instance1"}'
```


## OpenAPI spec
FastAPI enables generating an OpenAPI spec for its endpoints (`app.openapi()`).

Generate the checked-in backend spec with:

```bash
python scripts/export_openapi.py
```

The frontend keeps a copy at
`../triton-frontend/openapi/triton-backend/openapi.json` for generated Angular
API client updates. Backend CI verifies that both spec copies are current.

## MLflow attribution smoke test

With backend dependencies installed and Docker running, execute from
`triton-backend`:

```bash
python -m scripts.smoke_mlflow_attribution
```

This starts isolated MLflow 3.14 and gateway containers, checks direct-access
rejection and protected creator tags, and runs the unmodified MLflow SDK through
training, metric logging, artifact upload, and model registration. It removes
the test containers on exit and retains the built test image for subsequent
runs. It does not change the Kubernetes installation.

### Central workload credentials

Administrators configure image pull credentials under **Settings → Registries** and
S3 connections through the existing **S3 Profiles** admin menu entry. New deployments, development
workspaces, MLflow installations and profiling jobs use the stored registry
credentials automatically. Existing Kubernetes workloads are unchanged; recreate
them to use new registry credentials.

Registry credentials are encrypted using `REGISTRY_SECRET_ENCRYPTION_KEY`, falling
back to a stable `SESSION_SECRET`. Keep this key across restarts; after changing it,
save the registry credentials again. Read endpoints return configuration status,
never the registry secret.

The backend encrypts S3 profile and instance secret keys with
`S3_SECRET_ENCRYPTION_KEY`. Helm generates it on first install. For standalone deployments, set it to a stable
random value before starting the service. Back up the key with the database. For a disposable test
database, run the backend suite with a test-only key:

```bash
S3_SECRET_ENCRYPTION_KEY=disposable-test-key python -m unittest discover -s tests
```

Deployment and instance S3 forms accept a profile and repository prefix. Manual
connection credentials are rejected at the API/service boundary. Profiles owned
by administrators are available to members; existing personal profiles remain
available to their owners and can be maintained by administrators. Browser profile
responses omit secret keys. Authenticated managed workspaces still receive the
credentials required for S3 uploads through their dedicated profile endpoint.

### Metrics endpoint allowlist

Set `TRITON_METRICS_ALLOWED_URLS` in the backend environment to a comma-separated
list of exact trusted metrics URLs, for example
`http://triton-service.namespace.svc.cluster.local:18002/metrics,https://metrics.example.com/triton`.
Both utilization and inference metrics requests select their destination from
this server-side list. URL ports, paths and query strings must match exactly;
redirects are never followed. Only list endpoints under your control, including
DNS names whose resolution you trust.

**Upgrade requirement:** populate this list with the metrics URLs of existing
instances and newly deployed instances before restarting the backend. An unset
or empty list blocks all metrics fetches and reports a metrics error. Triton
health checks and inference requests are unaffected. Private Kubernetes service
addresses are supported when explicitly listed; there is no wildcard bypass.

Metrics label tokens are parsed sequentially and limited to 64 KiB of UTF-8.
Malformed, duplicate or oversized label sets yield no labels; valid quoted values
support escaped quotes, backslashes and newlines. This bounds parsing work even
when a configured metrics endpoint returns malformed data.

OIDC login and callback failures return stable error codes and log fixed error
categories. Provider exception text, chained tracebacks and callback parameters
are excluded from these log messages because they may contain credentials.
