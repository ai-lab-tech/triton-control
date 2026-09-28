const vscode = require("vscode");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const s3 = require("./s3-client");
const transfers = require("./s3-transfers");
const SCHEME = "triton-s3";
const rootPrefix = (p) => p.prefix ? p.prefix.replace(/^\/+|\/+$/g, "") + "/" : "";
const identity = (p) => JSON.stringify([p.endpoint, p.bucket, rootPrefix(p), p.force_path_style !== false]);

class S3FileSystem {
  constructor(getProfile) {
    this.getProfile = getProfile;
    this.changed = new vscode.EventEmitter();
    this.onDidChangeFile = this.changed.event;
    this.handles = new Map();
    this.nextHandle = 1;
  }
  async resolve(uri, allowEndpoint = false) {
    if (!vscode.workspace.isTrusted) throw vscode.FileSystemError.NoPermissions("Trust this workspace to access S3.");
    const match = /^(profile|endpoint)-(\d+)$/.exec(uri.authority);
    if (uri.scheme !== SCHEME || !match || uri.query || uri.fragment)
      throw vscode.FileSystemError.NoPermissions("Invalid S3 resource.");
    let profile = await this.getProfile(Number(match[2]));
    let key = uri.path.replace(/^\//, "");
    if (match[1] === "endpoint") {
      if (rootPrefix(profile)) throw vscode.FileSystemError.NoPermissions("Bucket browsing is unavailable for prefix-scoped profiles.");
      if (!key) {
        if (!allowEndpoint) throw vscode.FileSystemError.NoPermissions("Select a bucket or folder below the endpoint root.");
        return { profile, key: "", root: "", endpoint: true };
      }
      const bucket = key.split("/")[0];
      if (s3.validateBucketName(bucket)) throw vscode.FileSystemError.NoPermissions("Invalid S3 bucket name.");
      profile = await this.getProfile(`${match[2]}--${bucket}`);
      key = key.slice(bucket.length).replace(/^\//, "");
    }
    const root = rootPrefix(profile);
    if (key !== root.replace(/\/$/, "") && !key.startsWith(root))
      throw vscode.FileSystemError.NoPermissions("S3 path is outside the saved profile prefix.");
    if (key && key !== root.replace(/\/$/, "")) transfers.localRelative(key);
    return { profile, key, root, profileId: profile.id };
  }
  isRoot(key, root) { return !key || key === root || key === root.replace(/\/$/, ""); }
  async stat(uri) {
    const { profile, key, root, endpoint } = await this.resolve(uri, true);
    if (!endpoint && uri.authority.startsWith("endpoint-") && this.isRoot(key, root) && !await s3.bucketExists(profile))
      throw vscode.FileSystemError.FileNotFound(uri);
    if (this.isRoot(key, root)) return { type: vscode.FileType.Directory, ctime: 0, mtime: 0, size: 0 };
    const info = await s3.stat(profile, key);
    if (info && !key.endsWith("/")) return { type: vscode.FileType.File, ctime: 0, mtime: 0, size: info.size };
    const listing = await s3.list(profile, key.replace(/\/$/, "") + "/");
    if (info || listing.files.length || listing.folders.length)
      return { type: vscode.FileType.Directory, ctime: 0, mtime: 0, size: 0 };
    throw vscode.FileSystemError.FileNotFound(uri);
  }
  async readDirectory(uri) {
    const initial = await this.resolve(uri, true);
    if (initial.endpoint) return (await s3.listBuckets(initial.profile)).map(({ name }) => [name, vscode.FileType.Directory]);
    const prefix = initial.key ? initial.key.replace(/\/$/, "") + "/" : "";
    const entries = new Map(), seen = new Set();
    let next;
    const add = (name, type) => {
      transfers.localRelative(name);
      if (entries.has(name) && entries.get(name) !== type)
        throw vscode.FileSystemError.Unavailable("S3 has a file and folder with the same name; use separate names to browse them.");
      entries.set(name, type);
    };
    do {
      const { profile } = await this.resolve(uri);
      if (identity(profile) !== identity(initial.profile)) throw vscode.FileSystemError.Unavailable("S3 profile changed; refresh Explorer.");
      const page = await s3.list(profile, prefix, next);
      for (const folder of page.folders) {
        if (!folder.startsWith(prefix)) throw vscode.FileSystemError.Unavailable("Invalid S3 listing.");
        const name = folder.slice(prefix.length).replace(/\/$/, "");
        if (name.includes("/")) throw vscode.FileSystemError.Unavailable("Invalid S3 folder name.");
        add(name, vscode.FileType.Directory);
      }
      for (const file of page.files) {
        if (file.key === prefix) continue;
        if (!file.key.startsWith(prefix)) throw vscode.FileSystemError.Unavailable("Invalid S3 listing.");
        const name = file.key.slice(prefix.length);
        if (name.includes("/")) throw vscode.FileSystemError.Unavailable("Invalid S3 object name.");
        add(name, vscode.FileType.File);
      }
      if (entries.size > 10000) throw vscode.FileSystemError.Unavailable("This folder has more than 10,000 entries. Use smaller folders.");
      next = page.next;
      if (next && seen.has(next)) throw vscode.FileSystemError.Unavailable("Repeated S3 listing page.");
      seen.add(next);
    } while (next);
    return [...entries];
  }
  watch() { return new vscode.Disposable(() => {}); }
  notify(uri) {
    this.changed.fire([
      { type: vscode.FileChangeType.Changed, uri },
      { type: vscode.FileChangeType.Changed, uri: uri.with({ path: path.posix.dirname(uri.path) }) },
    ]);
  }
  async node(uri) {
    const { profile, key, root } = await this.resolve(uri);
    const stat = await this.stat(uri);
    return { kind: stat.type === vscode.FileType.Directory ? "folder" : "file", profileId: profile.id,
      bucket: profile.bucket, root: this.isRoot(key, root),
      ...(stat.type === vscode.FileType.Directory ? { prefix: key ? key.replace(/\/$/, "") + "/" : "" } : { key }) };
  }
  async createDirectory(uri) {
    const { profile, key, root } = await this.resolve(uri);
    if (this.isRoot(key, root)) {
      if (uri.authority.startsWith("endpoint-")) throw vscode.FileSystemError.NoPermissions("Use S3 Operations → Create Bucket… to create a bucket.");
      return;
    }
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), "triton-s3-directory-"));
    try {
      const filename = path.join(directory, "empty");
      await fs.writeFile(filename, "");
      const folderKey = key.replace(/\/$/, "") + "/";
      if (!await s3.head(profile, folderKey)) await s3.upload(profile, folderKey, filename);
      this.notify(uri);
    } finally { await fs.rm(directory, { recursive: true, force: true }); }
  }
  async readFile(uri) {
    const info = await this.stat(uri);
    if (info.type !== vscode.FileType.File) throw vscode.FileSystemError.FileIsADirectory(uri);
    if (info.size > 128 * 1024 ** 2) throw vscode.FileSystemError.Unavailable("Use drag-and-drop or Download for large S3 files.");
    const handle = await this.open(uri, { create: false });
    try { return await fs.readFile(this.handles.get(handle).filename); }
    finally { await this.close(handle); }
  }
  async writeFile(uri, content, options) {
    const { profile, key, root } = await this.resolve(uri);
    if (this.isRoot(key, root)) throw vscode.FileSystemError.FileIsADirectory(uri);
    const existing = await s3.stat(profile, key);
    if (!existing && !options.create) throw vscode.FileSystemError.FileNotFound(uri);
    if (existing && !options.overwrite) throw vscode.FileSystemError.FileExists(uri);
    const handle = await this.open(uri, { create: true });
    try {
      await this.write(handle, 0, content, 0, content.length);
      await this.close(handle);
    } catch (error) {
      await this.abort(handle);
      throw error;
    }
  }
  // fsChunks lets native Explorer transfers stream instead of buffering model files.
  async open(uri, options) {
    const initial = await this.resolve(uri);
    if (this.isRoot(initial.key, initial.root)) throw vscode.FileSystemError.FileIsADirectory(uri);
    const info = await s3.stat(initial.profile, initial.key);
    if (!options.create && !info) throw vscode.FileSystemError.FileNotFound(uri);
    if (info?.size > 5 * 1024 ** 3) throw vscode.FileSystemError.Unavailable("Files over 5 GiB are not supported.");
    const directory = await fs.mkdtemp(path.join(os.tmpdir(), "triton-s3-file-"));
    const filename = path.join(directory, "object");
    try {
      if (!options.create) await s3.download(initial.profile, initial.key, filename, { etag: info.etag });
      const file = await fs.open(filename, options.create ? "wx+" : "r", 0o600);
      const handle = this.nextHandle++;
      this.handles.set(handle, { uri, initial, identity: identity(initial.profile), directory, filename, file, write: !!options.create, etag: info?.etag, metadata: info?.metadata });
      return handle;
    } catch (error) { await fs.rm(directory, { recursive: true, force: true }); throw error; }
  }
  async read(handle, position, data, offset, length) {
    const entry = this.handles.get(handle);
    if (!entry) throw vscode.FileSystemError.Unavailable("S3 file handle closed.");
    return (await entry.file.read(data, offset, length, position)).bytesRead;
  }
  async write(handle, position, data, offset, length) {
    const entry = this.handles.get(handle);
    if (!entry?.write) throw vscode.FileSystemError.NoPermissions("S3 file is not open for writing.");
    if (position + length > 5 * 1024 ** 3) {
      entry.failed = true;
      throw vscode.FileSystemError.Unavailable("Files over 5 GiB are not supported.");
    }
    try { return (await entry.file.write(data, offset, length, position)).bytesWritten; }
    catch (error) { entry.failed = true; throw error; }
  }
  async abort(handle) {
    const entry = this.handles.get(handle);
    if (!entry) return;
    this.handles.delete(handle);
    await entry.file.close();
    await fs.rm(entry.directory, { recursive: true, force: true });
  }
  async close(handle) {
    const entry = this.handles.get(handle);
    if (!entry) return;
    this.handles.delete(handle);
    try {
      await entry.file.close();
      if (entry.write && !entry.failed) {
        const { profile } = await this.resolve(entry.uri);
        if (identity(profile) !== entry.identity) throw vscode.FileSystemError.Unavailable("The profile changed; S3 write cancelled.");
        await s3.upload(profile, entry.initial.key, entry.filename, { etag: entry.etag, metadata: entry.metadata });
        this.notify(entry.uri);
      }
    } finally { await fs.rm(entry.directory, { recursive: true, force: true }); }
  }
  async copy(source, destination, options) { return this.transfer(source, destination, options, "copy"); }
  async rename(source, destination, options) { return this.transfer(source, destination, options, "move"); }
  async transfer(source, destination, options, mode) {
    const node = await this.node(source);
    const dest = await this.resolve(destination);
    if (this.isRoot(dest.key, dest.root)) throw vscode.FileSystemError.NoPermissions("Cannot replace an S3 profile root.");
    const parent = dest.key.slice(0, dest.key.lastIndexOf("/") + 1);
    await transfers.transfer([node], { kind: "folder", prefix: parent, bucket: dest.profile.bucket, profileId: dest.profile.id },
      this.getProfile, mode, { name: dest.key.slice(parent.length), overwrite: async () => {
        if (!options.overwrite) throw vscode.FileSystemError.FileExists(destination);
        return "replace";
      } });
    this.notify(source); this.notify(destination);
  }
  async delete(uri, options) {
    const node = await this.node(uri);
    if (node.root) throw vscode.FileSystemError.NoPermissions("Cannot delete an S3 profile root.");
    const entries = await transfers.plan([node], this.getProfile);
    if (node.kind === "folder" && !options.recursive && entries.some((entry) => entry.key !== node.prefix))
      throw vscode.FileSystemError.NoPermissions("Folder is not empty.");
    let deleted = 0;
    try {
      for (const entry of entries) {
        const profile = await entry.current();
        const info = await s3.stat(profile, entry.key);
        if (info) await s3.remove(profile, entry.key, undefined, info.etag);
        deleted++;
      }
    } catch (error) { throw new Error(`${error.message} (${deleted} objects deleted.)`); }
    finally { this.notify(uri); }
  }
  dispose() {
    for (const handle of this.handles.keys()) this.abort(handle).catch(() => {});
    this.changed.dispose();
  }
}
module.exports = { S3FileSystem, SCHEME };
