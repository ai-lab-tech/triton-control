# Per-user workspace isolation smoke test

This standalone Playwright suite runs against a real Triton Control deployment
in a disposable kind cluster. It invites and activates two member accounts using
manual invitation links (no email is sent), then creates a workspace for each
through the authenticated API. Both users request the same workspace name.

The test verifies:

- Different workspace IDs, StatefulSets, pod names and pod UIDs.
- Each code-server container mounts `/workspace` from a bound PVC, with different
  PVC names, PVC UIDs and backing PersistentVolumes for the two users.
- The installed code-server version matches the requested version.
- Each member lists only their own workspace. Reading, proxying or deleting the
  other member's workspace returns HTTP 403, in both directions.
- Each member can open their own embedded code-server UI.
- Different contents written to the same file path remain separate.
- Restarting one pod preserves its PVC and files while the other user's pod,
  PVC and file contents remain unchanged.

This checks application access and mounted storage isolation. It does not test
network policies or isolation against a compromised Kubernetes node.

## Run locally

Requirements: Docker, kind, kubectl, Helm 3, Node.js 22, Python 3, internet access,
and a free local port 18080. From the repository root:

```bash
npm ci --prefix triton-frontend
cd triton-frontend
npx playwright install --with-deps chromium
cd ..
bash triton-frontend/e2e/workspace-isolation/run.sh
```

The runner builds the current checkout, loads it into a uniquely named cluster,
and installs the Helm chart. It uses a private kubeconfig and deletes its cluster
and temporary credentials on exit. Existing clusters are not used.

The default code-server version comes from `development.codeServer.version` in
the Helm chart. Override it for a single fresh installation test:

```bash
ISOLATION_SMOKE_VERSION=4.125.0 \
bash triton-frontend/e2e/workspace-isolation/run.sh
```

Use `KIND=/path/to/kind` or `ISOLATION_SMOKE_PYTHON=/path/to/python3` for tools
outside PATH. `ISOLATION_SMOKE_SKIP_BUILD=true` reuses the local
`triton-control:workspace-isolation` image. Leave it unset to test the current checkout.

## Reports and CI

```bash
cd triton-frontend
npx playwright show-report playwright-report/workspace-isolation
```

The test annotation shows the requested code-server version. The
`workspace-storage` attachment records the pod, PVC and backing volume identities
after verifying the installed version. Runtime logs and Kubernetes events are
under `triton-frontend/test-results/workspace-isolation-runtime/`.

`.github/workflows/workspace-isolation-smoke.yml` runs for relevant pull requests
and supports manual dispatch. It uploads reports and diagnostics.

For debugging, `ISOLATION_SMOKE_KEEP_ENV=true` preserves the test cluster and
private temporary directory; the runner prints both locations. Port forwarding
may need restarting. Remove them afterward with
`kind delete cluster --name <printed-cluster-name>` and delete the printed
temporary directory. Test traces and temporary state can contain test credentials.
