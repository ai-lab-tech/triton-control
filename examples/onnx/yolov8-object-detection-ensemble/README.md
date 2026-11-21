# YOLOv8 Object Detection Ensemble

Object detection pipeline served as one Triton ensemble:

```text
preprocess -> yolov8_onnx -> postprocess
```

## Model

- Model: YOLOv8n exported to ONNX
- Public model: `yolo_pipeline`
- Artifact: `yolov8_onnx/1/model.onnx`
- Backends: `ensemble`, `python`, `onnxruntime_onnx`
- Input: `IMAGE`, `UINT8`, shape `[640, 640, 3]`
- Outputs:
  - `BOXES`, shape `[N, 4]`
  - `SCORES`, shape `[N]`
  - `CLASS_IDS`, shape `[N]`

`model.onnx` is not committed. Create it with the notebook.

## 1. Create Development Workspace

In Triton Control, open **Development** and create the workspace:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-py3` |
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
2. Choose **Ensemble**.
3. Enter `model` as the repository name.
4. Choose **Custom pipeline** as the backend.
5. Enter `yolo_pipeline` as the ensemble model name.
6. Enter `3` as the number of steps.
7. Enter `preprocess` as the step 1 model name.
8. Choose **Python** as the step 1 backend.
9. Enter `yolov8_onnx` as the step 2 model name.
10. Choose **ONNX Runtime** as the step 2 backend.
11. Enter `postprocess` as the step 3 model name.
12. Choose **Python** as the step 3 backend.
13. Upload and replace this example's generated model files and `config.pbtxt`
    files in the generated repository.

Keep `yolo_pipeline/1/.keep` in the repository and upload it with the model
files. Triton requires at least one version under the ensemble model folder, and
object storage does not preserve empty directories.

Then open and run:

```text
export_yolov8_to_onnx.ipynb
```

This writes:

```text
yolov8_onnx/1/model.onnx
```

## 3. CPU or GPU

The example defaults to GPU in `yolov8_onnx/config.pbtxt`:

```protobuf
kind: KIND_GPU
```

For CPU-only testing, change it to:

```protobuf
kind: KIND_CPU
```

## 4. Deploy

Use these deployment settings:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-py3` |
| GPU count | At least `1` if you keep `KIND_GPU`; `0` if you changed ONNX to `KIND_CPU` |

1. In the opened code-server Explorer, right-click this repository folder.
2. Select **Triton Control: Deploy Model Repository** from the context menu.
3. Select S3 settings.
4. Enter the deployment settings shown above.
5. Deploy.

## 5. Test Inference

Generate a request with a built-in test image:

```bash
python3 make_request_payload.py
```

This writes `request.json` next to `make_request_payload.py`.

Use the Triton Control instance inference view first:

1. In Triton Control, open the deployed Triton instance.
2. Select model `yolo_pipeline`.
3. Open **Inference** and use the manual input view.
4. Paste the JSON request body from `request.json`.
5. Run inference.

For terminal testing, replace `localhost:8000` with the instance HTTP endpoint
unless you are port-forwarding it locally.

Send the same request with curl:

```bash
curl -X POST "http://localhost:8000/v2/models/yolo_pipeline/infer" \
  -H "Content-Type: application/json" \
  --data-binary @request.json
```

## 6. Capture the Experiment for Engineering

Use this capture to hand off the detector, including its preprocessing and
postprocessing, for evaluation on a reviewed set of street scenes. The
development deployment uses the weights already uploaded to S3. Keep large
artifacts there and select the source files you want engineering to review.

After running inference, change `yolov8_onnx/config.pbtxt` in the workspace, for
example by changing `KIND_GPU` to `KIND_CPU` for a proposed CPU deployment. Keep
the live experiment deployed while you capture the edited config.

Send the following JSON to `POST /api/instances/{instance_id}/preparation-requests`
with your authenticated session and a new `Idempotency-Key`. Replace the
deployment UID and generation with the values from
`GET /api/instances/{instance_id}/pods`. Use your actual workspace ID, adjust the
folder name if needed, and replace the commit placeholder with the output of
`git rev-parse HEAD` from the checkout that supplied the example.

```json
{
  "title": "Evaluate the YOLOv8 street-scene detector on CPU",
  "deployment_uid": "DEPLOYMENT-UID",
  "expected_generation": 1,
  "models": [
    {"name": "yolo_pipeline", "versions": ["1"]},
    {"name": "preprocess", "versions": ["1"]},
    {"name": "yolov8_onnx", "versions": ["1"]},
    {"name": "postprocess", "versions": ["1"]}
  ],
  "workspace": {
    "workspace_id": 7,
    "files": [
      "yolov8-object-detection-ensemble/README.md",
      "yolov8-object-detection-ensemble/yolov8_onnx/config.pbtxt"
    ]
  },
  "source": {
    "kind": "example",
    "repository": "github.com/ai-lab-tech/triton-control",
    "commit": "REPLACE_WITH_FULL_CHECKOUT_COMMIT",
    "project_path": "examples/onnx/yolov8-object-detection-ensemble",
    "workspace_model_root": "yolov8-object-detection-ensemble",
    "source_documents": ["yolov8-object-detection-ensemble/README.md"]
  },
  "expected_behavior": "Return boxes, confidence scores and class IDs for cars, bicycles and people.",
  "acceptance_criteria": "Measure per-class recall and p95 inference latency on a reviewed set of daytime and dusk street scenes."
}
```

Follow the returned operation URL and open the snapshot receipt once it
succeeds. The receipt inventories the S3 model files separately from the edited
config and lists `workspace_model_bytes_differ` for the edited config. The partial file selection also produces
`workspace_model_comparison_incomplete`.
Add any model card and license supplied with your weights to the workspace
selection and declare their paths in `source`. The preparation task tracks
missing provenance and evaluation inputs before the CPU configuration can be
validated. See the [snapshot capture guide](../../../docs/experiment-snapshots.md)
for other source types and recapture behavior.

## Optional Python Client

The Python client also calls the deployed Triton instance. Change the client URL
if your instance is not reachable at `localhost:8000`.

```bash
pip install tritonclient[http] pillow numpy
python infer_client.py path/to/image.jpg --url localhost:8000
```
