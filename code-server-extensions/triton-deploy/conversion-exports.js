const { randomUUID } = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");
const vscode = require("vscode");
const { workspaceConnection } = require("./build-client");
const profileApi = require("./profile-api");
const s3 = require("./s3-client");
const { planUpload } = require("./s3-files");

const conversionRoot = "/workspace/conversion";

function registerConversionExports(command, { getProfile, listProfiles }) {
  command("tritonControl.restoreConvertedModel", async () => {
    if (!vscode.workspace.isTrusted) throw new Error("Trust the assigned task workspace first.");
    const connection = workspaceConnection();
    const receipts = await profileApi.requestJson(connection.endpoint,
      "/api/preparation/conversion-exports/" + connection.workload, { token: connection.token });
    if (!receipts.length) return vscode.window.showInformationMessage("No saved conversions on this task.");
    const selected = await vscode.window.showQuickPick(receipts.map(receipt => ({ label: receipt.directory,
      description: "s3://" + receipt.bucket + "/" + receipt.prefix, receipt })), { title: "Restore a saved conversion" });
    if (!selected) return;
    const receipt = await profileApi.requestJson(connection.endpoint,
      "/api/preparation/conversion-exports/" + connection.workload + "/" + selected.receipt.export_id,
      { token: connection.token, maxResponseBytes: 64 * 1024 * 1024, failureHint: "Could not read this conversion receipt." });
    const profile = await getProfile(receipt.profile_id);
    const scope = profile.prefix?.replace(/^\/+|\/+$/g, "");
    if (profile.endpoint !== receipt.endpoint || profile.bucket !== receipt.bucket ||
        (scope && !receipt.prefix.startsWith(scope + "/"))) throw new Error("The S3 profile binding has changed.");
    const root = await fs.mkdtemp(path.join(conversionRoot, "restored-"));
    try {
      for (const obj of receipt.objects) {
        const relative = obj.key.slice(receipt.prefix.length + 1);
        if (!obj.key.startsWith(receipt.prefix + "/") || relative.split("/").some(p => !p || p === "." || p === ".."))
          throw new Error("Invalid saved model path.");
        const file = path.join(root, relative);
        await fs.mkdir(path.dirname(file), { recursive: true });
        await s3.download(profile, obj.key, file, { versionId: obj.version_id, expected: obj });
      }
    } catch (error) {
      await fs.rm(root, { recursive: true, force: true });
      throw error;
    }
    vscode.window.showInformationMessage("Restored verified conversion to " + root);
  });
  command("tritonControl.saveConvertedModel", async (uri) => {
    if (!vscode.workspace.isTrusted) throw new Error("Trust the assigned task workspace first.");
    if (!uri) {
      const selection = await vscode.window.showOpenDialog({ canSelectFolders: true, canSelectFiles: false,
        defaultUri: vscode.Uri.file(conversionRoot), openLabel: "Select converted Triton model" });
      if (!selection?.length) return;
      uri = selection[0];
    }
    const task = JSON.parse(await fs.readFile("/inputs/verified/task.json", "utf8"));
    if (uri?.scheme !== "file" || !task.task_id) throw new Error("Select a model folder in an assigned task workspace.");
    const profiles = await listProfiles();
    const choice = await vscode.window.showQuickPick(profiles.filter(p => p.bucket).map(profile => ({
      label: profile.name, description: profile.bucket, profile,
    })), { title: "Save converted model to a versioned S3 profile" });
    if (!choice) return;
    const { profile } = choice;
    const exportId = randomUUID();
    const prefix = [profile.prefix?.replace(/^\/+|\/+$/g, ""), "task-conversions", task.task_id, exportId]
      .filter(Boolean).join("/") + "/";
    const files = await planUpload([uri.fsPath], [conversionRoot], prefix);
    const modelRoot = prefix + path.basename(uri.fsPath) + "/";
    const paths = files.map(file => file.key.slice(modelRoot.length));
    const configs = paths.filter(key => /^(?:[^/]+\/)?config\.pbtxt$/.test(key));
    const versioned = new Set();
    for (const key of paths) {
      const parts = key.split("/");
      if (parts.length >= 2 && /^\d+$/.test(parts[0])) versioned.add("");
      if (parts.length >= 3 && /^\d+$/.test(parts[1])) versioned.add(parts[0] + "/");
    }
    if (!configs.length || configs.some(key => !versioned.has(key.slice(0, -"config.pbtxt".length)))) {
      throw new Error("Select a Triton model or repository containing configs and version directories.");
    }
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification,
      title: "Saving converted model" }, async progress => {
      const connection = workspaceConnection();
      const route = "/api/preparation/conversion-exports/" + connection.workload;
      const selection = { export_id: exportId, profile_id: profile.id,
        directory: path.relative(conversionRoot, uri.fsPath) || "." };
      const send = (suffix, body) => profileApi.requestJson(connection.endpoint, route + suffix,
        { token: connection.token, timeoutMs: (task.download_timeout_seconds + 30) * 1000,
          failureHint: "Check task history and the S3 profile before retrying the export.", body });
      await send("/started", selection);
      try {
        await s3.requireVersioning(profile);
        const objects = [];
        for (const file of files) {
          progress.report({ message: file.key.slice(modelRoot.length) });
          objects.push(await s3.upload(profile, file.key, file.file, { versionReceipt: true }));
        }
        await send("", { ...selection, objects });
      } catch (error) {
        await send("/interrupted", selection).catch(() => {});
        throw error;
      }
      vscode.window.showInformationMessage("Saved conversion verified and recorded in task history.");
    });
  });
}

module.exports = { registerConversionExports };
