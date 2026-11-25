const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { createRequire } = require("node:module");

function load(profiles = [], env = {}, requestJson = async () => profiles) {
  const filename = path.join(__dirname, "../extension.js");
  const localRequire = createRequire(filename);
  const sandbox = {
    module: { exports: {} },
    process: { env: { TRITON_CONTROL_PROFILE_URL: "https://control/profiles", TRITON_CONTROL_PROFILE_TOKEN: "token", ...env } },
    require(name) {
      if (name === "vscode") return { TreeItem: class {}, workspace: { isTrusted: true } };
      if (name === "./profile-api") return { requestJson };
      if (name === "./hf-builder") return {};
      return localRequire(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(filename, "utf8") +
    "\nmodule.exports = { deploymentPayload, resolveDeploymentProfile, submitWorkspaceDeployment };", sandbox);
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

test("managed deployment uses the workspace credential and exposes only the result to the webview", async () => {
  // Arrange
  const calls = [];
  const env = { TRITON_CONTROL_URL: "https://control", TRITON_CONTROL_TOKEN: "workspace-test" };
  const { submitWorkspaceDeployment } = load([], env, async (...args) => {
    calls.push(args);
    return { instance_id: 17 };
  });
  const payload = { deployment_name: "converted", candidate_inventory_digest: "sha256:" + "a".repeat(64) };

  // Act
  const result = await submitWorkspaceDeployment(payload);

  // Assert
  assert.equal(calls.length, 1);
  const [endpoint, route, options] = calls[0];
  assert.equal(endpoint, env.TRITON_CONTROL_URL);
  assert.equal(route, "/api/deployments");
  assert.equal(options.body, payload);
  assert.equal(options.token, env.TRITON_CONTROL_TOKEN);
  assert.deepEqual({ ...result }, { type: "deploymentResult", instanceId: 17 });
});
