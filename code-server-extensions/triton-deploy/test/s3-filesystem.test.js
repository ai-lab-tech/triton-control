const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
const fs = require("node:fs/promises");
const crypto = require("node:crypto");
const s3 = require("../s3-client");
const vscode = {
  workspace: { isTrusted: true },
  FileType: { File: 1, Directory: 2 },
  FileChangeType: { Changed: 1 },
  EventEmitter: class { constructor() { this.event = () => {}; } fire() {} dispose() {} },
  Disposable: class { constructor(fn) { this.dispose = fn; } },
  FileSystemError: Object.fromEntries(["NoPermissions", "FileNotFound", "FileExists", "FileIsADirectory", "Unavailable"].map((code) => [code, (message) => Object.assign(new Error(String(message)), { code })])),
};
const load = Module._load;
Module._load = function(name, ...args) { return name === "vscode" ? vscode : load.call(this, name, ...args); };
const { S3FileSystem } = require("../s3-filesystem");
Module._load = load;
const uri = (pathname) => ({ scheme: "triton-s3", authority: "profile-1", path: pathname, query: "", fragment: "",
  with(change) { return { ...this, ...change }; }, toString() { return `triton-s3://${this.authority}${this.path}`; } });
function fixture(t) {
  const old = { stat: s3.stat, head: s3.head, list: s3.list, download: s3.download, upload: s3.upload, remove: s3.remove };
  const store = new Map([["allowed/a.txt", Buffer.from("original")]]);
  const tag = (b) => crypto.createHash("sha256").update(b).digest("hex");
  s3.stat = async (_, key) => store.has(key) ? { etag: tag(store.get(key)), size: store.get(key).length } : null;
  s3.head = async (p, key) => (await s3.stat(p, key))?.etag;
  s3.list = async (_, prefix, next, signal, recursive) => {
    const folders = new Set(), files = [];
    for (const [key, data] of store) if (key.startsWith(prefix)) {
      const relative = key.slice(prefix.length);
      if (!recursive && relative.includes("/")) folders.add(prefix + relative.split("/")[0] + "/");
      else files.push({ key, size: data.length });
    }
    return { folders: [...folders], files, next: "" };
  };
  s3.download = async (_, key, filename, opts) => {
    if (!store.has(key) || (opts.etag && opts.etag !== tag(store.get(key)))) throw new Error("changed");
    await fs.writeFile(filename, store.get(key), { flag: "wx" });
  };
  s3.upload = async (_, key, filename, opts = {}) => {
    if (store.has(key) && opts.etag !== tag(store.get(key))) throw new Error("destination changed");
    store.set(key, await fs.readFile(filename)); return tag(store.get(key));
  };
  s3.remove = async (_, key, signal, etag) => {
    if (etag && tag(store.get(key)) !== etag) throw new Error("changed"); store.delete(key);
  };
  const profile = { id: 1, endpoint: "https://s3", bucket: "models", prefix: "allowed" };
  const provider = new S3FileSystem(async () => profile);
  t.after(() => { provider.dispose(); Object.assign(s3, old); });
  return { provider, store, profile };
}

test("native filesystem lists scoped roots and streams reads/writes in chunks", async (t) => {
  const { provider, store } = fixture(t);
  assert.deepEqual(await provider.readDirectory(uri("/allowed")), [["a.txt", 1]]);
  const read = await provider.open(uri("/allowed/a.txt"), { create: false });
  const bytes = Buffer.alloc(8);
  assert.equal(await provider.read(read, 0, bytes, 0, 8), 8);
  assert.equal(bytes.toString(), "original");
  await provider.close(read);
  const write = await provider.open(uri("/allowed/b.bin"), { create: true });
  await provider.write(write, 0, Buffer.from("first"), 0, 5);
  await provider.write(write, 5, Buffer.from("second"), 0, 6);
  assert.ok(!store.has("allowed/b.bin"));
  await provider.close(write);
  assert.equal(store.get("allowed/b.bin").toString(), "firstsecond");
});

test("native filesystem creates folders and copies/renames exact destinations", async (t) => {
  const { provider, store } = fixture(t);
  await provider.createDirectory(uri("/allowed/sub"));
  assert.equal((await provider.stat(uri("/allowed/sub"))).type, 2);
  await provider.copy(uri("/allowed/a.txt"), uri("/allowed/sub/copy.txt"), { overwrite: false });
  assert.equal(store.get("allowed/sub/copy.txt").toString(), "original");
  await provider.rename(uri("/allowed/sub/copy.txt"), uri("/allowed/renamed.txt"), { overwrite: false });
  assert.ok(!store.has("allowed/sub/copy.txt"));
  assert.equal(store.get("allowed/renamed.txt").toString(), "original");
  await provider.delete(uri("/allowed/sub"), { recursive: true });
  assert.ok(!store.has("allowed/sub/"));
});

test("native filesystem rejects out-of-profile access and root deletion", async (t) => {
  const { provider } = fixture(t);
  await assert.rejects(provider.stat(uri("/elsewhere/a")), /outside/);
  await assert.rejects(provider.delete(uri("/allowed"), { recursive: true }), /profile root/);
  await assert.rejects(provider.copy(uri("/allowed/a.txt"), uri("/outside/a"), { overwrite: true }), /outside/);
});

