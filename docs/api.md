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

## Central Registry and S3 Configuration

- `GET /api/settings/registry` (admin): returns `{ "configured": true }` or
  `{ "configured": false }`, never the secret.
- `PUT /api/settings/registry` (admin): accepts `dockerconfigjson` with a non-empty
  `auths` object. An empty string removes the central secret.
- `/api/s3-profiles`: members can list available profiles; only admins can create,
  update, or delete them. Browser responses return an empty `secret_key`.
- `POST /api/deployments`: send `s3_profile_id` and optional `repository_prefix`.
  The backend resolves connection credentials. Model-specific workflow requests
  additionally use `model_uri` and `model_name` with explicit model control.
- `PUT /api/instances/{instance_id}/s3`: when enabling S3, send `s3_profile_id`
  and optional `prefix`. Manual connection fields are rejected.
- `POST /api/workflows/s3-credentials`: select `s3_profile_id`; manual credential
  requests are rejected.

Managed workload creation rejects per-request `dockerconfigjson` overrides and
uses the central registry configuration. Existing workloads are unchanged when
an administrator updates the central configuration.
