const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { fileURLToPath, pathToFileURL } = require("node:url");
const s3 = require("../s3-client");
const profileApi = require("../profile-api");
const messages = [];
let choices = [], methods = [];
const token = { onCancellationRequested: () => ({ dispose() {} }) };
const vscode = {
  FileChangeType: { Changed: 1 },
  EventEmitter: class {
    constructor() {
      this.event = () => {};
    }
    fire() {}
    dispose() {}
  },
  workspace: { isTrusted: true, workspaceFolders: [] },
  DataTransferItem: class { constructor(value) { this.value = value; } async asString() { return String(this.value); } },
  Uri: {
    file: (filename) => ({ toString: () => pathToFileURL(filename).toString() }),
    parse: (value) => ({
      scheme: new URL(value).protocol.slice(0, -1),
      fsPath: fileURLToPath(value),
    }),
  },
  ProgressLocation: { Notification: 1 },
  ViewColumn: { Active: 1 },
  window: {
    withProgress: async (_, run) => run({ report() {} }, token),
    showWarningMessage: async () => choices.shift(),
    showInformationMessage: (message) => messages.push(message),
    showErrorMessage: (message) => messages.push(message),
    showInputBox: async () => { throw new Error("Manual credentials must not be requested"); },
    showQuickPick: async () => methods.shift(),
    createWebviewPanel: () => { throw new Error("S3 must not open a webview"); },
  },
};
const originalLoad = Module._load;
Module._load = function (name, ...args) {
  return name === "vscode" ? vscode : originalLoad.call(this, name, ...args);
};
const { S3Browser, ProfileSession, folderChange, registerS3Browser, automaticBucketView } = require("../s3-browser");
Module._load = originalLoad;
const profile = {
  id: 1,
  name: "dev",
  bucket: "models",
  endpoint: "https://minio",
  prefix: "",
};
const target = {
  kind: "folder",
  profileId: 1,
  bucket: "models",
  prefix: "workflows/",
};

test("managed profiles load without URL, credentials, or webview prompts", async (t) => {
  const original = profileApi.requestJson;
  const oldUrl = process.env.TRITON_CONTROL_PROFILE_URL;
  const oldToken = process.env.TRITON_CONTROL_PROFILE_TOKEN;
  process.env.TRITON_CONTROL_PROFILE_URL = "http://control/api/development/workspace-s3-profiles/ns/workspace";
  process.env.TRITON_CONTROL_PROFILE_TOKEN = "workspace-token";
  t.after(() => {
    profileApi.requestJson = original;
    if (oldUrl === undefined) delete process.env.TRITON_CONTROL_PROFILE_URL; else process.env.TRITON_CONTROL_PROFILE_URL = oldUrl;
    if (oldToken === undefined) delete process.env.TRITON_CONTROL_PROFILE_TOKEN; else process.env.TRITON_CONTROL_PROFILE_TOKEN = oldToken;
  });
  const session = new ProfileSession(() => {});
  profileApi.requestJson = async (url, path, options) => {
    assert.equal(url, process.env.TRITON_CONTROL_PROFILE_URL);
    assert.equal(path, "");
    assert.equal(options.token, "workspace-token");
    return [profile];
  };
  assert.deepEqual(await session.profiles(), [profile]);
  methods = [{ label: "dev", profileId: 1 }];
  await session.connect();
  assert.equal(session.selectedProfileId, 1);
  profileApi.requestJson = async () => [{ ...profile, ca_certificate: "rotated-ca" }];
  assert.equal((await session.profiles())[0].ca_certificate, "rotated-ca");
  let finish;
  profileApi.requestJson = () => new Promise((resolve) => { finish = resolve; });
  const pending = session.profiles();
  session.dispose();
  finish([profile]);
  await assert.rejects(pending, /closed/);
  vscode.workspace.isTrusted = false;
  await assert.rejects(session.profiles(), /Trust/);
  vscode.workspace.isTrusted = true;
});

test("missing workspace integration never falls back to manual credentials", async () => {
  const session = new ProfileSession(() => {});
  await assert.rejects(session.connect(), /integration update/);
});

