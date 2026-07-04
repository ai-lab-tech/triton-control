# Qwen2.5 0.5B Instruct with Triton TensorRT-LLM

Small instruction LLM served through Triton's TensorRT-LLM LLM API path. This
is a practical smoke test for TensorRT-LLM deployment without requiring a large
model.

## Model

- Model: `Qwen/Qwen2.5-0.5B-Instruct`
- Backend path: TensorRT-LLM LLM API with Triton Python backend
- Target: one CUDA GPU
- Model directory: `qwen2_5_0_5b_instruct_trtllm/`
- Runtime config: `qwen2_5_0_5b_instruct_trtllm/1/model.yaml`

The official TensorRT-LLM LLM API `model.py` and `helpers.py` files are not
committed. Create them with the notebook so they can match the TensorRT-LLM
version in the container you choose.

## 1. Create Development Workspace

In Triton Control, open **Development** and create the workspace:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3` |
| Image already has Development installed | Disabled |
| Workspace storage | At least `30Gi` |
| GPU count | `1` |

When the workspace is ready, open code-server from **Development**.

Install the Jupyter extension if needed. If the kernel is missing, run:

```bash
pip install notebook ipykernel
```

For private or gated Hugging Face models, set `HF_TOKEN` before deployment.
Qwen2.5 0.5B Instruct is public at the time this example was written.

## 2. Create the Repository and Runtime Files

Choose one path:

### Option A: Use the Example Repository

Copy or upload this example folder into `/workspace`.

### Option B: Create the Structure with the Plugin

1. In code-server, run **New Model Repository** from the Triton Control plugin.
2. Choose the TensorRT-LLM template.
3. Use `qwen2_5_0_5b_instruct_trtllm` as the model name.
4. Copy this example's README, `config.pbtxt`, `model.yaml`, notebook, and
   client into the generated repository.

Then open and run:

```text
prepare_qwen2_5_0_5b_trtllm.ipynb
```

This writes:

```text
qwen2_5_0_5b_instruct_trtllm/1/model.py
qwen2_5_0_5b_instruct_trtllm/1/helpers.py
```

By default the notebook downloads those files from the current TensorRT-LLM
`main` branch. To pin the files to a specific TensorRT-LLM release, set
`TENSORRT_LLM_REF` before running the notebook, for example:

```bash
export TENSORRT_LLM_REF=v1.0.0
```

Default `model.yaml`:

```yaml
model: Qwen/Qwen2.5-0.5B-Instruct
backend: "pytorch"

tensor_parallel_size: 1
pipeline_parallel_size: 1

triton_config:
  max_batch_size: 0
  decoupled: False
```

## 3. Deploy

Use these deployment settings:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3` |
| GPU count | At least `1` |

1. In the opened code-server Explorer, right-click this repository folder.
2. Select **Triton Control: Deploy Model Repository** from the context menu.
3. Select S3 settings.
4. Enter the deployment settings shown above.
5. Deploy.

Use the same TensorRT-LLM image family for the Development workspace and the
deployment. If you change the image tag, rerun the notebook with matching
TensorRT-LLM backend files.

## 4. Test Inference

Use the Triton Control instance inference view first:

1. In Triton Control, open the deployed Triton instance.
2. Select model `qwen2_5_0_5b_instruct_trtllm`.
3. Open **Inference** and use the manual input view.
4. Paste the JSON request body from the curl command below.
5. Run inference.

For terminal testing, replace `localhost:8000` with the instance HTTP endpoint
unless you are port-forwarding it locally.

```bash
curl -X POST "http://localhost:8000/v2/models/qwen2_5_0_5b_instruct_trtllm/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "text_input": "Give me a compact TensorRT-LLM deployment checklist.",
    "sampling_param_max_tokens": 128,
    "sampling_param_temperature": 0.2,
    "sampling_param_top_p": 0.95,
    "sampling_param_exclude_input_from_output": true
  }'
```

The response contains `text_output`.

## Optional Python Client

Tests the deployed Triton instance over HTTP. Replace `localhost:8000` with the
instance HTTP endpoint unless you are port-forwarding it locally:

```bash
pip install requests
python infer_client.py --url localhost:8000 "Give me a compact TensorRT-LLM deployment checklist."
```
