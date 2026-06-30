## ADDED Requirements

### Requirement: Scaffold command in code-server
The system SHALL provide a code-server command that creates a new Triton model repository scaffold in the user's workspace.

#### Scenario: Create scaffold from command palette
- **WHEN** a user runs the Triton repository scaffold command from code-server
- **THEN** the system prompts for repository type, template selection, model names, and target folder before writing repository files

#### Scenario: Refuse unsafe overwrite
- **WHEN** the selected target folder already contains files
- **THEN** the system MUST require the user to choose a different folder or explicitly cancel without overwriting existing files

### Requirement: Single-model templates
The system SHALL support single-model repository templates for Triton Python, ONNX Runtime, TensorRT plan, TensorRT-LLM, vLLM, and PyTorch/LibTorch model types.

#### Scenario: Generate Python backend repository
- **WHEN** a user selects the Python template and enters model name `preprocess`
- **THEN** the system creates a `preprocess/config.pbtxt` file and a `preprocess/1/model.py` starter file

#### Scenario: Generate backend or platform specific config
- **WHEN** a user selects a single-model template
- **THEN** the generated `config.pbtxt` MUST use the template's Triton config kind and value, such as `backend: "python"` or `platform: "tensorrt_plan"`

#### Scenario: Generate placeholder artifact guidance
- **WHEN** a selected template requires a binary or external model artifact that cannot be generated automatically
- **THEN** the system creates a placeholder or README explaining which artifact the user must provide and where it belongs

### Requirement: Template registry
The system SHALL define scaffold templates through registry metadata instead of hardcoding each backend directly in the command flow.

#### Scenario: Template metadata drives prompts
- **WHEN** the scaffold command displays available model templates
- **THEN** labels, backend/platform config values, default files, GPU hints, and ensemble-step eligibility come from template registry entries

#### Scenario: Add new template without wizard redesign
- **WHEN** a developer adds a new template registry entry with default files and config metadata
- **THEN** the scaffold command can offer that template without changing the high-level repository type flow

### Requirement: Ensemble pipeline templates
The system SHALL support ensemble pipeline scaffolding that creates child model folders and an ensemble model folder.

#### Scenario: Generate preset ensemble pipeline
- **WHEN** a user selects a preset pipeline such as Python preprocess to ONNX Runtime model to Python postprocess
- **THEN** the system creates folders for each child model and a separate ensemble folder with `platform: "ensemble"` in its `config.pbtxt`

#### Scenario: Generate ensemble scheduling
- **WHEN** the system creates an ensemble repository
- **THEN** the ensemble `config.pbtxt` MUST include `ensemble_scheduling` steps that reference the generated child model names

#### Scenario: Generate custom ordered pipeline
- **WHEN** a user selects a custom ensemble pipeline
- **THEN** the system allows the user to choose an ordered list of eligible step templates before generating child model folders and the ensemble config

### Requirement: Deploy flow compatibility
Generated repositories SHALL be compatible with the existing Triton Control deploy extension flow.

#### Scenario: Deploy generated repository
- **WHEN** a user runs the existing deploy command against a generated repository
- **THEN** the system uploads the repository using the existing S3 upload logic and creates deployments through the existing deployment API

#### Scenario: Preserve backend validation gate
- **WHEN** generated or edited `config.pbtxt` content is saved or deployed through Triton Control
- **THEN** backend-side protobuf validation remains the authoritative validation path for Triton config correctness

### Requirement: vLLM and TensorRT-LLM defaults
The system SHALL provide backend-specific defaults for vLLM and TensorRT-LLM templates because those workflows require specialized repository conventions.

#### Scenario: Generate vLLM template
- **WHEN** a user selects the vLLM template
- **THEN** the generated repository includes a vLLM-oriented `config.pbtxt` and starter model configuration files for the user to edit

#### Scenario: Deploy vLLM generated repository
- **WHEN** a generated vLLM repository is deployed through the existing deploy command
- **THEN** the deployment form defaults remain compatible with the existing vLLM repository sync behavior

#### Scenario: Generate TensorRT-LLM template
- **WHEN** a user selects the TensorRT-LLM template
- **THEN** the generated repository includes TensorRT-LLM backend metadata and guidance for placing engine or model artifacts required by that backend
