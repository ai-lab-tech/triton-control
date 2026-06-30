## 1. Extension Command Surface

- [ ] 1.1 Add a `tritonControl.newModelRepository` command contribution to `code-server-extensions/triton-deploy/package.json`.
- [ ] 1.2 Register the scaffold command in `extension.js` and route it through the existing code-server activation path.
- [ ] 1.3 Add command palette and explorer entry points that work from an open workspace folder.

## 2. Template Registry

- [ ] 2.1 Define template metadata for Python, ONNX Runtime, TensorRT plan, TensorRT-LLM, vLLM, and PyTorch/LibTorch.
- [ ] 2.2 Include config kind/value metadata for `backend` and `platform` based templates.
- [ ] 2.3 Include default files, placeholder guidance, GPU hints, and ensemble-step eligibility in each template entry.
- [ ] 2.4 Add pure helper functions for safe Triton model names, folder names, version folder paths, and overwrite checks.

## 3. Single-Model Scaffolding

- [ ] 3.1 Implement prompts for repository target folder, model name, and single-model template selection.
- [ ] 3.2 Generate model folder layout with `config.pbtxt` and versioned artifact locations.
- [ ] 3.3 Generate starter files for Python and editable placeholder guidance for artifact-based templates.
- [ ] 3.4 Open the generated repository or primary `config.pbtxt` in code-server after creation.

## 4. Ensemble Scaffolding

- [ ] 4.1 Implement preset ensemble choices for Python to ONNX Runtime to Python and Python to TensorRT to Python.
- [ ] 4.2 Implement a custom ordered pipeline prompt using templates marked as ensemble-step eligible.
- [ ] 4.3 Generate child model folders for every pipeline step.
- [ ] 4.4 Generate an ensemble model folder with `platform: "ensemble"` and `ensemble_scheduling` steps referencing child models.
- [ ] 4.5 Generate readable default tensor names and input/output maps that users can edit.

## 5. Deploy Flow Compatibility

- [ ] 5.1 Ensure generated repositories satisfy the existing deploy extension folder detection logic.
- [ ] 5.2 Preserve existing vLLM deployment defaults and repository sync behavior when deploying generated vLLM repositories.
- [ ] 5.3 Verify scaffolded repositories can be selected by `Triton Control: Deploy Model Repository`.

## 6. Tests and Documentation

- [ ] 6.1 Add focused tests or script-level checks for template registry output and generated file layouts.
- [ ] 6.2 Add tests for unsafe overwrite refusal and name normalization.
- [ ] 6.3 Add ensemble generation tests covering child folders and ensemble scheduling references.
- [ ] 6.4 Update `code-server-extensions/triton-deploy/README.md` with scaffold command usage and supported templates.
- [ ] 6.5 Run relevant extension and backend checks, including OpenSpec validation for this change.
