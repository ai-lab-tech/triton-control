const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { fileURLToPath } = require("node:url");
const s3 = require("../s3-client");
const profileApi = require("../profile-api");
const messages = [];
let choices = [], inputs = [], methods = [];
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
    showInputBox: async () => inputs.shift(),
    showQuickPick: async () => methods.shift(),
    createWebviewPanel: () => { throw new Error("S3 must not open a webview"); },
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

test("native sign-in fetches profiles dynamically and clears expired authentication", async (t) => {
  const original = profileApi.requestJson;
  t.after(() => { profileApi.requestJson = original; });
  const session = new ProfileSession(() => {});
  await assert.rejects(session.profiles(), /Connect S3/);
  inputs = ["http://control:8000", "member@example.com", "password"];
  methods = [{ label: "Email and password" }];
  let calls = [];
  profileApi.requestJson = async (endpoint, path, options) => {
    calls.push({ endpoint, path, options });
    return path.endsWith("login") ? { access_token: "session-token" } : [profile];
  };
  await session.connect();
  assert.equal(session.ready, true);
  assert.deepEqual(calls[0].options.body, { email: "member@example.com", password: "password" });
  assert.equal(calls[1].options.token, "session-token");
  assert.deepEqual(await session.profiles(), [profile]);
  assert.equal(calls.length, 3);
  profileApi.requestJson = async () => [{ ...profile, ca_certificate: "rotated-ca" }];
  assert.equal((await session.profiles())[0].ca_certificate, "rotated-ca");
  profileApi.requestJson = async () => { throw Object.assign(new Error("HTTP 401"), { status: 401 }); };
  await assert.rejects(session.profiles(), /401/);
  assert.equal(session.ready, false);
  assert.equal(session.token, undefined);
});

test("native token sign-in supports cancellation and disconnect rejects in-flight profiles", async (t) => {
  const original = profileApi.requestJson;
  t.after(() => { profileApi.requestJson = original; });
  const session = new ProfileSession(() => {});
  inputs = [undefined];
  await session.connect();
  assert.equal(session.ready, false);
  inputs = ["http://control:8000", "sso-token"];
  methods = [{ label: "Access token" }];
  profileApi.requestJson = async () => [profile];
  await session.connect();
  assert.equal(session.token, "sso-token");
  let finish;
  profileApi.requestJson = () => new Promise((resolve) => { finish = resolve; });
  const pending = session.profiles();
  session.dispose();
  finish([profile]);
  await assert.rejects(pending, /closed/);
  assert.equal(session.token, undefined);
  vscode.workspace.isTrusted = false;
  await assert.rejects(session.connect(), /Trust/);
  vscode.workspace.isTrusted = true;
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