test("native writes reject conflicts, preserve failed writes, and abort without upload", async (t) => {
  const { provider, store, profile } = fixture(t);
  await assert.rejects(provider.writeFile(uri("/allowed/a.txt"), Buffer.from("new"), { create: true, overwrite: false }), { code: "FileExists" });
  const write = await provider.open(uri("/allowed/a.txt"), { create: true });
  await provider.write(write, 0, Buffer.from("new"), 0, 3);
  store.set("allowed/a.txt", Buffer.from("concurrent"));
  await assert.rejects(provider.close(write), /destination changed/);
  assert.equal(store.get("allowed/a.txt").toString(), "concurrent");
  const cancelled = await provider.open(uri("/allowed/cancelled"), { create: true });
  await provider.write(cancelled, 0, Buffer.from("partial"), 0, 7);
  await provider.abort(cancelled);
  assert.ok(!store.has("allowed/cancelled"));
  const changed = await provider.open(uri("/allowed/changed"), { create: true });
  profile.endpoint = "https://another";
  await assert.rejects(provider.close(changed), /profile changed/);
  assert.ok(!store.has("allowed/changed"));
});


test("endpoint roots list buckets and route object operations to the selected bucket", async (t) => {
  const { provider, store, profile } = fixture(t);
  profile.prefix = "";
  provider.getProfile = async (id) => typeof id === "number" ? profile : { ...profile, id, bucket: String(id).slice(3) };
  const old = { listBuckets: s3.listBuckets, bucketExists: s3.bucketExists };
  t.after(() => Object.assign(s3, old));
  s3.listBuckets = async () => [{ name: "other-bucket" }];
  s3.bucketExists = async (p) => p.bucket === "other-bucket";
  const endpoint = (p) => uri(p).with({ authority: "endpoint-1" });
  assert.deepEqual(await provider.readDirectory(endpoint("/")), [["other-bucket", 2]]);
  assert.equal((await provider.stat(endpoint("/"))).type, 2);
  assert.equal((await provider.stat(endpoint("/other-bucket"))).type, 2);
  await assert.rejects(provider.stat(endpoint("/missing-bucket")), (e) => e.code === "FileNotFound");
  const resolved = await provider.resolve(endpoint("/other-bucket/model.bin"));
  assert.equal(resolved.profile.bucket, "other-bucket"); assert.equal(resolved.profileId, "1--other-bucket");
  assert.equal(resolved.key, "model.bin");
  await provider.writeFile(endpoint("/other-bucket/new.txt"), Buffer.from("new"), { create: true, overwrite: false });
  assert.equal(store.get("new.txt").toString(), "new");
  for (const p of ["/", "/other-bucket"]) {
    await assert.rejects(provider.delete(endpoint(p), { recursive: true }));
    await assert.rejects(provider.writeFile(endpoint(p), Buffer.from("bad"), { create: true, overwrite: true }));
    await assert.rejects(provider.createDirectory(endpoint(p)));
  }
  profile.prefix = "restricted";
  await assert.rejects(provider.readDirectory(endpoint("/")), /prefix-scoped/);
});


test("endpoint cross-bucket copies and moves preserve the right source and destination", async (t) => {
  const { provider, store, profile } = fixture(t);
  profile.prefix = ""; store.clear(); store.set("first-bucket/file.txt", Buffer.from("payload"));
  provider.getProfile = async (id) => typeof id === "number" ? profile : { ...profile, id, bucket: String(id).slice(3) };
  const key = (p, name) => `${p.bucket}/${name}`;
  s3.stat = async (p, name) => store.has(key(p, name)) ? { etag: "etag", size: store.get(key(p, name)).length } : null;
  s3.head = async (p, name) => (await s3.stat(p, name))?.etag;
  s3.download = async (p, name, filename) => fs.writeFile(filename, store.get(key(p, name)), { flag: "wx" });
  s3.upload = async (p, name, filename) => { store.set(key(p, name), await fs.readFile(filename)); return "etag"; };
  s3.remove = async (p, name) => store.delete(key(p, name));
  const endpoint = (p) => uri(p).with({ authority: "endpoint-1" });
  await provider.copy(endpoint("/first-bucket/file.txt"), endpoint("/second-bucket/copied.txt"), { overwrite: false });
  assert.equal(store.get("second-bucket/copied.txt").toString(), "payload");
  assert.ok(store.has("first-bucket/file.txt"));
  await provider.rename(endpoint("/first-bucket/file.txt"), endpoint("/second-bucket/moved.txt"), { overwrite: false });
  assert.equal(store.get("second-bucket/moved.txt").toString(), "payload");
  assert.ok(!store.has("first-bucket/file.txt"));
  await provider.delete(endpoint("/second-bucket/copied.txt"), { recursive: false });
  assert.ok(!store.has("second-bucket/copied.txt"));
  assert.ok(store.has("second-bucket/moved.txt"));
});
