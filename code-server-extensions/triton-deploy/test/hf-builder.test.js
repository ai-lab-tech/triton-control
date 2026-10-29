const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { runBuilder, readServingMetadata, registerHfBuilder } = require("../hf-builder");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "hf-builder-extension-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function captureError(action) {
  try {
    action();
    return null;
  } catch (error) {
    return error;
  }
}

function pythonModule(t, source) {
  const root = fixture(t);
  fs.writeFileSync(path.join(root, "triton_hf_builder.py"), source);
  const previous = process.env.PYTHONPATH;
  process.env.PYTHONPATH = root;
  t.after(() => {
    if (previous === undefined) delete process.env.PYTHONPATH;
    else process.env.PYTHONPATH = previous;
  });
}

test("split UTF-8 output preserves non-ASCII model paths", async (t) => {
  // Arrange
  pythonModule(t, [
    "import os,time",
    "data='\"模型\"'.encode('utf8')",
    "os.write(1,data[:2])",
    "time.sleep(0.05)",
    "os.write(1,data[2:])",
  ].join("\n"));
  // Act
  const result = await runBuilder("python3", []);
  // Assert
  assert.equal(result, "模型");
});

test("oversized builder output fails with a bounded output error", async (t) => {
  // Arrange
  pythonModule(t, "import sys\nsys.stdout.write('x' * (2 * 1024 * 1024))\n");
  // Act
  const error = await runBuilder("python3", []).then(() => null, failure => failure);
  // Assert
  assert.match(error.message, /exceeded 1 MiB/);
});

test("unsupported generated schema requires an extension update", (t) => {
  // Arrange
  const root = fixture(t);
  fs.writeFileSync(path.join(root, "serving.json"), JSON.stringify({
    generator: "triton-hf-builder", schema_version: 2,
  }));
  // Act
  const error = captureError(() => readServingMetadata(root));
  // Assert
  assert.match(error.message, /Unsupported HF builder serving schema: 2/);
});

test("ordinary repositories have no generated serving metadata", (t) => {
  // Arrange
  const root = fixture(t);

  // Act
  const metadata = readServingMetadata(root);

  // Assert
  assert.equal(metadata, null);
});

test("serving metadata supplies image, GPU count and editable requirements", (t) => {
  // Arrange
  const root = fixture(t);
  fs.writeFileSync(path.join(root, "serving.json"), JSON.stringify({
    generator: "triton-hf-builder", schema_version: 1, image: "test:1", gpu_count: 2,
    requirements_file: "../../outside", // Must never be followed.
  }));
  fs.writeFileSync(path.join(root, "requirements.txt"), "sentencepiece==0.2.1\n");

  // Act
  const metadata = readServingMetadata(root);

  // Assert
  assert.deepEqual(metadata, {
    image: "test:1", gpuCount: "2", requirementsTxt: "sentencepiece==0.2.1\n",
  });
});

test("malformed generated metadata is surfaced instead of silently ignored", (t) => {
  const root = fixture(t);
  for (const manifest of ["not json", JSON.stringify({
    generator: "triton-hf-builder", schema_version: 1, image: "test:1", gpu_count: -1,
  })]) {
    // Arrange
    fs.writeFileSync(path.join(root, "serving.json"), manifest);

    // Act
    const error = captureError(() => readServingMetadata(root));

    // Assert
    assert.ok(error instanceof Error);
  }
});

test("selecting a generated model folder finds its parent's dependency metadata", (t) => {
  // Arrange
  const root = fixture(t);
  const model = path.join(root, "demo");
  fs.mkdirSync(model);
  fs.writeFileSync(path.join(model, "config.pbtxt"), 'name: "demo"\n');
  fs.writeFileSync(path.join(root, "serving.json"), JSON.stringify({
    generator: "triton-hf-builder", schema_version: 1, model_name: "demo", image: "test:1", gpu_count: 1,
  }));
  fs.writeFileSync(path.join(root, "requirements.txt"), "numpy==2.0.0\n");

  // Act
  const metadata = readServingMetadata(model);

  // Assert
  assert.equal(metadata.requirementsTxt, "numpy==2.0.0\n");
});

