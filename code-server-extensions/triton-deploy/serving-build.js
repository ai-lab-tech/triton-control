// VS Code interaction only; transport and filesystem snapshotting are separate modules.
const fs = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const { snapshot } = require("./build-snapshot");
const { workspaceConnection, request, buildState, exactImage } = require("./build-client");
const keyFor = value => crypto.createHash("sha256").update(JSON.stringify(value)).digest("hex");

function registerServingBuild(context, vscode) {
  const output = vscode.window.createOutputChannel("Triton Control Builds");
  let disposed = false;
  const monitors = new Map();
  async function scope() {
    const connection = workspaceConnection();
    let document;
    try { document = JSON.parse(await fs.readFile("/inputs/verified/task.json", "utf8")); }
    catch { throw new Error("Open an assigned task workspace from Triton Control first."); }
    if (!/^[a-f0-9-]{36}$/.test(document.task_id || "")) throw new Error("Task manifest is unavailable. Reopen the task workspace.");
    return { connection, taskId: document.task_id };
  }
  function monitor(connection, id) {
    if (monitors.has(id) || disposed) return;
    let lastStatus, interrupted = false;
    async function refresh() {
      try {
        const build = await request(connection, `/api/builds/${id}`);
        if (disposed) return;
        const status = `${id} · ${buildState(build)} · attempt ${build.attempts.at(-1)?.number ?? "pending"}`;
        if (interrupted || status !== lastStatus) output.appendLine(status);
        lastStatus = status;
        interrupted = false;
        if (["succeeded", "failed", "cancelled"].includes(build.operation.state)) {
          if (build.operation.result?.archive) output.appendLine(`Local image archive (namespace/claim/file): ${build.operation.result.archive}`);
          const image = exactImage(build, build.attempts.at(-1)?.number);
          if (image) output.appendLine(`Candidate runtime: ${image}\nBuild success is not production readiness. Validate this exact digest with the model.`);
          monitors.delete(id); return;
        }
        monitors.set(id, setTimeout(refresh, 3000));
      } catch (error) {
        if (disposed) return;
        if (error.retryable || error.status === 429 || error.status >= 500) {
          if (!interrupted) output.appendLine(`${id} · Status unavailable. Reconnecting…`);
          interrupted = true;
          monitors.set(id, setTimeout(refresh, 10000));
          return;
        }
        output.appendLine(error.message); monitors.delete(id);
      }
    }
    monitors.set(id, null); void refresh();
  }
  async function start(resource) {
    const { connection, taskId } = await scope();
    const options = await request(connection, `/api/builds/options?task_id=${encodeURIComponent(taskId)}`);
    const selected = resource ? [resource] : await vscode.window.showOpenDialog({ canSelectFolders: false, canSelectFiles: true, canSelectMany: false, openLabel: "Select Dockerfile" });
    if (!selected?.length) return;
    const selectedPath = selected[0].fsPath;
    const fromFile = (await fs.lstat(selectedPath)).isFile();
    const folder = fromFile ? path.dirname(selectedPath) : selectedPath;
    const dockerfile = fromFile ? path.basename(selectedPath) : "Dockerfile";
    const defaultName = options.local_archive ? "serving" : options.repositories[0];
    const parseImage = value => {
      const match = /^([^@\s]+):([A-Za-z0-9_][A-Za-z0-9_.-]{0,127})$/.exec(value.trim());
      if (!match || !/^(?:[a-z0-9.-]+:[0-9]+\/)?[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*(?:\/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*$/.test(match[1])) return;
      if (!options.local_archive && !options.repositories.includes(match[1])) return;
      return { repository: match[1], tag: match[2] };
    };
    const name = await vscode.window.showInputBox({
      title: "Image name and tag", value: defaultName + ":latest",
      validateInput: value => parseImage(value) ? undefined : options.local_archive
        ? "Enter an image name and tag, for example opt-125m:v1."
        : "Enter a tag for one of these repositories: " + options.repositories.join(", "),
    });
    if (!name) return;
    const image = parseImage(name);
    if (!image) throw new Error("Invalid image name or tag.");
    const profiles = fromFile ? [] : options.staging_profiles || [];
    const profile = profiles.length > 1
      ? await vscode.window.showQuickPick(profiles, { title: "Storage for this build context" })
      : profiles[0];
    if (profiles.length && !profile) return;
    const frozen = await snapshot(folder, dockerfile, options.max_context_bytes, [], [], fromFile);
    frozen.request.task_id = taskId;
    if (fromFile) frozen.request.dockerfile_only = true;
    if (profile) frozen.request.staging_profile = profile;
    output.show(true);
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "Preparing serving image build", cancellable: true }, async (progress, cancellation) => {
      const fingerprint = keyFor(frozen.request);
      const stateKey = `build.contextKey.${fingerprint}`;
      let contextKey = context.workspaceState.get(stateKey) || fingerprint;
      let staged = await request(connection, "/api/build-contexts", { method: "POST", body: frozen.request, key: contextKey });
      if (!staged.ready && (["succeeded", "failed", "cancelled"].includes(staged.operation.state) || Date.parse(staged.expires_at) <= Date.now())) {
        // An explicit new submission can replace an unusable context. Persist the
        // new key BEFORE admission so a lost response/editor restart replays it.
        contextKey = crypto.randomUUID();
        await context.workspaceState.update(stateKey, contextKey);
        staged = await request(connection, "/api/build-contexts", { method: "POST", body: frozen.request, key: contextKey });
      }
      await context.workspaceState.update(`build.context.${taskId}`, staged.id);
      if (!staged.ready && !["succeeded", "failed", "cancelled"].includes(staged.operation.state)) {
        try { staged = await request(connection, `/api/build-contexts/${staged.id}/archive`, { method: "PUT", body: frozen.archive }); }
        catch (error) { if (error.status !== 409) throw error; }
      }
      for (let count = 0; !staged.ready && count < 90; count++) {
        if (disposed || cancellation.isCancellationRequested) return;
        if (["failed", "cancelled"].includes(staged.operation.state)) throw new Error("Context verification failed. Check Dockerfile digests and selected inputs; capture a corrected context.");
        if (staged.operation.state === "succeeded" || Date.parse(staged.expires_at) <= Date.now()) throw new Error("Context expired. Repeat Build serving image to upload a new snapshot.");
        progress.report({ message: "Verifying immutable context…" });
        await new Promise(resolve => setTimeout(resolve, 2000));
        staged = await request(connection, `/api/build-contexts/${staged.id}`);
      }
      if (!staged.ready) throw new Error("Context verification is still pending. Repeat the action with unchanged files to resume.");
      if (disposed || cancellation.isCancellationRequested) return;
      output.appendLine(`Verified context: ${staged.id}`);
      const body = { task_id: taskId, context_id: staged.id, ...image };
      const build = await request(connection, "/api/builds", { method: "POST", body, key: keyFor(body) });
      output.appendLine(`Task ${taskId}: build ${build.id} queued. Use “Inspect serving builds” for logs, cancellation and retry.`);
      monitor(connection, build.id);
    });
  }
  async function inspect() {
    const { connection, taskId } = await scope();
    const builds = await request(connection, `/api/builds?task_id=${encodeURIComponent(taskId)}`);
    if (!builds.length) {
      await vscode.window.showInformationMessage("No serving builds for this task yet.");
      return;
    }
    const chosen = await vscode.window.showQuickPick(builds.map(build => ({ label: `${buildState(build)} · ${build.id}`, description: `${build.attempts.length} attempts`, build })), { title: "Task serving builds (latest 50)" });
    if (!chosen) return;
    const build = chosen.build;
    const actions = ["Refresh / monitor", ...build.attempts.map(a => `Logs: attempt ${a.number}`)];
    if (!["succeeded", "failed", "cancelled"].includes(build.operation.state)) actions.push("Cancel build");
    if (build.operation.state === "failed" && build.attempts.at(-1)?.cleaned) actions.push("Retry as new attempt");
    if (exactImage(build, build.attempts.at(-1)?.number)) actions.push("Copy exact candidate image");
    const action = await vscode.window.showQuickPick(actions, { title: `Build ${build.id}` });
    if (!action) return;
    output.show(true);
    if (action.startsWith("Logs:")) {
      const logs = await request(connection, `/api/builds/${build.id}/logs?attempt=${Number(action.split(" ").at(-1))}`);
      output.clear(); output.appendLine(`Build ${build.id} · ${action}${logs.expired ? " · logs expired" : ""}${logs.truncated ? " · truncated" : ""}`); output.appendLine(logs.output);
    } else if (action === "Copy exact candidate image") {
      await vscode.env.clipboard.writeText(exactImage(build, build.attempts.at(-1).number));
      await vscode.window.showInformationMessage("Exact candidate image copied. Validation and release approval are still required.");
    } else {
      if (action === "Cancel build" || action === "Retry as new attempt") {
        await request(connection, `/api/builds/${build.id}/${action === "Cancel build" ? "cancel" : "retry"}`, { method: "POST" });
      }
      monitor(connection, build.id);
    }
  }
  const guard = fn => async (...args) => { try { await fn(...args); } catch (error) { await vscode.window.showErrorMessage(error.message); } };
  context.subscriptions.push(output,
    vscode.commands.registerCommand("tritonControl.buildServingImage", guard(start)),
    vscode.commands.registerCommand("tritonControl.inspectServingBuilds", guard(inspect)),
    { dispose() { disposed = true; for (const timer of monitors.values()) clearTimeout(timer); monitors.clear(); } });
}
module.exports = { registerServingBuild, keyFor };
