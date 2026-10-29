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

function builderWizard(t, { inferredTask, runtime = "transformers", selectedTask = "feature-extraction", cancelTask = false, destination, cancelDestination = false, finalAction } = {}) {
  const root = fixture(t);
  const source = path.join(root, "checkpoint");
  fs.mkdirSync(source);
  fs.writeFileSync(path.join(source, "config.json"), "{}");
  pythonModule(t, [
    "import sys,json,pathlib",
    "args=sys.argv[1:]",
    `if args[0]=='inspect': print(${JSON.stringify(JSON.stringify({ inferred_task: inferredTask ?? null }))})`,
    "else:",
    " out=pathlib.Path(args[args.index('--output')+1]); out.mkdir(parents=True)",
    " (out/'invocation.json').write_text(json.dumps(args))",
    " print(json.dumps({'repository':str(out)}))",
  ].join("\n"));
  const callbacks = new Map();
  const taskPrompts = [];
  const errors = [];
  const output = [];
  const destinationPrompts = [];
  const validationErrors = [];
  const deploymentCalls = [];
  const chosenDestination = destination ?? path.join(root, "chosen repository");
  const destinationPath = path.resolve(root, chosenDestination);
  const inputs = ["test/serving:1", "demo", "1"];
  const vscode = {
    ProgressLocation: { Notification: 1 },
    commands: { registerCommand: (name, callback) => {
      callbacks.set(name, callback);
      return { dispose() {} };
    }, executeCommand: async (...args) => { deploymentCalls.push(args); } },
    Uri: { file: (fsPath) => ({ fsPath }) },
    workspace: {
      getConfiguration: () => ({ get: () => "python3" }),
      workspaceFolders: [{ uri: { fsPath: root } }],
    },
    window: {
      showQuickPick: async (items, options) => {
        if (options.title === "HF Builder: Serving runtime") return items.find(item => item.runtime === runtime);
        if (options.title === "HF Builder: Serving device") return "cpu";
        if (options.title === "HF Builder: Model task") {
          taskPrompts.push(items);
          return cancelTask ? undefined : selectedTask;
        }
        throw new Error(`Unexpected prompt: ${options.title}`);
      },
      showInputBox: async (options) => {
        if (options.title === "HF Builder: Repository destination") {
          destinationPrompts.push(options);
          if (cancelDestination) return undefined;
          const error = options.validateInput(chosenDestination);
          if (error) { validationErrors.push(error); return undefined; }
          return chosenDestination;
        }
        return inputs.shift();
      },
      showOpenDialog: async () => [{ fsPath: root }],
      withProgress: async (_options, work) => work({}, {
        isCancellationRequested: false, onCancellationRequested: () => ({ dispose() {} }),
      }),
      showInformationMessage: async () => finalAction,
      showErrorMessage: async error => { errors.push(error); },
    },
  };
  registerHfBuilder({ subscriptions: [] }, vscode, {
    show() {}, append() {}, appendLine(line) { output.push(line); },
  }, () => {});
  const invocationPath = path.join(destinationPath, "invocation.json");
  return {
    run: (command = "tritonControl.buildHuggingFaceRepository") => callbacks.get(command)({ fsPath: source }),
    readInvocation: () => JSON.parse(fs.readFileSync(invocationPath, "utf8")),
    invocationExists: () => fs.existsSync(invocationPath),
    taskPrompts, errors, output, destinationPrompts, validationErrors, destinationPath, deploymentCalls,
  };
}

test("absolute destination is used directly without appending another repository folder", async (t) => {
  // Arrange
  const destination = path.join(fixture(t), "selected repository");
  const wizard = builderWizard(t, { inferredTask: "text-generation", destination });
  // Act
  await wizard.run();
  // Assert
  assert.deepEqual(wizard.errors, []);
  const args = wizard.readInvocation();
  assert.equal(args[args.indexOf("--output") + 1], destination);
  assert.equal(fs.existsSync(path.join(destination, "demo_triton_repository")), false);
});

test("relative destination is resolved against the workspace without a model-name suffix", async (t) => {
  // Arrange
  const destination = path.join("exports", "my-classifier");
  const wizard = builderWizard(t, { inferredTask: "text-classification", destination });
  // Act
  await wizard.run();
  // Assert
  assert.deepEqual(wizard.errors, []);
  const args = wizard.readInvocation();
  assert.equal(args[args.indexOf("--output") + 1], wizard.destinationPath);
  assert.equal(path.basename(wizard.destinationPath), "my-classifier");
});

test("existing destination is rejected by the dialog without generating or overwriting files", async (t) => {
  // Arrange
  const destination = fixture(t);
  const preserved = path.join(destination, "keep.txt");
  fs.writeFileSync(preserved, "existing data");
  const wizard = builderWizard(t, { inferredTask: "text-generation", destination });
  // Act
  await wizard.run();
  // Assert
  assert.equal(wizard.validationErrors.length, 1);
  assert.match(wizard.validationErrors[0], /already exists/);
  assert.equal(wizard.invocationExists(), false);
  assert.equal(fs.readFileSync(preserved, "utf8"), "existing data");
});

test("empty destination is rejected by the dialog", async (t) => {
  // Arrange
  const wizard = builderWizard(t, { inferredTask: "text-generation", destination: "  " });
  // Act
  await wizard.run();
  // Assert
  assert.equal(wizard.validationErrors.length, 1);
  assert.match(wizard.validationErrors[0], /required/);
  assert.equal(wizard.invocationExists(), false);
});

