const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const IMAGE_DEFAULTS = {
  transformers: "nvcr.io/nvidia/tritonserver:26.06-py3",
  vllm: "nvcr.io/nvidia/tritonserver:26.06-vllm-python-py3",
  "tensorrt-llm": "nvcr.io/nvidia/tritonserver:26.06-trtllm-python-py3",
};
const TASKS = ["text-generation", "text2text-generation", "text-classification", "feature-extraction"];

function runBuilder(python, args, { signal, onOutput = () => {} } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error("Model repository generation cancelled."));
      return;
    }
    const child = spawn(python, ["-m", "triton_hf_builder", ...args], {
      shell: false,
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    let overflow = false;
    const cancel = () => child.kill();
    signal?.addEventListener("abort", cancel, { once: true });
    const receive = (stream) => (chunk) => {
      const text = chunk.toString();
      if (stream === "stdout") stdout += text;
      else stderr += text;
      if (stdout.length + stderr.length > 1024 * 1024) {
        overflow = true;
        child.kill();
      } else {
        onOutput(text);
      }
    };
    child.stdout.on("data", receive("stdout"));
    child.stderr.on("data", receive("stderr"));
    child.on("error", (error) => {
      signal?.removeEventListener("abort", cancel);
      reject(new Error(`Cannot run ${python}: ${error.message}. Configure hfBuilderPython and install triton-hf-builder in that environment.`));
    });
    child.on("close", (code) => {
      signal?.removeEventListener("abort", cancel);
      if (signal?.aborted) return reject(new Error("Model repository generation cancelled."));
      if (overflow) return reject(new Error("Builder output exceeded 1 MiB."));
      if (code !== 0) {
        let message = stderr.trim();
        try { message = JSON.parse(message).error || message; } catch { /* Plain Python errors. */ }
        return reject(new Error(`HF builder failed: ${message || `exit ${code}`}`));
      }
      try {
        resolve(JSON.parse(stdout));
      } catch {
        reject(new Error("HF builder returned invalid JSON."));
      }
    });
  });
}

function readServingMetadata(sourceFolder) {
  let repositoryRoot = sourceFolder;
  let manifestPath = path.join(repositoryRoot, "serving.json");
  const selectingModelFolder = !fs.existsSync(manifestPath) && fs.existsSync(path.join(sourceFolder, "config.pbtxt"));
  if (selectingModelFolder) {
    repositoryRoot = path.dirname(sourceFolder);
    manifestPath = path.join(repositoryRoot, "serving.json");
  }
  if (!fs.existsSync(manifestPath)) return null;
  if (fs.statSync(manifestPath).size > 1024 * 1024) throw new Error("serving.json exceeds 1 MiB.");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  if (manifest.generator !== "triton-hf-builder" || manifest.schema_version !== 1) return null;
  if (selectingModelFolder && manifest.model_name !== path.basename(sourceFolder)) return null;
  if (typeof manifest.image !== "string" || !manifest.image || /\s/.test(manifest.image)) {
    throw new Error("Invalid serving image in serving.json.");
  }
  if (!Number.isInteger(manifest.gpu_count) || manifest.gpu_count < 0) {
    throw new Error("Invalid gpu_count in serving.json.");
  }
  // The filename is fixed by schema v1. Never read an arbitrary manifest path.
  const requirementsPath = path.join(repositoryRoot, "requirements.txt");
  if (fs.existsSync(requirementsPath) && fs.statSync(requirementsPath).size > 1024 * 1024) {
    throw new Error("requirements.txt exceeds 1 MiB.");
  }
  const requirements = fs.existsSync(requirementsPath) ? fs.readFileSync(requirementsPath, "utf8") : "";
  return { image: manifest.image, gpuCount: String(manifest.gpu_count), requirementsTxt: requirements };
}

function registerHfBuilder(context, vscode, outputChannel, refresh) {
  context.subscriptions.push(vscode.commands.registerCommand("tritonControl.buildHuggingFaceRepository", async (resource) => {
    try {
      let source = resource?.fsPath;
      if (!source || !fs.existsSync(path.join(source, "config.json"))) {
        source = (await vscode.window.showOpenDialog({
          canSelectFiles: false, canSelectFolders: true, canSelectMany: false,
          openLabel: "Select downloaded Hugging Face model",
        }))?.[0]?.fsPath;
      }
      if (!source) return;
      const python = vscode.workspace.getConfiguration("tritonControlDeploy").get("hfBuilderPython") || "python3";
      const info = await runBuilder(python, ["inspect", source]);
      const choice = await vscode.window.showQuickPick([
        { label: "Transformers / Python", runtime: "transformers", description: "Text generation, classification and raw features" },
        { label: "vLLM", runtime: "vllm", description: "Native Triton vLLM backend" },
        { label: "TensorRT-LLM LLM API", runtime: "tensorrt-llm", description: "Python backend with the PyTorch LLM API" },
      ], { title: "HF Builder: Serving runtime" });
      if (!choice) return;
      const runtime = choice.runtime;
      const tasks = runtime === "transformers" ? TASKS : ["text-generation"];
      const orderedTasks = [...tasks].sort((a, b) => Number(b === info.inferred_task) - Number(a === info.inferred_task));
      const task = await vscode.window.showQuickPick(orderedTasks, { title: "HF Builder: Model task" });
      if (!task) return;
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
      const parent = (await vscode.window.showOpenDialog({
        canSelectFiles: false, canSelectFolders: true, canSelectMany: false,
        openLabel: "Select parent folder for generated repository",
      }))?.[0]?.fsPath;
      if (!parent) return;
      const output = path.join(parent, `${name}_triton_repository`);
      const args = ["build", source, "--output", output, "--name", name,
        "--runtime", runtime, "--task", task, "--device", device, "--image", image.trim(),
        "--tensor-parallel-size", parallel];
      outputChannel.show(true);
      outputChannel.appendLine(`Building Triton repository from ${source}`);
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
  }));
}

module.exports = { registerHfBuilder, readServingMetadata, runBuilder };
