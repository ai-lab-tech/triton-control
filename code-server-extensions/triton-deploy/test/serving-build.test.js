const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const http = require("node:http");
const { snapshot } = require("../build-snapshot");
const { request, workspaceConnection, buildState, exactImage } = require("../build-client");
const { keyFor } = require("../serving-build");
async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "build-context-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.writeFile(path.join(root, "Dockerfile"), "FROM scratch\nCOPY model /model\n");
  await fs.writeFile(path.join(root, "model"), "model bytes");
  return root;
}
test("snapshot is frozen, deterministic and excludes credential files", async t => {
  // Arrange
  const root = await fixture(t);
  await fs.writeFile(path.join(root, ".env"), "sensitive");
  // Act
  const first = await snapshot(root, "Dockerfile", 10000);
  const second = await snapshot(root, "Dockerfile", 10000);
  await fs.writeFile(path.join(root, "model"), "changed");
  // Assert
  assert.deepEqual(first.archive, second.archive);
  assert.equal(first.request.archive_digest, "sha256:" + crypto.createHash("sha256").update(first.archive).digest("hex"));
  assert.equal(first.archive.includes(Buffer.from("sensitive")), false);
  assert.equal(first.archive.includes(Buffer.from("model bytes")), true);
});
test("snapshot refuses symlinks outside the selected context", async t => {
  // Arrange
  const root = await fixture(t);
  await fs.symlink("/etc/passwd", path.join(root, "escape"));
  // Act
  const result = snapshot(root, "Dockerfile", 10000);
  // Assert
  await assert.rejects(result, /symlinks/);
});
test("snapshot refuses oversized contexts before upload", async t => {
  // Arrange
  const root = await fixture(t);
  // Act
  const result = snapshot(root, "Dockerfile", 1024);
  // Assert
  await assert.rejects(result, /limit/);
});
test("snapshot detects private-key material without echoing its contents", async t => {
  // Arrange
  const root = await fixture(t);
  await fs.writeFile(path.join(root, "model"), "-----BEGIN PRIVATE KEY-----\nsecret");
  // Act
  const result = snapshot(root, "Dockerfile", 10000);
  // Assert
  await assert.rejects(result, error => /Credential material/.test(error.message) && !error.message.includes("secret"));
});
test("snapshot rejects a Dockerfile outside the context", async t => {
  // Arrange
  const root = await fixture(t);
  // Act
  const result = snapshot(root, "../Dockerfile", 10000);
  // Assert
  await assert.rejects(result, /relative/);
});
test("workspace connection uses the managed endpoint and workload identity", () => {
  // Arrange
  const env = { TRITON_CONTROL_PROFILE_URL: "https://control.test/base/api/development/workspace-s3-profiles/dev/code-1", TRITON_CONTROL_PROFILE_TOKEN: "fixture" };
  // Act
  const connection = workspaceConnection(env);
  // Assert
  assert.deepEqual(connection, { endpoint: "https://control.test/base", workload: "dev/code-1", token: "fixture" });
});
for (const status of [401, 403]) {
  test(`HTTP ${status} does not leak server credentials or follow redirects`, async t => {
    // Arrange
    const server = http.createServer((req, res) => { res.writeHead(status); res.end("sensitive token"); });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    t.after(() => new Promise(resolve => server.close(resolve)));
    const connection = { endpoint: `http://127.0.0.1:${server.address().port}`, workload: "dev/code", token: "fixture" };
    // Act
    const result = request(connection, "/api/builds");
    // Assert
    await assert.rejects(result, error => error.status === status && !error.message.includes("sensitive"));
  });
}
test("missing staging policy identifies the administrator action", async t => {
  // Arrange
  const server = http.createServer((req, res) => {
    res.writeHead(403);
    res.end(JSON.stringify({ detail: "Build context staging has not been configured" }));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const connection = { endpoint: `http://127.0.0.1:${server.address().port}`, workload: "dev/code", token: "fixture" };
  // Act
  const result = request(connection, "/api/builds/options");
  // Assert
  await assert.rejects(result, /administrator to configure.*build policy/);
});
test("failed attempts cannot expose a candidate digest", () => {
  // Arrange
  const build = { attempts: [{ number: 1, state: "failed", destination: "registry:5000/model:attempt", final_digest: "sha256:" + "a".repeat(64) }] };
  // Act
  const image = exactImage(build, 1);
  // Assert
  assert.equal(image, null);
});
test("successful attempt exposes exact image without resolving its mutable tag", () => {
  // Arrange
  const digest = "sha256:" + "a".repeat(64);
  const build = { attempts: [{ number: 2, state: "succeeded", destination: "registry:5000/model:attempt-2", final_digest: digest }] };
  // Act
  const image = exactImage(build, 2);
  // Assert
  assert.equal(image, "registry:5000/model@" + digest);
});
for (const [phase, label] of [["building", "Building"], ["pushing", "Pushing"]]) {
  test(`progress displays backend ${phase} state`, () => {
    // Arrange
    const build = { operation: { state: "pending", result: { phase } }, attempts: [{ number: 1, state: "running" }] };
    // Act
    const result = buildState(build);
    // Assert
    assert.equal(result, label);
  });
}
test("idempotency distinguishes task and Dockerfile selection", () => {
  // Arrange
  const original = { task_id: "one", dockerfile: "Dockerfile", archive_digest: "same" };
  // Act
  const replay = keyFor({ ...original });
  const otherTask = keyFor({ ...original, task_id: "two" });
  const otherDockerfile = keyFor({ ...original, dockerfile: "alternative" });
  // Assert
  assert.equal(replay, keyFor(original));
  assert.notEqual(replay, otherTask);
  assert.notEqual(replay, otherDockerfile);
});

for (const status of [401, 403]) {
  test(`extension action stops before folder selection when access returns ${status}`, async t => {
    // Arrange
    const { registerServingBuild } = require("../serving-build");
    const server = http.createServer((req, res) => { res.writeHead(status); res.end("denied"); });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    t.after(() => new Promise(resolve => server.close(resolve)));
    const previousUrl = process.env.TRITON_CONTROL_PROFILE_URL;
    const previousToken = process.env.TRITON_CONTROL_PROFILE_TOKEN;
    process.env.TRITON_CONTROL_PROFILE_URL = `http://127.0.0.1:${server.address().port}/api/development/workspace-s3-profiles/dev/code-one`;
    process.env.TRITON_CONTROL_PROFILE_TOKEN = "fixture";
    t.after(() => {
      if (previousUrl === undefined) delete process.env.TRITON_CONTROL_PROFILE_URL; else process.env.TRITON_CONTROL_PROFILE_URL = previousUrl;
      if (previousToken === undefined) delete process.env.TRITON_CONTROL_PROFILE_TOKEN; else process.env.TRITON_CONTROL_PROFILE_TOKEN = previousToken;
    });
    t.mock.method(fs, "readFile", async () => JSON.stringify({ task_id: "a".repeat(8) + "-aaaa-aaaa-aaaa-" + "a".repeat(12) }));
    const commands = new Map(), errors = [];
    let selected = false;
    const vscode = { window: {
      createOutputChannel: () => ({ dispose() {}, appendLine() {}, show() {} }),
      showOpenDialog: async () => { selected = true; },
      showErrorMessage: async message => errors.push(message),
    }, commands: { registerCommand: (name, handler) => { commands.set(name, handler); return { dispose() {} }; } } };
    const context = { subscriptions: [], workspaceState: { get: () => undefined, update: async () => {} } };
    registerServingBuild(context, vscode);
    t.after(() => context.subscriptions.forEach(item => item.dispose()));
    // Act
    await commands.get("tritonControl.buildServingImage")();
    // Assert
    assert.equal(selected, false);
    assert.equal(errors.length, 1);
    assert.match(errors[0], status === 401 ? /expired/ : /denied/);
  });
}

test("build command submits a frozen task-bound context and displays the exact candidate", { timeout: 5000 }, async t => {
  // Arrange
  const { registerServingBuild } = require("../serving-build");
  const root = await fixture(t), calls = [], commands = new Map(), outputLines = [];
  const taskId = "11111111-1111-1111-1111-111111111111", contextId = "b".repeat(32), buildId = "a".repeat(32);
  const digest = "sha256:" + "c".repeat(64);
  const completed = { id: buildId, task_id: taskId, operation: { state: "succeeded" }, attempts: [{ number: 1, state: "succeeded", destination: "registry/model:attempt", final_digest: digest }] };
  let shown;
  const displayed = new Promise(resolve => { shown = resolve; });
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", chunk => chunks.push(chunk));
    req.on("end", () => {
      calls.push({ method: req.method, url: req.url, headers: req.headers, body: Buffer.concat(chunks) });
      res.setHeader("content-type", "application/json");
      let body;
      if (req.url.startsWith("/api/builds/options")) body = { repositories: ["registry/model"], max_context_bytes: 10000 };
      else if (req.url === "/api/build-contexts") body = { id: contextId, ready: false, operation: { state: "pending" } };
      else if (req.url.endsWith("/archive")) body = { id: contextId, ready: true, operation: { state: "succeeded" } };
      else body = completed;
      res.end(JSON.stringify(body));
    });
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const previousUrl = process.env.TRITON_CONTROL_PROFILE_URL, previousToken = process.env.TRITON_CONTROL_PROFILE_TOKEN;
  process.env.TRITON_CONTROL_PROFILE_URL = `http://127.0.0.1:${server.address().port}/api/development/workspace-s3-profiles/dev/code`;
  process.env.TRITON_CONTROL_PROFILE_TOKEN = "fixture";
  t.after(() => {
    if (previousUrl === undefined) delete process.env.TRITON_CONTROL_PROFILE_URL; else process.env.TRITON_CONTROL_PROFILE_URL = previousUrl;
    if (previousToken === undefined) delete process.env.TRITON_CONTROL_PROFILE_TOKEN; else process.env.TRITON_CONTROL_PROFILE_TOKEN = previousToken;
  });
  const readFile = fs.readFile;
  t.mock.method(fs, "readFile", (...args) => args[0] === "/inputs/verified/task.json" ? Promise.resolve(JSON.stringify({ task_id: taskId })) : readFile(...args));
  await fs.rename(path.join(root, "Dockerfile"), path.join(root, "dockerfile"));
  await fs.writeFile(path.join(root, "dockerfile"), "FROM scratch\n");
  await fs.writeFile(path.join(root, "weights.bin"), Buffer.alloc(20000));
  const inputs = ["registry/model:v2"];
  const vscode = { ProgressLocation: { Notification: 1 }, window: {
    createOutputChannel: () => ({ dispose() {}, show() {}, appendLine(line) { outputLines.push(line); if (line.startsWith("Candidate runtime:")) shown(); } }),
    showInputBox: async () => inputs.shift(), showQuickPick: async () => "registry/model",
    showWarningMessage: async () => "Build serving image", showErrorMessage: async message => { throw new Error(message); },
    withProgress: async (options, run) => run({ report() {} }, { isCancellationRequested: false }),
  }, commands: { registerCommand: (name, handler) => { commands.set(name, handler); return { dispose() {} }; } } };
  const context = { subscriptions: [], workspaceState: { get: () => undefined, update: async () => {} } };
  registerServingBuild(context, vscode);
  t.after(() => context.subscriptions.forEach(item => item.dispose()));
  // Act
  await commands.get("tritonControl.buildServingImage")({ fsPath: path.join(root, "dockerfile") });
  await displayed;
  // Assert
  const admission = calls.find(call => call.url === "/api/build-contexts");
  const upload = calls.find(call => call.url.endsWith("/archive"));
  const build = calls.find(call => call.url === "/api/builds" && call.method === "POST");
  assert.equal(JSON.parse(admission.body).dockerfile_only, true);
  assert.equal(JSON.parse(admission.body).staging_profile, undefined);
  assert.equal(JSON.parse(admission.body).dockerfile, "dockerfile");
  assert.ok(!JSON.parse(admission.body).allowed_paths.includes("weights.bin"));
  assert.equal(JSON.parse(admission.body).task_id, taskId);
  assert.equal(JSON.parse(admission.body).archive_digest, "sha256:" + crypto.createHash("sha256").update(upload.body).digest("hex"));
  assert.deepEqual(JSON.parse(build.body), { task_id: taskId, context_id: contextId, repository: "registry/model", tag: "v2" });
  assert.equal(build.headers["x-triton-build-workspace"], "dev/code");
  assert.ok(build.headers["idempotency-key"]);
  assert.ok(outputLines.some(line => line.includes("registry/model@" + digest)));
  assert.ok(outputLines.some(line => line.includes("not production readiness")));
});


for (const command of ["buildServingImage", "inspectServingBuilds"]) {
  test(`${command} explains how to leave a personal workspace before reading build data`, async t => {
    // Arrange
    const { registerServingBuild } = require("../serving-build");
    workspaceEnvironment(t, "http://control.test/api/development/workspace-s3-profiles/dev/personal");
    t.mock.method(fs, "readFile", async () => { throw Object.assign(new Error("ENOENT: private path"), { code: "ENOENT" }); });
    const commands = new Map(), errors = [];
    const context = { subscriptions: [] };
    const vscode = { window: {
      createOutputChannel: () => ({ dispose() {} }),
      showErrorMessage: async message => errors.push(message),
    }, commands: { registerCommand: (name, handler) => { commands.set(name, handler); return { dispose() {} }; } } };
    registerServingBuild(context, vscode);
    t.after(() => context.subscriptions.forEach(item => item.dispose()));
    // Act
    await commands.get(`tritonControl.${command}`)();
    // Assert
    assert.deepEqual(errors, ["Open an assigned task workspace from Triton Control first."]);
  });
}

test("inspect explains empty task history without showing an empty picker", async t => {
  // Arrange
  const { registerServingBuild } = require("../serving-build");
  const calls = [], messages = [], commands = new Map();
  const taskId = "11111111-1111-1111-1111-111111111111";
  const server = http.createServer((req, res) => {
    calls.push(req.url);
    res.setHeader("content-type", "application/json");
    res.end("[]");
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  workspaceEnvironment(t, `http://127.0.0.1:${server.address().port}/api/development/workspace-s3-profiles/dev/task`);
  t.mock.method(fs, "readFile", async () => JSON.stringify({ task_id: taskId }));
  let picked = false;
  const context = { subscriptions: [] };
  const vscode = { window: {
    createOutputChannel: () => ({ dispose() {} }),
    showQuickPick: async () => { picked = true; },
    showInformationMessage: async message => messages.push(message),
    showErrorMessage: async message => { throw new Error(message); },
  }, commands: { registerCommand: (name, handler) => { commands.set(name, handler); return { dispose() {} }; } } };
  registerServingBuild(context, vscode);
  t.after(() => context.subscriptions.forEach(item => item.dispose()));
  // Act
  await commands.get("tritonControl.inspectServingBuilds")();
  // Assert
  assert.deepEqual(calls, [`/api/builds?task_id=${taskId}`]);
  assert.deepEqual(messages, ["No serving builds for this task yet."]);
  assert.equal(picked, false);
});

function workspaceEnvironment(t, url) {
  for (const [name, value] of Object.entries({ TRITON_CONTROL_PROFILE_URL: url, TRITON_CONTROL_PROFILE_TOKEN: "fixture" })) {
    const previous = process.env[name];
    process.env[name] = value;
    t.after(() => { if (previous === undefined) delete process.env[name]; else process.env[name] = previous; });
  }
}

async function recoveryFixture(t, initial, loseResponse = false) {
  const root = await fixture(t), calls = [], errors = [], commands = new Map(), state = new Map();
  const taskId = "11111111-1111-1111-1111-111111111111";
  const oldId = "b".repeat(32), newId = "c".repeat(32);
  let originalKey, ready = false;
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", chunk => chunks.push(chunk));
    req.on("end", () => {
      const key = req.headers["idempotency-key"];
      calls.push({ route: req.url, method: req.method, key, body: Buffer.concat(chunks) });
      res.setHeader("content-type", "application/json");
      let result;
      if (req.url.startsWith("/api/builds/options")) result = { repositories: ["registry/model"], max_context_bytes: 10000 };
      else if (req.url === "/api/build-contexts") {
        originalKey ??= key;
        if (key === originalKey) result = { id: oldId, ...initial };
        else {
          if (loseResponse) { loseResponse = false; req.socket.destroy(); return; }
          result = { id: newId, ready, operation: { state: ready ? "succeeded" : "pending" } };
        }
      } else if (req.url.endsWith("/archive")) {
        ready = true;
        result = { id: req.url.split("/")[3], ready, operation: { state: "succeeded" } };
      } else result = { id: "a".repeat(32), operation: { state: "succeeded" }, attempts: [] };
      res.end(JSON.stringify(result));
    });
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  workspaceEnvironment(t, `http://127.0.0.1:${server.address().port}/api/development/workspace-s3-profiles/dev/task`);
  const readFile = fs.readFile;
  t.mock.method(fs, "readFile", (...args) => args[0] === "/inputs/verified/task.json" ? Promise.resolve(JSON.stringify({ task_id: taskId })) : readFile(...args));
  const context = { subscriptions: [], workspaceState: {
    get: key => state.get(key), update: async (key, value) => { state.set(key, value); },
  } };
  const vscode = { ProgressLocation: { Notification: 1 }, window: {
    createOutputChannel: () => ({ dispose() {}, show() {}, appendLine() {} }),
    showInputBox: async options => options.value, showQuickPick: async () => "registry/model",
    showWarningMessage: async () => "Build serving image", showErrorMessage: async error => errors.push(error),
    withProgress: async (_, run) => run({ report() {} }, { isCancellationRequested: false }),
  }, commands: { registerCommand: (name, handler) => { commands.set(name, handler); return { dispose() {} }; } } };
  const register = () => require("../serving-build").registerServingBuild(context, vscode);
  register();
  t.after(() => context.subscriptions.forEach(item => item.dispose()));
  return { calls, errors, oldId, newId,
    run: () => commands.get("tritonControl.buildServingImage")({ fsPath: root }),
    restart: () => { context.subscriptions.forEach(item => item.dispose()); context.subscriptions = []; register(); },
  };
}

for (const state of ["succeeded", "failed", "cancelled"]) {
  test(`new submission replaces an unusable ${state} context without changing files`, async t => {
    // Arrange
    const fixture = await recoveryFixture(t, { ready: false, operation: { state }, expires_at: state === "succeeded" ? "2000-01-01T00:00:00Z" : "2100-01-01T00:00:00Z" });
    // Act
    await fixture.run();
    // Assert
    const admissions = fixture.calls.filter(call => call.route === "/api/build-contexts");
    assert.equal(admissions.length, 2);
    assert.notEqual(admissions[0].key, admissions[1].key);
    assert.deepEqual(admissions[0].body, admissions[1].body);
    assert.ok(fixture.calls.some(call => call.route === `/api/build-contexts/${fixture.newId}/archive`));
    assert.equal(JSON.parse(fixture.calls.find(call => call.route === "/api/builds").body).context_id, fixture.newId);
    assert.deepEqual(fixture.errors, []);
  });
}

test("replacement admission with a lost response reuses its key after editor restart", async t => {
  // Arrange
  const fixture = await recoveryFixture(t, { ready: false, operation: { state: "succeeded" } }, true);
  // Act
  await fixture.run();
  fixture.restart();
  await fixture.run();
  // Assert
  const admissions = fixture.calls.filter(call => call.route === "/api/build-contexts");
  assert.equal(admissions.length, 3);
  assert.notEqual(admissions[0].key, admissions[1].key);
  assert.equal(admissions[1].key, admissions[2].key);
  assert.equal(fixture.calls.filter(call => call.route === "/api/builds").length, 1);
  assert.equal(fixture.errors.length, 1);
});

test("pending context is resumed without replacing its idempotency key", async t => {
  // Arrange
  const fixture = await recoveryFixture(t, { ready: false, operation: { state: "pending" } });
  // Act
  await fixture.run();
  // Assert
  assert.equal(fixture.calls.filter(call => call.route === "/api/build-contexts").length, 1);
  assert.ok(fixture.calls.some(call => call.route === `/api/build-contexts/${fixture.oldId}/archive`));
  assert.deepEqual(fixture.errors, []);
});

test("Dockerfile menus match the workspace filename regardless of case", () => {
  const menus = require("../package.json").contributes.menus;
  for (const location of ["explorer/context", "editor/context"]) {
    const rule = menus[location].find(item => item.command === "tritonControl.buildServingImage").when;
    const [, expression, flags] = rule.match(/resourceFilename =~ \/(.+?)\/(i?)/);
    const matches = new RegExp(expression, flags);
    for (const name of ["dockerfile", "Dockerfile", "Dockerfile.gpu"]) assert.ok(matches.test(name));
    assert.ok(!matches.test("requirements.txt"));
  }
});
