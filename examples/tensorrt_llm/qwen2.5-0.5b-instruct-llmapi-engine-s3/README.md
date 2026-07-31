# Qwen2.5 0.5B Instruct with Native TensorRT-LLM Backend and Triton S3

Production-style variant for Qwen2.5. The notebook downloads the Hugging Face
model, selects the TensorRT engine implementation of the LLM API, and saves it
with `LLM.save()` inside the checked-in Triton repository. Triton loads the
saved engine instead of rebuilding Hugging Face weights during startup.

## Model

- Model: `Qwen/Qwen2.5-0.5B-Instruct`
- Triton backend: `tensorrtllm`
- Runtime: native Triton TensorRT-LLM backend
- Model name: `qwen2_5_0_5b_instruct_llmapi_engine`
- Model directory: `qwen2_5_0_5b_instruct_llmapi_engine/`
- Saved engine directory: `qwen2_5_0_5b_instruct_llmapi_engine/1/`
- Tokenizer directory: `qwen2_5_0_5b_instruct_llmapi_engine/1/`
- Runtime config: `qwen2_5_0_5b_instruct_llmapi_engine/config.pbtxt`
- Endpoint: `/v2/models/qwen2_5_0_5b_instruct_llmapi_engine/infer`

Generated `hf_model/`, engine files under `qwen2_5_0_5b_instruct_llmapi_engine/1/`, and
tokenizer files under `qwen2_5_0_5b_instruct_llmapi_engine/1/` are not committed.
Create them with the notebook before deploying.

## 1. Create Development Workspace

In Triton Control, open **Development** and create the workspace:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3` |
| Image already has Development installed | Disabled |
| Workspace storage | At least `30Gi` |
| GPU count | `1` |

When the workspace is ready, open code-server from **Development**.

Install the Jupyter extension if needed. Prefer kernel from Triton virtual
environment:

```bash
/opt/venv-tritonserver/bin/python -m pip install notebook ipykernel
/opt/venv-tritonserver/bin/python -m ipykernel install --user \
  --name tritonserver --display-name "Python (Triton Server)"
```

Select **Python (Triton Server)** as notebook kernel. Notebook also discovers
`/opt/venv-tritonserver` packages when system Python 3.12 kernel is used.

For private or gated Hugging Face models, set `HF_TOKEN` before running the
download cell.

Unlike the non-engine example, this notebook calls `LLM.save()` to save a
TensorRT engine. The development workspace therefore needs a GPU. The build
GPU and serving GPU should have the same architecture. Use the same Triton
image tag for preparation and deployment.

## 2. Create the Repository and Engine

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
3. Enter `qwen2.5-0.5b-instruct-llmapi-engine-s3` as the repository target
   folder name.
4. Enter `qwen2_5_0_5b_instruct_llmapi_engine` as the model name.
5. Choose the TensorRT-LLM backend model template.
6. Copy this example's notebook, README, client, and `config.pbtxt` into the
   repository folder.

Then open and run:

```text
prepare_qwen2_5_0_5b_llmapi_engine_s3.ipynb
```

The notebook writes:

```text
qwen2_5_0_5b_instruct_llmapi_engine/config.pbtxt
qwen2_5_0_5b_instruct_llmapi_engine/1/config.json
qwen2_5_0_5b_instruct_llmapi_engine/1/rank0.engine
qwen2_5_0_5b_instruct_llmapi_engine/1/tokenizer.json
```

`config.pbtxt` is part of this example. Native `tensorrtllm` backend loads the
saved engine directly; no `model.py` exists or is required. Tokenizer is kept
for the external inference client, not loaded by Triton backend.

Before deploying, verify that the generated repository is complete:

```bash
test -f qwen2_5_0_5b_instruct_llmapi_engine/config.pbtxt
test -f qwen2_5_0_5b_instruct_llmapi_engine/1/config.json
test -f qwen2_5_0_5b_instruct_llmapi_engine/1/rank0.engine
test -f qwen2_5_0_5b_instruct_llmapi_engine/1/tokenizer.json
```

## 3. Deploy

Use these deployment settings:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3` |
| GPU count | `1` |
| Memory | At least `12Gi`; use `16Gi` if available |
| Repository sync | Direct/native Triton S3 |
| Model control mode | Explicit |

1. In the opened code-server Explorer, right-click the repository folder for
   this example. It must be the folder that directly contains
   `qwen2_5_0_5b_instruct_llmapi_engine/config.pbtxt`.
2. Select **Triton Control: Deploy Model Repository** from the context menu.
3. Select S3 settings.
4. Enter the deployment settings shown above.
5. Deploy.

Build GPU architecture and deploy GPU architecture must match. Rebuild the
engine when the image tag, TensorRT-LLM version, GPU architecture, or engine
build settings change.

Use explicit model control on memory-constrained GPUs. To apply a changed
`config.pbtxt`, unload the model, save the file, and load the model again. A
poll-based live reload may try to initialize the replacement before releasing
the current engine and can run out of GPU memory.

Do not deploy this example with a `4Gi` container memory limit. If Kubernetes
reports `OOMKilled` with exit code `137`, increase the Triton deployment memory
and redeploy.

## 4. Test Inference

Native backend accepts token IDs. Replace `localhost:8000` with instance HTTP
endpoint unless port-forwarding locally. Following request uses example token
IDs; use Python client below for prompt tokenization and response decoding.

```bash
curl -X POST "http://localhost:8000/v2/models/qwen2_5_0_5b_instruct_llmapi_engine/infer" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": [
      {
        "name": "input_ids",
        "shape": [1, 3],
        "datatype": "INT32",
        "data": [[9707, 498, 30]]
      },
      {
        "name": "input_lengths",
        "shape": [1, 1],
        "datatype": "INT32",
        "data": [[3]]
      },
      {
        "name": "request_output_len",
        "shape": [1, 1],
        "datatype": "INT32",
        "data": [[80]]
      }
    ],
    "outputs": [{ "name": "output_ids" }, { "name": "sequence_length" }]
  }'
```

If loading fails with insufficient GPU memory after editing `config.pbtxt`,
unload the old model first or restart Triton before loading the new
configuration.

## Optional Python Client

```bash
python3 -m pip install requests transformers
python3 infer_client.py --url localhost:8000 "Give me a compact TensorRT-LLM production checklist."
```
