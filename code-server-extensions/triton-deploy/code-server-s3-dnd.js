// Managed code-server 4.125.0 integration. The extension API cannot override
// native Explorer drag effects. Patch both the cursor and the committed action.
const fs = require("node:fs/promises");
const path = require("node:path");

function s3DragCopy(event, items, target, fallback) {
  if (!event || !Array.isArray(items) || !items.length || items.some((item) => item.isRoot)) return fallback;
  const destination = target?.isDirectory ? target : target?.parent;
  if (!destination?.resource || !items.every((item) => item.resource)) return fallback;
  const remote = (resource) => resource.scheme === "triton-s3";
  if (!remote(destination.resource) && !items.some((item) => remote(item.resource))) return fallback;
  // Preserve the platform's explicit copy modifier (Ctrl on Windows/Linux,
  // Option on macOS); Shift forces a move otherwise.
  if (fallback) return true;
  if (event.shiftKey) return false;
  const volume = (resource) => resource.authority?.startsWith("endpoint-")
    ? `${resource.authority}/${resource.path.split("/")[1] || ""}` : resource.authority;
  return items.some(({ resource }) => resource.scheme !== destination.resource.scheme ||
    volume(resource) !== volume(destination.resource));
}

const marker = "/* triton-s3-windows-dnd-v1 */";
const helper = marker + s3DragCopy.toString().replace("function s3DragCopy", "tritonS3DragCopy");
// code-server serves its browser entry bundle. The internal VS Code bundle is
// shipped too, with different minified symbols; customize both explicitly.
const bundles = [
  ["lib/vscode/out/vs/code/browser/workbench/workbench.js", "uK", "w_", "e5e"],
  ["lib/vscode/out/vs/workbench/workbench.web.main.internal.js", "cK", "y_", "i5e"],
];
function patchWorkbench(source) {
  // Upgrade the previous managed helper while keeping both patched call sites.
  if (source.includes(marker) && !source.includes(helper)) {
    const start = source.indexOf(marker);
    const end = source.indexOf("handleDragOver(", start);
    if (end > start && source.slice(start, end).startsWith(marker + "tritonS3DragCopy(event, items, target, fallback) {"))
      source = source.slice(0, start) + helper + source.slice(end);
  }
  for (const [, native, controller, distinct] of bundles) {
    const hover = `handleDragOver(o,e,t,i,n){let r=n&&(n.ctrlKey&&!ht||n.altKey&&ht),s=o instanceof ${native}`;
    const drop = `let l=${distinct}([...s.keys()],m=>m.resource),c=n.ctrlKey&&!ht||n.altKey&&ht;`;
    const patchedHover = helper + `handleDragOver(o,e,t,i,n){let r=this.tritonS3DragCopy(n,o instanceof ${native}?[]:${controller}.getStatsFromDragAndDropData(o),e,n&&(n.ctrlKey&&!ht||n.altKey&&ht)),s=o instanceof ${native}`;
    const patchedDrop = `let l=${distinct}([...s.keys()],m=>m.resource),c=this.tritonS3DragCopy(n,l,e,n.ctrlKey&&!ht||n.altKey&&ht);`;
    if (source.includes(marker)) {
      if (source.split(patchedHover).length === 2 && source.split(patchedDrop).length === 2) return source;
    } else if (source.split(hover).length === 2 && source.split(drop).length === 2) {
      return source.replace(hover, patchedHover).replace(drop, patchedDrop);
    }
  }
  throw new Error(source.includes(marker)
    ? "Incomplete S3 drag-and-drop customization. Reinstall code-server 4.125.0."
    : "Unsupported code-server Explorer build. Windows-style S3 dragging requires code-server 4.125.0.");
}

async function install(root) {
  // Validate every bundle before mutating any of them.
  const changes = await Promise.all(bundles.map(async ([relative]) => {
    const filename = path.join(root, relative);
    const source = await fs.readFile(filename, "utf8");
    return { filename, source, patched: patchWorkbench(source) };
  }));
  for (const { filename, source, patched } of changes) {
    if (patched === source) continue;
    const info = await fs.stat(filename);
    const temporary = filename + ".triton-tmp";
    try {
      await fs.writeFile(temporary, patched, { mode: info.mode, flag: "wx" });
      await fs.rename(temporary, filename);
    } finally { await fs.rm(temporary, { force: true }); }
  }
}
if (require.main === module) {
  if (!process.argv[2]) throw new Error("Pass the code-server installation directory.");
  install(process.argv[2]).then(() => console.log("Windows-style S3 drag-and-drop enabled."))
    .catch((error) => { console.error(error.message); process.exitCode = 1; });
}
module.exports = { s3DragCopy, patchWorkbench, install };
