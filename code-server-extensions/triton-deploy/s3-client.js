const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const tls = require("node:tls");
const { Transform } = require("node:stream");
const { pipeline } = require("node:stream/promises");

const encode = (value) =>
  encodeURIComponent(value).replace(
    /[!\x27()*]/g,
    (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`,
  );
const hash = (value) => crypto.createHash("sha256").update(value).digest("hex");
const hmac = (key, value) =>
  crypto.createHmac("sha256", key).update(value).digest();

function signedRequest(
  profile,
  method,
  key,
  query = {},
  headers = {},
  payloadHash = hash(""),
  now = new Date(),
) {
  const endpoint = new URL(profile.endpoint);
  if (
    !["https:", "http:"].includes(endpoint.protocol) ||
    endpoint.username ||
    endpoint.password ||
    !["", "/"].includes(endpoint.pathname) ||
    endpoint.search ||
    endpoint.hash
  ) {
    throw new Error(
      "The S3 profile endpoint must be an HTTP(S) host without a path or credentials.",
    );
  }
  if (!profile.bucket || !profile.access_key || !profile.secret_key)
    throw new Error("The S3 profile is incomplete.");
  const virtual = profile.force_path_style === false;
  if (virtual) endpoint.hostname = `${profile.bucket}.${endpoint.hostname}`;
  const pathname = `${virtual ? "" : `/${encode(profile.bucket)}`}/${key.split("/").map(encode).join("/")}`;
  const search = Object.entries(query)
    .map(([k, v]) => [encode(k), encode(v)])
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${k}=${v}`)
    .join("&");
  const date = now.toISOString().replace(/[:-]|\.\d{3}/g, "");
  headers = {
    ...headers,
    host: endpoint.host,
    "x-amz-date": date,
    "x-amz-content-sha256": payloadHash,
  };
  const names = Object.keys(headers).sort();
  const canonical = [
    method,
    pathname,
    search,
    names.map((n) => `${n}:${String(headers[n]).trim()}\n`).join(""),
    names.join(";"),
    payloadHash,
  ].join("\n");
  const scope = `${date.slice(0, 8)}/${profile.region || "us-east-1"}/s3/aws4_request`;
  const signingKey = hmac(
    hmac(
      hmac(
        hmac(`AWS4${profile.secret_key}`, date.slice(0, 8)),
        profile.region || "us-east-1",
      ),
      "s3",
    ),
    "aws4_request",
  );
  const signature = hmac(
    signingKey,
    `AWS4-HMAC-SHA256\n${date}\n${scope}\n${hash(canonical)}`,
  ).toString("hex");
  headers.authorization = `AWS4-HMAC-SHA256 Credential=${profile.access_key}/${scope}, SignedHeaders=${names.join(";")}, Signature=${signature}`;
  return {
    protocol: endpoint.protocol,
    hostname: endpoint.hostname.replace(/^\[|\]$/g, ""),
    port: endpoint.port || undefined,
    method,
    path: pathname + (search ? `?${search}` : ""),
    headers,
    rejectUnauthorized: true,
    agent: false,
    ...(profile.ca_certificate
      ? { ca: [...tls.rootCertificates, profile.ca_certificate] }
      : {}),
  };
}

function request(
  profile,
  method,
  key,
  { query, headers, payloadHash, stream, signal, onBytes } = {},
) {
  return new Promise((resolve, reject) => {
    const options = signedRequest(
      profile,
      method,
      key,
      query,
      headers,
      payloadHash,
    );
    const req = (options.protocol === "https:" ? https : http).request(
      { ...options, signal },
      (res) => {
        const chunks = [];
        let size = 0;
        res.on("data", (chunk) => {
          size += chunk.length;
          if (size > 4 * 1024 * 1024)
            req.destroy(new Error("S3 response exceeds 4 MiB."));
          else chunks.push(chunk);
        });
        res.on("error", reject);
        res.on("end", () =>
          resolve({
            status: res.statusCode,
            headers: res.headers,
            body: Buffer.concat(chunks).toString("utf8"),
          }),
        );
      },
    );
    req.setTimeout(60000, () =>
      req.destroy(new Error("S3 request timed out.")),
    );
    req.on("error", reject);
    req.on("close", () => stream?.destroy());
    if (stream) {
      stream.on("error", (error) => req.destroy(error));
      stream.on("data", (chunk) => onBytes?.(chunk.length));
      stream.pipe(req);
    } else req.end();
  });
}

