# Minikube development setup

Use this environment to test Triton Control's Kubernetes deployments, Traefik
routing and certificate creation. For the faster Python/npm development loop,
see [Local project development](development.md).

Prerequisites: Docker, Minikube, kubectl, Helm, OpenSSL and
[mkcert](https://github.com/FiloSottile/mkcert). Run commands from the repository
root. This creates a separate `triton-dev` cluster; allow additional resources
for model servers. No GPU is configured by this guide.

## 1. Start the cluster and ingress controller

```bash
minikube start -p triton-dev --driver=docker --cpus=4 --memory=8192
kubectl config use-context triton-dev
helm repo add traefik https://traefik.github.io/charts
helm repo update
helm upgrade --install traefik traefik/traefik \
  -n traefik --create-namespace \
  --set providers.kubernetesIngress.enabled=true \
  --set providers.kubernetesCRD.enabled=true \
  --set service.type=LoadBalancer --wait
helm upgrade --install cert-manager oci://quay.io/jetstack/charts/cert-manager \
  -n cert-manager --create-namespace --set crds.enabled=true --wait
```

These commands select the available chart releases. Record `helm list -A` and
pin `--version` when sharing a reproducible team environment. Do not enable the
Minikube nginx ingress addon for this setup.

## 2. Create a development CA and issuer

Use a dedicated development CA. Its private key will be stored in this test
cluster; never import a production CA key. A `rootCA.pem` alone cannot sign certificates.

```bash
export CAROOT="$HOME/.local/share/triton-control/dev-ca"
mkdir -p "$CAROOT"
mkcert -install
kubectl -n cert-manager create secret tls triton-dev-ca \
  --cert="$CAROOT/rootCA.pem" --key="$CAROOT/rootCA-key.pem"
kubectl apply -f - <<'YAML'
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: triton-dev-ca
spec:
  ca:
    secretName: triton-dev-ca
YAML
kubectl wait --for=condition=Ready clusterissuer/triton-dev-ca --timeout=120s
```

The CA Secret belongs in `cert-manager`, the default namespace used to resolve
ClusterIssuer secrets. Keep CA keys out of Git. Other client machines need the
public root certificate installed in their trust store, never its private key.

## 3. Build and install Triton Control

Create the namespace/database Secret using step 2 of the
[installation guide](ingress-tls.md#2-configure-the-release), once only. Then:

```bash
minikube -p triton-dev image build -t triton-control:dev .
helm upgrade --install triton-control ./charts/triton-control \
  -n triton-control -f examples/kubernetes/values-minikube.yaml \
  --wait --timeout 10m
kubectl -n triton-control wait --for=condition=Ready \
  certificate/triton-control-tls --timeout=120s
```

The example uses the locally built image, persistent PostgreSQL, UI hostname
`control.triton.test` and model base domain `models.triton.test`.

## 4. Resolve names and open the UI

Keep this running in another terminal:

```bash
minikube -p triton-dev tunnel
```

Read Traefik's `EXTERNAL-IP`:

```bash
kubectl -n traefik get service traefik
```

Add an `/etc/hosts` entry using that IP:

```text
<TRAEFIK_EXTERNAL_IP> control.triton.test smollm.models.triton.test
```

`/etc/hosts` does not support wildcards. Add each test model hostname, or configure
local wildcard DNS. Open `https://control.triton.test` and create the first admin.

## 5. Test a model endpoint

Configure an S3 profile/model repository and deploy a CPU-compatible model using
[the deployment workflow](deployment.md). Select external access, hostname
`smollm`, and HTTPS. Verify:

```bash
kubectl -n triton-control get ingress,certificate
curl --cacert "$CAROOT/rootCA.pem" https://smollm.models.triton.test/v2/health/ready
```

Expect a ready certificate and HTTP 200 once Triton is ready. For basic-auth
endpoints, add `-u USERNAME` (curl prompts for the password). Test hostname
collisions by trying the same hostname on another deployment; creation should
be rejected. HTTP is disabled by this example; test the admin HTTP policy
separately in **Settings → Endpoints**.

## Rebuild after code changes

```bash
minikube -p triton-dev image build -t triton-control:dev .
kubectl -n triton-control rollout restart deployment/triton-control
kubectl -n triton-control rollout status deployment/triton-control --timeout=300s
```

Repeat the Helm command when chart/configuration changes. Update or recreate
managed workspaces to install a changed bundled code-server extension.
The separate MLflow plugin must be installed from its updated package/source.

Use `minikube stop -p triton-dev` to pause the environment. Deleting the cluster
also deletes its database and accounts; stopping/rebuilding the app does not.

See the upstream [Traefik setup](https://doc.traefik.io/traefik/getting-started/kubernetes/),
[cert-manager CA issuer](https://cert-manager.io/docs/configuration/ca/) and
[Minikube image commands](https://minikube.sigs.k8s.io/docs/commands/image/).