test("tree paginates and refuses profiles no longer owned by the session", async (t) => {
  const browser = new S3Browser();
  browser.session.ready = true;
  browser.session.profiles = async () => [profile];
  const oldList = s3.list;
  t.after(() => {
    s3.list = oldList;
    browser.dispose();
  });
  s3.list = async (_, prefix, next) =>
    next
      ? { folders: [], files: [{ key: `${prefix}second`, size: 2 }], next: "" }
      : {
          folders: [`${prefix}folder/`],
          files: [{ key: `${prefix}first`, size: 1 }],
          next: "token",
        };
  const children = await browser.getChildren(target);
  assert.equal(children.at(-1).kind, "more");
  await browser.load(target, "token");
  assert.equal((await browser.getChildren(target)).length, 3);
  browser.session.profiles = async () => [];
  await assert.rejects(browser.getChildren(target), /no longer available/);
});

test("workspace drag/drop uploads nested files and respects overwrite decisions", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "triton-browser-"));
  await fs.mkdir(path.join(root, "training/sub"), { recursive: true });
  await fs.writeFile(path.join(root, "training/a"), "a");
  await fs.writeFile(path.join(root, "training/sub/b"), "b");
  vscode.workspace.workspaceFolders = [
    { uri: { scheme: "file", authority: "", fsPath: root } },
  ];
  const browser = new S3Browser();
  browser.session.profiles = async () => [profile];
  const previous = { head: s3.head, upload: s3.upload };
  t.after(async () => {
    Object.assign(s3, previous);
    browser.dispose();
    await fs.rm(root, { recursive: true, force: true });
  });
  const uploaded = [];
  s3.head = async () => '"etag"';
  s3.upload = async (_, key, file, options) =>
    uploaded.push({ key, etag: options.etag });
  choices = ["Skip", "Replace"];
  await browser.handleDrop(
    target,
    new Map([
      [
        "text/uri-list",
        { asString: async () => `file://${root}/training\r\n` },
      ],
    ]),
    { isCancellationRequested: false },
  );
  assert.deepEqual(uploaded, [
    { key: "workflows/training/sub/b", etag: '"etag"' },
  ]);
  assert.ok(
    messages.some((message) => message.includes("1 uploaded, 1 skipped")),
  );
  uploaded.length = 0;
  choices = ["Replace All"];
  await browser.upload(target, [
    { scheme: "file", fsPath: path.join(root, "training") },
  ]);
  assert.equal(uploaded.length, 2);
  assert.equal(choices.length, 0);
  uploaded.length = 0;
  choices = [];
  await browser.upload(target, [
    { scheme: "file", fsPath: path.join(root, "training") },
  ]);
  assert.equal(uploaded.length, 0);
  assert.ok(messages.some((message) => message.includes("cancelled")));
});

test("drops on empty space use selected profile; object rows use their parent folder", async (t) => {
  const browser = new S3Browser();
  t.after(() => browser.dispose());
  browser.session.profiles = async () => [profile, { ...profile, id: 2, name: "other" }];
  browser.session.selectedProfileId = 1;
  const uploads = [];
  browser.upload = async (destination) => uploads.push(destination);
  const transfer = new Map([["text/uri-list", { asString: async () => "file:///workspace/a" }]]);
  await browser.handleDrop(undefined, transfer, token);
  assert.equal(uploads[0].profileId, 1);
  assert.equal(uploads[0].prefix, "");
  await browser.handleDrop({ kind: "file", key: "models/nested/a", profileId: 1, bucket: "models" }, transfer, token);
  assert.equal(uploads[1].prefix, "models/nested/");
  browser.session.selectedProfileId = undefined;
  methods = [];
  await browser.handleDrop(undefined, transfer, token);
  assert.equal(uploads.length, 2, "cancelled destination picker must not upload");
});

