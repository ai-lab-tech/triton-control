## 1. Extension Command Surface

- [x] 1.1 Add a `tritonControl.newModelRepository` command contribution to `code-server-extensions/triton-deploy/package.json`.
- [x] 1.2 Register the scaffold command in `extension.js` and route it through the existing code-server activation path.
- [x] 1.3 Add command palette and explorer entry points that work from an open workspace folder.

## 2. Template Registry

- [x] 2.1 Define template metadata for Python, ONNX Runtime, TensorRT plan, TensorRT-LLM, vLLM, and PyTorch/LibTorch.
- [x] 2.2 Include config kind/value metadata for `backend` and `platform` based templates.
- [x] 2.3 Include default files, placeholder guidance, GPU hints, and ensemble-step eligibility in each template entry.
- [x] 2.4 Add pure helper functions for safe Triton model names, folder names, version folder paths, and overwrite checks.

## 3. Single-Model Scaffolding

- [x] 3.1 Implement prompts for repository target folder, model name, and single-model template selection.
- [x] 3.2 Generate model folder layout with `config.pbtxt` and versioned artifact locations.
- [x] 3.3 Generate starter files for Python and editable placeholder guidance for artifact-based templates.
- [x] 3.4 Open the generated repository or primary `config.pbtxt` in code-server after creation.

## 4. Ensemble Scaffolding

- [x] 4.1 Implement preset ensemble choices for Python to ONNX Runtime to Python and Python to TensorRT to Python.
- [x] 4.2 Implement a custom ordered pipeline prompt using templates marked as ensemble-step eligible.
- [x] 4.3 Generate child model folders for every pipeline step.
- [x] 4.4 Generate an ensemble model folder with `platform: "ensemble"` and `ensemble_scheduling` steps referencing child models.
- [x] 4.5 Generate readable default tensor names and input/output maps that users can edit.

## 5. Deploy Flow Compatibility

- [x] 5.1 Ensure generated repositories satisfy the existing deploy extension folder detection logic.
- [x] 5.2 Preserve existing vLLM deployment defaults and repository sync behavior when deploying generated vLLM repositories.
- [x] 5.3 Verify scaffolded repositories can be selected by `Triton Control: Deploy Model Repository`.

## 6. Tests and Documentation

- [x] 6.1 Add focused tests or script-level checks for template registry output and generated file layouts.
- [x] 6.2 Add tests for unsafe overwrite refusal and name normalization.
- [x] 6.3 Add ensemble generation tests covering child folders and ensemble scheduling references.
- [x] 6.4 Update `code-server-extensions/triton-deploy/README.md` with scaffold command usage and supported templates.
- [x] 6.5 Run relevant extension and backend checks, including OpenSpec validation for this change.
