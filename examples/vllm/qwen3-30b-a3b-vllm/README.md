# Qwen3 30B A3B with Triton vLLM

Qwen3 30B A3B served by Triton's vLLM backend. The repository is uploaded to
S3 by Triton Control and loaded by Triton's vLLM model repository path.

## Model

- Model: `Qwen/Qwen3-30B-A3B`
- Triton backend: `vllm`
- Runtime: vLLM
- Model directory: `qwen3_30b_a3b/`
- Artifact directory: `qwen3_30b_a3b/1/model/`
- Config: `qwen3_30b_a3b/1/model.json`
- Input: `text_input`, `BYTES`
- Output: `text_output`, `BYTES`

`model/` is not committed. Create it with the notebook before deploying.

## 1. Create Development Workspace

In Triton Control, open **Development** and create the workspace:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-py3` |
| Image already has Development installed | Disabled |
| Workspace storage | At least `96Gi` |
| GPU count | `0` is enough for prepare; `1` is also fine |

When the workspace is ready, open code-server from **Development**.

Install the Jupyter extension if needed. If the kernel or downloader package is
missing, run:

```bash
pip install notebook ipykernel huggingface_hub
```

For private or gated Hugging Face models, set `HF_TOKEN` before running the
download cell.

The prepare notebook only downloads Hugging Face files. It does not need a GPU.
The deployed Triton instance still needs a GPU.

## 2. Create the Repository and Artifact

Choose one repository folder. The repository folder is the folder you upload
and deploy with Triton Control.

### Option A: Use the Example Repository

Put this example at one path only:

```text
<repository-folder>/
```

Do not copy the folder into itself.

### Option B: Create the Structure with the Plugin

1. In code-server, run **New Model Repository** from the Triton Control plugin.
2. Choose **Single model**.
3. Enter `qwen3-30b-a3b-vllm` as the repository target folder name.
4. Enter `qwen3_30b_a3b` as the model name.
5. Choose the vLLM backend model template.
6. Replace the generated files with this example's `config.pbtxt`,
   `model.json`, notebook, README, script, and client.

Then open and run:

```text
convert_qwen3_30b_a3b_to_vllm.ipynb
```

If code-server opens a blank notebook editor, run the equivalent script from the
example folder instead:

```bash
python convert_qwen3_30b_a3b_to_vllm.py
```

This writes:

```text
qwen3_30b_a3b/config.pbtxt
qwen3_30b_a3b/1/model/
qwen3_30b_a3b/1/model.json
```

Before deploying, verify that model weights exist:

```bash
find qwen3_30b_a3b/1/model -maxdepth 1 -type f -name '*.safetensors' | head
```

Default `model.json`:

```json
{
  "model": "./model",
  "tokenizer": "./model",
  "dtype": "auto",
  "max_model_len": 12000,
  "max_num_seqs": 2,
  "max_num_batched_tokens": 4096,
  "tensor_parallel_size": 1,
  "gpu_memory_utilization": 0.7,
  "trust_remote_code": true,
  "enable_prefix_caching": false,
  "enforce_eager": true
}
```

## 3. Deploy

Use these deployment settings:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-vllm-python-py3` |
| GPU count | `1` |
| Memory | `96Gi` on a 128 GB machine; leave the rest for the OS and Kubernetes |
| vLLM model backend | Enabled |

1. In the opened code-server Explorer, right-click the repository folder for
   this example. It must be the folder that directly contains
   `qwen3_30b_a3b/config.pbtxt`.
2. Select **Triton Control: Deploy Model Repository** from the context menu.
3. Select S3 settings.
4. Enter the deployment settings shown above.
5. Deploy.

This example uses `backend: "vllm"`, so deploy it with the vLLM Triton image.

## 4. Test Inference

Use Triton Control for the first inference smoke test:

1. In Triton Control, open the deployed Triton instance.
2. Select model `qwen3_30b_a3b`.
3. Open **Inference**.
4. Paste this JSON body and run inference:

```json
{
  "text_input": "Reason step by step: why is MoE useful for serving this model on a 128 GB GPU?",
  "parameters": {
    "stream": false,
    "max_tokens": 256,
    "temperature": 0.6,
    "top_p": 0.95
  }
}
```

For terminal testing, replace `localhost:8000` with the instance HTTP endpoint
unless you are port-forwarding it locally:

```bash
curl -X POST "http://localhost:8000/v2/models/qwen3_30b_a3b/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "text_input": "Reason step by step: why is MoE useful for serving this model on a 128 GB GPU?",
    "parameters": {
      "stream": false,
      "max_tokens": 256,
      "temperature": 0.6,
      "top_p": 0.95
    }
  }'
```

## Optional Python Client

Tests the deployed Triton instance over gRPC. Replace `localhost:8001` with the
instance gRPC endpoint unless you are port-forwarding it locally:

```bash
pip install tritonclient[grpc] numpy
python infer_client.py --url localhost:8001 "Explain when to reduce max_model_len for Qwen3 30B A3B."
```
