# Local Model Upload to a Polling Triton Instance

Train a small scikit-learn breast cancer classifier, upload the model folder
with Triton Control's S3 Browser, and let an existing Triton instance load it by
repository polling.

Do not use **Deploy Model Repository** for this walkthrough. Create the polling
instance first, then upload the existing model folder with the instance
**S3 Browser**.

## Model

- Model name: `breast_cancer_classifier`
- Backend: `python`
- Artifact: `local_breast_cancer_repository/breast_cancer_classifier/1/model.joblib`
- Input: `FEATURES`, `FP32`, shape `[30]`
- Outputs: `CLASS_ID` `[1]`, `PROBABILITIES` `[2]`

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

## 2. Create the Repository and Artifact

Choose one path:

### Option A: Use the Example Repository

Copy or upload this example folder into `/workspace`.

### Option B: Create the Structure with the Plugin

1. In code-server, run **New Model Repository** from the Triton Control plugin.
2. Choose the Python backend template.
3. Use `breast_cancer_classifier` as the model name.
4. Keep `KIND_CPU` in `config.pbtxt`.
5. Copy this example's `model.py` and notebook into the generated repository.

Then open and run:

```text
create_breast_cancer_classifier.ipynb
```

## 3. Create the Polling Deployment

In Triton Control, open **Add Deployment** and create a Triton instance with an
empty S3 repository prefix.

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:25.02-py3` |
| Repository prefix | `team-a/polling-repository` |
| GPU count | `0`; `config.pbtxt` uses `KIND_CPU` |
| vLLM model backend | Disabled |
| Model control mode | `poll` |
| Poll interval | `30` |
| `requirements.txt` | `scikit-learn` and `joblib` |

The instance **S3 Connection** must point to the same bucket and repository
prefix as the deployment.

## 4. Upload the Model Folder

In the instance **S3 Browser**, upload this existing model folder:

```text
local_breast_cancer_repository/breast_cancer_classifier
```

Correct S3 layout:

```text
team-a/polling-repository/
  breast_cancer_classifier/
    config.pbtxt
    1/
      model.py
      model.joblib
```

Do not upload the outer `local_breast_cancer_repository` folder.

## 5. Wait and Verify

Wait at least one poll interval, refresh the Triton instance, and confirm
`breast_cancer_classifier` appears in the model list.

If it does not appear, check:

- `model.joblib` exists
- `config.pbtxt` was uploaded
- S3 does not contain an extra parent folder
- `requirements.txt` includes `scikit-learn` and `joblib`

## 6. Test Inference

Use the Triton Control instance inference view first:

1. In Triton Control, open the polling Triton instance after it has loaded the
   model.
2. Select model `breast_cancer_classifier`.
3. Open **Inference** and use the manual input view.
4. Paste the JSON request body from the curl command below.
5. Run inference.

For terminal testing, replace `localhost:8000` with the instance HTTP endpoint
unless you are port-forwarding it locally.

```bash
curl -X POST "http://localhost:8000/v2/models/breast_cancer_classifier/infer" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": [
      {
        "name": "FEATURES",
        "shape": [1, 30],
        "datatype": "FP32",
        "data": [
          17.99, 10.38, 122.80, 1001.0, 0.11840,
          0.27760, 0.30010, 0.14710, 0.24190, 0.07871,
          1.0950, 0.9053, 8.589, 153.40, 0.006399,
          0.04904, 0.05373, 0.01587, 0.03003, 0.006193,
          25.38, 17.33, 184.60, 2019.0, 0.16220,
          0.66560, 0.71190, 0.26540, 0.46010, 0.11890
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

The Python client also calls the polling Triton instance. Change the client URL
if your instance is not reachable at `localhost:8000`.

```bash
pip install tritonclient[http] numpy
python infer_client.py --url localhost:8000
```
