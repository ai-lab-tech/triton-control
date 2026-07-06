# Local Host Model Upload to a Polling Triton Instance

This example starts with a Triton model repository that exists locally on your
host:

```text
local_breast_cancer_repository/breast_cancer_classifier
```

Create a polling Triton deployment with an S3 repository prefix, then upload the
local model folder into that running instance with the instance **S3 Browser**.
Triton loads the uploaded files from S3 by repository polling.

Do not use **Deploy Model Repository** for this walkthrough. Create the polling
instance first, then upload the existing model folder with the instance
**S3 Browser**.

## Model

- Model name: `breast_cancer_classifier`
- Backend: `python`
- Artifact: `local_breast_cancer_repository/breast_cancer_classifier/1/model.joblib`
- Input: `FEATURES`, `FP32`, shape `[30]`
- Outputs: `CLASS_ID` `[1]`, `PROBABILITIES` `[2]`

Use **Advanced Infrastructure** > **Extra Python Packages** to install
`scikit-learn` and `joblib` before Triton starts.

## 1. Create the Polling Deployment

In Triton Control, open **Add Deployment** and create a Triton instance that
uses an S3 repository prefix. The prefix is the remote model repository root
where the instance S3 Browser will upload the local model files.

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:25.02-py3` |
| Repository prefix | `team-a/polling-repository` |
| GPU count | `0` |
| vLLM model backend | Disabled |
| Model control mode | `poll` |
| Poll interval | `30` |
| Advanced Infrastructure > Extra Python Packages | `scikit-learn` and `joblib` |

The instance **S3 Connection** must point to the same bucket and repository
prefix as the deployment. For the example prefix above, the uploaded files must
end up under:

```text
team-a/polling-repository/
```

## 2. Prepare the Local Repository and Artifact

The repository structure is local on the host. If `model.joblib` already exists,
you can skip artifact generation and upload the folder in the next step.

Use this repository from the example folder:

```text
local_breast_cancer_repository/breast_cancer_classifier
```

Run the notebook only if you need to create the missing large artifact:

```text
create_breast_cancer_classifier.ipynb
```

If you run the notebook in a Triton Control code-server workspace, download or
otherwise copy the generated `breast_cancer_classifier` folder back to the local
host before uploading it with the instance **S3 Browser**.

## 3. Upload the Local Model Folder

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

## 4. Wait and Verify

Wait at least one poll interval, refresh the Triton instance, and confirm
`breast_cancer_classifier` appears in the model list.

If it does not appear, check:

- `model.joblib` exists
- `config.pbtxt` was uploaded
- S3 does not contain an extra parent folder
- **Advanced Infrastructure** > **Extra Python Packages** includes
  `scikit-learn` and `joblib`

## 5. Test Inference

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
