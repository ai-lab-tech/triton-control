const http = require("node:http");
const https = require("node:https");

function apiUrl(endpoint) {
  let url;
  try { url = new URL(endpoint); } catch { throw new Error("Enter a valid Triton Control HTTP(S) URL."); }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new Error("Use an HTTP(S) URL without credentials, query parameters, or a fragment.");
  return url;
}

function requestJson(endpoint, path, { token, body } = {}) {
  const url = apiUrl(endpoint);
  url.pathname = url.pathname.replace(/\/$/, "") + path;
  const payload = body === undefined ? undefined : JSON.stringify(body);
  return new Promise((resolve, reject) => {
    const req = (url.protocol === "https:" ? https : http).request(url, {
      method: payload ? "POST" : "GET",
      rejectUnauthorized: true,
      headers: {
        accept: "application/json",
        ...(token ? { authorization: `Bearer ${token}` } : {}),
        ...(payload ? { "content-type": "application/json", "content-length": Buffer.byteLength(payload) } : {}),
      },
    }, (res) => {
      const chunks = [];
      let size = 0;
      res.on("error", () => reject(new Error("Triton Control response interrupted.")));
      res.on("data", (chunk) => {
        size += chunk.length;
        if (size > 4 * 1024 * 1024) req.destroy(new Error("Triton Control response exceeds 4 MiB."));
        else chunks.push(chunk);
      });
      res.on("end", () => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          const hint = res.statusCode === 401 ? "Workspace access is no longer valid. Reopen the workspace from Triton Control."
            : res.statusCode === 403 ? "Your account needs active member or admin access."
            : "Could not load saved S3 profiles. Try refreshing S3 Browser.";
          const error = new Error(`Triton Control HTTP ${res.statusCode}. ${hint}`);
          error.status = res.statusCode;
          reject(error);
          return;
        }
        try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
        catch { reject(new Error("Triton Control returned an invalid response. Check the API URL.")); }
      });
    });
    const timer = setTimeout(() => req.destroy(new Error("Triton Control request timed out.")), 15000);
    req.on("close", () => clearTimeout(timer));
    req.on("error", (error) => reject(new Error(
      `Cannot connect to Triton Control (${error.code || "request failed"}). Check the URL, network, and HTTPS certificate.`,
    )));
    req.end(payload);
  });
}
module.exports = { apiUrl, requestJson };
