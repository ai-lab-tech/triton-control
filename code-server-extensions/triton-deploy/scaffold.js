const fs = require("fs");
const path = require("path");

const TENSOR_BLOCK = `input [
  {
    name: "INPUT__0"
    data_type: TYPE_FP32
    dims: [ 1 ]
  }
]
output [
  {
    name: "OUTPUT__0"
    data_type: TYPE_FP32
    dims: [ 1 ]
  }
]`;

const PYTHON_MODEL = `import numpy as np
import triton_python_backend_utils as pb_utils


class TritonPythonModel:
    def initialize(self, args):
        pass

    def execute(self, requests):
        responses = []
        for request in requests:
            input_tensor = pb_utils.get_input_tensor_by_name(request, "INPUT__0")
            output_tensor = pb_utils.Tensor("OUTPUT__0", input_tensor.as_numpy().astype(np.float32))
            responses.append(pb_utils.InferenceResponse(output_tensors=[output_tensor]))
        return responses

    def finalize(self):
        pass
`;

const TEMPLATES = [
  {
    id: "python",
    label: "Python backend",
    configKind: "backend",
    configValue: "python",
    gpuHint: "Optional GPU depending on model code.",
    ensembleStepEligible: true,
    files: [{ relativePath: "1/model.py", content: PYTHON_MODEL }],
  },
  {
    id: "onnx",
    label: "ONNX Runtime",
    configKind: "platform",
    configValue: "onnxruntime_onnx",
    gpuHint: "GPU optional when Triton ONNX Runtime GPU execution is configured.",
    ensembleStepEligible: true,
    files: [
      {
        relativePath: "1/README.md",
        content: artifactReadme("model.onnx", "Export or copy an ONNX model to this folder as model.onnx."),
      },
    ],
  },
  {
    id: "tensorrt",
    label: "TensorRT plan",
    configKind: "platform",
    configValue: "tensorrt_plan",
    gpuHint: "Requires an NVIDIA GPU and a compatible TensorRT engine.",
    ensembleStepEligible: true,
    files: [
      {
        relativePath: "1/README.md",
        content: artifactReadme("model.plan", "Build or copy a TensorRT plan file to this folder as model.plan."),
      },
    ],
  },
  {
    id: "tensorrt-llm",
    label: "TensorRT-LLM",
    configKind: "backend",
    configValue: "tensorrtllm",
    gpuHint: "Requires NVIDIA GPU resources and TensorRT-LLM engine artifacts.",
    ensembleStepEligible: false,
    files: [
      {
        relativePath: "1/README.md",
        content: artifactReadme(
          "TensorRT-LLM artifacts",
          "Place the engine, tokenizer, and backend-specific model files required by your TensorRT-LLM build here.",
        ),
      },
    ],
  },
  {
    id: "vllm",
    label: "vLLM",
    configKind: "backend",
    configValue: "vllm",
    gpuHint: "Requires GPU resources for typical vLLM deployments.",
    ensembleStepEligible: false,
    files: [
      {
        relativePath: "1/model.json",
        content: `${JSON.stringify({ model: "./model", tokenizer: "./model" }, null, 2)}\n`,
      },
      {
        relativePath: "1/README.md",
        content: artifactReadme(
          "model files",
          "Edit model.json and place or reference the model/tokenizer files expected by the vLLM backend.",
        ),
      },
    ],
  },
  {
    id: "pytorch",
    label: "PyTorch / LibTorch",
    configKind: "platform",
    configValue: "pytorch_libtorch",
    gpuHint: "GPU optional depending on the exported model and runtime configuration.",
    ensembleStepEligible: true,
    files: [
      {
        relativePath: "1/README.md",
        content: artifactReadme("model.pt", "Export or copy a TorchScript model to this folder as model.pt."),
      },
    ],
  },
];

const ENSEMBLE_PRESETS = [
  {
    id: "python-onnx-python",
    label: "Python -> ONNX Runtime -> Python",
    ensembleName: "pipeline",
    steps: [
      { name: "preprocess", templateId: "python" },
      { name: "model", templateId: "onnx" },
      { name: "postprocess", templateId: "python" },
    ],
  },
  {
    id: "python-tensorrt-python",
    label: "Python -> TensorRT -> Python",
    ensembleName: "pipeline",
    steps: [
      { name: "preprocess", templateId: "python" },
      { name: "model", templateId: "tensorrt" },
      { name: "postprocess", templateId: "python" },
    ],
  },
];

function artifactReadme(artifactName, guidance) {
  return `# Placeholder artifact

${guidance}

This scaffold is intentionally not a production model. Replace this guidance with the real ${artifactName} before deployment.
`;
}

function templateById(templateId) {
  const template = TEMPLATES.find((entry) => entry.id === templateId);
  if (!template) {
    throw new Error(`Unknown Triton template: ${templateId}`);
  }
  return template;
}

function normalizeTritonModelName(value, fallback = "model") {
  const normalized = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^[._-]+|[._-]+$/g, "");
  return normalized || fallback;
}

