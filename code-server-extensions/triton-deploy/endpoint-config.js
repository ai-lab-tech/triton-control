// Shared by the extension host and webview. Credentials are never persisted.
function endpointPayload(form, policy) {
  const mode = form.accessMode || "internal";
  if (mode === "internal") return { access_mode: "internal" };
  if (mode !== "ingress") throw new Error("Select a valid access mode.");
  if (policy && policy.ingress_enabled !== true) throw new Error("Model Ingress is disabled by the administrator.");
  let host = String(form.ingressHost || "").trim();
  let scheme = form.ingressScheme || "https";
  if (host.includes("://")) {
    let url;
    try { url = new URL(host); } catch { throw new Error("Enter a valid hostname or HTTP(S) URL."); }
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
        (url.pathname && url.pathname !== "/") || url.search || url.hash || url.port) {
      throw new Error("Enter a hostname without credentials, path, or port.");
    }
    host = url.hostname;
    scheme = url.protocol.slice(0, -1);
  }
  host = host.toLowerCase().replace(/\.$/, "");
  if (!host || host.length > 253 || host.split(".").some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))) {
    throw new Error("Enter a valid DNS hostname.");
  }
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
