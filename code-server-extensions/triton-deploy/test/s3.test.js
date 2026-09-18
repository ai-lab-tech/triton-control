const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const crypto = require("node:crypto");
const {
  signedRequest,
  parseList,
  list,
  head,
  upload,
} = require("../s3-client");
const { planUpload } = require("../s3-files");

const profile = {
  endpoint: "https://s3.example.com",
  bucket: "models",
  region: "us-east-1",
  access_key: "example-access",
  secret_key: "example-secret",
  force_path_style: true,
};

test("signing encodes object keys and query tokens without changing their meaning; TLS is mandatory", () => {
  const options = signedRequest(
    { ...profile, ca_certificate: "public-ca" },
    "GET",
    "a folder/a+b!'().txt",
    { prefix: "a folder/", "continuation-token": "a+b/=" },
    {},
    undefined,
    new Date("2026-01-01T00:00:00Z"),
  );
  assert.equal(
    options.path,
    "/models/a%20folder/a%2Bb%21%27%28%29.txt?continuation-token=a%2Bb%2F%3D&prefix=a%20folder%2F",
  );
  assert.equal(options.rejectUnauthorized, true);
  assert.ok(options.ca.includes("public-ca"));
  assert.match(
    options.headers.authorization,
    /Credential=example-access\/20260101\/us-east-1\/s3\/aws4_request/,
  );
  assert.equal(
    signedRequest({ ...profile, force_path_style: false }, "HEAD", "x")
      .hostname,
    "models.s3.example.com",
  );
  assert.throws(() =>
    signedRequest(
      { ...profile, endpoint: "https://user:pass@example.com" },
      "GET",
      "",
    ),
  );
});

test("switching profiles uses only the selected profile CA, including certificate updates", () => {
  const first = signedRequest({ ...profile, ca_certificate: "first-ca" }, "GET", "");
  const second = signedRequest({ ...profile, ca_certificate: "second-ca" }, "GET", "");
  const updated = signedRequest({ ...profile, ca_certificate: "rotated-ca" }, "GET", "");
  const publicEndpoint = signedRequest(profile, "GET", "");
  assert.ok(first.ca.includes("first-ca"));
  assert.ok(second.ca.includes("second-ca"));
  assert.ok(!second.ca.includes("first-ca"));
  assert.ok(updated.ca.includes("rotated-ca"));
  assert.ok(!updated.ca.includes("first-ca"));
  assert.equal(publicEndpoint.ca, undefined);
  for (const request of [first, second, updated, publicEndpoint]) {
    assert.equal(request.rejectUnauthorized, true);
  }
});

test("S3 listing decodes keys and pagination independently", () => {
  assert.deepEqual(
    parseList(`<ListBucketResult><EncodingType>url</EncodingType>
    <CommonPrefixes><Prefix>a+folder%2F</Prefix></CommonPrefixes>
    <Contents><Key>a%20folder%2Fa%26b.txt</Key><Size>42</Size></Contents>
    <NextContinuationToken>a+b/=&amp;x</NextContinuationToken></ListBucketResult>`),
    {
      folders: ["a folder/"],
      files: [{ key: "a folder/a&b.txt", size: 42 }],
      next: "a+b/=&x",
    },
  );
});

test("recursive planning retains the outer folder, deduplicates nested selections, and rejects symlinks", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "triton-s3-test-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, "training/sub"), { recursive: true });
  await fs.writeFile(path.join(root, "training/train.py"), "train");
  await fs.writeFile(path.join(root, "training/sub/data.csv"), "data");
  const files = await planUpload(
    [path.join(root, "training"), path.join(root, "training/train.py")],
    [root],
    "workflows/",
  );
  assert.deepEqual(files.map((f) => f.key).sort(), [
    "workflows/training/sub/data.csv",
    "workflows/training/train.py",
  ]);
  await fs.symlink(
    path.join(root, "training/train.py"),
    path.join(root, "training/link"),
  );
  await assert.rejects(
    planUpload([path.join(root, "training")], [root], ""),
    /Symbolic links/,
  );
  await assert.rejects(
    planUpload([root], [path.join(root, "training")], ""),
    /inside the open workspace/,
  );
  const abort = new AbortController();
  abort.abort();
  await assert.rejects(planUpload([root], [root], "", abort.signal), {
    name: "AbortError",
  });
});

