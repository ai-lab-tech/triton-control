# Qwen3 30B A3B with TensorRT-LLM LLM API and Triton S3

Qwen3 30B A3B served by Triton's Python backend with the TensorRT-LLM LLM API.
The repository is uploaded to S3 by Triton Control and loaded by Triton's S3
model repository path.

## Model

- Model: `Qwen/Qwen3-30B-A3B`
- Triton backend: `python`
- Runtime: TensorRT-LLM LLM API
- Model directory: `qwen3_30b_a3b_llmapi/`
- Artifact directory: `qwen3_30b_a3b_llmapi/1/model/`
- Input: `text_input`, `BYTES`, shape `[batch, 1]`
- Output: `text_output`, `BYTES`, shape `[batch, 1]`

`model/` is not committed. Create it with the notebook before deploying.

## 1. Create Development Workspace

In Triton Control, open **Development** and create the workspace:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3` |
| Image already has Development installed | Disabled |
| Workspace storage | At least `80Gi`; use `96Gi` if available |
| GPU count | `0` is enough for prepare; `1` is also fine |

When the workspace is ready, open code-server from **Development**.

Install the Jupyter extension if needed. If the kernel is missing, run:

```bash
pip install notebook ipykernel
```

For private or gated Hugging Face models, set `HF_TOKEN` before running the
download cell.

The prepare notebook only downloads Hugging Face files and installs Python
runtime dependencies. It does not build a TensorRT engine, so the development
workspace does not need to use the same GPU as serving and can run without a
GPU. The deployed Triton instance still needs a GPU.

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
3. Enter `qwen3-30b-a3b-llmapi-s3` as the repository target folder name.
4. Enter `qwen3_30b_a3b_llmapi` as the model name.
5. Choose the Python backend model template.
6. Replace the generated files with this example's `config.pbtxt`, `model.py`,
   notebook, README, and client.

Then open and run:

```text
prepare_qwen3_30b_a3b_llmapi_s3.ipynb
```

This writes:

```text
qwen3_30b_a3b_llmapi/config.pbtxt
qwen3_30b_a3b_llmapi/1/model.py
qwen3_30b_a3b_llmapi/1/model/
qwen3_30b_a3b_llmapi/1/python_deps/
```

The notebook uses temporary helper directories outside the repository folder
and removes them at the end. If an older failed run left `.notebook_deps/`,
`.notebook_cache/`, or `.notebook_home/` in this repository folder, delete them
before deploying. Triton treats every top-level folder in the repository as a
model.

Before deploying, verify that model weights exist:

```bash
find qwen3_30b_a3b_llmapi/1/model -maxdepth 1 -type f -name '*.safetensors' | head
test -d qwen3_30b_a3b_llmapi/1/python_deps/openai
```

## 3. Deploy

Use these deployment settings:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3` |
| GPU count | `1` |
| CPU | `4` |
| Memory | `96Gi` on a 128 GB machine; leave the rest for the OS and Kubernetes |
| Repository sync | Direct/native Triton S3 |

1. In the opened code-server Explorer, right-click the repository folder for
   this example. It must be the folder that directly contains
   `qwen3_30b_a3b_llmapi/config.pbtxt`.
2. Select **Triton Control: Deploy Model Repository** from the context menu.
3. Select S3 settings.
4. Enter the deployment settings shown above.
5. Deploy.

This example uses `backend: "python"`, so it keeps normal Triton S3 behavior.
It does not force a sidecar repository sync path.

Do not deploy this LLM API example with the base Triton image. The base image
has Triton's Python backend, but it does not include the `tensorrt_llm` Python
package. If the wrong image is used, model loading fails with:

```text
ModuleNotFoundError: No module named 'tensorrt_llm'
```

## 4. Test Inference

Use Triton Control for the first inference smoke test:

1. In Triton Control, open the deployed Triton instance.
2. Select model `qwen3_30b_a3b_llmapi`.
3. Open **Inference**.
4. Paste this JSON body and run inference:

```json
{
  "inputs": [
    {
      "name": "text_input",
      "shape": [1, 1],
      "datatype": "BYTES",
      "data": [
        "Reason step by step: why is MoE useful for serving this model on a 128 GB GPU?"
      ]
    },
    {
      "name": "stream",
      "shape": [1, 1],
      "datatype": "BOOL",
      "data": [false]
    },
    {
      "name": "sampling_parameters",
      "shape": [1, 1],
      "datatype": "BYTES",
      "data": [
        "{\"max_tokens\":256,\"temperature\":0.6,\"top_p\":0.95}"
      ]
    },
    {
      "name": "exclude_input_in_output",
      "shape": [1, 1],
      "datatype": "BOOL",
      "data": [true]
    }
  ],
  "outputs": [{ "name": "text_output" }]
}
```

For terminal testing, replace `localhost:8000` with the instance HTTP endpoint
unless you are port-forwarding it locally:

```bash
python3 -m pip install requests
python3 infer_client.py --url localhost:8000 \
  "Reason step by step: why is MoE useful for serving this model on a 128 GB GPU?"
```

The first request can be slow because TensorRT-LLM may initialize kernels and
runtime caches.

## Optional Python Client

```bash
python3 -m pip install requests
python3 infer_client.py --url localhost:8000 "Reason step by step: why is MoE useful for serving this model on a 128 GB GPU?"
```
