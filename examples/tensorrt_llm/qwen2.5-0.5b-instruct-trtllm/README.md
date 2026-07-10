# Qwen2.5 0.5B Instruct with Triton TensorRT-LLM

Small instruction LLM converted to a TensorRT-LLM checkpoint, built as a
TensorRT-LLM engine, and served with Triton's `tensorrtllm` backend.

> Legacy example: this repository uses the classic TensorRT-LLM backend flow
> with checkpoint conversion, engine build, and token tensor inputs. For new
> Qwen2.5 examples, prefer the LLM API variant in
> `examples/tensorrt_llm/qwen2.5-0.5b-instruct-llmapi-s3/`.

## Model

- Model: `Qwen/Qwen2.5-0.5B-Instruct`
- Backend: `tensorrtllm`
- Target: one CUDA GPU
- Model directory: `qwen2_5_0_5b_instruct_trtllm/`
- Engine directory: `qwen2_5_0_5b_instruct_trtllm/1/`

Generated Hugging Face model files, TensorRT-LLM checkpoints, and engine files
are not committed. Create them with the notebook.

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
python3 -m pip install notebook ipykernel
```

For private or gated Hugging Face models, set `HF_TOKEN` before deployment.
Qwen2.5 0.5B Instruct is public at the time this example was written.

## 2. Create the Repository and Engine

Choose one repository folder. The repository folder is the folder you will
upload and deploy with Triton Control.

### Option A: Use the Example Repository

Put this example at one path only:

```text
<repository-folder>/
```

Do not copy the folder into itself. This is wrong:

```text
<repository-folder>/<repository-folder>/
```

### Option B: Create the Structure with the Plugin

1. In code-server, run **New Model Repository** from the Triton Control plugin.
2. Choose **Single model**.
3. Enter `qwen2.5-0.5b-instruct-trtllm` as the repository target folder name.
4. Enter `qwen2_5_0_5b_instruct_trtllm` as the model name.
5. Choose the TensorRT-LLM model template.
6. Put `prepare_qwen2_5_0_5b_trtllm.ipynb`, `infer_client.py`, and `README.md`
   in the created `qwen2.5-0.5b-instruct-trtllm/` repository folder.
7. Replace the generated model `config.pbtxt` with this example's
   `qwen2_5_0_5b_instruct_trtllm/config.pbtxt`.


Then open and run:

```text
prepare_qwen2_5_0_5b_trtllm.ipynb
```

This writes the final Triton model repository:

```text
qwen2_5_0_5b_instruct_trtllm/config.pbtxt
qwen2_5_0_5b_instruct_trtllm/1/
qwen2_5_0_5b_instruct_trtllm/1/rank0.engine
qwen2_5_0_5b_instruct_trtllm/1/tokenizer/
```

The temporary folders `hf_model/`, `ckpt/`, and `engine/` are deleted by the
notebook after the final Triton model repository has been written.

Do not install `torch` in this notebook. Use the TensorRT-LLM image's Python
environment and tools (`/opt/venv-tritonserver/bin/python`, `trtllm-build`).

## 3. Deploy

Use these deployment settings:

| Field | Value |
| --- | --- |
| Image | `nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3` |
| GPU count | At least `1` |

1. In the opened code-server Explorer, right-click the repository folder for
   this example. It must be the folder that directly contains
   `qwen2_5_0_5b_instruct_trtllm/config.pbtxt`.
   Do not deploy a parent folder that only contains multiple repositories or
   examples.
2. Select **Triton Control: Deploy Model Repository** from the context menu.
3. Select S3 settings.
4. Enter the deployment settings shown above.
5. Deploy.

Use the same TensorRT-LLM image family for the Development workspace and the
deployment. If you change the image tag, rerun the notebook and rebuild the
engine.

## 4. Test Inference

Use Triton Control for the first inference smoke test:

1. Generate a Triton tensor payload for your prompt:

   ```bash
   python3 -m pip install transformers
   python3 infer_client.py --print-payload "Give me a compact TensorRT-LLM deployment checklist."
   ```

2. Open the deployed instance in Triton Control.
3. Open the `qwen2_5_0_5b_instruct_trtllm` model.
4. Open **Infer**.
5. Paste the generated JSON payload and send it.

This example model is a raw TensorRT-LLM backend model. It accepts token tensor
inputs, so Triton Control sends the request to `/infer` with `input_ids`,
`input_lengths`, and `request_output_len`.

Payload shape:

```json
{
  "inputs": [
    { "name": "input_ids", "shape": [1, 5], "datatype": "INT32", "data": [1, 2, 3, 4, 5] },
    { "name": "input_lengths", "shape": [1, 1], "datatype": "INT32", "data": [5] },
    { "name": "request_output_len", "shape": [1, 1], "datatype": "INT32", "data": [128] }
  ],
  "outputs": [{ "name": "output_ids" }, { "name": "sequence_length" }]
}
```

## Optional Python Client

After the Triton Control smoke test succeeds, you can also test the deployed
Triton instance directly over HTTP. Replace `localhost:8000` with the instance
HTTP endpoint unless you are port-forwarding it locally:

```bash
python3 -m pip install requests transformers
python3 infer_client.py --url localhost:8000 "Give me a compact TensorRT-LLM deployment checklist."
```

For an in-cluster deployment, the full endpoint has this shape:

```text
http://qwen2-5-0-5b-instruct-trtllm-service.triton-control.svc.cluster.local:18000/v2/models/qwen2_5_0_5b_instruct_trtllm/infer
```
