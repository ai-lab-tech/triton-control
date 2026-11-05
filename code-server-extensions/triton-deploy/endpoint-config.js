// Shared by the extension host and webview. Credentials are never persisted.
function endpointPayload(form, policy) {
  const mode = form.accessMode || "internal";
  if (mode === "internal") return { access_mode: "internal" };
  if (mode !== "ingress") throw new Error("Select a valid access mode.");
  if (policy && policy.ingress_enabled !== true) throw new Error("Model Ingress is disabled by the administrator.");
  let host = String(form.ingressHost || "").trim();
  let scheme = form.ingressScheme || "https";
  host = host.toLowerCase().replace(/\.$/, "");
  if (!host || host.length > 253 || host.split(".").some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))) {
    throw new Error("Enter a hostname only, without a protocol, port or path.");
  }
  if (policy?.base_domain && host.includes(".")) throw new Error("Enter only the short hostname; the configured domain is appended automatically.");
  if (!["http", "https"].includes(scheme)) throw new Error("Select HTTP or HTTPS.");
  if (scheme === "http" && policy && policy.allow_http !== true) {
    throw new Error("HTTP endpoints are disabled by the administrator. Use HTTPS.");
  }
  const auth = form.basicAuthEnabled === true;
  if (auth && scheme !== "https") throw new Error("Basic authentication requires HTTPS.");
  const payload = { access_mode: "ingress", ingress_host: host, ingress_scheme: scheme, basic_auth_enabled: auth };
  if (auth) {
    if (!form.basicAuthUsername || !form.basicAuthPassword) throw new Error("Enter both username and password.");
    payload.basic_auth_username = form.basicAuthUsername;
    payload.basic_auth_password = form.basicAuthPassword;
  }
  return payload;
}
module.exports = { endpointPayload };
