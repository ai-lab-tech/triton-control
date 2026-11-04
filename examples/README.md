# Examples

Examples cover model repositories, S3 uploads, and training workflows. Large
model artifacts are generally generated or downloaded using the example's
notebook or script. Follow its README for the required preparation.

## Use an Example

For training workflows, follow the dedicated instructions:

- [scikit-learn Iris training](workflows/sklearn-iris-training/README.md): run
  training through Argo Workflows and save the results to S3.
- [MLflow Iris autodeploy](workflows/mlflow-iris-autodeploy/README.md): train,
  log the model to MLflow, and deploy it from the training script.

For uploads to an existing polling instance, follow
[Local Model Upload with S3 Polling](s3_upload/local-model-upload-s3-polling/README.md).

For model repository examples, use these steps:

1. In Triton Control, open **Development** and create the workspace with the
   image, storage, and GPU settings shown in the example README.
2. When the workspace is ready, open code-server from **Development**.
3. Copy or upload the example folder into `/workspace`. Alternatively, run
   **Triton Control: New Model Repository** and follow the example README's
   template and file-copy instructions.
4. Run the example's notebook or preparation script to create the model artifacts.
5. Review `config.pbtxt`.
6. In the code-server Explorer, right-click the model repository folder and select
   **Triton Control: Deploy Model Repository**.
7. Select an S3 profile or enter manual S3 settings.
8. Use the serving image and deployment settings shown in the example README.
9. Deploy and test the running Triton instance using the example's request or client.

The Development workspace image is only the edit/build environment. The deploy
form selects the separate Triton image that serves the model.

Check each README for whether the code-server workspace needs a GPU. Most
examples only need CPU in code-server; GPU is usually needed on the deployed
Triton instance.

For manual UI testing, open **Triton Instances → your deployment → Details →
Models → model (version) → Infer** and paste the JSON request body shown in the
example. For terminal testing, replace `localhost:8000` or `localhost:8001` with the
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
Model Repository**.

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
| `tensorrt_llm/qwen2.5-0.5b-instruct-llmapi-engine-s3` | Qwen2.5 engine saved through LLM API and served through native TensorRT-LLM backend via Triton S3 | `tensorrtllm`, TensorRT-LLM LLM API engine |
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
| `workflows/mlflow-iris-autodeploy` | Train Iris, generate its Triton adapter, and deploy from one Python script | MLflow and Python workflow |

## Runtime Notes

- The Iris Python example uses `nvcr.io/nvidia/tritonserver:26.06-py3` plus
  the deploy form's **Extra Python Packages** field for its `requirements.txt` contents.
- PyTorch/ONNX examples in this folder serve with `nvcr.io/nvidia/tritonserver:26.06-py3`.
- TensorRT plan examples must build the plan on the same GPU class and with the
  same Triton image used for serving.
- TensorRT-LLM examples use `nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3`,
  including the LLM API examples that use a Python backend.
- vLLM examples use `nvcr.io/nvidia/tritonserver:26.06-vllm-python-py3`.
- Polling examples require the S3 Browser connection and Triton deployment
  repository prefix to point to the same S3 repository root.