function check(response, allowed = []) {
  if (
    (response.status >= 200 && response.status < 300) ||
    allowed.includes(response.status)
  )
    return response;
  // Do not include response bodies: providers may echo sensitive request values.
  const error = new Error(
    response.status === 412
      ? "The S3 object changed during upload. Refresh and try again."
      : `S3 request failed (HTTP ${response.status}). Check profile permissions, endpoint, and bucket.`,
  );
  error.status = response.status;
  throw error;
}
const xmlDecode = (value) =>
  value.replace(/&(#x[\da-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (_, code) => {
    if (code.startsWith("#"))
      return String.fromCodePoint(
        code[1].toLowerCase() === "x"
          ? parseInt(code.slice(2), 16)
          : Number(code.slice(1)),
      );
    return { amp: "&", lt: "<", gt: ">", quot: '"', apos: "\x27" }[code];
  });
const tag = (xml, name) =>
  xmlDecode(
    xml.match(new RegExp(`<${name}>([\\s\\S]*?)</${name}>`))?.[1] || "",
  );
function parseList(xml) {
  const decodeKey = (value) =>
    tag(xml, "EncodingType") === "url"
      ? decodeURIComponent(value.replace(/\+/g, " "))
      : value;
  return {
    folders: [
      ...xml.matchAll(/<CommonPrefixes>([\s\S]*?)<\/CommonPrefixes>/g),
    ].map((m) => decodeKey(tag(m[1], "Prefix"))),
    files: [...xml.matchAll(/<Contents>([\s\S]*?)<\/Contents>/g)].map((m) => ({
      key: decodeKey(tag(m[1], "Key")),
      size: Number(tag(m[1], "Size")),
    })),
    next: tag(xml, "NextContinuationToken"),
  };
}
async function list(profile, prefix, continuation, signal, recursive = false) {
  const query = {
    "list-type": "2",
    ...(recursive ? {} : { delimiter: "/" }),
    prefix,
    "encoding-type": "url",
    "max-keys": "500",
  };
  if (continuation) query["continuation-token"] = continuation;
  return parseList(
    check(await request(profile, "GET", "", { query, signal })).body,
  );
}
async function stat(profile, key, signal) {
  const response = check(
    await request(profile, "HEAD", key, { signal }),
    [404],
  );
  return response.status === 404 ? null : {
    etag: response.headers.etag,
    size: Number(response.headers["content-length"] || 0),
    metadata: Object.fromEntries(Object.entries(response.headers).filter(([name]) =>
      name.startsWith("x-amz-meta-") || ["content-type", "content-encoding", "content-language", "content-disposition", "cache-control", "expires"].includes(name))),
  };
}
async function head(profile, key, signal) {
  return (await stat(profile, key, signal))?.etag || null;
}
async function download(profile, key, filename, { signal, etag, onBytes } = {}) {
  signal?.throwIfAborted();
  const options = signedRequest(profile, "GET", key, {}, etag ? { "if-match": etag } : {});
  const response = await new Promise((resolve, reject) => {
    const req = (options.protocol === "https:" ? https : http).request({ ...options, signal }, resolve);
    req.setTimeout(60000, () => req.destroy(new Error("S3 download timed out.")));
    req.on("error", reject);
    req.end();
  });
  let file;
  let complete = false;
  try {
    check({ status: response.statusCode });
    file = await fs.promises.open(filename, "wx", 0o600);
    let bytes = 0;
    const limit = new Transform({ transform(chunk, encoding, callback) {
      bytes += chunk.length;
      if (bytes > 5 * 1024 ** 3) return callback(new Error("Files over 5 GiB are not supported."));
      onBytes?.(chunk.length);
      callback(null, chunk);
    } });
    // The explicit descriptor lets us remove only the file we created on failure.
    await pipeline(response, limit, file.createWriteStream(), { signal });
    complete = true;
  } finally {
    response.destroy();
    if (file) {
      await file.close();
      if (!complete) await fs.promises.unlink(filename).catch(() => {});
    }
  }
}
async function upload(profile, key, file, { signal, onBytes, etag, metadata = {} } = {}) {
  signal?.throwIfAborted();
  const stat = await fs.promises.stat(file);
  if (stat.size > 5 * 1024 ** 3)
    throw new Error(
      "Files over 5 GiB require multipart upload, which is not supported yet.",
    );
  const digest = crypto.createHash("sha256");
  for await (const chunk of fs.createReadStream(file, { signal }))
    digest.update(chunk);
  signal?.throwIfAborted();
  const headers = {
    ...metadata,
    "content-length": String(stat.size),
    ...(etag ? { "if-match": etag } : { "if-none-match": "*" }),
  };
  const response = check(
    await request(profile, "PUT", key, {
      headers,
      payloadHash: digest.digest("hex"),
      stream: fs.createReadStream(file, { signal }),
      signal,
      onBytes,
    }),
  );
  return response.headers.etag;
}
async function remove(profile, key, signal, etag) {
  if (!key) throw new Error("Select an S3 object to delete.");
  check(await request(profile, "DELETE", key, { signal, headers: etag ? { "if-match": etag } : {} }));
}
module.exports = { signedRequest, parseList, list, stat, head, download, upload, remove };