test("folder deletion lists every page before confirmation and includes folder marker", async (t) => {
  const browser = new S3Browser();
  browser.session.profiles = async () => [profile];
  const previous = { list: s3.list, remove: s3.remove };
  t.after(() => { Object.assign(s3, previous); browser.dispose(); });
  const actions = [];
  s3.list = async (_, prefix, next, signal, recursive) => {
    assert.equal(prefix, "workflows/");
    assert.equal(recursive, true);
    actions.push("list");
    return { files: next ? [{ key: "workflows/nested/b" }] : [{ key: "workflows/" }, { key: "workflows/a" }], next: next ? "" : "page2" };
  };
  s3.remove = async (_, key) => actions.push(key);
  choices = [undefined];
  await browser.deleteTarget(target);
  assert.deepEqual(actions, ["list", "list"]);
  actions.length = 0;
  choices = ["Delete"];
  await browser.deleteTarget(target);
  assert.deepEqual(actions, ["list", "list", "workflows/", "workflows/a", "workflows/nested/b"]);
  assert.equal(browser.active, undefined);
});

test("deletion rejects profile roots, changed destinations, and keys outside folder", async (t) => {
  const browser = new S3Browser();
  const previous = { list: s3.list, remove: s3.remove };
  t.after(() => { Object.assign(s3, previous); browser.dispose(); });
  browser.session.profiles = async () => [profile];
  let removed = 0;
  s3.remove = async () => removed++;
  await assert.rejects(browser.deleteTarget({ ...target, root: true }), /profile root/);
  await assert.rejects(browser.deleteTarget({ ...target, prefix: "" }), /profile root/);
  s3.list = async () => ({ files: [{ key: "workflows-other/a" }], next: "" });
  await assert.rejects(browser.deleteTarget(target), /outside/);
  let calls = 0;
  browser.session.profiles = async () => [{ ...profile, endpoint: ++calls > 1 ? "https://other" : profile.endpoint }];
  choices = ["Delete"];
  await assert.rejects(browser.deleteTarget({ kind: "file", key: "a", profileId: 1, bucket: "models" }), /destination changed/);
  assert.equal(removed, 0);
});

test("object deletion refreshes credentials and reports partial failure", async (t) => {
  const browser = new S3Browser();
  const previous = { list: s3.list, remove: s3.remove };
  t.after(() => { Object.assign(s3, previous); browser.dispose(); });
  let revision = 0;
  browser.session.profiles = async () => [{ ...profile, secret_key: String(++revision) }];
  const removed = [];
  s3.remove = async (current, key) => { removed.push(key); assert.equal(current.secret_key, "2"); };
  choices = ["Delete"];
  await browser.deleteTarget({ kind: "file", key: "a+b.txt", profileId: 1, bucket: "models" });
  assert.deepEqual(removed, ["a+b.txt"]);
  s3.list = async () => ({ files: [{ key: "workflows/a" }, { key: "workflows/b" }], next: "" });
  let attempts = 0;
  s3.remove = async () => { if (++attempts === 2) throw new Error("HTTP 403"); };
  choices = ["Delete"];
  await assert.rejects(browser.deleteTarget(target), /HTTP 403.*1 objects deleted/);
  assert.equal(browser.active, undefined);
});


test("S3 drag exports readable workspace files and internal drops choose copy or move", async (t) => {
  const transfers = require("../s3-transfers");
  const originalStage = transfers.stage;
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "s3-drag-test-"));
  const filename = path.join(directory, "a.txt");
  await fs.writeFile(filename, "downloaded");
  transfers.stage = async () => ({ directory, paths: [filename] });
  const browser = new S3Browser();
  t.after(async () => { transfers.stage = originalStage; browser.dispose(); await fs.rm(directory, { recursive: true, force: true }); });
  const data = new Map();
  const node = { kind: "file", key: "a.txt", profileId: 1, bucket: "models" };
  await browser.handleDrag([node], data, { isCancellationRequested: false });
  assert.equal(await fs.readFile(fileURLToPath(await data.get("text/uri-list").asString()), "utf8"), "downloaded");
  const mime = "application/vnd.code.tree.tritoncontrol.s3browser";
  assert.equal(data.get(mime).value[0].key, "a.txt");
  const calls = [];
  browser.copyMove = async (...args) => calls.push(args);
  methods = ["Copy", "Move", undefined];
  await browser.handleDrop(target, data, token);
  await browser.handleDrop(target, data, token);
  await browser.handleDrop(target, data, token);
  assert.deepEqual(calls.map((call) => call[2]), ["copy", "move"]);
});


