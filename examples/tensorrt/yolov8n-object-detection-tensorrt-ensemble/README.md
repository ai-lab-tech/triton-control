# YOLOv8n Object Detection with TensorRT

Object detection pipeline served as one Triton ensemble with a TensorRT plan in
the middle:

```text
preprocess -> yolov8_trt -> postprocess
```

## Model

- Model: YOLOv8n exported to ONNX, then built as a TensorRT plan
- Public model: `yolov8_trt_pipeline`
- Artifact: `yolov8_trt/1/model.plan`
- Backends: `ensemble`, `python`, `tensorrt_plan`
- Input: `IMAGE`, `UINT8`, shape `[640, 640, 3]`
- Outputs:
  - `BOXES`, shape `[N, 4]`
  - `SCORES`, shape `[N]`
  - `CLASS_IDS`, shape `[N]`

`model.plan` is not committed. Create it with the notebook.

TensorRT plan files are hardware and runtime specific. Build this example on
the same GPU class, with the same Triton image, that you will use for serving.
Rebuild the plan if the GPU, CUDA, TensorRT, or Triton image changes.

## 1. Create Development Workspace

In Triton Control, open **Development** and create the workspace:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:25.02-py3` |
| Image already has Development installed | Disabled |
| Workspace storage | At least `20Gi` |
| GPU count | `1` |

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
2. Choose the `Python -> TensorRT -> Python` ensemble pipeline template.
3. Use `yolov8_trt_pipeline` as the public ensemble model.
4. Use `preprocess`, `yolov8_trt`, and `postprocess` as the step names.
5. Copy this example's configs, Python models, helper scripts, and notebook into
   the generated repository.

Keep `yolov8_trt_pipeline/1/.keep` in the repository and upload it with the
model files. Triton requires at least one version under the ensemble model
folder, and object storage does not preserve empty directories.

Then open and run:

```text
export_yolov8_to_tensorrt.ipynb
```

This writes:

```text
yolov8n.onnx
yolov8_trt/1/model.plan
```

The notebook uses `trtexec` from the Triton image. Keep the same image for
build and deploy:

```text
nvcr.io/nvidia/tritonserver:25.02-py3
```

## 3. Deploy

Use these deployment settings:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:25.02-py3` |
| GPU count | At least `1`; `yolov8_trt/config.pbtxt` uses `KIND_GPU` |

1. In the opened code-server Explorer, right-click this repository folder.
2. Select **Triton Control: Deploy Model Repository** from the context menu.
3. Select S3 settings.
4. Enter the deployment settings shown above.
5. Deploy the whole folder as one model repository.

Run inference against `yolov8_trt_pipeline`, not the internal child models.

## 4. Test Inference

Generate a request from an image file:

```bash
pip install pillow numpy
python make_curl_payload.py path/to/image.jpg > request.json
```

Use the Triton Control instance inference view first:

1. In Triton Control, open the deployed Triton instance.
2. Select model `yolov8_trt_pipeline`.
3. Open **Inference** and use the manual input view.
4. Paste the JSON request body from `request.json`.
5. Run inference.

For terminal testing, replace `localhost:8000` with the instance HTTP endpoint
unless you are port-forwarding it locally.

Send the same request with curl:

```bash
curl -X POST "http://localhost:8000/v2/models/yolov8_trt_pipeline/infer" \
  -H "Content-Type: application/json" \
  --data-binary @request.json
```

## Optional Python Client

The Python client also calls the deployed Triton instance. Change the client URL
if your instance is not reachable at `localhost:8000`.

```bash
pip install tritonclient[http] pillow numpy
python infer_client.py path/to/image.jpg --url localhost:8000
```
