const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const http = require("node:http");
const crypto = require("node:crypto");
const { transfer, stage, saveStage, localRelative } = require("../s3-transfers");
const s3 = require("../s3-client");
const etag = (body) => `"${crypto.createHash("sha256").update(body).digest("hex")}"`;
const xml = (value) => value.replace(/&/g, "&amp;").replace(/</g, "&lt;");
async function fixture(t) {
  const objects = new Map([["/models/src/a.txt", Buffer.from("alpha")], ["/models/src/sub/b.txt", Buffer.from("beta")]]);
  const requests = [];
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://test");
    const key = decodeURIComponent(url.pathname);
    requests.push({ method: req.method, key, headers: req.headers });
    const body = objects.get(key);
    if (req.method === "GET" && url.searchParams.has("list-type")) {
      const prefix = "/models/" + url.searchParams.get("prefix");
      res.end(`<ListBucketResult><EncodingType>url</EncodingType>${[...objects].filter(([key]) => key.startsWith(prefix)).map(([key, data]) => `<Contents><Key>${xml(encodeURIComponent(key.slice(8)))}</Key><Size>${data.length}</Size></Contents>`).join("")}</ListBucketResult>`);
      return;
    }
    if (req.headers["if-match"] && (!body || req.headers["if-match"] !== etag(body))) { res.writeHead(412); res.end(); return; }
    if (req.headers["if-none-match"] === "*" && body) { res.writeHead(412); res.end(); return; }
    if (req.method === "HEAD" || req.method === "GET") {
      res.writeHead(body ? 200 : 404, body ? { etag: etag(body), "content-length": body.length } : {});
      res.end(req.method === "HEAD" ? undefined : body);
    } else if (req.method === "PUT") {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const data = Buffer.concat(chunks);
      objects.set(key, data);
      res.writeHead(200, { etag: etag(data) }); res.end();
    } else if (req.method === "DELETE") {
      objects.delete(key); res.writeHead(204); res.end();
    } else { res.writeHead(405); res.end(); }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "triton-transfer-test-"));
  t.after(async () => { server.closeAllConnections(); server.close(); await fs.rm(directory, { recursive: true, force: true }); });
  const profile = { id: 1, endpoint: `http://127.0.0.1:${server.address().port}`, bucket: "models", prefix: "", access_key: "access", secret_key: "secret" };
  const getProfile = async () => profile;
  const source = { kind: "folder", prefix: "src/", bucket: "models", profileId: 1 };
  const target = { kind: "folder", prefix: "dest/", bucket: "models", profileId: 1 };
  return { objects, requests, directory, profile, getProfile, source, target };
}

test("copy and move preserve nested bytes; moves delete only after every copy succeeds", async (t) => {
  const f = await fixture(t);
  assert.deepEqual(await transfer([f.source], f.target, f.getProfile, "copy"), { copied: 2, deleted: 0, skipped: 0 });
  assert.equal(f.objects.get("/models/dest/src/a.txt").toString(), "alpha");
  assert.ok(f.objects.has("/models/src/a.txt"));
  f.requests.length = 0;
  assert.deepEqual(await transfer([f.source], { ...f.target, prefix: "moved/" }, f.getProfile, "move"), { copied: 2, deleted: 2, skipped: 0 });
  assert.ok(!f.objects.has("/models/src/a.txt"));
  assert.ok(!f.objects.has("/models/src/sub/b.txt"));
  const mutations = f.requests.filter((r) => ["PUT", "DELETE"].includes(r.method));
  assert.deepEqual(mutations.map((r) => r.method), ["PUT", "PUT", "DELETE", "DELETE"]);
  assert.ok(mutations.filter((r) => r.method === "DELETE").every((r) => r.headers["if-match"]));
});

test("move skips leave originals; failed overwrite and cancellation never delete sources", async (t) => {
  const f = await fixture(t);
  f.objects.set("/models/dest/src/a.txt", Buffer.from("existing"));
  const result = await transfer([f.source], f.target, f.getProfile, "move", { overwrite: async () => "skip" });
  assert.deepEqual(result, { copied: 1, deleted: 1, skipped: 1 });
  assert.equal(f.objects.get("/models/src/a.txt").toString(), "alpha");
  assert.equal(f.objects.get("/models/dest/src/a.txt").toString(), "existing");
  await assert.rejects(transfer([f.source], f.target, f.getProfile, "move", { overwrite: async () => "cancel" }), /cancelled/);
  assert.ok(f.objects.has("/models/src/a.txt"));
  const abort = new AbortController(); abort.abort();
  await assert.rejects(transfer([f.source], f.target, f.getProfile, "move", { signal: abort.signal }));
  assert.ok(f.objects.has("/models/src/a.txt"));
});

