## Context

Triton Control already creates managed code-server workspaces and bundles the `triton-control-deploy` extension into each workspace. That extension can upload an existing Triton model repository to S3-compatible storage and create a Triton Control deployment through the existing deployment API.

The missing workflow is repository creation. Users must currently know the Triton repository folder layout, backend-specific artifact names, and `config.pbtxt` syntax before the deploy extension is useful. This change adds a scaffold command to the existing extension so users can create a valid starter repository in `/workspace`, edit it, and then use the existing deploy command.

## Goals / Non-Goals

**Goals:**

- Add a code-server command for creating Triton repository templates from inside the managed workspace.
- Support both single-model repositories and ensemble/pipeline repositories.
- Represent templates through a registry so new backend/platform templates can be added incrementally.
- Generate starter `config.pbtxt` files and placeholder artifacts for Python, ONNX Runtime, TensorRT plan, TensorRT-LLM, vLLM, and PyTorch/LibTorch.
- Reuse the existing upload/deploy extension flow after files are generated.
- Keep backend-side `config.pbtxt` validation as the authoritative correctness check.

**Non-Goals:**

- Build a graphical pipeline editor in the Angular application.
- Guarantee generated placeholder artifacts are production-ready models.
- Replace Triton Control's existing S3 browser, deployment API, or protobuf validation.
- Change the existing deploy command behavior for already valid repositories.
- Dynamically fetch Triton template definitions from NVIDIA at runtime.

## Decisions

### Keep scaffolding in the code-server extension

The scaffold workflow belongs next to the existing deploy command because the files are created in the user's persistent `/workspace` volume and immediately edited in code-server.

Alternative considered: add an Angular wizard that writes files through S3 APIs. That would help remote repositories, but it would not help the local code-server editing flow and would duplicate workspace file operations.

### Preserve existing deploy as the baseline

The new scaffold command will create files that the existing deploy command can already consume. The deploy command's S3 upload, deployment payload generation, vLLM sync behavior, and deployment-created navigation message should remain the baseline behavior.

Alternative considered: combine scaffold and deploy into one larger wizard. That would make the first implementation harder to verify and would blur the current stable boundary between repository editing and deployment.

### Use a template registry

Templates will be described by metadata instead of hardcoded command branches. Each template entry should include an id, display label, Triton config kind (`backend` or `platform`), config value, default files, supported artifact placeholders, whether GPU is expected, and whether the template can participate as an ensemble step.

Alternative considered: implement one prompt path per backend. That is faster for the first two templates but becomes difficult to maintain once TensorRT-LLM, vLLM, LibTorch, and future backends are added.

### Generate editable starter repositories

The extension will generate minimal but explicit files: model folders, version folders where required, starter `config.pbtxt`, and placeholder model artifacts or README files when the actual binary artifact must be supplied by the user.

Alternative considered: generate only `config.pbtxt`. That leaves users to discover the rest of the Triton folder structure themselves, which is the main problem this change is intended to solve.

### Treat ensembles as repository graphs

Ensemble scaffolding will generate child model folders plus a separate ensemble model folder whose `config.pbtxt` uses `platform: "ensemble"` and `ensemble_scheduling`. The first implementation can use ordered-step prompts and preset pipeline shapes instead of a visual editor.

Alternative considered: generate a single empty ensemble config only. That is technically valid as a starting point, but it does not demonstrate how child models and tensor maps relate.

### Validate through existing deploy/save paths

The scaffold command should avoid becoming a second Triton config validator. It can prevent obvious local mistakes such as empty names or overwriting an existing folder, but final `config.pbtxt` correctness remains with the backend's existing protobuf validation during S3 save/deploy flows.

Alternative considered: embed protobuf validation in the extension. That would increase package complexity and could drift from the backend's version-aware validation.

## Risks / Trade-offs

- Template drift from Triton behavior -> Keep templates minimal, cover them with generation tests, and rely on backend validation for final correctness.
- Placeholder artifacts may look deployable when they are not -> Add generated README notes beside placeholders and keep deploy validation errors visible.
- Ensemble tensor maps are easy to generate incorrectly -> Start with simple ordered templates and readable names, then let users edit generated `config.pbtxt`.
- The extension is currently a single JavaScript file -> Keep template generation in small pure functions first; split files only if the extension build/package path is updated.
- Backend/platform naming can be confusing (`backend: "pytorch"` vs `platform: "pytorch_libtorch"`) -> Store the exact emitted `config.pbtxt` field in template metadata and display user-friendly labels separately.

## Migration Plan

No data migration is required. Existing workspaces will receive the updated extension when new Development workspace resources are created or recreated. Existing deployments and uploaded repositories are unchanged.

Rollback is removing or disabling the new scaffold command from the extension package. The existing deploy command remains independent.

## Open Questions

- Should TensorRT-LLM templates target `backend: "tensorrtllm"` by default, or should the template support both current deployment conventions and older repository examples?
- Should the first ensemble wizard expose arbitrary tensor names, or use generated names and expect users to edit the resulting config?
- Should template metadata eventually move to a backend endpoint so UI and extension surfaces can share the same catalog?
