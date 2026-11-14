# Iris example verification

Run the AAA unit tests using Python 3.12 and the pinned training dependencies:

```bash
python -m pip install numpy==2.2.6 pandas==2.3.3 scikit-learn==1.7.2
python -m unittest discover -s examples/workflows/tests -v
```

These tests train real pipelines, mock only MLflow/deployment boundaries, and
check the two registration paths, selected model/profile, deterministic prediction,
and propagation of logging, registration and deployment failures. Backend CI runs
them separately from backend dependencies.

## Live Minikube review — 2026-11-14

The reviewed scripts were executed in the development workspace against real
MLflow/S3 and Triton deployments using isolated test names, plugin wheels
`mlflow-triton==0.4.6` and `mlflow-triton-control==0.4.6`, MLflow 3.14.0,
NumPy 2.2.6, pandas 2.3.3 and scikit-learn 1.7.2.

- Log with registration: registry version 2, Triton version 2, inference `[0, 2]`.
- Separate registration: registry version 1, Triton version 1, inference `[0, 2]`.
- Requests came from each model's generated `request.json` artifact, not a
  handcrafted substitute.
- The initial log run registered version 1 but failed deployment due to an
  unconfigured fallback S3 profile. Rerunning with the installation's profile
  created version 2. Select the correct S3 profile explicitly outside Argo.
- Both Triton 26.06 and 26.10 images failed the original venv startup because
  `ensurepip` was absent. An actual container test verified `venv --without-pip`
  plus system pip `--python`; the corrected startup was applied only to the two
  review deployments, and both completed inference afterward.

The backend installer fix must be deployed before these examples work with those
stock images. Test scripts and pods exercised the real services directly; this
review did not submit the complete S3-backed Argo workflow. The modified workflow
manifests still require an installation-specific script upload and profile link.