test("failed copy phase retains every source and destination races do not overwrite", async (t) => {
  const f = await fixture(t);
  const originalUpload = s3.upload;
  let copies = 0;
  t.after(() => { s3.upload = originalUpload; });
  s3.upload = async (...args) => { if (++copies === 2) throw new Error("copy failed"); return originalUpload(...args); };
  await assert.rejects(transfer([f.source], f.target, f.getProfile, "move"), /1 copied, 0 source/);
  assert.ok(f.objects.has("/models/src/a.txt"));
  assert.ok(f.objects.has("/models/src/sub/b.txt"));
  s3.upload = async (...args) => { f.objects.set("/models/other/src/a.txt", Buffer.from("concurrent")); return originalUpload(...args); };
  await assert.rejects(transfer([f.source], { ...f.target, prefix: "other/" }, f.getProfile, "move"), /412|changed/);
  assert.equal(f.objects.get("/models/other/src/a.txt").toString(), "concurrent");
});

test("downloads stream binary files, preserve nesting and respect local overwrite choices", async (t) => {
  const f = await fixture(t);
  f.objects.set("/models/src/binary", crypto.randomBytes(5 * 1024 * 1024));
  const staged = await stage([f.source], f.getProfile);
  t.after(() => fs.rm(staged.directory, { recursive: true, force: true }));
  const result = await saveStage(staged, f.directory, [f.directory]);
  assert.equal(result.saved, 3);
  assert.deepEqual(await fs.readFile(path.join(f.directory, "src/binary")), f.objects.get("/models/src/binary"));
  await fs.writeFile(path.join(f.directory, "src/a.txt"), "local");
  assert.equal((await saveStage(staged, f.directory, [f.directory], { overwrite: async () => "skip" })).skipped, 3);
  assert.equal(await fs.readFile(path.join(f.directory, "src/a.txt"), "utf8"), "local");
  await saveStage(staged, f.directory, [f.directory], { overwrite: async () => "replace" });
  assert.equal(await fs.readFile(path.join(f.directory, "src/a.txt"), "utf8"), "alpha");
});

test("downloads reject unsafe keys and symlinks; transfers reject self/descendant and profile roots", async (t) => {
  const f = await fixture(t);
  for (const key of ["../escape", "a/../../escape", "/absolute", "a\\b", "a//b"])
    assert.throws(() => localRelative(key), /safely/);
  await assert.rejects(transfer([f.source], { ...f.target, prefix: "src/nested/" }, f.getProfile, "copy"), /itself/);
  await assert.rejects(transfer([f.source], { ...f.target, prefix: "" }, f.getProfile, "move"), /itself/);
  await assert.rejects(stage([{ ...f.source, root: true }], f.getProfile), /profile root/);
  const staged = await stage([f.source], f.getProfile);
  t.after(() => fs.rm(staged.directory, { recursive: true, force: true }));
  await fs.symlink(os.tmpdir(), path.join(f.directory, "src"));
  await assert.rejects(saveStage(staged, f.directory, [f.directory]), /symbolic/);
  await assert.rejects(saveStage(staged, os.tmpdir(), [f.directory]), /inside the workspace/);
});

test("moves retain a concurrently changed source or destination", async (t) => {
  const f = await fixture(t);
  const source = { kind: "file", key: "src/a.txt", bucket: "models", profileId: 1 };
  const originalUpload = s3.upload;
  t.after(() => { s3.upload = originalUpload; });
  s3.upload = async (...args) => {
    const result = await originalUpload(...args);
    f.objects.set("/models/src/a.txt", Buffer.from("new source"));
    return result;
  };
  await assert.rejects(transfer([source], f.target, f.getProfile, "move"), /changed|412/);
  assert.equal(f.objects.get("/models/src/a.txt").toString(), "new source");
  s3.upload = async (...args) => {
    const result = await originalUpload(...args);
    f.objects.set("/models/next/a.txt", Buffer.from("new destination"));
    return result;
  };
  await assert.rejects(transfer([source], { ...f.target, prefix: "next/" }, f.getProfile, "move"), /Source retained/);
  assert.ok(f.objects.has("/models/src/a.txt"));
});

test("downloads do not overwrite an existing file and abort removes partial files", async (t) => {
  const f = await fixture(t);
  const filename = path.join(f.directory, "existing");
  await fs.writeFile(filename, "keep");
  await assert.rejects(s3.download(f.profile, "src/a.txt", filename), /EEXIST/);
  assert.equal(await fs.readFile(filename, "utf8"), "keep");
  f.objects.set("/models/large", crypto.randomBytes(2 * 1024 * 1024));
  const abort = new AbortController();
  const partial = path.join(f.directory, "partial");
  await assert.rejects(s3.download(f.profile, "large", partial, { signal: abort.signal, onBytes: () => abort.abort() }));
  await assert.rejects(fs.stat(partial), /ENOENT/);
});
