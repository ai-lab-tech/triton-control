const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  ENSEMBLE_PRESETS,
  TEMPLATES,
  normalizeTritonModelName,
  scaffoldEnsembleRepository,
  scaffoldSingleModelRepository,
  templateById,
  versionFolderPath,
} = require("../scaffold");

function tempRepository(name) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `triton-${name}-`));
}

test("template registry includes required model types and config metadata", () => {
  const byId = Object.fromEntries(TEMPLATES.map((template) => [template.id, template]));

  for (const id of ["python", "onnx", "tensorrt", "tensorrt-llm", "vllm", "pytorch"]) {
    assert.ok(byId[id], `missing template ${id}`);
    assert.ok(["backend", "platform"].includes(byId[id].configKind));
    assert.ok(byId[id].configValue);
    assert.ok(byId[id].gpuHint);
    assert.ok(Array.isArray(byId[id].files));
  }

  assert.equal(byId.python.configKind, "backend");
  assert.equal(byId.python.configValue, "python");
  assert.equal(byId.onnx.configKind, "platform");
  assert.equal(byId.onnx.configValue, "onnxruntime_onnx");
  assert.equal(byId["tensorrt-llm"].ensembleStepEligible, true);
  assert.equal(byId.vllm.ensembleStepEligible, true);
});

test("ensemble presets include TensorRT-LLM and vLLM pipelines", () => {
  const byId = Object.fromEntries(ENSEMBLE_PRESETS.map((preset) => [preset.id, preset]));

  assert.deepEqual(
    byId["python-tensorrt-llm-python"].steps.map((step) => step.templateId),
    ["python", "tensorrt-llm", "python"],
  );
  assert.deepEqual(
    byId["python-vllm-python"].steps.map((step) => step.templateId),
    ["python", "vllm", "python"],
  );
});

test("single Python scaffold creates config and model.py in version folder", () => {
  const targetFolder = path.join(tempRepository("single-python"), "repo");

  const result = scaffoldSingleModelRepository({
    targetFolder,
    modelName: "Preprocess",
    templateId: "python",
  });

  const config = fs.readFileSync(path.join(targetFolder, "preprocess", "config.pbtxt"), "utf8");
  assert.match(config, /name: "preprocess"/);
  assert.match(config, /backend: "python"/);
  assert.ok(fs.existsSync(path.join(targetFolder, "preprocess", "1", "model.py")));
  assert.equal(result.configPath, path.join(targetFolder, "preprocess", "config.pbtxt"));
});

test("artifact templates create editable placeholder guidance", () => {
  const targetFolder = path.join(tempRepository("single-onnx"), "repo");

  scaffoldSingleModelRepository({
    targetFolder,
    modelName: "classifier",
    templateId: "onnx",
  });

  const config = fs.readFileSync(path.join(targetFolder, "classifier", "config.pbtxt"), "utf8");
  const readme = fs.readFileSync(path.join(targetFolder, "classifier", "1", "README.md"), "utf8");
  assert.match(config, /platform: "onnxruntime_onnx"/);
  assert.match(readme, /model\.onnx/);
});

test("unsafe overwrite is refused", () => {
  const targetFolder = tempRepository("overwrite");
  fs.writeFileSync(path.join(targetFolder, "existing.txt"), "content", "utf8");

  assert.throws(
    () => scaffoldSingleModelRepository({ targetFolder, modelName: "model", templateId: "python" }),
    /already contains files/,
  );
});

test("model names are normalized and version paths are stable", () => {
  assert.equal(normalizeTritonModelName(" Fraud Model! "), "fraud_model");
  assert.equal(versionFolderPath("repo/model", 2), path.join("repo/model", "2"));
});

test("ensemble scaffold creates child models and ensemble scheduling references", () => {
  const targetFolder = path.join(tempRepository("ensemble"), "repo");

  scaffoldEnsembleRepository({
    targetFolder,
    ensembleName: "Fraud Pipeline",
    steps: [
      { name: "preprocess", templateId: "python" },
      { name: "score", templateId: "onnx" },
      { name: "postprocess", templateId: "python" },
    ],
  });

  for (const folder of ["preprocess", "score", "postprocess", "fraud_pipeline"]) {
    assert.ok(fs.existsSync(path.join(targetFolder, folder, "config.pbtxt")), `missing ${folder}`);
  }
  const ensembleConfig = fs.readFileSync(path.join(targetFolder, "fraud_pipeline", "config.pbtxt"), "utf8");
  assert.match(ensembleConfig, /platform: "ensemble"/);
  assert.match(ensembleConfig, /ensemble_scheduling/);
  assert.match(ensembleConfig, /model_name: "preprocess"/);
  assert.match(ensembleConfig, /model_name: "score"/);
  assert.match(ensembleConfig, /model_name: "postprocess"/);
  assert.equal(templateById("tensorrt-llm").ensembleStepEligible, true);
  assert.equal(templateById("vllm").ensembleStepEligible, true);
});

test("ensemble scaffold accepts TensorRT-LLM and vLLM steps", () => {
  for (const templateId of ["tensorrt-llm", "vllm"]) {
    const targetFolder = path.join(tempRepository(`ensemble-${templateId}`), "repo");

    scaffoldEnsembleRepository({
      targetFolder,
      ensembleName: `${templateId} pipeline`,
      steps: [
        { name: "preprocess", templateId: "python" },
        { name: "model", templateId },
        { name: "postprocess", templateId: "python" },
      ],
    });

    const config = fs.readFileSync(path.join(targetFolder, "model", "config.pbtxt"), "utf8");
    assert.match(config, new RegExp(`backend: "${templateById(templateId).configValue}"`));
  }
});
