const http = require("node:http");
const https = require("node:https");
const { apiUrl } = require("./profile-api");

function workspaceConnection(env = process.env) {
  const url = apiUrl(env.TRITON_CONTROL_PROFILE_URL || "");
  const match = url.pathname.match(/^(.*)\/api\/development\/workspace-s3-profiles\/([^/]+)\/([^/]+)$/);
  if (!match || !env.TRITON_CONTROL_PROFILE_TOKEN) throw new Error("Open an assigned task workspace from Triton Control first.");
  return { endpoint: url.origin + match[1], workload: `${decodeURIComponent(match[2])}/${decodeURIComponent(match[3])}`, token: env.TRITON_CONTROL_PROFILE_TOKEN };
}
function buildErrorMessage(status, chunks) {
  if (status === 401) return "Workspace access expired. Reopen your task workspace.";
  if (status === 400) {
    let reason;
    try { reason = JSON.parse(Buffer.concat(chunks).toString("utf8")).detail; } catch {}
    const known = new Set([
      "Build staging bucket versioning must be enabled",
      "Select an available HTTPS S3 profile for this build",
      "Dockerfile base image must be a supported image reference with an exact digest",
      "Every external FROM image must have a declared exact digest",
      "Dockerfile or dependency files missing from the selected context",
      "Build archive is incomplete or its checksum differs",
      "Credential arguments must not be embedded in the Dockerfile",
    ]);
    return known.has(reason) ? reason : "The server rejected the build input (HTTP 400).";
  }
  if (status === 403) {
    let detail;
    try { detail = JSON.parse(Buffer.concat(chunks).toString("utf8"))?.detail; }
    catch { /* Proxies may return plain text. Never display an untrusted response body. */ }
    if (detail === "Build context staging has not been configured") {
      return "Build context staging is missing. Ask an administrator to configure the development installation's build policy.";
    }
    return "Build access denied. Check task assignment and installation build policy.";
  }
  return `Build request failed (HTTP ${status}). Refresh before retrying; the request may already exist.`;
}
function request(connection, route, { method = "GET", body, key } = {}) {
  if (!/^\/api\/(builds|build-contexts)([/?]|$)/.test(route)) throw new Error("Invalid build API route.");
  const url = new URL(connection.endpoint + route);
  const binary = Buffer.isBuffer(body);
  const payload = body === undefined ? undefined : binary ? body : Buffer.from(JSON.stringify(body));
  return new Promise((resolve, reject) => {
    const req = (url.protocol === "https:" ? https : http).request(url, { method, rejectUnauthorized: true, headers: {
      accept: "application/json", authorization: `Bearer ${connection.token}`,
      "X-Triton-Build-Workspace": connection.workload,
      ...(key ? { "Idempotency-Key": key } : {}),
      ...(payload ? { "Content-Type": binary ? "application/x-tar" : "application/json", "Content-Length": payload.length } : {}),
    } }, res => {
      const chunks = []; let size = 0;
      res.on("data", chunk => { size += chunk.length; if (size > 4 * 1024 * 1024) req.destroy(new Error("Build response too large.")); else chunks.push(chunk); });
      res.on("error", () => reject(new Error("Build response interrupted.")));
      res.on("end", () => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          const error = new Error(buildErrorMessage(res.statusCode, chunks));
          error.status = res.statusCode; reject(error); return;
        }
        try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
        catch { reject(new Error("Invalid build response.")); }
      });
    });
    const timer = setTimeout(() => req.destroy(new Error("Build request timed out. Refresh before resubmitting.")), binary ? 120000 : 15000);
    req.on("close", () => clearTimeout(timer));
    req.on("error", () => reject(new Error("Build connection failed. Check connectivity and refresh before resubmitting.")));
    req.end(payload);
  });
}
function buildState(build) {
  if (build.operation.cancel_requested && !["succeeded", "failed", "cancelled"].includes(build.operation.state)) return "Cancelling";
  if (["succeeded", "failed", "cancelled"].includes(build.operation.state)) return build.operation.state;
  const latest = build.attempts.at(-1);
  if (!latest || latest.state === "failed") return "Queued";
  if (latest.finished_at) return "Retaining result / cleanup";
  return build.operation.result?.phase === "pushing" ? "Pushing" : build.operation.result?.phase === "building" ? "Building" : "Queued";
}
function exactImage(build, number) {
  const attempt = build.attempts.find(item => item.number === number);
  if (build.operation?.result?.archive) return null;
  if (!attempt || attempt.state !== "succeeded" || !/^sha256:[a-f0-9]{64}$/.test(attempt.final_digest || "")) return null;
  return attempt.destination.replace(/:[^/:]+$/, "") + "@" + attempt.final_digest;
}
module.exports = { workspaceConnection, request, buildState, exactImage };
