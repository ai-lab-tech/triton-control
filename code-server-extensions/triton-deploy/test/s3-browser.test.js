const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { fileURLToPath } = require("node:url");
const s3 = require("../s3-client");
const messages = [];
let choices = [],
  posted,
  received,
  disposed;
const token = { onCancellationRequested: () => ({ dispose() {} }) };
const vscode = {
  EventEmitter: class {
    constructor() {
      this.event = () => {};
    }
    fire() {}
    dispose() {}
  },
  workspace: { isTrusted: true, workspaceFolders: [] },
  Uri: {
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
    createWebviewPanel: () => ({
      webview: {
        onDidReceiveMessage: (fn) => {
          received = fn;
        },
        postMessage: (message) => {
          posted = message;
        },
      },
      onDidDispose: (fn) => {
        disposed = fn;
      },
      dispose: () => disposed(),
      reveal() {},
    }),
  },
};
const originalLoad = Module._load;
Module._load = function (name, ...args) {
  return name === "vscode" ? vscode : originalLoad.call(this, name, ...args);
};
const { S3Browser, ProfileSession } = require("../s3-browser");
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

test("browser session rejects unauthenticated responses and closing cancels pending requests", async () => {
  const session = new ProfileSession(() => {});
  await assert.rejects(session.profiles(), /Connect S3/);
  session.connect();
  received({ type: "ready" });
  let pending = session.profiles();
  received({ id: posted.id, error: "HTTP 401: sign in" });
  await assert.rejects(pending, /401/);
  pending = session.profiles();
  received({ id: posted.id, profiles: [profile] });
  assert.deepEqual(await pending, [profile]);
  pending = session.profiles();
  session.dispose();
  await assert.rejects(pending, /closed/);
  assert.equal(session.pending.size, 0);
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
    token,
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