test("uploads stream signed bytes, preserve existing objects, and abort active HTTP requests", async (t) => {
  const objects = new Map();
  const server = http.createServer(async (req, res) => {
    if (req.method === "GET") {
      res.end(
        "<ListBucketResult><Contents><Key>uploaded.txt</Key><Size>5</Size></Contents></ListBucketResult>",
      );
      return;
    }
    if (req.method === "HEAD") {
      if (req.url.includes("forbidden")) {
        res.writeHead(403);
        res.end();
        return;
      }
      res.writeHead(
        objects.has(req.url) ? 200 : 404,
        objects.has(req.url) ? { etag: '"version-1"' } : {},
      );
      res.end();
      return;
    }
    if (req.url.endsWith("hang")) return;
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    assert.equal(
      req.headers["x-amz-content-sha256"],
      crypto.createHash("sha256").update(body).digest("hex"),
    );
    assert.match(
      req.headers.authorization,
      /SignedHeaders=.*if-(?:none-)?match/,
    );
    if (objects.has(req.url) && req.headers["if-none-match"] === "*") {
      res.writeHead(412);
      res.end();
      return;
    }
    objects.set(req.url, body.toString());
    res.writeHead(200);
    res.end();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  const p = {
    ...profile,
    endpoint: `http://127.0.0.1:${server.address().port}`,
  };
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "triton-s3-http-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const file = path.join(root, "upload.txt");
  await fs.writeFile(file, "hello");
  assert.equal(await head(p, "uploaded.txt"), null);
  let bytes = 0;
  await upload(p, "uploaded.txt", file, {
    onBytes: (count) => {
      bytes += count;
    },
  });
  assert.equal(bytes, 5);
  assert.equal(objects.get("/models/uploaded.txt"), "hello");
  assert.equal(await head(p, "uploaded.txt"), '"version-1"');
  await assert.rejects(
    upload(p, "uploaded.txt", file),
    /changed during upload/,
  );
  await upload(p, "uploaded.txt", file, { etag: '"version-1"' });
  assert.equal((await list(p, "", null)).files[0].key, "uploaded.txt");
  await assert.rejects(head(p, "forbidden"), /HTTP 403/);
  const abort = new AbortController();
  const pending = upload(p, "hang", file, { signal: abort.signal });
  const timer = setTimeout(() => abort.abort(), 50);
  await assert.rejects(pending, { name: "AbortError" });
  clearTimeout(timer);
});

test("recursive listing omits delimiter and deletion signs the exact encoded object key", async (t) => {
  const { remove } = require("../s3-client");
  const seen = [];
  const server = http.createServer((req, res) => {
    seen.push({ url: req.url, method: req.method, authorization: req.headers.authorization });
    if (req.method === "DELETE") {
      res.writeHead(req.url.endsWith("forbidden") ? 403 : 204);
      res.end();
    } else {
      res.end("<ListBucketResult><EncodingType>url</EncodingType><Contents><Key>folder%2Fa%2Bb.txt</Key><Size>1</Size></Contents></ListBucketResult>");
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  const p = { ...profile, endpoint: `http://127.0.0.1:${server.address().port}` };
  assert.equal((await list(p, "folder/", "next+page", undefined, true)).files[0].key, "folder/a+b.txt");
  const query = new URL(seen[0].url, p.endpoint).searchParams;
  assert.equal(query.has("delimiter"), false);
  assert.equal(query.get("prefix"), "folder/");
  assert.equal(query.get("continuation-token"), "next+page");
  await remove(p, "folder/a+b.txt");
  assert.equal(seen[1].method, "DELETE");
  assert.equal(seen[1].url, "/models/folder/a%2Bb.txt");
  assert.match(seen[1].authorization, /^AWS4-HMAC-SHA256/);
  await assert.rejects(remove(p, ""), /Select an S3 object/);
  await assert.rejects(remove(p, "forbidden"), /HTTP 403/);
});
