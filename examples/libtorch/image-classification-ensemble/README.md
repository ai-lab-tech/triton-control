# Image Classification Ensemble

One public Triton endpoint that runs Python image preprocessing and then a
TorchScript ResNet18 classifier.

## Model

- Public model: `image_pipeline`
- Input: `IMAGE`, `UINT8`, shape `[224, 224, 3]`
- Output: `PROBABILITIES`, `FP32`, shape `[1000]`
- Backends: `ensemble`, `python`, `pytorch_libtorch`
- Artifact: `resnet18_libtorch/1/model.pt`

## 1. Create Development Workspace

In Triton Control, open **Development** and create the workspace:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/pytorch:26.06-py3` |
| Image already has Development installed | Disabled |
| Workspace storage | At least `20Gi` |
| GPU count | `0` |

When the workspace is ready, open code-server from **Development**.

Use NVIDIA's PyTorch image for the workspace because the notebook exports a
TorchScript artifact with Python `torch` and `torchvision`. Do not install
`torch` or `torchvision` in the notebook when using this image. The deployment
step below still uses the Triton image because Triton serves the exported
artifact with the PyTorch/LibTorch backend.

## 2. Create the Repository and Artifact

Choose one path:

### Option A: Use the Example Repository

Copy or upload this example folder into `/workspace`.

### Option B: Create the Structure with the Plugin

1. In code-server, run **New Model Repository** from the Triton Control plugin.
2. Choose **Ensemble**.
3. Enter `model` as the repository name.
4. Choose **Custom pipeline** as the backend.
5. Enter `image_pipeline` as the ensemble model name.
6. Enter `2` as the number of steps.
7. Enter `preprocess` as the step 1 model name.
8. Choose **Python** as the step 1 backend.
9. Enter `resnet18_libtorch` as the step 2 model name.
10. Choose **PyTorch/LibTorch** as the step 2 backend.
11. Upload and replace this example's generated model files and `config.pbtxt`
    files in the generated repository.

Keep `image_pipeline/1/.keep` in the repository and upload it with the model
files. Triton requires at least one version under the ensemble model folder, and
object storage does not preserve empty directories.

Then open and run:

```text
create_resnet18_libtorch.ipynb
```

This writes:

```text
resnet18_libtorch/1/model.pt
```

## 3. Deploy

Use these deployment settings:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-py3` |
| GPU count | `0`; this example does not force `KIND_GPU` |

1. In the opened code-server Explorer, right-click this repository folder.
2. Select **Triton Control: Deploy Model Repository** from the context menu.
3. Select S3 settings.
4. Enter the deployment settings shown above.
5. Deploy the whole folder as one model repository.

Run inference against `image_pipeline`, not the internal child models.

## 4. Test Inference

Generate a small JSON request:

```bash
python make_curl_payload.py
```

Use the Triton Control instance inference view first:

1. In Triton Control, open the deployed Triton instance.
2. Select model `image_pipeline`.
3. Open **Inference** and use the manual input view.
4. Paste the JSON request body from `request.json`.
5. Run inference.

For terminal testing, replace `localhost:8000` with the instance HTTP endpoint
unless you are port-forwarding it locally.

Send the same request with curl:

```bash
curl -X POST "http://localhost:8000/v2/models/image_pipeline/infer" \
  -H "Content-Type: application/json" \
  --data-binary @request.json
```

The response contains `PROBABILITIES` with shape `[1, 1000]`.

## Optional Python Client

The Python client also calls the deployed Triton instance. Change the client URL
if your instance is not reachable at `localhost:8000`.

```bash
pip install tritonclient[http] numpy
python infer_client.py --url localhost:8000
```
