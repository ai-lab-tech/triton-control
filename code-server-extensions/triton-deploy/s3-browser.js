const vscode = require("vscode");
const profileApi = require("./profile-api");
const path = require("node:path");
const s3 = require("./s3-client");
const { planUpload } = require("./s3-files");
const transfers = require("./s3-transfers");
const fs = require("node:fs/promises");
const S3_MIME = "application/vnd.code.tree.tritoncontrol.s3browser";

class ProfileSession {
  constructor(onChange) {
    this.onChange = onChange;
    this.ready = true;
    this.generation = 0;
  }
  async connect() {
    const profiles = await this.profiles();
    if (!profiles.length) {
      vscode.window.showInformationMessage("No saved S3 profiles. Create one in Triton Control, then refresh S3 Browser.");
      return;
    }
    const selected = await vscode.window.showQuickPick(profiles.map((profile) => ({
      label: profile.name,
      description: profile.bucket,
      profileId: profile.id,
    })), { title: "Choose S3 profile", placeHolder: "Select a saved S3 profile" });
    if (!selected) return;
    this.selectedProfileId = selected.profileId;
    this.ready = true;
    this.onChange();
  }
  async profiles() {
    if (!vscode.workspace.isTrusted)
      throw new Error("Trust this workspace before accessing S3 profiles.");
    const endpoint = process.env.TRITON_CONTROL_PROFILE_URL;
    const token = process.env.TRITON_CONTROL_PROFILE_TOKEN;
    if (!endpoint || !token)
      throw new Error("This workspace needs the S3 profile integration update. Ask your Triton Control administrator to update it.");
    const generation = this.generation;
    // Explorer can stat many files concurrently. Share only the in-flight
    // lookup; subsequent operations still fetch current credentials/ownership.
    let pending = this.pending;
    if (!pending || pending.endpoint !== endpoint || pending.token !== token) {
      pending = { endpoint, token, promise: profileApi.requestJson(endpoint, "", { token }).then((profiles) => {
        if (generation !== this.generation) throw new Error("S3 profile connection closed.");
        if (!Array.isArray(profiles)) throw new Error("Invalid S3 profile response.");
        return profiles;
      }) };
      this.pending = pending;
    }
    try { return await pending.promise; }
    finally { if (this.pending === pending) this.pending = undefined; }
  }
  dispose() {
    this.generation++;
    this.pending = undefined;
    this.ready = false;
    this.selectedProfileId = undefined;
    this.onChange();
  }
}

