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

## Perf Analyzer Notes

When running Perf Analyzer in Kubernetes, JSON input payloads are written to
`/dev/shm/pa_input.json` inside the Perf Analyzer pod before execution. This
avoids failures in restricted containers where `/tmp` is mounted read-only.

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
