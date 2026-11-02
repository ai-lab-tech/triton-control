const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

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
    let receivedBytes = 0;
    let killTimer;
    const cancel = () => {
      child.kill();
      if (!killTimer) {
        killTimer = setTimeout(() => child.kill("SIGKILL"), 5000);
        killTimer.unref();
      }
    };
    const cleanup = () => {
      clearTimeout(killTimer);
      signal?.removeEventListener("abort", cancel);
    };
    signal?.addEventListener("abort", cancel, { once: true });
    const receive = (stream) => (chunk) => {
      if (overflow) return;
      receivedBytes += Buffer.byteLength(chunk, "utf8");
      if (receivedBytes > 1024 * 1024) {
        overflow = true;
        cancel();
        return;
      }
      if (stream === "stdout") stdout += chunk;
      else stderr += chunk;
      onOutput(chunk);
    };
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", receive("stdout"));
    child.stderr.on("data", receive("stderr"));
    child.on("error", (error) => {
      cleanup();
      reject(new Error(`Cannot run ${python}: ${error.message}. Configure hfBuilderPython and install triton-hf-builder in that environment.`));
    });
    child.on("close", (code) => {
      cleanup();
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
  if (manifest.generator !== "triton-hf-builder") return null;
  if (selectingModelFolder && manifest.model_name !== path.basename(sourceFolder)) return null;
  if (manifest.schema_version !== 1) {
    throw new Error(`Unsupported HF builder serving schema: ${manifest.schema_version}. Update the extension.`);
  }
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

module.exports = { runBuilder, readServingMetadata };
