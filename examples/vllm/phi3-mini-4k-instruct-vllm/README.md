# Phi-3 Mini 4K Instruct with Triton vLLM

Small vLLM example for checking the Triton Control vLLM deploy path before
trying a larger model.

## Model

- Model: `microsoft/Phi-3-mini-4k-instruct`
- Backend: `vllm`
- Target: one CUDA GPU, up to 16 GB VRAM
- Artifact directory: `phi3_mini_4k_instruct/1/model/`
- Config: `phi3_mini_4k_instruct/1/model.json`

`model/` is not committed. Create it with the notebook.

## 1. Create Development Workspace

In Triton Control, open **Development** and create the workspace:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-py3` |
| Image already has Development installed | Disabled |
| Workspace storage | At least `30Gi` |
| GPU count | `0` |

When the workspace is ready, open code-server from **Development**.

Install the Jupyter extension if needed. If the kernel is missing, run:

```bash
pip install notebook ipykernel
```

## 2. Create the Repository and Artifact

Choose one path:

### Option A: Use the Example Repository

Copy or upload this example folder into `/workspace`.

### Option B: Create the Structure with the Plugin

1. In code-server, run **New Model Repository** from the Triton Control plugin.
2. Choose **Single model**.
3. Enter `model` as the model repository folder.
4. Enter `phi3_mini_4k_instruct` as the model name.
5. Choose the vLLM backend model template.
6. Upload and replace this example's `config.pbtxt`, `model.json`, notebook, and
   client into the generated repository with the context menu.

Then open and run:

```text
convert_phi3_mini_4k_instruct_to_vllm.ipynb
```

This writes:

```text
phi3_mini_4k_instruct/1/model/
phi3_mini_4k_instruct/1/model.json
```

The notebook progress bar can stay at `0%`. Check the real download progress
from the code-server terminal:

```bash
du -sh phi3_mini_4k_instruct/1/model/.cache/huggingface/download
ls -lh phi3_mini_4k_instruct/1/model/*.safetensors
```

The download is complete when both weight shards exist:

```text
phi3_mini_4k_instruct/1/model/model-00001-of-00002.safetensors
phi3_mini_4k_instruct/1/model/model-00002-of-00002.safetensors
```

If the size does not change for several minutes, interrupt the notebook cell and
run it again. Hugging Face resumes from the partial download.

Before deploying, verify that the model directory contains the downloaded
weights:

```bash
find phi3_mini_4k_instruct/1/model -maxdepth 1 -type f | head
```

If this prints nothing or the directory does not exist, rerun the notebook. The
deployment will fail with `Cannot find any model weights` when
`phi3_mini_4k_instruct/1/model/` is missing from the uploaded repository.

Default `model.json`:

```json
{
  "model": "./model",
  "tokenizer": "./model",
  "dtype": "float16",
  "max_model_len": 4096,
  "gpu_memory_utilization": 0.75,
  "trust_remote_code": true,
  "enforce_eager": true
}
```

## 3. Deploy

Use these deployment settings:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-vllm-python-py3` |
| GPU count | At least `1` |

1. In the opened code-server Explorer, right-click this repository folder.
2. Select **Triton Control: Deploy Model Repository** from the context menu.
3. Select S3 settings.
4. Enter the deployment settings shown above.
5. Deploy.

If the webview fails because HTTPS is not trusted, run **Triton Control: Upload
Model Repository (Simple Wizard)** instead.

## 4. Test Inference

Use the Triton Control instance inference view first:

1. In Triton Control, open the deployed Triton instance.
2. Select model `phi3_mini_4k_instruct`.
3. Open **Inference** and use the manual input view.
4. Paste the JSON request body from the curl command below.
5. Run inference.

For terminal testing, replace `localhost:8000` with the instance HTTP endpoint
unless you are port-forwarding it locally.

```bash
curl -X POST "http://localhost:8000/v2/models/phi3_mini_4k_instruct/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "text_input": "Give me a small vLLM deployment checklist.",
    "parameters": {
      "stream": false,
      "max_tokens": 128,
      "temperature": 0.2,
      "top_p": 0.95
    }
  }'
```

The response contains `text_output`.

## Optional Python Client

Tests the deployed Triton instance over gRPC. Replace `localhost:8001` with the
instance gRPC endpoint unless you are port-forwarding it locally:

```bash
pip install tritonclient[grpc] numpy
python infer_client.py --url localhost:8001 "Give me a small vLLM deployment checklist."
```
