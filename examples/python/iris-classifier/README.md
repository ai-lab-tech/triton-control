# Iris Classifier

Small scikit-learn Iris classifier served with Triton's Python backend.

## Model

- Model: `RandomForestClassifier`
- Artifact: `iris_classifier/1/model.joblib`
- Backend: `python`
- Input: `FEATURES`, `FP32`, shape `[4]`
- Outputs:
  - `CLASS_ID`, `INT64`, shape `[1]`
  - `PROBABILITIES`, `FP32`, shape `[3]`

Use Triton Control's `requirements.txt` field to install `scikit-learn` and
`joblib` before Triton starts.

## 1. Create Development Workspace

In Triton Control, open **Development** and create the workspace:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:25.02-py3` |
| Image already has Development installed | Disabled |
| Workspace storage | At least `20Gi` |
| GPU count | `0` |

When the workspace is ready, open code-server from **Development**.

Install the Jupyter extension if needed. If the Python notebook kernel is
missing, run:

```bash
pip install notebook ipykernel
```

## 2. Create the Repository and Artifact

Choose one path:

### Option A: Use the Example Repository

Copy or upload this example folder into `/workspace`.

### Option B: Create the Structure with the Plugin

1. In code-server, run **New Model Repository** from the Triton Control plugin.
2. Choose the Python backend template.
3. Use `iris_classifier` as the model name.
4. Keep `KIND_CPU` in `config.pbtxt`.
5. Copy this example's `model.py` and notebook into the generated repository.

Then open and run:

```text
create_iris_classifier.ipynb
```

This writes:

```text
iris_classifier/1/model.joblib
```

## 3. Deploy

Use these deployment settings:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:25.02-py3` |
| GPU count | `0`; `config.pbtxt` uses `KIND_CPU` |
| `requirements.txt` | `scikit-learn` and `joblib` |

1. In the opened code-server Explorer, right-click this repository folder.
2. Select **Triton Control: Deploy Model Repository** from the context menu.
3. Select S3 settings.
4. Enter the deployment settings shown above.
5. Deploy.

## 4. Test Inference

Use the Triton Control instance inference view first:

1. In Triton Control, open the deployed Triton instance.
2. Select model `iris_classifier`.
3. Open **Inference** and use the manual input view.
4. Paste the JSON request body from the curl command below.
5. Run inference.

For terminal testing, replace `localhost:8000` with the instance HTTP endpoint
unless you are port-forwarding it locally.

```bash
curl -X POST "http://localhost:8000/v2/models/iris_classifier/infer" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": [
      {
        "name": "FEATURES",
        "shape": [2, 4],
        "datatype": "FP32",
        "data": [
          5.1, 3.5, 1.4, 0.2,
          6.7, 3.1, 4.7, 1.5
        ]
      }
    ],
    "outputs": [
      { "name": "CLASS_ID" },
      { "name": "PROBABILITIES" }
    ]
  }'
```

## Optional Python Client

The Python client also calls the deployed Triton instance. Change the client URL
if your instance is not reachable at `localhost:8000`.

```bash
pip install tritonclient[http] numpy
python infer_client.py --url localhost:8000
```