test("Python invocation preserves literal arguments and parses JSON", async (t) => {
  // Arrange
  const root = fixture(t);
  fs.writeFileSync(path.join(root, "triton_hf_builder.py"), "import sys,json\nprint(json.dumps({'args':sys.argv[1:]}))\n");
  const previous = process.env.PYTHONPATH;
  process.env.PYTHONPATH = root;
  t.after(() => {
    if (previous === undefined) delete process.env.PYTHONPATH;
    else process.env.PYTHONPATH = previous;
  });
  const literal = "a folder;$(touch BAD)`echo unsafe`";

  // Act
  const result = await runBuilder("python3", ["inspect", literal]);

  // Assert
  assert.deepEqual(result.args, ["inspect", literal]);
  assert.equal(fs.existsSync(path.join(root, "BAD")), false);
});

test("missing executable produces an actionable error", async () => {
  // Arrange
  const executable = "/missing/hf-builder-python";

  // Act
  const error = await runBuilder(executable, []).then(() => null, (failure) => failure);

  // Assert
  assert.ok(error instanceof Error);
  assert.match(error.message, /Configure hfBuilderPython/);
});

test("cancelled invocation produces a cancellation error", async () => {
  // Arrange
  const controller = new AbortController();
  controller.abort();

  // Act
  const error = await runBuilder("python3", [], { signal: controller.signal })
    .then(() => null, (failure) => failure);

  // Assert
  assert.ok(error instanceof Error);
  assert.match(error.message, /cancelled/);
});

test("editor command is registered and a cancelled folder selection does no work", async () => {
  // Arrange
  let callback;
  let command;
  const context = { subscriptions: [] };
  const vscode = {
    commands: { registerCommand: (name, handler) => {
      command = name;
      callback = handler;
      return { dispose() {} };
    } },
    window: { showOpenDialog: async () => undefined },
  };

  // Act
  registerHfBuilder(context, vscode, {}, () => {});
  await callback();

  // Assert
  assert.equal(command, "tritonControl.buildHuggingFaceRepository");
  assert.equal(context.subscriptions.length, 1);
});

test("editor wizard passes selected runtime and image to the independent CLI", async (t) => {
  // Arrange
  const root = fixture(t);
  const source = path.join(root, "downloaded model");
  fs.mkdirSync(source);
  fs.writeFileSync(path.join(source, "config.json"), "{}");
  fs.writeFileSync(path.join(root, "triton_hf_builder.py"), [
    "import sys,json,pathlib",
    "args=sys.argv[1:]",
    "if args[0]=='inspect': print(json.dumps({'inferred_task':'text-generation'}))",
    "else:",
    " out=pathlib.Path(args[args.index('--output')+1]); out.mkdir()",
    " (out/'invocation.json').write_text(json.dumps(args))",
    " print(json.dumps({'repository':str(out)}))",
  ].join("\n"));
  const previous = process.env.PYTHONPATH;
  process.env.PYTHONPATH = root;
  t.after(() => {
    if (previous === undefined) delete process.env.PYTHONPATH;
    else process.env.PYTHONPATH = previous;
  });
  let callback;
  let refreshed = false;
  let reportedError;
  const inputs = ["custom/vllm:1.0", "my_model", "2"];
  const context = { subscriptions: [] };
  const vscode = {
    ProgressLocation: { Notification: 1 },
    commands: { registerCommand: (_name, handler) => { callback = handler; return { dispose() {} }; } },
    workspace: { getConfiguration: () => ({ get: () => "python3" }) },
    window: {
      showQuickPick: async (items) => items[1]?.runtime === "vllm" ? items[1] : items[0],
      showInputBox: async () => inputs.shift(),
      showOpenDialog: async () => [{ fsPath: root }],
      withProgress: async (_options, work) => work({}, {
        isCancellationRequested: false, onCancellationRequested: () => ({ dispose() {} }),
      }),
      showInformationMessage: async () => undefined,
      showErrorMessage: async (error) => { reportedError = error; },
    },
  };
  registerHfBuilder(context, vscode, { show() {}, append() {}, appendLine() {} }, () => { refreshed = true; });

  // Act
  await callback({ fsPath: source });

  // Assert
  assert.equal(reportedError, undefined);
  const invocation = JSON.parse(fs.readFileSync(path.join(root, "my_model_triton_repository/invocation.json"), "utf8"));
  assert.equal(invocation[invocation.indexOf("--runtime") + 1], "vllm");
  assert.equal(invocation[invocation.indexOf("--image") + 1], "custom/vllm:1.0");
  assert.equal(invocation[invocation.indexOf("--tensor-parallel-size") + 1], "2");
  assert.equal(invocation[1], source);
  assert.equal(refreshed, true);
});
