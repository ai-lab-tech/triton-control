const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { createRequire } = require("node:module");

function load(profiles = [], upload) {
  const filename = path.join(__dirname, "../extension.js");
  const localRequire = createRequire(filename);
  const sandbox = {
    module: { exports: {} },
    process: { env: { TRITON_CONTROL_PROFILE_URL: "https://control/profiles", TRITON_CONTROL_PROFILE_TOKEN: "token" } },
    require(name) {
      if (name === "vscode") return { TreeItem: class {}, workspace: { isTrusted: true } };
      if (name === "./s3-client" && upload) return { upload };
      if (name === "./profile-api") return { requestJson: async () => profiles };
      if (name === "./hf-builder") return {};
      return localRequire(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(filename, "utf8") +
    "\nmodule.exports = { deploymentPayload, resolveDeploymentProfile, normalizeForm, uploadRepository };", sandbox);
  return sandbox.module.exports;
}

test("deployment references its profile and exact uploaded repository without credentials", () => {
  // Arrange
  const { deploymentPayload } = load();
  const form = { profileId: "5", prefix: "/team/", deploymentName: "iris", image: "triton",
    endpoint: "https://s3", bucket: "models", accessKeyId: "private-access", secretAccessKey: "private-secret" };
  // Act
  const payload = deploymentPayload(form);
  // Assert
  assert.equal(payload.s3_profile_id, 5);
  assert.equal(payload.repository_prefix, "team/iris");
  assert.equal(payload.s3_url, undefined);
  assert.equal(payload.s3_secret_key, undefined);
  assert.ok(!JSON.stringify(payload).includes("private"));
});

test("upload connection is resolved from the authenticated profile rather than form fields", async () => {
  // Arrange
  const { resolveDeploymentProfile } = load([{ id: 5, endpoint: "https://managed", bucket: "models",
    access_key: "managed-access", secret_key: "managed-secret", region: "eu", force_path_style: true }]);
  // Act
  const resolved = await resolveDeploymentProfile({profileId: "5", endpoint: "https://override", secretAccessKey: "override"});
  // Assert
  assert.equal(resolved.endpoint, "https://managed");
  assert.equal(resolved.secretAccessKey, "managed-secret");
});

test("an unavailable profile cannot fall back to manual credentials", async () => {
  // Arrange
  const { resolveDeploymentProfile } = load();
  // Act
  const result = resolveDeploymentProfile({profileId: "5", secretAccessKey: "manual"});
  // Assert
  await assert.rejects(result, /Select an admin-configured S3 profile/);
});

test("simple upload keeps a scoped profile root in object keys and deployment payload", async (t) => {
  // Arrange
  const keys = [];
  const api = load([{ id: 5, prefix: "team/models", endpoint: "https://s3", bucket: "models" }],
    async (_, key) => keys.push(key));
  const source = fs.mkdtempSync(path.join(require("node:os").tmpdir(), "scoped-upload-"));
  t.after(() => fs.rmSync(source, { recursive: true, force: true }));
  fs.writeFileSync(path.join(source, "config.pbtxt"), 'name: "iris"');
  const profile = await api.resolveDeploymentProfile({ profileId: 5, deploymentName: "serving", modelName: "iris" });
  // Act
  const form = api.normalizeForm(profile);
  await api.uploadRepository(source, form, () => {});
  const payload = api.deploymentPayload(form);
  // Assert
  assert.deepEqual(keys, ["team/models/serving/iris/config.pbtxt"]);
  assert.equal(payload.repository_prefix, "team/models/serving");
});
