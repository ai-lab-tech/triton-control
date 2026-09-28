const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const s3 = require("./s3-client");
const { within } = require("./s3-files");

const rootPrefix = (profile) => profile.prefix ? profile.prefix.replace(/^\/+|\/+$/g, "") + "/" : "";
const location = (profile) => JSON.stringify([profile.endpoint, profile.bucket, rootPrefix(profile), profile.force_path_style !== false]);
function validate(node, profile) {
  const key = node.kind === "folder" ? node.prefix : node.key;
  if (!["folder", "file"].includes(node.kind) || typeof key !== "string" ||
      node.bucket !== profile.bucket || !key.startsWith(rootPrefix(profile)))
    throw new Error("The S3 profile destination changed. Refresh and try again.");
  return key;
}
async function bind(node, getProfile) {
  const profile = await getProfile(node.profileId);
  validate(node, profile);
  const identity = location(profile);
  return async () => {
    const current = await getProfile(node.profileId);
    validate(node, current);
    if (location(current) !== identity) throw new Error("The S3 profile destination changed. Refresh and try again.");
    return current;
  };
}
function selectedNodes(nodes) {
  const unique = [...new Map(nodes.map((node) => [`${node.profileId}:${node.kind}:${node.key ?? node.prefix}`, node])).values()];
  for (const node of unique) {
    if (node.root || !["file", "folder"].includes(node.kind) || !(node.key ?? node.prefix))
      throw new Error("Select objects or folders below the S3 profile root.");
  }
  return unique.filter((node) => !unique.some((parent) => parent !== node && parent.kind === "folder" &&
    parent.profileId === node.profileId && parent.bucket === node.bucket && (node.key ?? node.prefix).startsWith(parent.prefix)));
}
async function plan(nodes, getProfile, signal) {
  const result = [];
  for (const node of selectedNodes(nodes)) {
    signal?.throwIfAborted();
    const current = await bind(node, getProfile);
    const key = validate(node, await current());
    const base = node.kind === "folder" ? key.slice(0, -1) : key;
    const parent = base.slice(0, base.lastIndexOf("/") + 1);
    const add = (entry) => {
      if (!entry.key.startsWith(key) || (node.kind === "file" && entry.key !== key))
        throw new Error("S3 returned an object outside the selection.");
      if (entry.size > 5 * 1024 ** 3) throw new Error("Files over 5 GiB are not supported.");
      result.push({ ...entry, relative: entry.key.slice(parent.length), current, node });
      if (result.length > 10000) throw new Error("Select at most 10,000 objects per operation.");
    };
    if (node.kind === "file") {
      const info = await s3.stat(await current(), key, signal);
      if (!info) throw new Error("The S3 source object no longer exists.");
      add({ key, ...info });
    } else {
      if (!key.endsWith("/") || key === rootPrefix(await current()))
        throw new Error("Select a folder below the profile root.");
      const seen = new Set();
      let next;
      do {
        signal?.throwIfAborted();
        const page = await s3.list(await current(), key, next, signal, true);
        page.files.forEach(add);
        next = page.next;
        if (next && seen.has(next)) throw new Error("S3 returned repeated listing pages.");
        seen.add(next);
      } while (next);
    }
  }
  const destinations = new Set();
  for (const file of result) {
    if (destinations.has(file.relative)) throw new Error("Selections contain duplicate destination names. Transfer them separately.");
    destinations.add(file.relative);
  }
  return result;
}
function localRelative(key) {
  const relative = key.endsWith("/") ? key.slice(0, -1) : key;
  if (!relative || relative.includes("\\") || /[\x00-\x1f]/.test(relative) ||
      relative.split("/").some((part) => !part || part === "." || part === ".."))
    throw new Error("This S3 object name cannot be safely represented as a workspace path.");
  return relative;
}
async function stage(nodes, getProfile, { signal, report = () => {} } = {}) {
  const entries = await plan(nodes, getProfile, signal);
  entries.forEach((entry) => localRelative(entry.relative));
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "triton-s3-download-"));
  try {
    for (const entry of entries) {
      signal?.throwIfAborted();
      const filename = path.join(directory, localRelative(entry.relative));
      if (entry.key.endsWith("/")) { await fs.mkdir(filename, { recursive: true }); continue; }
      await fs.mkdir(path.dirname(filename), { recursive: true });
      const profile = await entry.current();
      const info = await s3.stat(profile, entry.key, signal);
      if (!info?.etag) throw new Error("The source object changed or has no ETag. Refresh and try again.");
      report(entry.relative);
      await s3.download(profile, entry.key, filename, { signal, etag: info.etag });
    }
    return { directory, entries, paths: (await fs.readdir(directory)).map((name) => path.join(directory, name)) };
  } catch (error) {
    await fs.rm(directory, { recursive: true, force: true });
    throw error;
  }
}
async function transfer(nodes, target, getProfile, mode, { signal, overwrite, report = () => {}, name } = {}) {
  if (!["copy", "move"].includes(mode)) throw new Error("Choose Copy or Move.");
  const destination = await bind(target, getProfile);
  if (target.kind !== "folder") throw new Error("Choose a destination folder.");
  const destProfile = await destination();
  for (const node of selectedNodes(nodes)) {
    const sourceProfile = await getProfile(node.profileId);
    const sameStore = sourceProfile.endpoint.replace(/\/$/, "") === destProfile.endpoint.replace(/\/$/, "") && sourceProfile.bucket === destProfile.bucket;
    const sourceKey = node.key ?? node.prefix;
    const destinationName = name || sourceKey.replace(/\/$/, "").split("/").pop();
    const destKey = target.prefix + destinationName + (node.kind === "folder" ? "/" : "");
    if (sameStore && (destKey === sourceKey || (node.kind === "folder" && target.prefix.startsWith(sourceKey))))
      throw new Error("Cannot copy or move an S3 item into itself or its own subfolder.");
  }
  const entries = await plan(nodes, getProfile, signal);
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "triton-s3-transfer-"));
  let copied = 0, deleted = 0, skipped = 0;
  const committed = [];
  try {
    for (const entry of entries) {
      signal?.throwIfAborted();
      const source = await entry.current();
      const dest = await destination();
      const relative = name ? name + entry.relative.slice(entry.relative.split("/")[0].length) : entry.relative;
      const key = target.prefix + relative;
      const existing = await s3.head(dest, key, signal);
      if (existing) {
        const choice = await overwrite?.(key);
        if (choice === "skip") { skipped++; continue; }
        if (choice !== "replace") throw new Error("Transfer cancelled.");
      }
      const info = await s3.stat(source, entry.key, signal);
      if (!info?.etag) throw new Error("The source object changed or has no ETag. Refresh and try again.");
      const filename = path.join(directory, "object");
      report(`Copying ${entry.relative}`);
      await s3.download(source, entry.key, filename, { signal, etag: info.etag });
      let copiedEtag;
      try {
        await entry.current();
        copiedEtag = await s3.upload(await destination(), key, filename, { signal, etag: existing, metadata: info.metadata });
      } finally { await fs.unlink(filename); }
      copied++;
      committed.push({ entry, etag: info.etag, key, copiedEtag });
    }
    // A failed/cancelled copy phase leaves every source intact.
    if (mode === "move") for (const { entry, etag, key, copiedEtag } of committed) {
      signal?.throwIfAborted();
      const dest = await destination();
      if (!copiedEtag || await s3.head(dest, key, signal) !== copiedEtag)
        throw new Error("The copied destination changed. Source retained.");
      report(`Removing source ${entry.relative}`);
      await s3.remove(await entry.current(), entry.key, signal, etag);
      deleted++;
    }
    return { copied, deleted, skipped };
  } catch (error) {
    throw new Error(`${error.message} (${copied} copied, ${deleted} source objects removed, ${skipped} skipped.)`);
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
}
async function saveStage(staged, destination, roots, { signal, overwrite } = {}) {
  const realRoots = await Promise.all(roots.map((root) => fs.realpath(root)));
  const realDestination = await fs.realpath(destination);
  if (!realRoots.some((root) => within(root, realDestination))) throw new Error("Choose a folder inside the workspace.");
  let saved = 0, skipped = 0;
  for (const entry of staged.entries) {
    signal?.throwIfAborted();
    const relative = localRelative(entry.relative);
    const target = path.join(realDestination, relative);
    const directories = relative.split("/").slice(0, entry.key.endsWith("/") ? undefined : -1);
    let parent = realDestination;
    for (const part of directories) {
      parent = path.join(parent, part);
      await fs.mkdir(parent).catch((error) => { if (error.code !== "EEXIST") throw error; });
      const stat = await fs.lstat(parent);
      if (stat.isSymbolicLink() || !stat.isDirectory()) throw new Error("Download destination contains a symbolic link or file.");
    }
    if (entry.key.endsWith("/")) continue;
    const existing = await fs.lstat(target).catch((error) => { if (error.code !== "ENOENT") throw error; return null; });
    if (existing && (!existing.isFile() || existing.isSymbolicLink())) throw new Error("Download destination is not a regular file.");
    if (existing) {
      const choice = await overwrite?.(relative);
      if (choice === "skip") { skipped++; continue; }
      if (choice !== "replace") throw new Error(`Download cancelled (${saved} saved, ${skipped} skipped).`);
    }
    signal?.throwIfAborted();
    const temporary = path.join(path.dirname(target), `.triton-download-${crypto.randomBytes(12).toString("hex")}`);
    try {
      await fs.copyFile(path.join(staged.directory, relative), temporary, 1);
      if (existing) {
        const now = await fs.lstat(target);
        if (now.isSymbolicLink() || now.ino !== existing.ino || now.mtimeMs !== existing.mtimeMs || now.size !== existing.size)
          throw new Error("The workspace destination changed during download.");
        await fs.rename(temporary, target);
      } else {
        await fs.link(temporary, target); // Never replace a file created since the existence check.
      }
      saved++;
    } finally { await fs.unlink(temporary).catch((error) => { if (error.code !== "ENOENT") throw error; }); }
  }
  return { saved, skipped };
}
module.exports = { plan, stage, transfer, saveStage, selectedNodes, localRelative };