test("cancelling the destination stops generation", async (t) => {
  // Arrange
  const wizard = builderWizard(t, { inferredTask: "text-generation", cancelDestination: true });
  // Act
  await wizard.run();
  // Assert
  assert.deepEqual(wizard.errors, []);
  assert.equal(wizard.destinationPrompts.length, 1);
  assert.equal(wizard.invocationExists(), false);
});

test("deployment handoff uses the chosen repository root", async (t) => {
  // Arrange
  const wizard = builderWizard(t, { inferredTask: "text-generation", finalAction: "Deploy repository" });
  // Act
  await wizard.run();
  // Assert
  assert.deepEqual(wizard.errors, []);
  assert.deepEqual(wizard.deploymentCalls, [
    ["tritonControl.deployModelRepository", { fsPath: wizard.destinationPath }],
  ]);
});

for (const task of ["text-generation", "text-classification", "text2text-generation"]) {
  test(`detected ${task} skips the task dialog and leaves task inference to Python`, async (t) => {
    // Arrange
    const wizard = builderWizard(t, { inferredTask: task });
    // Act
    await wizard.run();
    // Assert
    assert.deepEqual(wizard.errors, []);
    assert.equal(wizard.taskPrompts.length, 0);
    assert.equal(wizard.readInvocation().includes("--task"), false);
    assert.ok(wizard.output.some(line => line.includes(`${task} (detected automatically)`)));
  });
}

test("ambiguous metadata asks for a task and sends the selection to Python", async (t) => {
  // Arrange
  const wizard = builderWizard(t, { inferredTask: null, selectedTask: "text-classification" });
  // Act
  await wizard.run();
  // Assert
  assert.deepEqual(wizard.errors, []);
  assert.equal(wizard.taskPrompts.length, 1);
  const args = wizard.readInvocation();
  assert.equal(args[args.indexOf("--task") + 1], "text-classification");
});

test("choose-task command lets users request feature extraction explicitly", async (t) => {
  // Arrange
  const wizard = builderWizard(t, { inferredTask: "text-generation", selectedTask: "feature-extraction" });
  const command = "tritonControl.buildHuggingFaceRepositoryWithTask";
  const metadata = JSON.parse(fs.readFileSync(path.join(__dirname, "../package.json"), "utf8"));
  // Act
  await wizard.run(command);
  // Assert
  assert.deepEqual(wizard.errors, []);
  assert.equal(wizard.taskPrompts.length, 1);
  const args = wizard.readInvocation();
  assert.equal(args[args.indexOf("--task") + 1], "feature-extraction");
  assert.ok(metadata.activationEvents.includes(`onCommand:${command}`));
  assert.ok(metadata.contributes.commands.some(item => item.command === command));
});

test("incompatible runtime does not silently replace an inferred classification task", async (t) => {
  // Arrange
  const wizard = builderWizard(t, { inferredTask: "text-classification", runtime: "vllm" });
  // Act
  await wizard.run();
  // Assert
  assert.equal(wizard.errors.length, 1);
  assert.match(wizard.errors[0], /vllm.*does not support.*text-classification/);
  assert.equal(wizard.taskPrompts.length, 0);
  assert.equal(wizard.invocationExists(), false);
});

test("cancelling an ambiguous task stops before generating a repository", async (t) => {
  // Arrange
  const wizard = builderWizard(t, { inferredTask: null, cancelTask: true });
  // Act
  await wizard.run();
  // Assert
  assert.deepEqual(wizard.errors, []);
  assert.equal(wizard.taskPrompts.length, 1);
  assert.equal(wizard.invocationExists(), false);
});

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
  const callbacks = new Map();
  const context = { subscriptions: [] };
  const vscode = {
    commands: { registerCommand: (name, handler) => {
      callbacks.set(name, handler);
      return { dispose() {} };
    } },
    window: { showOpenDialog: async () => undefined },
  };

  // Act
  registerHfBuilder(context, vscode, {}, () => {});
  await callbacks.get("tritonControl.buildHuggingFaceRepository")();

  // Assert
  assert.equal(callbacks.has("tritonControl.buildHuggingFaceRepository"), true);
  assert.equal(callbacks.has("tritonControl.buildHuggingFaceRepositoryWithTask"), true);
  assert.equal(context.subscriptions.length, 2);
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
  const destination = path.join(root, "selected destination");
  const inputs = ["custom/vllm:1.0", "my_model", "2", destination];
  const context = { subscriptions: [] };
  const vscode = {
    ProgressLocation: { Notification: 1 },
    commands: { registerCommand: (name, handler) => {
      if (name === "tritonControl.buildHuggingFaceRepository") callback = handler;
      return { dispose() {} };
    } },
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
  const invocation = JSON.parse(fs.readFileSync(path.join(destination, "invocation.json"), "utf8"));
  assert.equal(invocation[invocation.indexOf("--runtime") + 1], "vllm");
  assert.equal(invocation[invocation.indexOf("--image") + 1], "custom/vllm:1.0");
  assert.equal(invocation[invocation.indexOf("--tensor-parallel-size") + 1], "2");
  assert.equal(invocation.includes("--task"), false);
  assert.equal(invocation[1], source);
  assert.equal(refreshed, true);
});