test("profile mounting preserves local roots when adding, selecting, and disconnecting", () => {
  const local = { name: "workspace", uri: { toString: () => "file:///workspace" } };
  const remote = { name: "S3", uri: { toString: () => "triton-s3://profile-1/" } };
  assert.deepEqual(folderChange([local], [local, remote]), { start: 1, count: 0, folders: [remote] });
  assert.deepEqual(folderChange([local, remote], [local]), { start: 1, count: 1, folders: [] });
  assert.deepEqual(folderChange([local, remote], [local, remote]), { start: 2, count: 0, folders: [] });
});


test("filesystem activation preserves roots and S3 menu clipboard bypasses browser clipboard", async (t) => {
  const saved = { url: process.env.TRITON_CONTROL_PROFILE_URL, token: process.env.TRITON_CONTROL_PROFILE_TOKEN,
    request: profileApi.requestJson, workspace: vscode.workspace, commands: vscode.commands, uri: vscode.Uri };
  const commands = new Map();
  let updates = 0, provider, foldersChanged;
  const contexts = [];
  vscode.workspace = { isTrusted: true, workspaceFolders: [], workspaceFile: { toString: () => "file:///test.code-workspace" },
    onDidChangeWorkspaceFolders: (callback) => { foldersChanged = callback; return { dispose() {} }; },
    registerFileSystemProvider: (_, value) => { provider = value; return { dispose() {} }; }, updateWorkspaceFolders: () => { updates++; return true; } };
  vscode.commands = { registerCommand: (name, fn) => { commands.set(name, fn); return { dispose() {} }; }, executeCommand: async (...args) => { contexts.push(args); } };
  process.env.TRITON_CONTROL_PROFILE_URL = "http://test/profiles";
  process.env.TRITON_CONTROL_PROFILE_TOKEN = "test";
  profileApi.requestJson = async () => [profile];
  Module._load = function (name, ...args) { return name === "vscode" ? vscode : originalLoad.call(this, name, ...args); };
  const subscriptions = [];
  t.after(() => {
    Module._load = originalLoad;
    for (const subscription of subscriptions) subscription.dispose?.();
    vscode.workspace = saved.workspace; vscode.commands = saved.commands; vscode.Uri = saved.uri; profileApi.requestJson = saved.request;
    if (saved.url === undefined) delete process.env.TRITON_CONTROL_PROFILE_URL; else process.env.TRITON_CONTROL_PROFILE_URL = saved.url;
    if (saved.token === undefined) delete process.env.TRITON_CONTROL_PROFILE_TOKEN; else process.env.TRITON_CONTROL_PROFILE_TOKEN = saved.token;
  });
  registerS3Browser({ subscriptions, globalState: { get() {}, async update() {} } });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(updates, 0);
  assert.deepEqual(contexts.at(-1), ["setContext", "tritonControl.s3Connected", false]);
  vscode.workspace.workspaceFolders = [{ uri: { scheme: "triton-s3" } }];
  await foldersChanged();
  assert.deepEqual(contexts.at(-1), ["setContext", "tritonControl.s3Connected", true]);
  vscode.workspace.workspaceFolders = [];
  await foldersChanged();
  assert.deepEqual(contexts.at(-1), ["setContext", "tritonControl.s3Connected", false]);
  assert.ok(commands.has("tritonControl.connectS3"));
  const transfers = require("../s3-transfers"), originalTransfer = transfers.transfer;
  t.after(() => { transfers.transfer = originalTransfer; });
  provider.node = async (uri) => uri.descriptor;
  provider.notify = () => {};
  vscode.Uri = { ...vscode.Uri, from: (value) => value };
  const source = { kind: "file", profileId: 1, bucket: "models", key: "a.txt" };
  let transferred;
  transfers.transfer = async (nodes, destination, getProfile, mode) => {
    transferred = { nodes, destination, mode };
    return { copied: 1, deleted: mode === "move" ? 1 : 0, skipped: 0 };
  };
  for (const [command, mode] of [["copy", "copy"], ["cut", "move"]]) {
    await commands.get(`tritonControl.${command}S3`)({ scheme: "triton-s3", descriptor: source });
    await commands.get("tritonControl.pasteS3")({ scheme: "triton-s3", descriptor: target });
    assert.deepEqual(transferred, { nodes: [source], destination: target, mode });
  }
  const originalUpload = S3Browser.prototype.upload, uploads = [];
  S3Browser.prototype.upload = async (destination, uris) => uploads.push({ destination, uris });
  t.after(() => { S3Browser.prototype.upload = originalUpload; });
  const root = { ...target, root: true, prefix: "" };
  const rootUri = { scheme: "triton-s3", authority: "profile-1", descriptor: root };
  const localFile = { scheme: "file", fsPath: "/workspace/model.bin" };
  vscode.workspace.workspaceFolders = [{ uri: rootUri, name: "S3 profile" }];
  await commands.get("tritonControl.copyS3")(localFile);
  await commands.get("tritonControl.pasteS3")(rootUri);
  await commands.get("tritonControl.uploadWorkspaceS3")(localFile);
  assert.deepEqual(uploads, [ { destination: root, uris: [localFile] }, { destination: root, uris: [localFile] } ]);
});


