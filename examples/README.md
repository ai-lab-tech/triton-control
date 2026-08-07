# Examples

Large model artifacts are not committed. Each example includes the repository
layout plus a notebook that creates the missing artifact.

## Use an Example

1. In Triton Control, open **Development** and create the workspace with the
   image, storage, and GPU settings shown in the example README.
2. When the workspace is ready, open code-server from **Development**.
3. Choose one repository path.
4. Option A: copy or upload the ready example folder into `/workspace`.
5. Option B: run **New Model Repository** from the Triton Control plugin and
   choose the backend/template shown in the example README.
6. Run the example's notebook to create the model artifact.
7. Review `config.pbtxt`.
8. In the code-server Explorer, right-click the repository folder.
9. Select **Triton Control: Deploy Model Repository** from the context menu.
10. Select an S3 profile or enter manual S3 settings.
11. Use the concrete Triton image shown in the example README.
12. Deploy and test the running Triton instance with its **Inference** manual
    input view. The README curl command uses the same request body.

The Development workspace image is only the edit/build environment. The deploy
form selects the separate Triton image that serves the model.

Check each README for whether the code-server workspace needs a GPU. Most
examples only need CPU in code-server; GPU is usually needed on the deployed
Triton instance.

For manual UI testing, open the Triton instance, select the loaded model, open
**Inference**, and paste the JSON request body shown in the example. For
terminal testing, replace `localhost:8000` or `localhost:8001` with the
instance HTTP or gRPC endpoint unless you are port-forwarding locally.

## Create Your Own Repository

Use the same plugin path for your own model:

1. In Triton Control, open **Development** and create the workspace with the
   image, storage, and GPU settings needed for your model work.
2. When the workspace is ready, open code-server from **Development**.
3. Run **New Model Repository** from the Triton Control plugin.
4. Choose the backend/template, such as Python, ONNX Runtime, PyTorch/LibTorch,
   vLLM, or an ensemble pipeline.
5. Replace the generated placeholders with the real artifacts and config.
6. Right-click the repository folder in the code-server Explorer and select
   **Triton Control: Deploy Model Repository**.

For an already existing model repository, open it in `/workspace`, then
right-click it in the code-server Explorer and select **Triton Control: Deploy
Model Repository**. For the polling S3 example, use the instance **S3 Browser**
because the Triton instance already exists.

## Examples

Examples are grouped by Triton backend or deployment pattern:

```text
python/
libtorch/
onnx/
tensorrt/
tensorrt_llm/
vllm/
s3_upload/
workflows/
```

| Path | Use | Backend |
| --- | --- | --- |
| `tensorrt/yolov8n-object-detection-tensorrt-ensemble` | YOLOv8 object detection optimized as a TensorRT plan | `ensemble`, `python`, `tensorrt_plan` |
| `tensorrt/distilbert-squad-question-answering-tensorrt` | Text question answering optimized as a TensorRT plan | `ensemble`, `python`, `tensorrt_plan` |
| `tensorrt_llm/qwen2.5-0.5b-instruct-trtllm` | Small raw TensorRT-LLM backend smoke test | `tensorrtllm` |
| `tensorrt_llm/qwen2.5-0.5b-instruct-llmapi-s3` | Small TensorRT-LLM LLM API smoke test via Triton S3 | `python`, TensorRT-LLM LLM API |
| `tensorrt_llm/qwen3-30b-a3b-llmapi-s3` | Qwen3 30B A3B via TensorRT-LLM LLM API for 128 GB VRAM | `python`, TensorRT-LLM LLM API |
| `vllm/phi3-mini-4k-instruct-vllm` | Smaller vLLM LLM smoke test | `vllm` |
| `vllm/qwen3-4b-instruct-vllm` | Qwen3 4B LLM on a 16 GB GPU | `vllm` |
| `vllm/qwen3-30b-a3b-vllm` | Qwen3 30B A3B reasoning LLM for 128 GB VRAM | `vllm` |
| `onnx/yolov8-object-detection-ensemble` | YOLOv8 image detection pipeline | `ensemble`, `python`, `onnxruntime_onnx` |
| `libtorch/image-classification-ensemble` | Image preprocessing plus ResNet18 | `ensemble`, `python`, `pytorch_libtorch` |
| `libtorch/distilbert-sentiment-gpu` | GPU sentiment classification | `pytorch_libtorch` |
| `python/iris-classifier` | Small scikit-learn tabular classifier | `python` |
| `s3_upload/local-model-upload-s3-polling` | Upload a model to an existing polling instance | `python` |
| `workflows/sklearn-iris-training` | Run scikit-learn training through Argo with S3-backed source and result artifacts | Training workflow |

## Runtime Notes

- Python backend examples use `nvcr.io/nvidia/tritonserver:26.06-py3` plus
  the deploy form's `requirements.txt` field when extra packages are needed.
- PyTorch/ONNX examples in this folder use `nvcr.io/nvidia/tritonserver:26.06-py3`.
- TensorRT plan examples must build the plan on the same GPU class and with the
  same Triton image used for serving.
- TensorRT-LLM examples use `nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3`.
- vLLM examples use `nvcr.io/nvidia/tritonserver:26.06-vllm-python-py3`.

- Polling examples require the S3 Browser connection and Triton deployment
  repository prefix to point to the same S3 repository root.
