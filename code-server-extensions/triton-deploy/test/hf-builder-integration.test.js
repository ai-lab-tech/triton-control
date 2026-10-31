const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { runBuilder, readServingMetadata } = require("../hf-builder-client");

const python = process.env.HF_BUILDER_INTEGRATION_PYTHON;
const enabled = { skip: !python, timeout: 120_000 };

for (const [task, runtime] of [
  ["text-generation", "transformers"],
  ["text-classification", "transformers"],
  ["text2text-generation", "transformers"],
  ["feature-extraction", "transformers"],
  ["text-generation", "vllm"],
  ["text-generation", "tensorrt-llm"],
]) {
  test(`published builder supports ${task} through the ${runtime} plugin contract`, enabled, async (t) => {
    // Arrange
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "published-hf-builder-"));
    t.after(() => fs.rmSync(root, { recursive: true, force: true }));
    const source = path.join(root, "checkpoint with spaces");
    const output = path.join(root, "repository with spaces");
    const image = "example.test/triton:compatibility";
    execFileSync(python, [path.join(__dirname, "hf-builder-fixture.py"), source, task]);
    const device = runtime === "transformers" ? "cpu" : "gpu";

    // Act
    const inspected = await runBuilder(python, ["inspect", source]);
    const args = ["build", source, "--output", output, "--name", "fixture_model",
      "--runtime", runtime, "--device", device, "--image", image, "--tensor-parallel-size", "1"];
    // Base encoder checkpoints need the same explicit task choice as the wizard.
    if (!inspected.inferred_task) args.push("--task", task);
    await runBuilder(python, args);
    const metadata = readServingMetadata(output);
    const modelMetadata = readServingMetadata(path.join(output, "fixture_model"));

    // Assert
    if (task === "feature-extraction") assert.ok([null, task].includes(inspected.inferred_task));
    else assert.equal(inspected.inferred_task, task);
    assert.ok(inspected.weight_files.includes("model.safetensors"));
    assert.equal(metadata.image, image);
    assert.equal(metadata.gpuCount, device === "cpu" ? "0" : "1");
    assert.deepEqual(modelMetadata, metadata);
    assert.match(fs.readFileSync(path.join(output, "fixture_model", "config.pbtxt"), "utf8"), /fixture_model/);
    assert.equal(typeof metadata.requirementsTxt, "string");
    assert.ok(fs.readdirSync(path.join(output, "fixture_model", "1")).length > 0);
  });
}

test("published builder imports from Python and a real notebook kernel", enabled, () => {
  // Arrange
  const expected = process.env.HF_BUILDER_EXPECTED_VERSION;
  if (!expected) throw new Error("HF_BUILDER_EXPECTED_VERSION is required for integration tests");

  // Act
  const output = execFileSync(python, [path.join(__dirname, "hf-builder-notebook.py"), expected], {
    encoding: "utf8", timeout: 110_000,
  });

  // Assert
  assert.deepEqual(JSON.parse(output), { version: expected, python: true, notebook: true });
});