test("workspace root files paste directly into bucket roots and retain workspace boundaries", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "triton-root-upload-"));
  const outside = await fs.mkdtemp(path.join(os.tmpdir(), "triton-outside-upload-"));
  const oldFolders = vscode.workspace.workspaceFolders;
  const original = { head: s3.head, upload: s3.upload };
  const browser = new S3Browser();
  browser.session.profiles = async () => [profile];
  t.after(async () => { Object.assign(s3, original); vscode.workspace.workspaceFolders = oldFolders;
    browser.dispose(); await fs.rm(root, { recursive: true, force: true }); await fs.rm(outside, { recursive: true, force: true }); });
  vscode.workspace.workspaceFolders = [{ uri: { scheme: "file", fsPath: root } },
    { uri: { scheme: "triton-s3", fsPath: "/" } }];
  const filename = path.join(root, "model.bin");
  const content = Buffer.from([0, 1, 255, 33]);
  await fs.writeFile(filename, content);
  const uploaded = [];
  s3.head = async () => null;
  s3.upload = async (_, key, file) => uploaded.push({ key, content: await fs.readFile(file) });
  browser.setWorkspaceClipboard([{ scheme: "file", fsPath: filename }]);
  await browser.paste({ ...target, root: true, prefix: "" });
  assert.deepEqual(uploaded, [{ key: "model.bin", content }]);
  assert.deepEqual(await fs.readFile(filename), content);
  await fs.writeFile(path.join(outside, "outside.txt"), "outside");
  browser.setWorkspaceClipboard([{ scheme: "file", fsPath: path.join(outside, "outside.txt") }]);
  await assert.rejects(browser.paste({ ...target, root: true, prefix: "" }), /inside the open workspace/);
  assert.equal(uploaded.length, 1);
});


