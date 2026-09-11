const vscode = require("vscode");
const crypto = require("node:crypto");
const path = require("node:path");
const s3 = require("./s3-client");
const { planUpload } = require("./s3-files");

class ProfileSession {
  constructor(onChange) {
    this.pending = new Map();
    this.onChange = onChange;
  }
  connect() {
    if (!vscode.workspace.isTrusted)
      throw new Error("Trust this workspace before connecting S3 profiles.");
    if (this.panel) {
      this.panel.reveal();
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      "tritonControlS3Session",
      "S3 Profile Connection",
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [],
      },
    );
    this.panel = panel;
    const nonce = crypto.randomBytes(24).toString("hex");
    panel.webview.onDidReceiveMessage((message) => {
      if (message?.type === "ready") {
        this.ready = true;
        this.onChange();
        return;
      }
      const pending = this.pending.get(message?.id);
      if (!pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) pending.reject(new Error(String(message.error)));
      else if (Array.isArray(message.profiles))
        pending.resolve(message.profiles);
      else pending.reject(new Error("Invalid S3 profile response."));
    });
    panel.onDidDispose(() => {
      this.panel = undefined;
      this.ready = false;
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(new Error("S3 profile connection closed."));
      }
      this.pending.clear();
      this.onChange();
    });
    panel.webview.html = `<!DOCTYPE html><html><head>
      <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline'; connect-src 'self';">
      <style>body{font-family:var(--vscode-font-family);color:var(--vscode-foreground);padding:24px;line-height:1.6}p{max-width:640px}</style>
      </head><body><h2>S3 profiles</h2><p id="status">Connecting to Triton Control…</p>
      <p>Open <strong>Triton Control → S3 Browser</strong> to browse your saved profiles.
      Drag workspace files or folders onto a bucket or S3 folder to upload them.</p>
      <p>Keep this tab open; you can switch to another editor. Closing it disconnects the S3 browser.
      Credentials are fetched through your signed-in Triton Control session and are not saved by this browser.</p>
      <script nonce="${nonce}">
      const api = acquireVsCodeApi();
      window.addEventListener('message', async ({data}) => {
        if (data.type !== 'profiles') return;
        try {
          const response = await fetch('/api/s3-profiles', {credentials:'include', cache:'no-store'});
          if (!response.ok) throw new Error('Cannot load S3 profiles (HTTP ' + response.status + '). Sign in to Triton Control with a member or admin account.');
          const profiles = await response.json();
          if (!Array.isArray(profiles)) throw new Error('Unexpected profile response. Reopen this workspace through Triton Control.');
          document.getElementById('status').textContent = profiles.length ? 'Connected. Select a profile in S3 Browser.' : 'No saved profiles. Create one in Triton Control → S3 Profiles, then refresh S3 Browser.';
          api.postMessage({id:data.id, profiles});
        } catch (error) {
          document.getElementById('status').textContent = error.message;
          api.postMessage({id:data.id, error:error.message});
        }
      });
      api.postMessage({type:'ready'});
      </script></body></html>`;
  }
  profiles() {
    if (!vscode.workspace.isTrusted || !this.panel || !this.ready)
      return Promise.reject(
        new Error(
          "Connect S3 profiles first. Open this workspace through Triton Control over trusted HTTPS.",
        ),
      );
    return new Promise((resolve, reject) => {
      const id = crypto.randomBytes(16).toString("hex");
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new Error(
            "Profile connection timed out. Reopen Connect S3 Profiles.",
          ),
        );
      }, 15000);
      this.pending.set(id, { resolve, reject, timer });
      this.panel.webview.postMessage({ type: "profiles", id });
    });
  }
  dispose() {
    this.panel?.dispose();
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
    this.dropMimeTypes = ["text/uri-list"];
    this.dragMimeTypes = [];
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
    item.contextValue = `tritonS3${node.kind}`;
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
      return profiles.map((p) => ({
        kind: "folder",
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
    if (node.bucket !== profile.bucket || !node.prefix.startsWith(prefix))
      throw new Error(
        "The profile destination changed. Refresh S3 Browser before uploading.",
      );
  }
  async handleDrop(target, transfer, token) {
    try {
      if (!target || target.kind !== "folder")
        throw new Error("Drop onto an S3 bucket or folder.");
      const item = transfer.get("text/uri-list");
      if (!item) return;
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
    if (!target || target.kind !== "folder")
      throw new Error("Select an S3 bucket or folder first.");
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
        "An S3 upload is already running. Wait or cancel it first.",
      );
    if (!vscode.workspace.isTrusted)
      throw new Error("Trust this workspace before uploading.");
    const folders = vscode.workspace.workspaceFolders || [];
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
    const dropped = dropToken?.onCancellationRequested(() =>
      controller.abort(),
    );
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
  }
}
function registerS3Browser(context) {
  const browser = new S3Browser();
  const view = vscode.window.createTreeView("tritonControl.s3Browser", {
    treeDataProvider: browser,
    dragAndDropController: browser,
  });
  context.subscriptions.push(browser, view);
  const command = (name, run) =>
    context.subscriptions.push(
      vscode.commands.registerCommand(name, async (...args) => {
        try {
          await run(...args);
        } catch (error) {
          vscode.window.showErrorMessage(error.message);
        }
      }),
    );
  command("tritonControl.connectS3", () => browser.session.connect());
  command("tritonControl.refreshS3", () => browser.refresh());
  command("tritonControl.uploadS3", (target) => browser.chooseUpload(target));
  command("tritonControl.moreS3", async (node) => {
    await browser.load(node.parent, node.continuation);
    browser.changed.fire(node.parent);
  });
}
module.exports = { registerS3Browser, S3Browser, ProfileSession };