class S3Browser {
  constructor() {
    this.changed = new vscode.EventEmitter();
    this.onDidChangeTreeData = this.changed.event;
    this.session = new ProfileSession(() => {
      if (!this.session.ready) this.active?.abort();
      this.refresh();
    });
    this.dropMimeTypes = ["text/uri-list", S3_MIME];
    this.dragMimeTypes = ["text/uri-list", S3_MIME];
    this.stagedDownloads = new Set();
    this.pages = new Map();
  }
  refresh() {
    this.pages.clear();
    this.changed.fire();
  }
  async profile(id) {
    const profile = (await this.session.profiles()).find((p) => p.id === id);
    if (!profile)
      throw new Error(
        "This S3 profile is no longer available to the signed-in user.",
      );
    return profile;
  }
  getTreeItem(node) {
    const item = new vscode.TreeItem(
      node.label,
      node.kind === "folder"
        ? vscode.TreeItemCollapsibleState.Collapsed
        : vscode.TreeItemCollapsibleState.None,
    );
    item.contextValue = node.root ? "tritonS3root" : `tritonS3${node.kind}`;
    item.iconPath = new vscode.ThemeIcon(
      node.kind === "folder"
        ? "folder"
        : node.kind === "file"
          ? "file"
          : "refresh",
    );
    if (node.kind === "connect")
      item.command = {
        command: "tritonControl.connectS3",
        title: "Connect S3 Profiles",
      };
    if (node.kind === "more")
      item.command = {
        command: "tritonControl.moreS3",
        title: "Load More",
        arguments: [node],
      };
    if (node.kind === "file")
      item.description = `${node.size.toLocaleString()} bytes`;
    if (node.bucket)
      item.tooltip = `s3://${node.bucket}/${node.prefix ?? node.key ?? ""}`;
    return item;
  }
  async getChildren(node) {
    if (!this.session.ready)
      return [{ kind: "connect", label: "Connect S3 Profiles" }];
    if (!node) {
      const profiles = await this.session.profiles();
      return profiles.filter((p) => !this.session.selectedProfileId || p.id === this.session.selectedProfileId).map((p) => ({
        kind: "folder",
        root: true,
        label: `${p.name} · ${p.bucket}`,
        profileId: p.id,
        bucket: p.bucket,
        prefix: p.prefix ? `${p.prefix.replace(/^\/+|\/+$/g, "")}/` : "",
      }));
    }
    if (node.kind !== "folder") return [];
    this.validateTarget(node, await this.profile(node.profileId));
    const id = `${node.profileId}:${node.prefix}`;
    if (!this.pages.has(id)) await this.load(node);
    return this.pages.get(id) || [];
  }
  async load(node, continuation) {
    const profile = await this.profile(node.profileId);
    this.validateTarget(node, profile);
    const listing = await s3.list(profile, node.prefix, continuation);
    const id = `${node.profileId}:${node.prefix}`;
    const previous = continuation
      ? (this.pages.get(id) || []).filter((n) => n.kind !== "more")
      : [];
    const children = [
      ...listing.folders.map((prefix) => ({
        kind: "folder",
        label: prefix.slice(node.prefix.length).replace(/\/$/, ""),
        prefix,
        profileId: node.profileId,
        bucket: profile.bucket,
      })),
      ...listing.files
        .filter((file) => file.key !== node.prefix)
        .map((file) => ({
          kind: "file",
          label: file.key.slice(node.prefix.length),
          ...file,
          profileId: node.profileId,
          bucket: profile.bucket,
        })),
    ];
    if (listing.next)
      children.push({
        kind: "more",
        label: "Load more…",
        parent: node,
        continuation: listing.next,
      });
    this.pages.set(id, [...previous, ...children]);
  }
  validateTarget(node, profile) {
    const prefix = profile.prefix
      ? `${profile.prefix.replace(/^\/+|\/+$/g, "")}/`
      : "";
    if (node.bucket !== profile.bucket || typeof (node.prefix ?? node.key) !== "string" || !(node.prefix ?? node.key).startsWith(prefix))
      throw new Error(
        "The profile destination changed. Refresh S3 Browser and try again.",
      );
  }
  async operation(title, run, dropToken) {
    if (this.active) throw new Error("An S3 operation is already running. Wait or cancel it first.");
    if (!vscode.workspace.isTrusted) throw new Error("Trust this workspace before transferring files.");
    const controller = new AbortController();
    this.active = controller;
    const dropped = typeof dropToken?.onCancellationRequested === "function"
      ? dropToken.onCancellationRequested(() => controller.abort()) : undefined;
    if (dropToken?.isCancellationRequested) controller.abort();
    try {
      return await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification, title, cancellable: true,
      }, async (progress, token) => {
        const cancel = token.onCancellationRequested(() => controller.abort());
        if (token.isCancellationRequested) controller.abort();
        try { return await run(controller.signal, (message) => progress.report({ message })); }
        finally { cancel.dispose(); }
      });
    } finally {
      dropped?.dispose();
      this.active = undefined;
      this.refresh();
    }
  }
  async overwrite(key) {
    const choice = await vscode.window.showWarningMessage(`Replace ${key}?`, { modal: true }, "Replace", "Skip");
    return choice === "Replace" ? "replace" : choice === "Skip" ? "skip" : "cancel";
  }
  async handleDrag(nodes, transfer, token) {
    try {
      nodes = transfers.selectedNodes(nodes);
      if (!nodes.length) return;
      // Only identifiers and keys travel through drag data, never credentials.
      const descriptors = nodes.map(({ kind, key, prefix, profileId, bucket, root }) => ({ kind, key, prefix, profileId, bucket, root }));
      transfer.set(S3_MIME, new vscode.DataTransferItem(descriptors));
      const staged = await this.operation("Prepare S3 download", (signal, report) =>
        transfers.stage(nodes, (id) => this.profile(id), { signal, report }), token);
      this.stagedDownloads.add(staged.directory);
      const workspace = vscode.workspace.workspaceFolders?.[0]?.uri;
      const uris = staged.paths.map((filename) => workspace?.scheme === "vscode-remote"
        ? workspace.with({ path: filename, query: "", fragment: "" })
        : vscode.Uri.file(filename));
      transfer.set("text/uri-list", new vscode.DataTransferItem(uris.map((uri) => uri.toString()).join("\r\n")));
    } catch (error) {
      transfer.delete(S3_MIME);
      transfer.delete("text/uri-list");
      vscode.window.showErrorMessage(error.message);
    }
  }
  async copyMove(nodes, target, mode, token) {
    target = await this.uploadTarget(target);
    if (!target) return;
    const result = await this.operation(mode === "move" ? "Move S3 objects" : "Copy S3 objects", (signal, report) =>
      transfers.transfer(nodes, target, (id) => this.profile(id), mode, {
        signal, report, overwrite: (key) => this.overwrite(`s3://${target.bucket}/${key}`),
      }), token);
    vscode.window.showInformationMessage(`S3 transfer finished: ${result.copied} copied, ${result.deleted} source objects removed, ${result.skipped} skipped.`);
  }
  setClipboard(nodes, mode) {
    this.clipboard = { nodes: transfers.selectedNodes(nodes), mode };
    vscode.window.showInformationMessage(`S3 ${mode === "move" ? "cut" : "copy"}: select a destination folder and choose Paste into S3.`);
  }
  setWorkspaceClipboard(uris) {
    if (!uris.length || uris.some((uri) => !["file", "vscode-remote"].includes(uri?.scheme)))
      throw new Error("Select workspace files or folders to copy into S3.");
    this.clipboard = { workspaceUris: [...uris], mode: "copy" };
    vscode.window.showInformationMessage("Copied for S3. Right-click a bucket or S3 folder and choose S3 Operations → Paste.");
  }
  async paste(target) {
    if (this.clipboard?.workspaceUris?.length) {
      const destination = await this.uploadTarget(target);
      if (destination) await this.upload(destination, this.clipboard.workspaceUris);
      return;
    }
    if (!this.clipboard?.nodes.length) throw new Error("Choose S3 Operations → Copy on a workspace or S3 item first.");
    await this.copyMove(this.clipboard.nodes, target, this.clipboard.mode);
    if (this.clipboard.mode === "move") this.clipboard = undefined;
  }
  async download(nodes) {
    const selection = await vscode.window.showOpenDialog({ canSelectFolders: true, canSelectFiles: false,
      canSelectMany: false, openLabel: "Download here", defaultUri: vscode.workspace.workspaceFolders?.[0]?.uri });
    if (!selection?.length) return;
    const result = await this.operation("Download S3 objects", async (signal, report) => {
      const staged = await transfers.stage(nodes, (id) => this.profile(id), { signal, report });
      try {
        return await transfers.saveStage(staged, selection[0].fsPath,
          (vscode.workspace.workspaceFolders || []).filter((folder) => folder.uri.scheme !== "triton-s3").map((folder) => folder.uri.fsPath),
          { signal, overwrite: (key) => this.overwrite(key) });
      } finally { await fs.rm(staged.directory, { recursive: true, force: true }); }
    });
    vscode.window.showInformationMessage(`S3 download finished: ${result.saved} saved, ${result.skipped} skipped.`);
  }
  async uploadTarget(target) {
    if (target?.kind === "more") target = target.parent;
    if (target?.kind === "file") {
      return { ...target, kind: "folder", prefix: target.key.slice(0, target.key.lastIndexOf("/") + 1) };
    }
    if (target?.kind === "folder") return target;
    const roots = (await this.getChildren()).filter((node) => node.kind === "folder");
    if (roots.length === 1) return roots[0];
    if (!roots.length) throw new Error("Choose an S3 profile before uploading.");
    const choice = await vscode.window.showQuickPick(roots.map((node) => ({
      label: node.label, description: `s3://${node.bucket}/${node.prefix}`, target: node,
    })), { title: "Upload destination", placeHolder: "Choose an S3 profile" });
    return choice?.target;
  }
  async deleteTarget(target) {
    if (!target || !["file", "folder"].includes(target.kind) || target.root)
      throw new Error("Select an S3 object or folder, not the profile root.");
    if (!vscode.workspace.isTrusted) throw new Error("Trust this workspace before deleting.");
    if (this.active) throw new Error("An S3 operation is already running. Wait or cancel it first.");
    const controller = new AbortController();
    this.active = controller;
    let completed = 0;
    try {
      await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: `Delete s3://${target.bucket}/${target.key ?? target.prefix}`,
        cancellable: true,
      }, async (progress, cancelToken) => {
        const cancel = cancelToken.onCancellationRequested(() => controller.abort());
        if (cancelToken.isCancellationRequested) controller.abort();
        try {
          const original = await this.profile(target.profileId);
          this.validateTarget(target, original);
          const destination = [original.endpoint, original.bucket, original.prefix, original.force_path_style];
          const currentProfile = async () => {
            controller.signal.throwIfAborted();
            const profile = await this.profile(target.profileId);
            this.validateTarget(target, profile);
            if ([profile.endpoint, profile.bucket, profile.prefix, profile.force_path_style]
              .some((value, index) => value !== destination[index]))
              throw new Error("The profile destination changed. Refresh and start deletion again.");
            return profile;
          };
          const keys = new Set();
          if (target.kind === "file") {
            if (!target.key) throw new Error("Select an S3 object to delete.");
            keys.add(target.key);
          } else {
            const rootPrefix = original.prefix ? original.prefix.replace(/^\/+|\/+$/g, "") + "/" : "";
            if (!target.prefix || !target.prefix.endsWith("/") || target.prefix === rootPrefix)
              throw new Error("Select a folder below the profile root.");
            let next;
            const seen = new Set();
            do {
              progress.report({ message: "Checking folder contents…" });
              const listing = await s3.list(await currentProfile(), target.prefix, next, controller.signal, true);
              for (const file of listing.files) {
                if (!file.key.startsWith(target.prefix)) throw new Error("S3 returned an object outside the selected folder.");
                keys.add(file.key);
                if (keys.size > 10000) throw new Error("Delete smaller folders (at most 10,000 objects at once).");
              }
              next = listing.next;
              if (next && seen.has(next)) throw new Error("S3 returned a repeated continuation token.");
              seen.add(next);
            } while (next);
          }
          if (!keys.size) { vscode.window.showInformationMessage("This folder is already empty."); return; }
          controller.signal.throwIfAborted();
          const choice = await vscode.window.showWarningMessage(
            `Delete ${target.kind === "folder" ? `folder and ${keys.size} objects` : "object"} at s3://${target.bucket}/${target.key ?? target.prefix}?`,
            { modal: true, detail: "Deleted objects are not moved to a trash folder. Previous versions, if enabled, are retained." },
            "Delete",
          );
          if (choice !== "Delete") return;
          for (const key of keys) {
            const profile = await currentProfile();
            controller.signal.throwIfAborted();
            await s3.remove(profile, key, controller.signal);
            completed++;
            progress.report({ message: `${completed}/${keys.size} deleted`, increment: 100 / keys.size });
          }
          vscode.window.showInformationMessage(`S3 deletion finished: ${completed} objects deleted.`);
        } finally { cancel.dispose(); }
      });
    } catch (error) {
      if (controller.signal.aborted)
        vscode.window.showInformationMessage(`S3 deletion cancelled. ${completed} objects deleted.`);
      else throw new Error(`${error.message} (${completed} objects deleted before stopping.)`);
    } finally {
      this.active = undefined;
      this.refresh();
    }
  }
  async handleDrop(target, transfer, token) {
    try {
      const internal = transfer.get(S3_MIME);
      if (internal) {
        const nodes = internal.value;
        if (!Array.isArray(nodes)) throw new Error("Invalid S3 drag selection.");
        const action = await vscode.window.showQuickPick(["Copy", "Move"], { title: "S3 transfer", placeHolder: "Copy or move the selected S3 items?" });
        if (action) await this.copyMove(nodes, target, action.toLowerCase(), token);
        return;
      }
      const item = transfer.get("text/uri-list");
      if (!item) throw new Error("Drag files or folders from the workspace Explorer.");
      target = await this.uploadTarget(target);
      if (!target || token?.isCancellationRequested) return;
      const uris = (await item.asString())
        .split(/\r?\n/)
        .filter((line) => line && !line.startsWith("#"))
        .map((line) => vscode.Uri.parse(line));
      await this.upload(target, uris, token);
    } catch (error) {
      vscode.window.showErrorMessage(error.message);
    }
  }
  async chooseUpload(target) {
    target = await this.uploadTarget(target);
    if (!target) return;
    const kind = await vscode.window.showQuickPick(["Files", "Folder"], {
      placeHolder: "Upload workspace files or a folder",
    });
    if (!kind) return;
    const uris = await vscode.window.showOpenDialog({
      canSelectMany: kind === "Files",
      canSelectFiles: kind === "Files",
      canSelectFolders: kind === "Folder",
      openLabel: "Upload to S3",
    });
    if (uris) await this.upload(target, uris);
  }
  async upload(target, uris, dropToken) {
    if (this.active)
      throw new Error(
        "An S3 operation is already running. Wait or cancel it first.",
      );
    if (!vscode.workspace.isTrusted)
      throw new Error("Trust this workspace before uploading.");
    const folders = (vscode.workspace.workspaceFolders || []).filter((folder) =>
      ["file", "vscode-remote"].includes(folder.uri.scheme));
    for (const uri of uris) {
      if (
        !["file", "vscode-remote"].includes(uri.scheme) ||
        !path.isAbsolute(uri.fsPath)
      ) {
        throw new Error(
          "Drag files from this workspace Explorer. Computer-to-browser drops are not supported.",
        );
      }
    }
    const controller = new AbortController();
    this.active = controller;
    const dropped = typeof dropToken?.onCancellationRequested === "function"
      ? dropToken.onCancellationRequested(() => controller.abort()) : undefined;
    if (dropToken?.isCancellationRequested) controller.abort();
    let completed = 0,
      skipped = 0;
    try {
      await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `Upload to s3://${target.bucket}/${target.prefix}`,
          cancellable: true,
        },
        async (progress, token) => {
          const cancel = token.onCancellationRequested(() =>
            controller.abort(),
          );
          try {
            let profile = await this.profile(target.profileId);
            this.validateTarget(target, profile);
            const destination = {
              endpoint: profile.endpoint,
              bucket: profile.bucket,
              prefix: profile.prefix,
            };
            progress.report({ message: "Checking workspace files…" });
            const files = await planUpload(
              uris.map((u) => u.fsPath),
              folders.map((f) => f.uri.fsPath),
              target.prefix,
              controller.signal,
            );
            const total = files.reduce((sum, file) => sum + file.size, 0);
            let overwriteAll = false;
            for (const file of files) {
              controller.signal.throwIfAborted();
              // Recheck session ownership and current credentials before every file.
              profile = await this.profile(target.profileId);
              if (
                Object.keys(destination).some(
                  (key) => destination[key] !== profile[key],
                )
              )
                throw new Error(
                  "The profile destination changed. Refresh and start the upload again.",
                );
              progress.report({
                message: `${completed + skipped + 1}/${files.length}: ${path.basename(file.file)}`,
              });
              const etag = await s3.head(profile, file.key, controller.signal);
              if (etag && !overwriteAll) {
                const choice = await vscode.window.showWarningMessage(
                  `Replace s3://${profile.bucket}/${file.key}?`,
                  { modal: true },
                  "Replace",
                  "Replace All",
                  "Skip",
                );
                if (!choice) {
                  controller.abort();
                  controller.signal.throwIfAborted();
                }
                if (choice === "Skip") {
                  skipped++;
                  progress.report({
                    increment: total ? (file.size / total) * 100 : 0,
                  });
                  continue;
                }
                overwriteAll = choice === "Replace All";
              }
              await s3.upload(profile, file.key, file.file, {
                signal: controller.signal,
                etag,
                onBytes: (bytes) =>
                  progress.report({
                    increment: total ? (bytes / total) * 100 : 0,
                  }),
              });
              completed++;
            }
          } finally {
            cancel.dispose();
          }
        },
      );
      vscode.window.showInformationMessage(
        `S3 upload finished: ${completed} uploaded, ${skipped} skipped.`,
      );
    } catch (error) {
      if (controller.signal.aborted)
        vscode.window.showInformationMessage(
          `S3 upload cancelled. ${completed} completed files remain in S3.`,
        );
      else
        throw new Error(
          `${error.message} (${completed} files uploaded before stopping.)`,
        );
    } finally {
      dropped?.dispose();
      this.active = undefined;
      this.refresh();
    }
  }
  dispose() {
    this.active?.abort();
    this.session.dispose();
    this.changed.dispose();
    for (const directory of this.stagedDownloads) fs.rm(directory, { recursive: true, force: true }).catch(() => {});
    this.stagedDownloads.clear();
  }
}
function folderChange(current, desired) {
  const same = (a, b) => a && b && a.uri.toString() === b.uri.toString() && a.name === b.name;
  let start = 0, end = current.length, nextEnd = desired.length;
  while (start < end && start < nextEnd && same(current[start], desired[start])) start++;
  while (end > start && nextEnd > start && same(current[end - 1], desired[nextEnd - 1])) { end--; nextEnd--; }
  return { start, count: end - start, folders: desired.slice(start, nextEnd) };
}
function registerS3Browser(context) {
  const { S3FileSystem, SCHEME } = require("./s3-filesystem");
  const browser = new S3Browser();
  const provider = new S3FileSystem((id) => browser.profile(id));
  context.subscriptions.push(browser, provider,
    vscode.workspace.registerFileSystemProvider(SCHEME, provider, { isCaseSensitive: true }));
  const updateConnectionContext = () => vscode.commands.executeCommand("setContext", "tritonControl.s3Connected",
    (vscode.workspace.workspaceFolders || []).some((folder) => folder.uri.scheme === SCHEME));
  updateConnectionContext();
  context.subscriptions.push(vscode.workspace.onDidChangeWorkspaceFolders(updateConnectionContext));
  async function mountProfiles(choose = false, all = false) {
    const pending = context.globalState.get("s3.pendingWorkspace");
    if (pending && vscode.workspace.workspaceFile?.fsPath === pending) {
      await context.globalState.update("s3.pendingWorkspace", undefined);
      if (!vscode.workspace.workspaceFolders?.length) {
        await vscode.commands.executeCommand("workbench.action.reloadWindow");
        return;
      }
    }
    let profiles = await browser.session.profiles();
    if (choose) {
      const choice = await vscode.window.showQuickPick(profiles.map((profile) => ({
        label: profile.name, description: profile.bucket, profile,
      })), { title: "Choose S3 profile" });
      if (!choice) return;
      profiles = [choice.profile];
      await context.globalState.update("s3.selectedProfile", choice.profile.id);
      await context.globalState.update("s3.disconnected", false);
    }
    if (all) {
      await context.globalState.update("s3.selectedProfile", undefined);
      await context.globalState.update("s3.disconnected", false);
    } else if (!choose && context.globalState.get("s3.selectedProfile")) {
      profiles = profiles.filter((profile) => profile.id === context.globalState.get("s3.selectedProfile"));
    }
    // onFileSystem activation can run before VS Code has initialized the
    // workspace folders. Updating then would replace the real local roots.
    if (!choose && !all && vscode.workspace.workspaceFile && !vscode.workspace.workspaceFolders?.length) return;
    const current = vscode.workspace.workspaceFolders || [];
    const local = current.filter((folder) => folder.uri.scheme !== SCHEME).map(({ uri, name }) => ({ uri, name }));
    const remote = profiles.map((profile) => ({
      uri: vscode.Uri.from({ scheme: SCHEME, authority: `profile-${profile.id}`,
        path: "/" + (profile.prefix ? profile.prefix.replace(/^\/+|\/+$/g, "") : "") }),
      name: `S3 · ${profile.name} · ${profile.bucket}`,
    }));
    const desired = [...local, ...remote];
    if (!vscode.workspace.workspaceFile && local.length && remote.length) {
      // code-server can omit the original remote folder when creating an
      // untitled mixed-provider workspace. Serialize local paths explicitly.
      await fs.mkdir(context.globalStorageUri.fsPath, { recursive: true });
      const filename = path.join(context.globalStorageUri.fsPath, "s3-explorer.code-workspace");
      await fs.writeFile(filename, JSON.stringify({ folders: desired.map(({ uri, name }) =>
        uri.scheme === "file" ? { path: uri.fsPath, name } : { uri: uri.toString(), name }) }, null, 2));
      await context.globalState.update("s3.pendingWorkspace", filename);
      await vscode.commands.executeCommand("vscode.openFolder", vscode.Uri.file(filename), { forceReuseWindow: true });
      return;
    }
    const change = folderChange(current, desired);
    if (change.count || change.folders.length)
      if (!vscode.workspace.updateWorkspaceFolders(change.start, change.count, ...change.folders))
        throw new Error("Could not add S3 profiles to Explorer. Reload the window and try again.");
    browser.session.ready = true;
    await vscode.commands.executeCommand("workbench.view.explorer");
    for (const folder of remote) provider.notify(folder.uri);
  }
  const command = (name, run) => context.subscriptions.push(vscode.commands.registerCommand(name, async (...args) => {
    try { return await run(...args); } catch (error) { vscode.window.showErrorMessage(error.message); }
  }));
  const node = async (uri) => {
    if (!uri || uri.scheme !== SCHEME) throw new Error("Select an S3 object or folder in Explorer.");
    return provider.node(uri);
  };
  command("tritonControl.connectS3", () => mountProfiles(true));
  command("tritonControl.switchS3Profile", () => mountProfiles(true));
  command("tritonControl.refreshS3", () => mountProfiles(false, true));
  command("tritonControl.disconnectS3", async () => {
    await context.globalState.update("s3.disconnected", true);
    const folders = vscode.workspace.workspaceFolders || [];
    const local = folders.filter((folder) => folder.uri.scheme !== SCHEME).map(({ uri, name }) => ({ uri, name }));
    const change = folderChange(folders, local);
    if (change.count || change.folders.length) vscode.workspace.updateWorkspaceFolders(change.start, change.count, ...change.folders);
    browser.session.dispose();
  });
  command("tritonControl.uploadS3", async (uri) => {
    try { await browser.chooseUpload(await node(uri)); } finally { provider.notify(uri); }
  });
  command("tritonControl.uploadWorkspaceS3", async (uri, selected) => {
    const roots = (vscode.workspace.workspaceFolders || []).filter((folder) => folder.uri.scheme === SCHEME);
    if (!roots.length) throw new Error("Connect an S3 profile first.");
    const destination = roots.length === 1 ? roots[0] : (await vscode.window.showQuickPick(
      roots.map((folder) => ({ label: folder.name, folder })), { title: "Upload to bucket root" }))?.folder;
    if (!destination) return;
    try { await browser.upload(await node(destination.uri), selected?.length ? selected : [uri]); }
    finally { provider.notify(destination.uri); }
  });
  command("tritonControl.downloadS3", async (uri, selected) => browser.download(await Promise.all((selected?.length ? selected : [uri]).map(node))));
  command("tritonControl.deleteS3", async (uri) => { await browser.deleteTarget(await node(uri)); provider.notify(uri); });
  command("tritonControl.copyS3", async (uri, selected) => {
    const uris = selected?.length ? selected : [uri];
    if (uris.every((item) => ["file", "vscode-remote"].includes(item?.scheme))) browser.setWorkspaceClipboard(uris);
    else browser.setClipboard(await Promise.all(uris.map(node)), "copy");
  });
  command("tritonControl.cutS3", async (uri, selected) =>
    browser.setClipboard(await Promise.all((selected?.length ? selected : [uri]).map(node)), "move"));
  command("tritonControl.pasteS3", async (uri) => {
    const sources = browser.clipboard?.nodes || [];
    try { await browser.paste(await node(uri)); }
    finally {
      provider.notify(uri);
      for (const source of sources) provider.notify(vscode.Uri.from({ scheme: SCHEME,
        authority: `profile-${source.profileId}`, path: "/" + (source.key ?? source.prefix) }));
    }
  });
  if (!context.globalState.get("s3.disconnected") && process.env.TRITON_CONTROL_PROFILE_URL && process.env.TRITON_CONTROL_PROFILE_TOKEN)
    mountProfiles().catch((error) => vscode.window.showErrorMessage(error.message));
}

module.exports = { registerS3Browser, S3Browser, ProfileSession, folderChange };
