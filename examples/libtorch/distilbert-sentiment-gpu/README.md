# DistilBERT Sentiment on GPU

TorchScript DistilBERT sentiment classifier served with Triton's native
PyTorch/LibTorch backend.

## Model

- Source model: `distilbert-base-uncased-finetuned-sst-2-english`
- Served artifact: `distilbert_sentiment/1/model.pt`
- Backend/platform: `pytorch_libtorch`
- Target: GPU
- Input:
  - `input__0`, `INT64`, shape `[32]`
  - `input__1`, `INT64`, shape `[32]`
- Output: `output__0`, `FP32`, shape `[2]`

## 1. Create Development Workspace

In Triton Control, open **Development** and create the workspace:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/pytorch:26.06-py3` |
| Image already has Development installed | Disabled |
| Workspace storage | At least `20Gi` |
| GPU count | `1` |

When the workspace is ready, open code-server from **Development**.

The workspace uses NVIDIA's PyTorch image because the notebook exports the
TorchScript artifact with Python `torch`. The deployment step below still uses
the Triton image because Triton serves the exported artifact with the
PyTorch/LibTorch backend.

## 2. Create the Repository and Artifact

Choose one path:

### Option A: Use the Example Repository

Copy or upload this example folder into `/workspace`.

### Option B: Create the Structure with the Plugin

1. In code-server, run **New Model Repository** from the Triton Control plugin.
2. Choose **Single model**.
3. Enter `model` as the model repository folder.
4. Enter `distilbert_sentiment` as the model name.
5. Choose the PyTorch/LibTorch model template.
6. Upload and replace this example's `config.pbtxt` and notebook into the generated
   repository with the context menu.

Then open and run:

```text
create_distilbert_sentiment.ipynb
```

This writes:

```text
distilbert_sentiment/1/model.pt
```

## 3. Deploy

Use these deployment settings:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-py3` |
| GPU count | At least `1`; `config.pbtxt` uses `KIND_GPU` |

1. In the opened code-server Explorer, right-click this repository folder.
2. Select **Triton Control: Deploy Model Repository** from the context menu.
3. Select S3 settings.
4. Enter the deployment settings shown above.
5. Deploy.

## 4. Test Inference

Use the Triton Control instance inference view first:

1. In Triton Control, open the deployed Triton instance.
2. Select model `distilbert_sentiment`.
3. Open **Inference** and use the manual input view.
4. Paste the JSON request body from the curl command below.
5. Run inference.

For terminal testing, replace `localhost:8000` with the instance HTTP endpoint
unless you are port-forwarding it locally.

This sends token IDs for a short positive sentence.

```bash
curl -X POST "http://localhost:8000/v2/models/distilbert_sentiment/infer" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": [
      {
        "name": "input__0",
        "shape": [1, 32],
        "datatype": "INT64",
        "data": [
          101, 2023, 3185, 2003, 10392, 102,
          0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
          0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
          0, 0, 0, 0, 0, 0
        ]
      },
      {
        "name": "input__1",
        "shape": [1, 32],
        "datatype": "INT64",
        "data": [
          1, 1, 1, 1, 1, 1,
          0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
          0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
          0, 0, 0, 0, 0, 0
        ]
      }
    ],
    "outputs": [{ "name": "output__0" }]
  }'
```

Label `0` is negative and label `1` is positive.

## Optional Python Client

The Python client also calls the deployed Triton instance. Change the client URL
if your instance is not reachable at `localhost:8000`.

Tokenizes normal text before sending it to Triton:

```bash
pip install tritonclient[http] numpy transformers
python infer_client.py --url localhost:8000
```
