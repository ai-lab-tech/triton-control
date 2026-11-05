# Kubernetes Ingress and TLS setup

Use this guide for a cluster installation. For a complete local test environment,
see [Minikube development setup](local-kubernetes.md).

## Choose the access mode

| Access | Required components | Helm configuration |
| --- | --- | --- |
| Port-forward/internal only | Kubernetes | `ingress.enabled: false`, `tritonDeployments.ingressEnabled: false` |
| HTTP model endpoints | Traefik | `tritonDeployments.ingressEnabled: true`, `tritonDeployments.allowHttp: true` |
| HTTPS with an existing certificate | Traefik and a TLS Secret | `tritonDeployments.ingressTlsSecretName` |
| HTTPS with automatic certificates | Traefik, cert-manager and a ready ClusterIssuer | `tritonDeployments.certManagerClusterIssuer` |

`ingress.*` exposes Triton Control's UI/API. `tritonDeployments.*` controls model
endpoints independently. Disabling one does not disable the other.
HTTP needs no cert-manager; basic authentication requires HTTPS.
The administrator can override the HTTP default under **Settings → Endpoints**.

## 1. Prepare the cluster

You need Helm, kubectl, persistent storage, and Traefik with the Kubernetes
Ingress and Kubernetes CRD providers enabled. The CRD provider is required for
basic-auth middleware. The application chart does not install Traefik or cert-manager.

For automatic certificates, install [cert-manager](https://cert-manager.io/docs/installation/helm/)
and configure a ClusterIssuer using your organization's certificate authority or
[ACME provider](https://cert-manager.io/docs/configuration/acme/).
Check it before installing Triton Control:

```bash
kubectl get ingressclass traefik
kubectl wait --for=condition=Ready clusterissuer/platform-issuer --timeout=120s
```

Point `control.example.com` and `*.models.example.com` at the Traefik load balancer.
Use your own domains in the following values file. Wildcard DNS routes names;
it does not create certificates.

## 2. Configure the release

Run from the repository root, where `charts/triton-control` exists:

```bash
cp examples/kubernetes/values-https.yaml values-cluster.yaml
```

The [example values](https://github.com/ai-lab-tech/triton-control/blob/main/examples/kubernetes/values-https.yaml)
include the UI paths and model policy. Edit the domains and `platform-issuer`
in `values-cluster.yaml`.
Users will enter `smollm`; the API creates `smollm.models.example.com`.
Set `baseDomain` without `*.` or a URL scheme. Triton Control's hostname is separate.

Create the database Secret once for a new installation (keep it across upgrades):

```bash
kubectl create namespace triton-control
DB_PASSWORD=$(openssl rand -hex 24)
kubectl -n triton-control create secret generic triton-control-db \
  --from-literal=POSTGRES_DB=triton_backend \
  --from-literal=POSTGRES_USER=triton \
  --from-literal=POSTGRES_PASSWORD="$DB_PASSWORD" \
  --from-literal=database-url="postgresql://triton:$DB_PASSWORD@triton-control-postgresql:5432/triton_backend"
unset DB_PASSWORD
```

These names assume release and namespace `triton-control`. For an existing database,
use its existing credentials; creating a new Secret does not change its password.
The chart generates application session/encryption secrets on first installation
and preserves them on upgrade. Back up those Secrets and the database together.
For externally managed secrets/database options, see the [chart reference](https://github.com/ai-lab-tech/triton-control/tree/main/charts/triton-control).

## 3. Install and verify

```bash
helm upgrade --install triton-control ./charts/triton-control \
  -n triton-control -f values-cluster.yaml --wait --timeout 10m
kubectl -n triton-control wait --for=condition=Ready \
  certificate/triton-control-tls --timeout=120s
kubectl -n triton-control get ingress,certificate
```

Open `https://control.example.com` and complete the
[first administrator setup](user-management.md#first-admin).
Create a model endpoint with hostname `smollm` and protocol HTTPS.
Cert-manager creates its Certificate from the generated Ingress; users do not
write a Certificate YAML for each model. The ClusterIssuer is cluster-wide,
so `kubectl get issuer -n triton-control` may correctly show no resources.

## Existing TLS certificate instead

Create a TLS Secret in `triton-control` containing the certificate chain and key:

```bash
kubectl -n triton-control create secret tls model-wildcard-tls \
  --cert=fullchain.pem --key=privkey.pem
```

Set `tritonDeployments.ingressTlsSecretName: model-wildcard-tls` and clear
`tritonDeployments.certManagerClusterIssuer`. The certificate must cover
`*.models.example.com`. For the UI, set `ingress.tls[].secretName` to a Secret
covering its hostname and remove the `cert-manager.io/cluster-issuer` annotation.
Cert-manager is unnecessary in this mode; arrange renewal yourself.

## Troubleshooting

- **Host cannot resolve:** check DNS or use `curl --resolve HOST:443:TRAEFIK_IP https://HOST/`.
- **Certificate pending:** run `kubectl -n triton-control describe certificate NAME`
  and `kubectl describe clusterissuer NAME`.
- **Wrong page/404:** verify DNS points to Traefik and the Ingress uses class `traefik`.
- **Untrusted certificate:** check hostname coverage and client trust in the issuing CA.
- **No external access allowed:** check `tritonDeployments.ingressEnabled` and
  **Settings → Endpoints**. Existing endpoints are not rewritten by policy/domain changes.

For internal-only access, disable both ingress switches, then run
`kubectl -n triton-control port-forward svc/triton-control 8080:8080`
and open `http://localhost:8080`.