test("parallel Explorer lookups share one profile request and retry after failure", async (t) => {
  const original = profileApi.requestJson;
  const oldUrl = process.env.TRITON_CONTROL_PROFILE_URL, oldToken = process.env.TRITON_CONTROL_PROFILE_TOKEN;
  process.env.TRITON_CONTROL_PROFILE_URL = "http://test/profiles";
  process.env.TRITON_CONTROL_PROFILE_TOKEN = "test";
  const session = new ProfileSession(() => {});
  t.after(() => { profileApi.requestJson = original; session.dispose();
    if (oldUrl === undefined) delete process.env.TRITON_CONTROL_PROFILE_URL; else process.env.TRITON_CONTROL_PROFILE_URL = oldUrl;
    if (oldToken === undefined) delete process.env.TRITON_CONTROL_PROFILE_TOKEN; else process.env.TRITON_CONTROL_PROFILE_TOKEN = oldToken;
  });
  let finish, fail, requests = 0;
  profileApi.requestJson = () => { requests++; return new Promise((resolve, reject) => { finish = resolve; fail = reject; }); };
  const parallel = Array.from({ length: 50 }, () => session.profiles());
  assert.equal(requests, 1);
  finish([profile]);
  assert.ok((await Promise.all(parallel)).every((result) => result[0] === profile));
  const next = session.profiles();
  assert.equal(requests, 2);
  fail(new Error("offline"));
  await assert.rejects(next, /offline/);
  const retry = session.profiles();
  assert.equal(requests, 3);
  finish([{ ...profile, ca_certificate: "updated" }]);
  assert.equal((await retry)[0].ca_certificate, "updated");
});


test("endpoint bucket profiles use fresh owner credentials and cannot escape a profile prefix", async (t) => {
  const browser = new S3Browser(); t.after(() => browser.dispose());
  let current = { ...profile, secret_key: "first" };
  browser.session.profiles = async () => [current];
  assert.equal((await browser.profile("1--other--bucket")).bucket, "other--bucket");
  current = { ...current, secret_key: "rotated" };
  assert.equal((await browser.profile("1--other--bucket")).secret_key, "rotated");
  current = { ...current, prefix: "restricted" };
  await assert.rejects(browser.profile("1--other--bucket"), /prefix-scoped/);
  browser.session.profiles = async () => [];
  await assert.rejects(browser.profile("1--other--bucket"), /no longer available/);
});


