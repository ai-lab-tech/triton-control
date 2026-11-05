const test = require("node:test");
const assert = require("node:assert/strict");
const { endpointPayload } = require("../endpoint-config");

test("cluster-only strips stale ingress and credentials", () => {
  // Arrange
  const form = { accessMode: "internal", ingressHost: "model.test", basicAuthPassword: "secret" };
  // Act
  const result = endpointPayload(form, { ingress_enabled: false });
  // Assert
  assert.deepEqual(result, { access_mode: "internal" });
});

test("HTTPS authentication uses the shared API fields without controller selection", () => {
  // Arrange
  const form = { accessMode: "ingress", ingressHost: "MODEL.test", basicAuthEnabled: true,
    basicAuthUsername: "user", basicAuthPassword: "secret", ingressClassName: "nginx" };
  // Act
  const result = endpointPayload(form, { ingress_enabled: true, allow_http: false });
  // Assert
  assert.deepEqual(result, { access_mode: "ingress", ingress_host: "model.test", ingress_scheme: "https",
    basic_auth_enabled: true, basic_auth_username: "user", basic_auth_password: "secret" });
});

test("disabled ingress is rejected", () => {
  // Arrange
  const form = { accessMode: "ingress", ingressHost: "model.test" };
  // Act / Assert
  assert.throws(() => endpointPayload(form, { ingress_enabled: false }), /disabled/);
});

test("HTTP URL cannot bypass HTTPS policy", () => {
  // Arrange
  const form = { accessMode: "ingress", ingressHost: "http://model.test" };
  // Act / Assert
  assert.throws(() => endpointPayload(form, { ingress_enabled: true, allow_http: false }), /hostname only/);
});

test("permitted HTTP sends no authentication secrets", () => {
  // Arrange
  const form = { accessMode: "ingress", ingressHost: "model.test", ingressScheme: "http", basicAuthPassword: "stale" };
  // Act
  const result = endpointPayload(form, { ingress_enabled: true, allow_http: true });
  // Assert
  assert.equal(result.ingress_scheme, "http");
  assert.equal(result.basic_auth_password, undefined);
});

test("basic authentication requires HTTPS", () => {
  // Arrange
  const form = { accessMode: "ingress", ingressHost: "model.test", ingressScheme: "http", basicAuthEnabled: true,
    basicAuthUsername: "user", basicAuthPassword: "secret" };
  // Act / Assert
  assert.throws(() => endpointPayload(form, { ingress_enabled: true, allow_http: true }), /requires HTTPS/);
});

test("partial basic authentication is rejected", () => {
  // Arrange
  const form = { accessMode: "ingress", ingressHost: "model.test", basicAuthEnabled: true, basicAuthUsername: "user" };
  // Act / Assert
  assert.throws(() => endpointPayload(form, { ingress_enabled: true }), /both username and password/);
});

test("URL credentials are rejected without echoing them", () => {
  // Arrange
  const form = { accessMode: "ingress", ingressHost: "https://user:secret@model.test" };
  // Act / Assert
  assert.throws(() => endpointPayload(form, { ingress_enabled: true }), error => !error.message.includes("secret"));
});

test("deployment webview renders the policy controls and valid JavaScript", () => {
  // Arrange
  const fs = require("node:fs");
  const path = require("node:path");
  const vm = require("node:vm");
  const { createRequire } = require("node:module");
  const filename = path.resolve(__dirname, "../extension.js");
  const localRequire = createRequire(filename);
  const sandbox = { require: name => name === "vscode" ? { TreeItem: class {} } : localRequire(name),
    module: { exports: {} }, __dirname: path.dirname(filename) };
  const source = fs.readFileSync(filename, "utf8") + "\nmodule.exports.renderHtml = renderHtml;";
  // Act
  vm.runInNewContext(source, sandbox, { filename });
  const html = sandbox.module.exports.renderHtml({ cspSource: "test" }, "nonce", {});
  const script = html.match(/<script nonce="nonce">([\s\S]*?)<\/script>/)[1];
  // Assert
  assert.doesNotThrow(() => new vm.Script(script));
  assert.match(html, /api\/deployments\/policy/);
  assert.match(html, /name="basicAuthPassword"/);
  assert.doesNotMatch(html, /name="ingressClassName"/);
});
