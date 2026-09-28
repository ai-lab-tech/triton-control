const fs = require("node:fs/promises");
const path = require("node:path");

function within(root, file) {
  const relative = path.relative(root, file);
  return (
    relative === "" ||
    (relative !== ".." &&
      !relative.startsWith(`..${path.sep}`) &&
      !path.isAbsolute(relative))
  );
}
async function planUpload(sources, roots, prefix, signal) {
  const realRoots = await Promise.all(roots.map((root) => fs.realpath(root)));
  const result = [];
  const keys = new Set();
  async function walk(file, key) {
    signal?.throwIfAborted();
    const stat = await fs.lstat(file);
    if (stat.isSymbolicLink())
      throw new Error(
        "Symbolic links are not uploaded. Select regular workspace files or folders.",
      );
    const real = await fs.realpath(file);
    if (!realRoots.some((root) => within(root, real)))
      throw new Error("Only files inside the open workspace can be uploaded.");
    if (stat.isDirectory()) {
      const names = await fs.readdir(file);
      for (const name of names)
        await walk(path.join(file, name), `${key}/${name}`);
    } else if (stat.isFile()) {
      if (stat.size > 5 * 1024 ** 3)
        throw new Error("Files over 5 GiB require multipart upload.");
      if (keys.has(key))
        throw new Error(
          `Multiple selected files have the same destination: ${key}`,
        );
      keys.add(key);
      result.push({ file: real, key, size: stat.size });
      if (result.length > 10000)
        throw new Error("Select at most 10,000 files per upload.");
    } else throw new Error("Only regular files and folders can be uploaded.");
  }
  const selected = [...new Set(sources.map((file) => path.resolve(file)))];
  for (const file of selected.filter(
    (file) =>
      !selected.some((parent) => parent !== file && within(parent, file)),
  )) {
    await walk(file, `${prefix}${path.basename(file)}`);
  }
  return result;
}
module.exports = { planUpload, within };
