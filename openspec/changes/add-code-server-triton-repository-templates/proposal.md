## Why

Users can deploy an existing Triton model repository from the managed code-server workspace, but they still need to know Triton's repository layout and `config.pbtxt` shape before they can start. A scaffolded repository wizard lowers that barrier and makes the existing deploy flow useful for new models, backend-specific examples, and ensemble pipelines.

## What Changes

- Add a code-server command for creating a new Triton model repository from templates.
- Support single-model templates for common Triton backends and platforms, including Python, ONNX Runtime, TensorRT plan, TensorRT-LLM, vLLM, and PyTorch/LibTorch.
- Support ensemble/pipeline templates that generate child model folders and an ensemble `config.pbtxt`.
- Use a template registry so additional Triton backends/platforms can be added without redesigning the wizard.
- Reuse the existing code-server deploy extension flow after scaffolding.
- Keep backend `config.pbtxt` validation as the correctness gate during upload/deploy.

## Capabilities

### New Capabilities

- `triton-repository-scaffolding`: Users can generate Triton model repository structures and starter `config.pbtxt` files from code-server templates, including single-model and ensemble pipeline repositories.

### Modified Capabilities

- None.

## Impact

- Affected code-server extension: `code-server-extensions/triton-deploy`.
- Affected development workspace packaging: `triton-backend/app/services/development/kubernetes.py` already bundles the extension into workspaces.
- Affected tests: extension-level unit coverage or focused script tests for template generation, plus backend tests if template metadata is exposed by an API.
- No breaking API or storage changes are expected.
