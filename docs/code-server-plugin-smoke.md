# Code-server plugin smoke tests

Playwright exercises the three features bundled in **Triton Control Deploy**
against a real code-server process. The existing Node.js unit tests remain the
fast first check; this suite checks the workbench and extension-host integration.

## Coverage

- Create a Python model repository through the native wizard. Verify generated
  files inside the pod and repository discovery in the workspace view.
- Choose an S3 profile, open an object, edit and save it, and verify its content
  independently through the MinIO API.
- Drag a repository from the workspace to S3 and a file back to the workspace.
  Verify destination contents and source preservation. This tests the workbench
  patch against the actual code-server release, not a synthetic JavaScript bundle.
- Open the deployment webview inside Triton Control's embedded workspace,
  select a profile, and upload the repository. Verify the real API response,
  S3 contents, and Kubernetes Deployment creation, then delete the deployment.
- Restart the workspace pod and verify files and extension commands still work.
  When a candidate version is supplied, repeat the suite after upgrading the
  same StatefulSet and verify that baseline workspace files survive.

Deployment uses a small test image: this suite checks the plugin's upload and
deployment handoff, not model loading or GPU inference.

## Run locally

Requirements: Docker, kind, kubectl, Helm 3, Node.js 22, Python with `boto3`,
and internet access for images, code-server, and marketplace downloads.
Ports 18080 and 19000 must be free.

From the repository root:

```bash
npm ci --prefix triton-frontend
python3 -m pip install boto3==1.34.122
cd triton-frontend
npx playwright install --with-deps chromium
cd ..
bash triton-frontend/e2e/code-server/run.sh
```

The runner builds the current checkout, creates a uniquely named kind cluster,
builds MinIO from a pinned upstream release, deploys Triton Control with its
Helm chart and disposable MinIO, and starts
Playwright. The production workspace startup installs code-server and the
bundled extension. Credentials are generated for each run. A separate kubeconfig
protects existing clusters; by default the runner removes its own cluster and temporary
credentials on exit.

The baseline defaults to the Helm chart's managed code-server version. For upgrades:

```bash
PLUGIN_SMOKE_VERSION=4.125.0 \
PLUGIN_SMOKE_CANDIDATE_VERSION=<candidate-version> \
bash triton-frontend/e2e/code-server/run.sh
```

The upgrade changes the version in the real workspace startup command and rolls
the StatefulSet while preserving its PVC. Unsupported workbench patches and
failed extension activation fail the suite; they are not skipped.
For a separate fresh candidate installation:

```bash
PLUGIN_SMOKE_VERSION=<candidate-version> \
PLUGIN_SMOKE_PHASE=fresh-candidate \
bash triton-frontend/e2e/code-server/run.sh
```

Use `KIND=/path/to/kind` or `PLUGIN_SMOKE_PYTHON=/path/to/venv/bin/python` for
tools outside PATH. `PLUGIN_SMOKE_SKIP_BUILD=true` reuses the local
`triton-control:plugin-smoke` image; leave it unset to test the current checkout.

## CI and diagnostics

`.github/workflows/code-server-smoke.yml` runs on pull requests touching the
extension, workspace integration, chart, image, or test harness. Manual runs
accept `candidate_version` and test both a persisted-workspace upgrade and a
fresh candidate installation. The suite runs serially with no automatic retries.

Traces, videos, failure screenshots, pod logs, and Kubernetes events are saved
under `triton-frontend/test-results/`. HTML reports are under
`triton-frontend/playwright-report/`. CI uploads diagnostics on failure too.
Traces include test account/session data: keep this suite confined to its
disposable environment and treat artifacts as containing test credentials.

```bash
cd triton-frontend
npx playwright show-report playwright-report/code-server/baseline
```

`npm run test:smoke:plugins` is the browser-only entry point used by the runner.
It needs the prepared environment and `PLUGIN_SMOKE_*` variables; use `run.sh`
for a normal local run.

For local debugging, `PLUGIN_SMOKE_KEEP_ENV=true` leaves the isolated cluster
and private temporary runner directory available after the tests. The runner
prints their names. Port forwards may need restarting after the runner exits.
Delete that cluster with `kind delete cluster --name <printed-cluster-name>` and
remove the printed temporary directory when finished. Do not enable this in CI.