function safeFolderName(value, fallback = "model") {
  const normalized = normalizeTritonModelName(value, fallback);
  if (normalized.includes("/") || normalized.includes("\\") || normalized === "." || normalized === "..") {
    throw new Error("Model name must be a single folder name.");
  }
  return normalized;
}

function versionFolderPath(modelFolder, version = "1") {
  return path.join(modelFolder, String(version));
}

function assertSafeTargetFolder(targetFolder) {
  const resolved = path.resolve(targetFolder);
  if (fs.existsSync(resolved)) {
    const stats = fs.statSync(resolved);
    if (!stats.isDirectory()) {
      throw new Error("Target folder exists and is not a directory.");
    }
    const entries = fs.readdirSync(resolved).filter((entry) => entry !== ".DS_Store");
    if (entries.length) {
      throw new Error("Target folder already contains files. Choose an empty folder or cancel.");
    }
  }
  return resolved;
}

function renderModelConfig(modelName, template) {
  return `name: "${modelName}"
${template.configKind}: "${template.configValue}"
max_batch_size: 0
${TENSOR_BLOCK}
`;
}

function renderEnsembleConfig(ensembleName, steps) {
  const stepBlocks = steps.map((step, index) => {
    const inputName = index === 0 ? "ENSEMBLE_INPUT" : `STEP_${index}_OUTPUT`;
    const outputName = index === steps.length - 1 ? "ENSEMBLE_OUTPUT" : `STEP_${index + 1}_OUTPUT`;
    return `    {
      model_name: "${step.name}"
      model_version: -1
      input_map {
        key: "INPUT__0"
        value: "${inputName}"
      }
      output_map {
        key: "OUTPUT__0"
        value: "${outputName}"
      }
    }`;
  }).join("\n");

  return `name: "${ensembleName}"
platform: "ensemble"
max_batch_size: 0
input [
  {
    name: "ENSEMBLE_INPUT"
    data_type: TYPE_FP32
    dims: [ 1 ]
  }
]
output [
  {
    name: "ENSEMBLE_OUTPUT"
    data_type: TYPE_FP32
    dims: [ 1 ]
  }
]
ensemble_scheduling {
  step [
${stepBlocks}
  ]
}
`;
}

function writeFile(targetFolder, relativePath, content) {
  const filePath = path.join(targetFolder, relativePath);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content, "utf8");
  return filePath;
}

function writeModelFolder(repositoryFolder, modelName, template) {
  const folderName = safeFolderName(modelName);
  const modelFolder = path.join(repositoryFolder, folderName);
  fs.mkdirSync(versionFolderPath(modelFolder), { recursive: true });
  writeFile(modelFolder, "config.pbtxt", renderModelConfig(folderName, template));
  for (const file of template.files) {
    writeFile(modelFolder, file.relativePath, file.content);
  }
  return modelFolder;
}

function scaffoldSingleModelRepository({ targetFolder, modelName, templateId }) {
  const repositoryFolder = assertSafeTargetFolder(targetFolder);
  fs.mkdirSync(repositoryFolder, { recursive: true });
  const template = templateById(templateId);
  const modelFolder = writeModelFolder(repositoryFolder, modelName, template);
  return {
    repositoryFolder,
    modelFolder,
    configPath: path.join(modelFolder, "config.pbtxt"),
    template,
  };
}

function scaffoldEnsembleRepository({ targetFolder, ensembleName, steps }) {
  if (!Array.isArray(steps) || steps.length < 2) {
    throw new Error("An ensemble requires at least two steps.");
  }
  const repositoryFolder = assertSafeTargetFolder(targetFolder);
  fs.mkdirSync(repositoryFolder, { recursive: true });
  const normalizedSteps = steps.map((step) => {
    const template = templateById(step.templateId);
    if (!template.ensembleStepEligible) {
      throw new Error(`${template.label} cannot be used as an ensemble step.`);
    }
    const name = safeFolderName(step.name);
    writeModelFolder(repositoryFolder, name, template);
    return { name, template };
  });
  const normalizedEnsembleName = safeFolderName(ensembleName || "pipeline", "pipeline");
  const ensembleFolder = path.join(repositoryFolder, normalizedEnsembleName);
  fs.mkdirSync(ensembleFolder, { recursive: true });
  writeFile(ensembleFolder, "config.pbtxt", renderEnsembleConfig(normalizedEnsembleName, normalizedSteps));
  return {
    repositoryFolder,
    ensembleFolder,
    configPath: path.join(ensembleFolder, "config.pbtxt"),
    steps: normalizedSteps,
  };
}

module.exports = {
  ENSEMBLE_PRESETS,
  TEMPLATES,
  assertSafeTargetFolder,
  normalizeTritonModelName,
  renderEnsembleConfig,
  renderModelConfig,
  safeFolderName,
  scaffoldEnsembleRepository,
  scaffoldSingleModelRepository,
  templateById,
  versionFolderPath,
};
