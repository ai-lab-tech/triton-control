# Qwen2.5 0.5B Instruct with TensorRT-LLM LLM API and Triton S3

Small instruction LLM served by Triton's Python backend with the TensorRT-LLM
LLM API. The repository is uploaded to S3 by Triton Control and loaded by
Triton's S3 model repository path.

## Model

- Model: `Qwen/Qwen2.5-0.5B-Instruct`
- Triton backend: `python`
- Runtime: TensorRT-LLM LLM API
- Model directory: `qwen2_5_0_5b_instruct_llmapi/`
- Artifact directory: `qwen2_5_0_5b_instruct_llmapi/1/model/`
- Input: `text_input`, `BYTES`, shape `[1]`
- Output: `text_output`, `BYTES`, shape `[1]`

`model/` is not committed. Create it with the notebook before deploying.

## 1. Create Development Workspace

In Triton Control, open **Development** and create the workspace:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3` |
| Image already has Development installed | Disabled |
| Workspace storage | At least `30Gi` |
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
3. Enter `qwen2.5-0.5b-instruct-llmapi-s3` as the repository target folder name.
4. Enter `qwen2_5_0_5b_instruct_llmapi` as the model name.
5. Choose the Python backend model template.
6. Replace the generated files with this example's `config.pbtxt`, `model.py`,
   notebook, README, and client.

Then open and run:

```text
prepare_qwen2_5_0_5b_llmapi_s3.ipynb
```

This writes:

```text
qwen2_5_0_5b_instruct_llmapi/config.pbtxt
qwen2_5_0_5b_instruct_llmapi/1/model.py
qwen2_5_0_5b_instruct_llmapi/1/model/
qwen2_5_0_5b_instruct_llmapi/1/python_deps/
```

The notebook uses temporary helper directories outside the repository folder
and removes them at the end. If an older failed run left `.notebook_deps/`,
`.notebook_cache/`, or `.notebook_home/` in this repository folder, delete them
before deploying. Triton treats every top-level folder in the repository as a
model.

Before deploying, verify that model weights exist:

```bash
find qwen2_5_0_5b_instruct_llmapi/1/model -maxdepth 1 -type f | head
test -d qwen2_5_0_5b_instruct_llmapi/1/python_deps/openai
```

## 3. Deploy

Use these deployment settings:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3` |
| GPU count | At least `1` |
| Memory | At least `12Gi`; use `16Gi` if available |
| Repository sync | Direct/native Triton S3 |

1. In the opened code-server Explorer, right-click the repository folder for
   this example. It must be the folder that directly contains
   `qwen2_5_0_5b_instruct_llmapi/config.pbtxt`.
2. Select **Triton Control: Deploy Model Repository** from the context menu.
3. Select S3 settings.
4. Enter the deployment settings shown above.
5. Deploy.

This example uses `backend: "python"`, so it keeps normal Triton S3 behavior.
It does not force the vLLM/TensorRT-LLM sidecar repository sync path.

Do not deploy this LLM API example with a `4Gi` memory limit. TensorRT-LLM can
load the model and then still be OOM-killed on the first generation request.
If inference returns a disconnected/failed request and the pod shows
`OOMKilled` with exit code `137`, increase the Triton deployment memory and
redeploy.

## 4. Test Inference

Use Triton Control for the first inference smoke test:

1. In Triton Control, open the deployed Triton instance.
2. Select model `qwen2_5_0_5b_instruct_llmapi`.
3. Open **Inference**.
4. Paste this JSON body and run inference:

```json
{
  "inputs": [
    {
      "name": "text_input",
      "shape": [1],
      "datatype": "BYTES",
      "data": ["Give me a compact TensorRT-LLM LLM API deployment checklist."]
    }
  ],
  "outputs": [{ "name": "text_output" }]
}
```

If Triton Control only shows `Triton request failed`, check the Triton pod
status/logs. If Kubernetes reports `OOMKilled` with exit code `137`, the
request JSON is not the problem. The Triton container memory limit is too low.
Redeploy with at least `12Gi` memory, preferably `16Gi`.

For terminal testing, replace `localhost:8000` with the instance HTTP endpoint
unless you are port-forwarding it locally:

```bash
curl -X POST "http://localhost:8000/v2/models/qwen2_5_0_5b_instruct_llmapi/infer" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": [
      {
        "name": "text_input",
        "shape": [1],
        "datatype": "BYTES",
        "data": ["Give me a compact TensorRT-LLM LLM API deployment checklist."]
      }
    ],
    "outputs": [{ "name": "text_output" }]
  }'
```

## Optional Python Client

```bash
python3 -m pip install requests
python3 infer_client.py --url localhost:8000 "Give me a compact TensorRT-LLM LLM API deployment checklist."
```
