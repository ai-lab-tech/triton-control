# Getting Started

The quickest way to run Triton Control is Docker Compose. For active development,
run the backend with Python and the frontend with npm.

## Docker Compose

Prerequisite: Docker Desktop or another Docker engine. Host Node.js, npm, and
Java are not required for this path because the Dockerfile installs the
frontend build tools inside the Node build stage and regenerates the
Swagger/OpenAPI client before building Angular.

```bash
docker compose up --build
```

Open:

- Frontend: `http://localhost:8080`
- Backend API: `http://localhost:8000`

PostgreSQL is exposed on:

```text
127.0.0.1:5433
```

## Podman Compose

```bash
podman-compose -f podman-compose.yaml up --build
```

The exposed URLs match Docker Compose.

## Kubernetes Quick Start

Use Kubernetes when you want to run Triton Control in a shared cluster or close
to production. The Helm chart is in `charts/triton-control`.

Prerequisites:

- Kubernetes `v1.19` or newer. The chart uses `networking.k8s.io/v1` Ingress,
  which is stable from Kubernetes `v1.19`.
- `kubectl` configured for the target cluster.
- Helm `v3`.
- A container registry reachable by the cluster.
- An Ingress controller if `ingress.enabled=true`, for example nginx-ingress.
- A default StorageClass, or an explicit `postgresql.persistence.storageClass`,
  when using the bundled PostgreSQL database with persistence enabled.

Build and push the combined frontend/backend image:

```bash
docker build -t registry.example.com/triton-control:0.1.0 .
docker push registry.example.com/triton-control:0.1.0
```

The image build requires Docker on the host. It does not require host npm or
Java. The build needs network access to install npm packages and, if the
Swagger generator jar is not already cached in the build context, to download
`swagger-codegen-cli.jar`.

Create a values file for your cluster:

```yaml
app:
  image:
    repository: registry.example.com/triton-control
    tag: "0.1.0"
  secretEnv:
    SESSION_SECRET: "replace-me"
    JWT_SECRET: "replace-me"
    S3_SECRET_ENCRYPTION_KEY: "replace-me"

postgresql:
  enabled: true
  auth:
    database: triton_backend
    username: triton
    password: "replace-me"

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: triton-control.example.com
      paths:
        frontend:
          - path: /
            pathType: Prefix
        backend:
          - path: /api
            pathType: Prefix
          - path: /auth
            pathType: Prefix
          - path: /login
            pathType: Prefix
          - path: /logout
            pathType: Prefix
          - path: /whoami
            pathType: Prefix
```

Install the chart:

```bash
helm upgrade --install triton-control ./charts/triton-control \
  --namespace triton-control \
  --create-namespace \
  -f values-k8s.yaml
```

For a cluster without Ingress, set `ingress.enabled=false` and port-forward the
frontend service:

```bash
kubectl -n triton-control port-forward svc/triton-control 8080:80
```

Then open:

```text
http://localhost:8080
```

## Local Development

Prerequisites:

- Python `3.12`.
- Node.js and npm for the frontend.
- Java, Bash, curl, and Python if you run `npm run generate:api`; that script
  runs `swagger-codegen-cli.jar`.

Start PostgreSQL:

```bash
cd triton-backend/postgresql
docker compose up -d
```

Start the backend:

```bash
cd triton-backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python main.py
```

On Windows PowerShell:

```powershell
cd triton-backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
python main.py
```

Start the frontend in a second terminal:

```bash
cd triton-frontend
npm ci
npm run generate:api
npm run start:http
```

Open:

```text
http://localhost:4200
```

## Triton URLs From Docker

When Triton Control runs in Docker, `127.0.0.1` inside the app container points to
the app container, not to your host and not to another Triton container.

Use a published host port:

```text
http://host.docker.internal:<published-triton-http-port>
```

or attach the Triton container to the Compose network:

```bash
docker network connect triton-control tritonserver-explicit
```

Then use:

```text
http://tritonserver-explicit:8000
```
