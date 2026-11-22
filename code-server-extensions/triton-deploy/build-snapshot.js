// Bounded, immutable USTAR snapshot. No shell, symlinks or credential files.
const fs = require("node:fs/promises");
const { constants } = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const secretDirs = new Set([".git", ".aws", ".ssh", ".kube", ".docker", ".azure", ".config", ".gnupg"]);
const secretNames = new Set([".env", ".npmrc", ".pypirc", ".netrc", "credentials", "kubeconfig", "id_rsa", "id_ed25519"]);
function secretPath(name) {
  const parts = name.toLowerCase().split("/");
  const file = parts.at(-1);
  return parts.some(p => secretDirs.has(p)) || secretNames.has(file) || file.startsWith(".env.") || /\.(pem|key|p12|pfx|jks)$/.test(file);
}
function safePath(name) {
  if (!name || name.includes("\\") || name.startsWith("/") || name.split("/").some(p => !p || p === "." || p === "..") || /[\x00-\x1f\x7f]/.test(name))
    throw new Error("Select a relative file path inside the context folder.");
  return name;
}
function header(name, size, executable) {
  const block = Buffer.alloc(512);
  let leaf = name, prefix = "";
  if (Buffer.byteLength(name) > 100) {
    const split = name.lastIndexOf("/");
    leaf = name.slice(split + 1); prefix = name.slice(0, split);
    if (split < 0 || Buffer.byteLength(leaf) > 100 || Buffer.byteLength(prefix) > 155)
      throw new Error("Context path exceeds POSIX tar limits.");
  }
  block.write(leaf, 0, 100);
  const octal = (value, offset, width) => block.write(value.toString(8).padStart(width - 1, "0") + "\0", offset, width);
  octal(executable ? 0o755 : 0o644, 100, 8); octal(0, 108, 8); octal(0, 116, 8);
  octal(size, 124, 12); octal(0, 136, 12);
  block.fill(32, 148, 156); block[156] = 48;
  block.write("ustar\0", 257); block.write("00", 263); block.write(prefix, 345, 155);
  const checksum = block.reduce((sum, byte) => sum + byte, 0);
  block.write(checksum.toString(8).padStart(6, "0") + "\0 ", 148, 8);
  return block;
}
async function snapshot(folder, dockerfile, maxBytes, baseImages = [], dependencies = [], selectedFilesOnly = false) {
  safePath(dockerfile); dependencies.forEach(safePath);
  const root = await fs.realpath(folder);
  const limit = Math.min(maxBytes, 64 * 1024 * 1024);
  if (!Number.isSafeInteger(limit) || limit < 1024) throw new Error("Invalid build context limit.");
  const included = new Set([dockerfile, ...dependencies, ".dockerignore", dockerfile + ".dockerignore"]);
  const ancestors = new Set();
  if (selectedFilesOnly) {
    for (const file of included) {
      for (let parent = path.posix.dirname(file); parent !== "."; parent = path.posix.dirname(parent))
        ancestors.add(parent);
    }
  }
  const chunks = [], names = new Set();
  let bytes = 1024, entries = 0;
  async function visit(relative = "") {
    const directory = path.join(root, relative);
    if (await fs.realpath(directory) !== directory) throw new Error("Context directories must not be symlinks.");
    for (const name of (await fs.readdir(directory)).sort()) {
      const entry = relative ? `${relative}/${name}` : name;
      if (selectedFilesOnly && !included.has(entry) && !ancestors.has(entry)) continue;
      if (++entries > 10000) throw new Error("Context contains too many entries.");
      safePath(entry);
      if (secretPath(entry)) continue;
      const file = path.join(root, entry), info = await fs.lstat(file);
      if (info.isSymbolicLink()) throw new Error("Context must not contain symlinks.");
      if (info.isDirectory()) { await visit(entry); continue; }
      if (!info.isFile()) throw new Error("Context must contain only regular files.");
      if (await fs.realpath(file) !== file) throw new Error("Context path changed while capturing.");
      bytes += 512 + Math.ceil(info.size / 512) * 512;
      if (bytes > limit) throw new Error("Context exceeds the build upload limit. Select a smaller folder.");
      const handle = await fs.open(file, constants.O_RDONLY | constants.O_NOFOLLOW);
      let data;
      try {
        const opened = await handle.stat();
        if (opened.ino !== info.ino || opened.dev !== info.dev || !opened.isFile() || opened.size !== info.size)
          throw new Error("Context changed while capturing. Retry with stable files.");
        data = Buffer.alloc(info.size);
        let offset = 0;
        while (offset < data.length) {
          const result = await handle.read(data, offset, data.length - offset, offset);
          if (!result.bytesRead) throw new Error("Context changed while capturing.");
          offset += result.bytesRead;
        }
        const after = await handle.stat();
        if (after.size !== info.size || after.mtimeMs !== info.mtimeMs || after.ctimeMs !== info.ctimeMs)
          throw new Error("Context changed while capturing.");
      } finally { await handle.close(); }
      if (/-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----|(?:AKIA|ASIA)[A-Z0-9]{16}/.test(data.toString("latin1")))
        throw new Error("Credential material detected in the selected context.");
      names.add(entry);
      chunks.push(header(entry, data.length, !!(info.mode & 0o111)), data, Buffer.alloc((-data.length % 512 + 512) % 512));
    }
  }
  await visit();
  if (!names.has(dockerfile) || dependencies.some(name => !names.has(name))) throw new Error("Dockerfile or dependency file is missing or excluded.");
  const archive = Buffer.concat([...chunks, Buffer.alloc(1024)]);
  return { archive, request: { schema_version: 1, archive_digest: "sha256:" + crypto.createHash("sha256").update(archive).digest("hex"),
    archive_bytes: archive.length, dockerfile, allowed_paths: selectedFilesOnly ? [...names] : ["."], dependency_files: dependencies, base_images: baseImages } };
}
module.exports = { snapshot, safePath, secretPath };