test("bucket commands reuse the selected profile, preserve connection on denial, and restore scoped view", async (t) => {
  const saved = { workspace: vscode.workspace, commands: vscode.commands, uri: vscode.Uri,
    input: vscode.window.showInputBox, profiles: ProfileSession.prototype.profiles,
    listBuckets: s3.listBuckets, createBucket: s3.createBucket };
  const commands = new Map(), state = new Map([["s3.disconnected", true]]), subscriptions = [];
  const makeUri = (value) => ({ ...value, query: "", fragment: "",
    toString() { return `${this.scheme}://${this.authority || ""}${this.path}`; },
    with(change) { return makeUri({ ...this, ...change }); } });
  const local = { name: "workspace", uri: makeUri({ scheme: "file", path: "/workspace" }) };
  const scoped = { name: "S3 profile", uri: makeUri({ scheme: "triton-s3", authority: "profile-1", path: "/" }) };
  vscode.Uri = { ...vscode.Uri, from: makeUri };
  vscode.workspace = { isTrusted: true, workspaceFolders: [local, scoped], workspaceFile: { fsPath: "/workspace/test.code-workspace" },
    onDidChangeWorkspaceFolders: () => ({ dispose() {} }), registerFileSystemProvider: () => ({ dispose() {} }),
    updateWorkspaceFolders: (start, count, ...folders) => { vscode.workspace.workspaceFolders.splice(start, count, ...folders); return true; } };
  vscode.commands = { registerCommand: (name, run) => { commands.set(name, run); return { dispose() {} }; }, executeCommand: async () => {} };
  let current = { ...profile }, denied = true, created;
  ProfileSession.prototype.profiles = async () => [current];
  s3.listBuckets = async () => { if (denied) throw new Error("Listing denied (HTTP 403)"); return [{ name: "new-bucket" }]; };
  s3.createBucket = async (p, name) => { created = { id: p.id, name }; };
  vscode.window.showInputBox = async () => "new-bucket";
  Module._load = function (name, ...args) { return name === "vscode" ? vscode : originalLoad.call(this, name, ...args); };
  t.after(() => {
    for (const item of subscriptions) item.dispose?.();
    Module._load = originalLoad; vscode.workspace = saved.workspace; vscode.commands = saved.commands; vscode.Uri = saved.uri;
    vscode.window.showInputBox = saved.input; ProfileSession.prototype.profiles = saved.profiles;
    s3.listBuckets = saved.listBuckets; s3.createBucket = saved.createBucket;
  });
  registerS3Browser({ subscriptions, globalState: { get: (key, fallback) => state.has(key) ? state.get(key) : fallback,
    update: async (key, value) => state.set(key, value) } });
  await commands.get("tritonControl.showS3Buckets")(scoped.uri);
  assert.equal(vscode.workspace.workspaceFolders[1].uri.authority, "profile-1");
  assert.match(messages.at(-1), /403/);
  // CreateBucket does not require ListBuckets permission.
  await commands.get("tritonControl.createS3Bucket")(scoped.uri);
  assert.deepEqual(created, { id: 1, name: "new-bucket" });
  denied = false;
  await commands.get("tritonControl.showS3Buckets")(scoped.uri);
  const endpoint = vscode.workspace.workspaceFolders[1].uri;
  assert.equal(endpoint.authority, "endpoint-1");
  assert.equal(vscode.workspace.workspaceFolders[0], local);
  await commands.get("tritonControl.refreshS3")();
  assert.equal(vscode.workspace.workspaceFolders[1].uri.authority, "endpoint-1");
  await commands.get("tritonControl.showS3ProfileFolder")(endpoint);
  assert.equal(vscode.workspace.workspaceFolders[1].uri.authority, "profile-1");
  await commands.get("tritonControl.refreshS3")();
  assert.equal(vscode.workspace.workspaceFolders[1].uri.authority, "profile-1");
  state.delete("s3.scopedProfiles");
  await commands.get("tritonControl.refreshS3")();
  assert.equal(vscode.workspace.workspaceFolders[1].uri.authority, "endpoint-1");
  denied = true;
  const messageCount = messages.length;
  await commands.get("tritonControl.refreshS3")();
  assert.equal(vscode.workspace.workspaceFolders[1].uri.authority, "profile-1");
  assert.equal(messages.length, messageCount, messages.at(-1));
  // A slow detection result must not reconnect after an explicit disconnect.
  let finishProbe;
  s3.listBuckets = () => new Promise((resolve) => { finishProbe = resolve; });
  const mounting = commands.get("tritonControl.refreshS3")();
  await new Promise((resolve) => setImmediate(resolve));
  await commands.get("tritonControl.disconnectS3")();
  finishProbe([]);
  await mounting;
  assert.deepEqual(vscode.workspace.workspaceFolders, [local]);
  current = { ...profile, prefix: "restricted" }; created = undefined;
  await commands.get("tritonControl.createS3Bucket")(scoped.uri);
  assert.equal(created, undefined); assert.match(messages.at(-1), /restricted to a prefix/);
});


test("automatic bucket discovery handles success, denial, offline endpoints, and scoped profiles", async (t) => {
  const original = s3.listBuckets; t.after(() => { s3.listBuckets = original; });
  let requests = 0;
  s3.listBuckets = async () => { requests++; return []; };
  assert.equal(await automaticBucketView(profile), true);
  assert.equal(await automaticBucketView({ ...profile, prefix: "restricted" }), false);
  assert.equal(requests, 1);
  s3.listBuckets = async () => { throw Object.assign(new Error("denied"), { status: 403 }); };
  assert.equal(await automaticBucketView(profile), false);
  s3.listBuckets = async () => { throw new Error("offline"); };
  assert.equal(await automaticBucketView(profile), false);
});

test("automatic bucket discovery times out and cancels without waiting for the provider", async (t) => {
  const original = s3.listBuckets; t.after(() => { s3.listBuckets = original; });
  let signal, finish;
  s3.listBuckets = (_, requestSignal) => { signal = requestSignal; return new Promise((resolve) => { finish = resolve; }); };
  const started = Date.now();
  assert.equal(await automaticBucketView(profile, 10), false);
  assert.ok(signal.aborted);
  assert.ok(Date.now() - started < 1000);
  finish([]); // Late success must not change the selected view.
});
