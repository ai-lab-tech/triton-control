# Getting Started

The quickest way to run Triton Control is Docker Compose. For active development,
run the backend with Python and the frontend with npm.

## Docker Compose

With Docker installed, start the published image:

```bash
docker pull ailabtechtriton/triton-control:v1.2.3
docker tag ailabtechtriton/triton-control:v1.2.3 triton-control:compose
docker compose up --no-build
```

After the stack starts, open `http://localhost:8080` in your browser.

To build from source instead:

```bash
docker compose up --build
```

The backend API is available at `http://localhost:8000` and PostgreSQL at
`127.0.0.1:5433`.

Docker Compose does not provide Kubernetes. Deployments, Development
workspaces, managed MLflow, and Argo Workflows are therefore disabled.

## Podman Compose

With Podman and `podman-compose` installed, start the published image:

```bash
podman pull docker.io/ailabtechtriton/triton-control:v1.2.3
podman tag docker.io/ailabtechtriton/triton-control:v1.2.3 \
  localhost/triton-control:compose
podman-compose -f podman-compose.yaml up --no-build
```

After the stack starts, open `http://localhost:8080` in your browser.

To build from source instead:

```bash
podman-compose -f podman-compose.yaml up --build
```

The backend API is available at `http://localhost:8000` and PostgreSQL at
`127.0.0.1:5433`.

Podman Compose does not provide Kubernetes. Deployments, Development
workspaces, managed MLflow, and Argo Workflows are therefore disabled.

## Kubernetes Quick Start

Follow [Kubernetes Ingress and TLS setup](ingress-tls.md) for a cluster installation,
including example Helm values, DNS, certificates and internal-only access.
For development, use [Minikube with Traefik and mkcert](local-kubernetes.md).

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
