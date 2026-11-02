const fs = require("fs");
const path = require("path");
const { runBuilder, readServingMetadata } = require("./hf-builder-client");

const IMAGE_DEFAULTS = {
  transformers: "nvcr.io/nvidia/tritonserver:26.06-py3",
  vllm: "nvcr.io/nvidia/tritonserver:26.06-vllm-python-py3",
  "tensorrt-llm": "nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3",
};
const TASKS = ["text-generation", "text2text-generation", "text-classification", "feature-extraction"];

function registerHfBuilder(context, vscode, outputChannel, refresh) {
  const buildRepository = (chooseTask) => async (resource) => {
    try {
      let source = resource?.fsPath;
      if (!source || !fs.existsSync(path.join(source, "config.json"))) {
        source = (await vscode.window.showOpenDialog({
          canSelectFiles: false, canSelectFolders: true, canSelectMany: false,
          openLabel: "Select downloaded Hugging Face model",
        }))?.[0]?.fsPath;
      }
      if (!source) return;
      const python = vscode.workspace.getConfiguration("tritonControlDeploy").get("hfBuilderPython")
        || process.env.TRITON_CONTROL_HF_BUILDER_PYTHON || "python3";
      const info = await runBuilder(python, ["inspect", source]);
      const choice = await vscode.window.showQuickPick([
        { label: "Transformers / Python", runtime: "transformers", description: "Text generation, classification and raw features" },
        { label: "vLLM", runtime: "vllm", description: "Native Triton vLLM backend" },
        { label: "TensorRT-LLM LLM API", runtime: "tensorrt-llm", description: "Python backend with the PyTorch LLM API" },
      ], { title: "HF Builder: Serving runtime" });
      if (!choice) return;
      const runtime = choice.runtime;
      const tasks = runtime === "transformers" ? TASKS : ["text-generation"];
      const inferredTask = TASKS.includes(info.inferred_task) ? info.inferred_task : null;
      if (inferredTask && !tasks.includes(inferredTask)) {
        throw new Error(`The ${runtime} runtime does not support the detected task ${inferredTask}. Choose Transformers / Python for this checkpoint.`);
      }
      let task;
      if (chooseTask || !inferredTask) {
        const orderedTasks = [...tasks].sort((a, b) => Number(b === inferredTask) - Number(a === inferredTask));
        task = await vscode.window.showQuickPick(orderedTasks, { title: "HF Builder: Model task" });
        if (!task) return;
      }
      let device = "gpu";
      if (runtime === "transformers") {
        device = await vscode.window.showQuickPick(["gpu", "cpu"], { title: "HF Builder: Serving device" });
        if (!device) return;
      }
      const image = await vscode.window.showInputBox({
        title: "HF Builder: Target serving image", value: IMAGE_DEFAULTS[runtime],
        prompt: "Use a versioned image containing the selected runtime; compatibility needs serving validation.",
        validateInput: (value) => value.trim() ? undefined : "Image is required.",
        ignoreFocusOut: true,
      });
      if (image === undefined) return;
      const name = await vscode.window.showInputBox({
        title: "HF Builder: Triton model name",
        value: path.basename(source).replace(/[^A-Za-z0-9_-]/g, "_").replace(/^[_-]+/, "") || "hf_model",
        validateInput: (value) => /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(value) ? undefined : "Use letters, digits, underscores or hyphens.",
        ignoreFocusOut: true,
      });
      if (name === undefined) return;
      let parallel = "1";
      if (runtime !== "transformers") {
        parallel = await vscode.window.showInputBox({
          title: "HF Builder: Tensor parallel GPU count", value: "1", ignoreFocusOut: true,
          validateInput: (value) => /^[1-9]\d*$/.test(value) ? undefined : "Enter a positive integer.",
        });
        if (parallel === undefined) return;
      }
      const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || path.dirname(source);
      const destination = await vscode.window.showInputBox({
        title: "HF Builder: Repository destination",
        value: path.join(workspaceRoot, `${name}_triton_repository`),
        prompt: "New repository folder: absolute path or relative to the workspace. This is the repository root; no extra folder is appended.",
        validateInput: (value) => {
          if (!value.trim()) return "Repository destination is required.";
          if (fs.existsSync(path.resolve(workspaceRoot, value.trim()))) {
            return "This path already exists. Choose a new repository folder.";
          }
          return undefined;
        },
        ignoreFocusOut: true,
      });
      if (destination === undefined) return;
      const output = path.resolve(workspaceRoot, destination.trim());
      const args = ["build", source, "--output", output, "--name", name,
        "--runtime", runtime, "--device", device, "--image", image.trim(),
        "--tensor-parallel-size", parallel];
      if (task) args.push("--task", task);
      outputChannel.show(true);
      outputChannel.appendLine(`Building Triton repository from ${source}`);
      outputChannel.appendLine(`Repository destination: ${output}`);
      outputChannel.appendLine(`Model task: ${task || inferredTask}${task ? " (selected)" : " (detected automatically)"}`);
      await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification, title: "Building Hugging Face Triton repository", cancellable: true,
      }, async (_progress, token) => {
        const controller = new AbortController();
        const disposable = token.onCancellationRequested(() => controller.abort());
        if (token.isCancellationRequested) controller.abort();
        try {
          await runBuilder(python, args, { signal: controller.signal, onOutput: (text) => outputChannel.append(text) });
        } finally {
          disposable.dispose();
        }
      });
      refresh();
      const action = await vscode.window.showInformationMessage(
        "Repository generated. Review dependencies and validate inference in the serving image.",
        "Open config", "Deploy repository",
      );
      if (action === "Open config") {
        await vscode.window.showTextDocument(vscode.Uri.file(path.join(output, name, "config.pbtxt")));
      } else if (action === "Deploy repository") {
        await vscode.commands.executeCommand("tritonControl.deployModelRepository", vscode.Uri.file(output));
      }
    } catch (error) {
      outputChannel.appendLine(error.message);
      outputChannel.show(true);
      await vscode.window.showErrorMessage(error.message);
    }
  };
  for (const [command, chooseTask] of [
    ["tritonControl.buildHuggingFaceRepository", false],
    ["tritonControl.buildHuggingFaceRepositoryWithTask", true],
  ]) {
    context.subscriptions.push(vscode.commands.registerCommand(command, buildRepository(chooseTask)));
  }
}

module.exports = { registerHfBuilder, readServingMetadata, runBuilder };
