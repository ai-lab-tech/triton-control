# API Documentation

The backend is a FastAPI application and exposes OpenAPI documentation at
runtime.

## Runtime API Docs

When the backend is running:

- Swagger UI: `https://localhost:8000/docs`
- ReDoc: `https://localhost:8000/redoc`

If HTTPS is disabled in `triton-backend/.env`, use:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

## OpenAPI Spec

The backend OpenAPI spec is exported by:

```bash
cd triton-backend
python scripts/export_openapi.py
```

The generated file is:

```text
triton-backend/openapi.json
```

The frontend keeps a copy used for API client generation:

```text
triton-frontend/openapi/triton-backend/openapi.json
```

## Generate Frontend API Client

```bash
cd triton-frontend
npm run generate:api
```

The generated TypeScript Angular client is written to:

```text
triton-frontend/src/app/api/generated/
```

Regenerate the client when backend request/response schemas or route signatures
change.

## CI Check

Backend CI exports the OpenAPI spec and verifies that both checked-in copies are
current:

```text
triton-backend/openapi.json
triton-frontend/openapi/triton-backend/openapi.json
```

If the check fails, regenerate the backend spec, copy it to the frontend OpenAPI
folder, run `npm run generate:api`, and commit the changed files.
