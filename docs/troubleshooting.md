# Troubleshooting

## Docker Cannot Connect To Engine

On Windows, this usually means Docker Desktop is not running or the current
shell cannot access the Docker pipe.

Check:

```powershell
docker version
docker context ls
```

Start Docker Desktop and use the correct context, commonly `default` or
`desktop-linux`.

## Backend Cannot Connect To Postgres

For local Python execution, the backend expects:

```text
postgresql://triton:tritonpw@localhost:5433/triton_backend
```

Start the backend PostgreSQL container:

```bash
cd triton-backend/postgresql
docker compose up -d
```

Verify the port mapping:

```bash
docker compose ps
```

Expected:

```text
127.0.0.1:5433->5432
```

## App Container Cannot Resolve `postgres`

Use the root Compose file:

```bash
docker compose up --build
```

Both `triton-control` and `postgres` must be on the same Compose network. The
default network is named:

```text
triton-control
```

## Triton Instance URL Does Not Work From Docker

Do not use `127.0.0.1` for a Triton instance when Triton Control runs in Docker.
Use:

```text
http://host.docker.internal:<published-triton-http-port>
```

or connect the Triton container to the `triton-control` network:

```bash
docker network connect triton-control tritonserver-explicit
```

Then use:

```text
http://tritonserver-explicit:8000
```

## Minikube Ingress Host Does Not Resolve From Triton Control

The Triton instance URL is checked by the Triton Control backend, not by the
browser. A Windows `hosts` file entry only helps processes that use that hosts
file. It does not automatically help a Docker container or a Kubernetes Pod.

First make sure the hostname is exactly the same everywhere. For example, this
instance URL:

```text
http://triton11-test.localtest.me
```

needs this exact hosts entry:

```text
192.168.49.2 triton11-test.localtest.me
```

`test11-triton.localtest.me` is a different hostname and will not match.

If Triton Control runs in Docker Compose, add the same host mapping to the
`triton-control` service, or use a DNS name that resolves without a local hosts
file:

```yaml
extra_hosts:
  - "triton11-test.localtest.me:192.168.49.2"
```

If Triton Control runs inside Kubernetes, the Windows hosts file is irrelevant
to the backend Pod. Use an internal Kubernetes Service URL when possible, or use
cluster DNS/CoreDNS/a real DNS record that resolves from inside the cluster.

If Triton Control runs directly on the Windows host and the hosts entry is
correct but the backend still cannot reach the Triton ingress, check proxy
environment variables. Direct Triton HTTP clients ignore `HTTP_PROXY` and
`HTTPS_PROXY` by default via `TRITON_HTTP_TRUST_ENV=false`, because local
Minikube ingress names usually need to use the Windows hosts file directly. Set
`TRITON_HTTP_TRUST_ENV=true` only when Triton must be reached through a proxy.

For Minikube ingress, wildcard DNS services such as `sslip.io` or `nip.io` can
avoid local hosts-file drift:

```text
http://triton11-test.192.168.49.2.sslip.io
```

## code-server Plugin Window Does Not Open

If the Development workspace loads but the **Triton Control Deploy** plugin
window stays blank or the browser console shows this error:

```text
'crypto.subtle' is not available so webviews will not work
```

then code-server is running in a browser context that is not secure. VS Code
webviews need a secure context. These origins work:

```text
https://triton-control.example.com
http://localhost:<port>
```

These origins do not work for webviews:

```text
http://triton-control.test
http://<minikube-ip>
```

Use one of these fixes:

- expose Triton Control through an HTTPS ingress with a trusted certificate
- or port-forward the Triton Control service and open it through localhost:

```bash
kubectl -n triton-control port-forward svc/triton-control 8080:8080
```

Then open:

```text
http://localhost:8080
```

The `api/auth/me` `401 Unauthorized` message during page load is normally only
the frontend checking whether a user is already signed in. It is not the
code-server webview failure.

## Metrics URL Missing `/metrics`

The backend automatically appends `/metrics` when the configured metrics URL has
no path.

Examples:

```text
http://triton:8002 -> http://triton:8002/metrics
triton:8002/       -> http://triton:8002/metrics
```
