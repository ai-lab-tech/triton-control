### Run Postgres

The PostgreSQL compose setup supports both TLS and non-TLS local development.

#### With TLS

The default compose file starts PostgreSQL with TLS using the certificates from `triton-backend/tls`.

Make sure these files exist:
	- `triton-backend/tls/cert.pem`
	- `triton-backend/tls/key.pem`

Start or restart Postgres:

```bash
cd triton-backend/postgresql
docker compose up -d --force-recreate
```

Use a TLS-enabled database URL in backend `.env` with certificate verification:

```bash
DATABASE_URL=postgresql://triton:tritonpw@localhost:5433/triton_backend?sslmode=verify-full&sslrootcert=/absolute/path/to/triton-backend/tls/rootCA.pem
```

#### Without TLS

Use the non-TLS compose file when local certificate verification is not needed:

```bash
cd triton-backend/postgresql
docker compose -f docker-compose.no-tls.yaml up -d --force-recreate
```

Use the plain database URL in backend `.env`:

```bash
DATABASE_URL=postgresql://triton:tritonpw@localhost:5433/triton_backend
```

### Add issuer to Postgres db
```bash
docker exec -i triton-backend-postgres psql -U triton -d triton_backend -c "UPDATE oidc_config SET issuer='http://localhost:8080/realms/master', client_id='triton-fastapi', redirect_uri='http://127.0.0.1:8000/auth/callback', client_secret='change-me', scopes='openid profile email' WHERE id=1;"
```

### Update instance
```bash
docker exec -i triton-backend-postgres psql -U triton -d triton_backend -c "UPDATE triton_instances
SET
s3_endpoint = 'https://localhost:9000',
s3_use_https = true,
s3_verify_ssl = true,
s3_address_style = 'path',
s3_enabled = true
WHERE id = 8;"
```


